"""
IPL Fantasy 2026 — DatabaseManager                          Golden File v5.7
===========================================================================
v5.7:
  Schema:
    players.points          INTEGER — cumulative season fantasy pts (cap/vc-aware
                            aggregate across all weeks a player appeared in a
                            user's selected XI). Kept in sync by
                            update_player_points().
    user_selections.points_per_match  TEXT (JSON) — per-match breakdown stored
                            directly on the selection row as
                            {match_id: pts, ...}. One blob per (user, week),
                            so every week owns its own data with zero aliasing
                            between W1-W10.
  Logic:
    update_week_points() now also:
      • writes points_per_match JSON onto every user_selections row.
      • calls update_player_points() so players.points stays current.
    update_player_points() — new method; aggregates awarded pts (cap/vc baked)
      from user_match_points per player and writes to players.points.
    get_players() returns points column.
    get_history() returns points_per_match per week row.
v5.6: user_match_points table, players.season_pts, per-match leaderboard.
v5.5: Leaderboard from SUM(week_pts), MVP via pmp.
v5.4: week_pts + update_week_points().
v5.3: rebuild_scores_and_points().
"""

import json
import math
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import DEADLINE_HOUR, DEADLINE_MIN, DB_VER  # noqa: F401


_SCHEMA = """
PRAGMA journal_mode  = WAL;
PRAGMA foreign_keys  = ON;

CREATE TABLE IF NOT EXISTS players (
    id         TEXT    PRIMARY KEY,
    name       TEXT    NOT NULL,
    team       TEXT    NOT NULL,
    price      REAL    NOT NULL DEFAULT 0 CHECK (price >= 0),
    role       TEXT    NOT NULL DEFAULT 'BAT' CHECK (role IN ('BAT','BOWL','AR','WK')),
    season_pts INTEGER NOT NULL DEFAULT 0,
    points     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS matches (
    id            TEXT PRIMARY KEY,
    week_no       INTEGER NOT NULL DEFAULT 1 CHECK (week_no >= 1),
    title         TEXT NOT NULL DEFAULT '',
    teams_json    TEXT NOT NULL DEFAULT '[]',
    date_label    TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'upcoming'
                  CHECK (status IN ('upcoming','live','completed')),
    scorecard_url TEXT,
    raw_json      TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS user_selections (
    display_name    TEXT    NOT NULL CHECK (length(display_name) BETWEEN 1 AND 30),
    week_no         INTEGER NOT NULL DEFAULT 1 CHECK (week_no >= 1),
    tw_team_json    TEXT    NOT NULL DEFAULT '[]',
    tw_cap_id       TEXT,
    tw_vc_id        TEXT,
    nw_team_json    TEXT    NOT NULL DEFAULT '[]',
    nw_cap_id       TEXT,
    nw_vc_id        TEXT,
    week_pts        INTEGER NOT NULL DEFAULT 0,
    points_per_match TEXT   NOT NULL DEFAULT '{}',
    PRIMARY KEY (display_name, week_no)
);

CREATE TABLE IF NOT EXISTS match_scores (
    match_id       TEXT    NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    player_id      TEXT    NOT NULL,
    runs           INTEGER NOT NULL DEFAULT 0 CHECK (runs >= 0),
    balls          INTEGER NOT NULL DEFAULT 0 CHECK (balls >= 0),
    fours          INTEGER NOT NULL DEFAULT 0 CHECK (fours >= 0),
    sixes          INTEGER NOT NULL DEFAULT 0 CHECK (sixes >= 0),
    got_out        INTEGER NOT NULL DEFAULT 0 CHECK (got_out  IN (0,1)),
    duck           INTEGER NOT NULL DEFAULT 0 CHECK (duck     IN (0,1)),
    overs          REAL    NOT NULL DEFAULT 0 CHECK (overs >= 0),
    runs_conceded  INTEGER NOT NULL DEFAULT 0 CHECK (runs_conceded >= 0),
    wickets        INTEGER NOT NULL DEFAULT 0 CHECK (wickets  BETWEEN 0 AND 10),
    maidens        INTEGER NOT NULL DEFAULT 0 CHECK (maidens  >= 0),
    lbw_bowled     INTEGER NOT NULL DEFAULT 0 CHECK (lbw_bowled >= 0),
    catches        INTEGER NOT NULL DEFAULT 0 CHECK (catches  BETWEEN 0 AND 10),
    stumpings      INTEGER NOT NULL DEFAULT 0 CHECK (stumpings >= 0),
    run_out_direct INTEGER NOT NULL DEFAULT 0 CHECK (run_out_direct >= 0),
    run_out_assist INTEGER NOT NULL DEFAULT 0 CHECK (run_out_assist >= 0),
    played         INTEGER NOT NULL DEFAULT 0 CHECK (played   IN (0,1)),
    raw_score_json TEXT    NOT NULL DEFAULT '{}',
    PRIMARY KEY (match_id, player_id)
);

CREATE TABLE IF NOT EXISTS player_match_points (
    match_id      TEXT    NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    player_id     TEXT    NOT NULL,
    week_no       INTEGER NOT NULL,
    base_pts      INTEGER NOT NULL DEFAULT 0,
    multiplier    REAL    NOT NULL DEFAULT 1.0 CHECK (multiplier IN (1.0, 1.5, 2.0)),
    final_pts     REAL    NOT NULL DEFAULT 0,
    calculated_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (match_id, player_id)
);

CREATE TABLE IF NOT EXISTS user_match_points (
    display_name TEXT    NOT NULL CHECK (length(display_name) BETWEEN 1 AND 30),
    week_no      INTEGER NOT NULL,
    match_id     TEXT    NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    pts          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (display_name, match_id)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_us_name     ON user_selections (display_name);
CREATE INDEX IF NOT EXISTS idx_us_week     ON user_selections (week_no);
CREATE INDEX IF NOT EXISTS idx_ms_match    ON match_scores (match_id);
CREATE INDEX IF NOT EXISTS idx_pmp_player  ON player_match_points (player_id);
CREATE INDEX IF NOT EXISTS idx_pmp_week    ON player_match_points (week_no);
CREATE INDEX IF NOT EXISTS idx_pmp_match_p ON player_match_points (match_id, player_id);
CREATE INDEX IF NOT EXISTS idx_ump_name    ON user_match_points (display_name);
CREATE INDEX IF NOT EXISTS idx_ump_week    ON user_match_points (week_no);
"""


# v5.6: total_pts sourced from user_match_points (per-match, cap/vc baked in)
_LEADERBOARD_SQL = """
WITH
user_totals AS (
    SELECT us.display_name,
           COALESCE(SUM(ump.pts), 0) AS total_pts,
           COUNT(DISTINCT CASE WHEN ump.pts > 0 THEN ump.match_id END) AS matches_counted
    FROM  user_selections us
    LEFT JOIN user_match_points ump ON ump.display_name = us.display_name
        AND (CAST(:week_no AS INTEGER) IS NULL OR ump.week_no = CAST(:week_no AS INTEGER))
    GROUP BY us.display_name
),
scored_points AS (
    SELECT us.display_name, pmp.match_id, je.value AS player_id, pmp.base_pts,
           CASE WHEN je.value = us.tw_cap_id THEN ROUND(pmp.base_pts * 2.0)
                WHEN je.value = us.tw_vc_id  THEN ROUND(pmp.base_pts * 1.5)
                ELSE pmp.base_pts END AS awarded_pts
    FROM  user_selections us, JSON_EACH(us.tw_team_json) AS je
    INNER JOIN player_match_points pmp ON pmp.player_id = je.value
        AND pmp.week_no = us.week_no
    WHERE (CAST(:week_no AS INTEGER) IS NULL OR us.week_no = CAST(:week_no AS INTEGER))
),
mvp_data AS (
    SELECT display_name, MAX(awarded_pts) AS mvp_awarded_pts
    FROM  scored_points GROUP BY display_name
),
mvp_resolve AS (
    SELECT sp.display_name, MIN(sp.player_id) AS mvp_player_id, sp.awarded_pts AS mvp_pts
    FROM  scored_points sp
    INNER JOIN mvp_data md ON md.display_name=sp.display_name AND sp.awarded_pts=md.mvp_awarded_pts
    GROUP BY sp.display_name, sp.awarded_pts
),
ranked AS (
    SELECT ut.display_name, ut.total_pts, ut.matches_counted,
           COALESCE(mr.mvp_player_id,'') AS mvp_player_id,
           COALESCE(mr.mvp_pts, 0)       AS mvp_pts,
           DENSE_RANK() OVER (ORDER BY ut.total_pts DESC) AS rank
    FROM  user_totals ut LEFT JOIN mvp_resolve mr USING (display_name)
),
league_benchmarks AS (
    SELECT ROUND(AVG(total_pts),1) AS league_avg,
           MAX(total_pts) AS top_score, COUNT(*) AS member_count
    FROM  user_totals
)
SELECT r.rank, r.display_name, r.total_pts, r.matches_counted,
       r.mvp_player_id, COALESCE(p.name, r.mvp_player_id) AS mvp_player_name,
       r.mvp_pts, lb.league_avg, lb.top_score, lb.member_count
FROM ranked r CROSS JOIN league_benchmarks lb
LEFT JOIN players p ON p.id = r.mvp_player_id
ORDER BY r.rank, r.display_name
"""


def _normalise_overs(raw: float) -> float:
    if raw <= 0: return 0.0
    full_overs = math.floor(raw)
    ball_digit = min(5, max(0, round((raw - full_overs) * 10)))
    return full_overs + ball_digit / 6


def calc_pts(s: dict) -> int:
    if not s or not s.get("played"): return 0
    runs    = max(0, int(s.get("runs",  0)))
    balls   = max(0, int(s.get("balls", 0)))
    fours   = max(0, min(runs, int(s.get("fours",  0))))
    sixes   = max(0, int(s.get("sixes",   0)))
    wickets = max(0, min(10,  int(s.get("wickets", 0))))
    overs   = _normalise_overs(max(0.0, float(s.get("overs", 0))))
    rc      = max(0, int(s.get("runsConceded",  s.get("runs_conceded",  0))))
    maidens = max(0, int(s.get("maidens", 0)))
    catches = max(0, min(10,  int(s.get("catches",  0))))
    stump   = max(0, int(s.get("stumpings",     0)))
    rod     = max(0, int(s.get("runOutDirect",  s.get("run_out_direct", 0))))
    roa     = max(0, int(s.get("runOutAssist",  s.get("run_out_assist", 0))))
    lbwb    = max(0, min(wickets, int(s.get("lbwBowled", s.get("lbw_bowled", 0)))))
    duck    = bool(s.get("duck", False))
    got_out = bool(s.get("gotOut", s.get("got_out", False)))
    pts = 4
    pts += runs + fours + sixes * 2
    if   runs >= 100: pts += 16
    elif runs >= 50:  pts += 8
    elif runs >= 30:  pts += 4
    if duck and got_out and balls >= 1: pts -= 2
    if balls >= 10:
        sr = (runs / balls) * 100
        if   sr >  125: pts += 6
        elif sr >= 110: pts += 4
        elif sr >= 100: pts += 2
        elif sr <   60: pts -= 4
        elif sr <   70: pts -= 2
    pts += wickets * 25 + lbwb * 8 + maidens * 12
    if wickets >= 2: pts += 4
    if wickets >= 3: pts += 4
    if wickets >= 4: pts += 8
    if wickets >= 5: pts += 8
    if overs >= 2:
        eco = rc / overs
        if   eco >  12: pts -= 6
        elif eco >= 11: pts -= 4
        elif eco >= 10: pts -= 2
        elif eco <   5: pts += 6
        elif eco <   6: pts += 4
        elif eco <   7: pts += 2
    pts += catches * 8
    if catches >= 3: pts += 4
    pts += stump * 12 + rod * 12 + roa * 6
    return round(pts)


def _jloads(s, default):
    if not s: return default
    try:    return json.loads(s)
    except: return default


def _upsert_match(con: sqlite3.Connection, m: dict) -> None:
    mid = m.get("id")
    if not mid: return
    raw_copy = {k: v for k, v in m.items() if k != "scores"}
    teams  = m.get("teams", [])
    date   = m.get("date", m.get("date_label", ""))
    wk     = int(m.get("wk", m.get("week_no", 1)))
    title  = m.get("title", "")
    status = m.get("status", "upcoming")
    con.execute("""
        INSERT INTO matches (id,week_no,title,teams_json,date_label,status,raw_json)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            week_no=excluded.week_no, title=excluded.title, teams_json=excluded.teams_json,
            date_label=excluded.date_label, status=excluded.status, raw_json=excluded.raw_json
    """, (mid, wk, title, json.dumps(teams), date, status, json.dumps(raw_copy)))
    for pid, sc in m.get("scores", {}).items():
        if not isinstance(sc, dict): continue
        con.execute("""
            INSERT INTO match_scores (
                match_id,player_id,runs,balls,fours,sixes,got_out,duck,
                overs,runs_conceded,wickets,maidens,lbw_bowled,
                catches,stumpings,run_out_direct,run_out_assist,played,raw_score_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(match_id,player_id) DO UPDATE SET
                runs=excluded.runs, balls=excluded.balls, fours=excluded.fours,
                sixes=excluded.sixes, got_out=excluded.got_out, duck=excluded.duck,
                overs=excluded.overs, runs_conceded=excluded.runs_conceded,
                wickets=excluded.wickets, maidens=excluded.maidens,
                lbw_bowled=excluded.lbw_bowled, catches=excluded.catches,
                stumpings=excluded.stumpings, run_out_direct=excluded.run_out_direct,
                run_out_assist=excluded.run_out_assist, played=excluded.played,
                raw_score_json=excluded.raw_score_json
        """, (
            mid, pid,
            max(0,int(sc.get("runs",0))), max(0,int(sc.get("balls",0))),
            max(0,int(sc.get("fours",0))), max(0,int(sc.get("sixes",0))),
            1 if sc.get("gotOut",sc.get("got_out",False)) else 0,
            1 if sc.get("duck",False) else 0,
            max(0.0,float(sc.get("overs",0))),
            max(0,int(sc.get("runsConceded",sc.get("runs_conceded",0)))),
            min(10,max(0,int(sc.get("wickets",0)))),
            max(0,int(sc.get("maidens",0))),
            max(0,int(sc.get("lbwBowled",sc.get("lbw_bowled",0)))),
            min(10,max(0,int(sc.get("catches",0)))),
            max(0,int(sc.get("stumpings",0))),
            max(0,int(sc.get("runOutDirect",sc.get("run_out_direct",0)))),
            max(0,int(sc.get("runOutAssist",sc.get("run_out_assist",0)))),
            1 if sc.get("played",False) else 0,
            json.dumps(sc),
        ))


class DatabaseManager:
    def __init__(self, path):
        self._path  = str(path)
        self._local = threading.local()
        self._wlock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self._path, timeout=30, check_same_thread=False,
                                  detect_types=sqlite3.PARSE_DECLTYPES)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode = WAL")
            con.execute("PRAGMA foreign_keys = ON")
            con.execute("PRAGMA busy_timeout  = 30000")
            self._local.con = con
        return con

    @contextmanager
    def _read(self):
        yield self._connect()

    @contextmanager
    def _write(self):
        with self._wlock:
            con = self._connect()
            try:
                con.execute("BEGIN IMMEDIATE")
                yield con
                con.commit()
            except Exception:
                con.rollback()
                raise

    def _init_schema(self):
        con = sqlite3.connect(self._path, timeout=30)
        con.execute("PRAGMA busy_timeout = 30000")
        con.executescript(_SCHEMA)
        # Safe migrations — ignore if column already exists
        migrations = [
            "ALTER TABLE user_selections ADD COLUMN week_pts INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE players ADD COLUMN season_pts INTEGER NOT NULL DEFAULT 0",
            # v5.7 new columns
            "ALTER TABLE players ADD COLUMN points INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE user_selections ADD COLUMN points_per_match TEXT NOT NULL DEFAULT '{}'",
        ]
        for stmt in migrations:
            try:
                con.execute(stmt); con.commit()
            except sqlite3.OperationalError:
                pass  # column already exists — safe to skip
        con.close()

    def get_meta(self, key, default=""):
        with self._read() as con:
            row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_meta(self, key, value):
        with self._write() as con:
            con.execute("INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)", (key, value))

    def get_state(self) -> dict:
        with self._read() as con:
            rows = con.execute("""
                SELECT display_name, tw_team_json, tw_cap_id, tw_vc_id,
                       nw_team_json, nw_cap_id, nw_vc_id
                FROM user_selections
                WHERE week_no = (SELECT MAX(week_no) FROM user_selections u2
                                 WHERE u2.display_name = user_selections.display_name)
            """).fetchall()
            members = {}
            for r in rows:
                members[r["display_name"]] = {
                    "this_week": {"team": _jloads(r["tw_team_json"],[]), "cap": r["tw_cap_id"], "vc": r["tw_vc_id"]},
                    "next_week": {"team": _jloads(r["nw_team_json"],[]), "cap": r["nw_cap_id"], "vc": r["nw_vc_id"]},
                }
            match_rows = con.execute(
                "SELECT id,week_no,title,teams_json,date_label,status,raw_json "
                "FROM matches ORDER BY week_no,id"
            ).fetchall()
            matches = []
            for mr in match_rows:
                base  = _jloads(mr["raw_json"], {})
                entry = {"id": mr["id"], "wk": mr["week_no"], "title": mr["title"],
                         "teams": _jloads(mr["teams_json"],[]), "date": mr["date_label"],
                         "status": mr["status"]}
                for k, v in base.items():
                    if k not in entry: entry[k] = v
                score_rows = con.execute(
                    "SELECT player_id,raw_score_json FROM match_scores WHERE match_id=?",
                    (mr["id"],),
                ).fetchall()
                if score_rows:
                    entry["scores"] = {sr["player_id"]: _jloads(sr["raw_score_json"],{}) for sr in score_rows}
                matches.append(entry)
        return {"members": members, "matches": matches,
                "_saved": self.get_meta("_saved","never"),
                "_last_rollover": self.get_meta("_last_rollover","")}

    def save_state(self, payload: dict) -> str:
        members = payload.get("members", {})
        matches = payload.get("matches", [])
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._write() as con:
            row = con.execute("SELECT COALESCE(MAX(week_no),1) AS wn FROM user_selections").fetchone()
            current_week = row["wn"] if row else 1
            for name, data in members.items():
                if not isinstance(data,dict) or not name or len(name)>30: continue
                tw = data.get("this_week") or {}
                nw = data.get("next_week") or {}
                if "this_week" not in data and "team" in data:
                    tw = {"team": data.get("team",[]), "cap": data.get("cap"), "vc": data.get("vc")}
                    nw = dict(tw)
                con.execute("""
                    INSERT INTO user_selections (display_name,week_no,tw_team_json,tw_cap_id,tw_vc_id,nw_team_json,nw_cap_id,nw_vc_id)
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(display_name,week_no) DO UPDATE SET
                        tw_team_json=excluded.tw_team_json, tw_cap_id=excluded.tw_cap_id, tw_vc_id=excluded.tw_vc_id,
                        nw_team_json=excluded.nw_team_json, nw_cap_id=excluded.nw_cap_id, nw_vc_id=excluded.nw_vc_id
                """, (name, current_week,
                      json.dumps(tw.get("team",[]) or []), tw.get("cap"), tw.get("vc"),
                      json.dumps(nw.get("team",[]) or []), nw.get("cap"), nw.get("vc")))
            for m in matches:
                if "id" in m: _upsert_match(con, m)
            con.execute("INSERT OR REPLACE INTO meta (key,value) VALUES ('_saved',?)", (now_iso,))
        for mid in [m["id"] for m in matches if "id" in m and m.get("scores")]:
            self.recalculate_points(match_id=mid)
        return now_iso

    def upsert_member(self, name: str, data: dict) -> None:
        with self._write() as con:
            row = con.execute("SELECT COALESCE(MAX(week_no),1) AS wn FROM user_selections").fetchone()
            current_week = row["wn"] if row else 1
            tw = data.get("this_week") or {}
            nw = data.get("next_week") or {}
            if "this_week" not in data:
                tw = {"team": data.get("team",[]), "cap": data.get("cap"), "vc": data.get("vc")}
                nw = dict(tw)
            con.execute("""
                INSERT INTO user_selections (display_name,week_no,tw_team_json,tw_cap_id,tw_vc_id,nw_team_json,nw_cap_id,nw_vc_id)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(display_name,week_no) DO UPDATE SET
                    tw_team_json=excluded.tw_team_json, tw_cap_id=excluded.tw_cap_id, tw_vc_id=excluded.tw_vc_id,
                    nw_team_json=excluded.nw_team_json, nw_cap_id=excluded.nw_cap_id, nw_vc_id=excluded.nw_vc_id
            """, (name, current_week,
                  json.dumps(tw.get("team",[]) or []), tw.get("cap"), tw.get("vc"),
                  json.dumps(nw.get("team",[]) or []), nw.get("cap"), nw.get("vc")))
            con.execute("INSERT OR REPLACE INTO meta (key,value) VALUES ('_saved',?)",
                        (datetime.now(timezone.utc).isoformat(),))

    def upsert_match(self, m: dict) -> None:
        mid = m.get("id")
        with self._write() as con:
            _upsert_match(con, m)
            con.execute("INSERT OR REPLACE INTO meta (key,value) VALUES ('_saved',?)",
                        (datetime.now(timezone.utc).isoformat(),))
        if mid and m.get("scores"):
            self.recalculate_points(match_id=mid)

    def recalculate_points(self, match_id=None) -> int:
        with self._read() as con:
            if match_id:
                score_rows = con.execute(
                    "SELECT ms.match_id, ms.player_id, ms.raw_score_json, m.week_no "
                    "FROM match_scores ms JOIN matches m ON m.id=ms.match_id WHERE ms.match_id=?",
                    (match_id,),
                ).fetchall()
            else:
                score_rows = con.execute(
                    "SELECT ms.match_id, ms.player_id, ms.raw_score_json, m.week_no "
                    "FROM match_scores ms JOIN matches m ON m.id=ms.match_id"
                ).fetchall()
        if not score_rows: return 0
        now_iso = datetime.now(timezone.utc).isoformat()
        rows_written = 0
        with self._write() as con:
            for row in score_rows:
                sc       = _jloads(row["raw_score_json"], {})
                base_pts = calc_pts(sc)
                con.execute("""
                    INSERT INTO player_match_points (match_id,player_id,week_no,base_pts,multiplier,final_pts,calculated_at)
                    VALUES (?,?,?,?,1.0,?,?)
                    ON CONFLICT(match_id,player_id) DO UPDATE SET
                        week_no=excluded.week_no, base_pts=excluded.base_pts,
                        final_pts=excluded.final_pts, calculated_at=excluded.calculated_at
                """, (row["match_id"], row["player_id"], row["week_no"],
                       base_pts, float(base_pts), now_iso))
                rows_written += 1
        return rows_written

    def update_player_season_pts(self) -> int:
        """
        v5.6 — Set players.season_pts = SUM(base_pts) from player_match_points.
        Returns number of players with season_pts > 0.
        """
        with self._write() as con:
            con.execute("UPDATE players SET season_pts = 0")
            con.execute("""
                UPDATE players SET season_pts = (
                    SELECT COALESCE(SUM(pmp.base_pts), 0)
                    FROM player_match_points pmp
                    WHERE pmp.player_id = players.id
                )
            """)
        with self._read() as con:
            row = con.execute("SELECT COUNT(*) FROM players WHERE season_pts > 0").fetchone()
            return row[0] if row else 0

    def update_player_points(self) -> int:
        """
        v5.7 — Set players.points = SUM of cap/vc-awarded pts from user_match_points.
        Each player's total reflects the actual fantasy points earned across all
        users' selections (averaged if selected by multiple users) — useful as a
        form guide in the team picker.
        In practice with 2 users we sum across all appearances and divide by user count.
        Returns number of players with points > 0.
        """
        with self._read() as con:
            # Count distinct users for averaging
            user_count = max(1, con.execute(
                "SELECT COUNT(DISTINCT display_name) FROM user_selections"
            ).fetchone()[0])

            # Sum awarded pts per player across all user_match_points rows
            # (a player earns pts only in weeks they appear in that user's XI)
            awarded = con.execute("""
                SELECT je.value AS player_id,
                       SUM(
                           CASE
                               WHEN je.value = us.tw_cap_id THEN ROUND(pmp.base_pts * 2.0)
                               WHEN je.value = us.tw_vc_id  THEN ROUND(pmp.base_pts * 1.5)
                               ELSE pmp.base_pts
                           END
                       ) AS total_awarded
                FROM user_selections us, JSON_EACH(us.tw_team_json) AS je
                INNER JOIN player_match_points pmp
                    ON pmp.player_id = je.value AND pmp.week_no = us.week_no
                GROUP BY je.value
            """).fetchall()

        pts_map = {r["player_id"]: r["total_awarded"] for r in awarded}

        with self._write() as con:
            con.execute("UPDATE players SET points = 0")
            for pid, pts in pts_map.items():
                con.execute(
                    "UPDATE players SET points = ? WHERE id = ?",
                    (int(pts or 0), pid)
                )

        return sum(1 for v in pts_map.values() if v and v > 0)

    def update_week_points(self) -> int:
        """
        v5.7 — Recompute week_pts AND points_per_match for every user_selections row,
        plus populate user_match_points for per-match granularity.

        Key guarantee (W1-W10 isolation):
          Each user_selections row owns its own points_per_match JSON blob:
            { match_id: awarded_pts, ... }
          This is scoped strictly to that row's (display_name, week_no), so W3 data
          can never bleed into W4 even if both rows share the same tw_team_json.

        Returns number of user_selections rows updated.
        """
        # ── Read ───────────────────────────────────────────────────────────────────
        with self._read() as con:
            sels = con.execute("""
                SELECT display_name, week_no, tw_team_json, tw_cap_id, tw_vc_id
                FROM user_selections
            """).fetchall()

            pmp_map = {}
            for r in con.execute(
                "SELECT player_id, match_id, base_pts FROM player_match_points"
            ).fetchall():
                pmp_map[(r["player_id"], r["match_id"])] = r["base_pts"]

            # week_no -> [match_id, ...]  (only completed matches score)
            week_matches: dict = {}
            for r in con.execute(
                "SELECT id, week_no FROM matches WHERE LOWER(status)='completed'"
            ).fetchall():
                week_matches.setdefault(r["week_no"], []).append(r["id"])

        # ── Compute ─────────────────────────────────────────────────────────────────
        ump_rows   = []   # (display_name, week_no, match_id, pts)
        wk_totals  = {}   # (display_name, week_no) -> int
        # v5.7: per-row points_per_match blob {match_id: pts}
        ppm_blobs  = {}   # (display_name, week_no) -> dict

        for sel in sels:
            name = sel["display_name"]
            wk   = sel["week_no"]
            try:
                ids = json.loads(sel["tw_team_json"] or "[]")
            except Exception:
                ids = []
            cap = sel["tw_cap_id"]
            vc  = sel["tw_vc_id"]

            wk_total  = 0
            match_blob = {}  # {match_id: pts} — isolated to this (user, week)

            for mid in week_matches.get(wk, []):
                match_pts = 0
                for pid in ids:
                    bp = pmp_map.get((pid, mid))
                    if bp is not None:
                        mult = 2.0 if pid == cap else (1.5 if pid == vc else 1.0)
                        match_pts += round(bp * mult)
                ump_rows.append((name, wk, mid, match_pts))
                match_blob[mid] = match_pts
                wk_total += match_pts

            wk_totals[(name, wk)]  = wk_total
            ppm_blobs[(name, wk)]  = match_blob   # v5.7: own blob per week row

        # ── Write ──────────────────────────────────────────────────────────────────
        updated = 0
        with self._write() as con:
            # user_match_points (granular per-match lookup)
            for name, wk, mid, pts in ump_rows:
                con.execute("""
                    INSERT INTO user_match_points (display_name, week_no, match_id, pts)
                    VALUES (?,?,?,?)
                    ON CONFLICT(display_name, match_id) DO UPDATE SET
                        pts=excluded.pts, week_no=excluded.week_no
                """, (name, wk, mid, pts))

            # user_selections — write week_pts AND points_per_match together
            for (name, wk), pts in wk_totals.items():
                ppm_json = json.dumps(ppm_blobs.get((name, wk), {}))
                con.execute(
                    "UPDATE user_selections SET week_pts=?, points_per_match=? "
                    "WHERE display_name=? AND week_no=?",
                    (pts, ppm_json, name, wk)
                )
                updated += 1

        # v5.7: refresh players.points after every week recalc
        self.update_player_points()
        return updated

    def get_user_match_points(self, display_name: str) -> list:
        """
        v5.6 — Return per-match points for a user, joined with match metadata.
        Sorted by week_no, match_id.
        """
        with self._read() as con:
            rows = con.execute("""
                SELECT ump.display_name, ump.week_no, ump.match_id, ump.pts,
                       m.title, m.status, m.teams_json
                FROM user_match_points ump
                JOIN matches m ON m.id = ump.match_id
                WHERE ump.display_name = ?
                ORDER BY ump.week_no, ump.match_id
            """, (display_name,)).fetchall()
        return [
            {
                "week_no":  r["week_no"],
                "match_id": r["match_id"],
                "title":    r["title"],
                "status":   r["status"],
                "teams":    _jloads(r["teams_json"], []),
                "pts":      r["pts"],
            }
            for r in rows
        ]

    def rebuild_scores_and_points(self, json_dir=None) -> dict:
        """
        v5.7 — Full wipe + rebuild on server restart:
        1. DELETE match_scores, player_match_points, user_match_points.
        2. Reset season_pts, points, points_per_match, week_pts on all rows.
        3. Re-ingest from JSON cache.
        4. recalculate_points()  → player_match_points.
        5. update_week_points()  → user_match_points + user_selections.{week_pts, points_per_match}.
        6. update_player_season_pts() → players.season_pts.
        7. update_player_points()     → players.points (already called inside update_week_points).
        """
        if json_dir is None:
            json_dir = Path(self._path).parent / "matches"
        json_dir = Path(json_dir)

        with self._write() as con:
            con.execute("DELETE FROM player_match_points")
            con.execute("DELETE FROM match_scores")
            con.execute("DELETE FROM user_match_points")
            con.execute("UPDATE user_selections SET week_pts = 0, points_per_match = '{}'")
            con.execute("UPDATE players SET season_pts = 0, points = 0")

        files_ingested = 0
        if json_dir.exists():
            files = sorted(
                json_dir.glob("*.json"),
                key=lambda f: int(m.group(1)) if (m := re.search(r"(\d+)", f.stem)) else 0
            )
            with self._write() as con:
                for fp in files:
                    try:
                        with open(fp) as fh:
                            match_data = json.load(fh)
                        if "id" not in match_data:
                            continue
                        _upsert_match(con, match_data)
                        files_ingested += 1
                    except Exception as e:
                        print(f"  [rebuild] skip {fp.name}: {e}")

        pmp_rows        = self.recalculate_points()
        week_pts_rows   = self.update_week_points()   # also calls update_player_points()
        player_pts_rows = self.update_player_season_pts()

        return {
            "files_ingested":  files_ingested,
            "pmp_rows":        pmp_rows,
            "week_pts_rows":   week_pts_rows,
            "player_pts_rows": player_pts_rows,
        }

    def hydrate_from_json(self, json_dir="data/matches") -> int:
        json_dir = Path(json_dir)
        if not json_dir.exists(): return 0
        files = sorted(
            json_dir.glob("*.json"),
            key=lambda f: int(m.group(1)) if (m := re.search(r"(\d+)", f.stem)) else 0
        )
        if not files: return 0
        count = 0
        with self._write() as con:
            for fp in files:
                try:
                    with open(fp) as fh: match_data = json.load(fh)
                    if "id" in match_data: _upsert_match(con, match_data); count += 1
                except Exception as e:
                    print(f"  [hydrate] skip {fp.name}: {e}")
        if count:
            self.recalculate_points()
            print(f"  [hydrate] Ingested {count} matches from {json_dir}")
        return count

    def ping_stats(self) -> dict:
        with self._read() as con:
            member_count = con.execute("SELECT COUNT(DISTINCT display_name) AS n FROM user_selections").fetchone()["n"]
            scored_count = con.execute("SELECT COUNT(DISTINCT match_id) AS n FROM match_scores WHERE played=1").fetchone()["n"]
        return {"members": member_count, "matches_scored": scored_count, "saved": self.get_meta("_saved","never")}

    def get_leaderboard(self, week_no=None) -> dict:
        with self._read() as con:
            rows = con.execute(_LEADERBOARD_SQL, {"week_no": week_no}).fetchall()
            if week_no is None:
                wk_rows = con.execute(
                    "SELECT display_name, week_no, week_pts FROM user_selections ORDER BY week_no"
                ).fetchall()
            else:
                wk_rows = con.execute(
                    "SELECT display_name, week_no, week_pts FROM user_selections WHERE week_no=?",
                    (week_no,)
                ).fetchall()
            weekly = {}
            for wr in wk_rows:
                weekly.setdefault(wr["display_name"], []).append(
                    {"week_no": wr["week_no"], "pts": wr["week_pts"]}
                )
        if not rows:
            empty = {"league_avg": 0.0, "top_score": 0, "member_count": 0}
            return {"week_no": week_no, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "league_avg": 0.0, "top_score": 0, "member_count": 0,
                    "meta": empty, "standings": [], "rankings": []}
        first = rows[0]
        standings = [
            {"rank": r["rank"], "name": r["display_name"], "total_pts": r["total_pts"],
             "matches_counted": r["matches_counted"],
             "weekly": weekly.get(r["display_name"], []),
             "mvp": {"player_id": r["mvp_player_id"], "player_name": r["mvp_player_name"], "pts": r["mvp_pts"]}}
            for r in rows
        ]
        meta = {"league_avg": first["league_avg"], "top_score": first["top_score"], "member_count": first["member_count"]}
        return {"week_no": week_no, "generated_at": datetime.now(timezone.utc).isoformat(),
                "league_avg": first["league_avg"], "top_score": first["top_score"],
                "member_count": first["member_count"], "meta": meta,
                "standings": standings, "rankings": standings}

    def get_etags(self) -> dict:
        return {"state": self.get_meta("_saved", "never")}

    def get_current_week(self) -> int:
        with self._read() as con:
            row = con.execute("SELECT COALESCE(MAX(week_no),1) AS wn FROM user_selections").fetchone()
            return int(row["wn"]) if row else 1

    def get_players(self) -> list:
        with self._read() as con:
            # v5.7: return both season_pts (base, no multiplier) and points (cap/vc awarded)
            rows = con.execute(
                "SELECT id,name,team,role,price,season_pts,points FROM players ORDER BY points DESC, season_pts DESC, name"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_history(self, name: str) -> dict:
        with self._read() as con:
            current_week = self.get_current_week()
            rows = con.execute("""
                SELECT week_no,tw_team_json,tw_cap_id,tw_vc_id,nw_team_json,nw_cap_id,nw_vc_id,
                       week_pts,points_per_match
                FROM user_selections WHERE display_name=? ORDER BY week_no ASC
            """, (name,)).fetchall()
        weeks = [
            {"week_no": r["week_no"], "is_current": r["week_no"]==current_week,
             "this_week": {"team": _jloads(r["tw_team_json"],[]), "cap": r["tw_cap_id"], "vc": r["tw_vc_id"]},
             "next_week": {"team": _jloads(r["nw_team_json"],[]), "cap": r["nw_cap_id"], "vc": r["nw_vc_id"]},
             "week_pts": r["week_pts"],
             # v5.7: per-week isolated match breakdown {match_id: pts}
             "points_per_match": _jloads(r["points_per_match"], {})}
            for r in rows
        ]
        return {"name": name, "current_week": current_week, "weeks": weeks, "ok": True}

    def validate_budget(self, player_ids: list, budget: float = 100.0) -> tuple:
        if not player_ids: return True, 0.0
        with self._read() as con:
            ph   = ",".join("?" * len(player_ids))
            rows = con.execute(f"SELECT id,price FROM players WHERE id IN ({ph})", player_ids).fetchall()
        price_map = {r["id"]: r["price"] for r in rows}
        total = round(sum(price_map.get(pid, 0.0) for pid in player_ids), 1)
        return total <= budget, total

    def save_next_week(self, name: str, team: list, cap, vc) -> dict:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._write() as con:
            row = con.execute("SELECT COALESCE(MAX(week_no),1) AS wn FROM user_selections").fetchone()
            current_week = int(row["wn"]) if row else 1
            con.execute("""
                INSERT INTO user_selections (display_name,week_no,tw_team_json,tw_cap_id,tw_vc_id,nw_team_json,nw_cap_id,nw_vc_id)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(display_name,week_no) DO UPDATE SET
                    nw_team_json=excluded.nw_team_json, nw_cap_id=excluded.nw_cap_id, nw_vc_id=excluded.nw_vc_id
            """, (name, current_week, "[]", None, None, json.dumps(team), cap, vc))
            con.execute("INSERT OR REPLACE INTO meta (key,value) VALUES ('_saved',?)", (now_iso,))
        return {"week_no": current_week}

    def rollover_season(self, force=False, max_weeks=8, deadline_hour=14,
                        deadline_min=0, resolver_callback=None) -> dict:
        now = datetime.now(timezone.utc)
        if not force:
            days_since_mon = now.weekday()
            lmd = (now - timedelta(days=days_since_mon)).replace(
                hour=deadline_hour, minute=deadline_min, second=0, microsecond=0, tzinfo=timezone.utc)
            if lmd > now: lmd -= timedelta(days=7)
            last_raw = self.get_meta("_last_rollover", "")
            if last_raw:
                try:
                    last_dt = datetime.fromisoformat(last_raw)
                    if last_dt.tzinfo is None: last_dt = last_dt.replace(tzinfo=timezone.utc)
                    if lmd <= last_dt:
                        return {"ok":True,"rolled":False,"new_week_no":None,
                                "season_complete":False,"reason":"Already rolled for this deadline"}
                except ValueError: pass
        current_week = self.get_current_week()
        if current_week >= max_weeks:
            return {"ok":True,"rolled":False,"new_week_no":None,
                    "season_complete":True,"reason":f"Season complete — {max_weeks} weeks reached"}
        with self._read() as con:
            users = con.execute(
                "SELECT display_name, MAX(week_no) AS cur_wk FROM user_selections GROUP BY display_name"
            ).fetchall()
        if not users:
            return {"ok":True,"rolled":False,"new_week_no":None,
                    "season_complete":False,"reason":"No members found"}
        new_week_no = int(users[0]["cur_wk"]) + 1
        now_iso = now.isoformat()
        with self._write() as con:
            for u in users:
                uname  = u["display_name"]
                cur_wk = int(u["cur_wk"])
                cur_row = con.execute("""
                    SELECT tw_team_json,tw_cap_id,tw_vc_id,nw_team_json,nw_cap_id,nw_vc_id
                    FROM user_selections WHERE display_name=? AND week_no=?
                """, (uname, cur_wk)).fetchone()
                if not cur_row: continue
                nw_team = cur_row["nw_team_json"] or "[]"
                nw_cap  = cur_row["nw_cap_id"]
                nw_vc   = cur_row["nw_vc_id"]
                if _jloads(nw_team,[]) == []:
                    nw_team = cur_row["tw_team_json"] or "[]"
                    nw_cap  = cur_row["tw_cap_id"]
                    nw_vc   = cur_row["tw_vc_id"]
                if resolver_callback is not None:
                    try:
                        resolved = resolver_callback(_jloads(nw_team,[]))
                        nw_team  = json.dumps(resolved)
                    except Exception: pass
                con.execute("""
                    INSERT OR IGNORE INTO user_selections
                        (display_name,week_no,tw_team_json,tw_cap_id,tw_vc_id,nw_team_json,nw_cap_id,nw_vc_id)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (uname, cur_wk+1, nw_team, nw_cap, nw_vc, nw_team, nw_cap, nw_vc))
            if not force:
                con.execute("INSERT OR REPLACE INTO meta (key,value) VALUES ('_last_rollover',?)", (now_iso,))
            con.execute("INSERT OR REPLACE INTO meta (key,value) VALUES ('_saved',?)", (now_iso,))
        return {"ok":True,"rolled":True,"new_week_no":new_week_no,
                "season_complete":new_week_no>=max_weeks}

    def reset(self) -> None:
        with self._write() as con:
            for t in ("match_scores","player_match_points","user_match_points",
                      "user_selections","matches","meta"):
                con.execute(f"DELETE FROM {t}")

    def do_rollover(self) -> dict:
        """Legacy single-table rollover (kept for compat)."""
        now = datetime.now(timezone.utc)
        days_since_mon = now.weekday()
        lmd = (now - timedelta(days=days_since_mon)).replace(
            hour=DEADLINE_HOUR, minute=DEADLINE_MIN, second=0, microsecond=0, tzinfo=timezone.utc)
        if lmd > now: lmd -= timedelta(days=7)
        last_raw = self.get_meta("_last_rollover", "")
        if last_raw:
            try:
                last_dt = datetime.fromisoformat(last_raw)
                if last_dt.tzinfo is None: last_dt = last_dt.replace(tzinfo=timezone.utc)
                if lmd <= last_dt: return {"ok": True, "rolled": False}
            except ValueError: pass
        with self._write() as con:
            con.execute("""
                UPDATE user_selections SET
                    tw_team_json = CASE WHEN json_array_length(nw_team_json)>0 THEN nw_team_json ELSE tw_team_json END,
                    tw_cap_id    = CASE WHEN json_array_length(nw_team_json)>0 THEN nw_cap_id    ELSE tw_cap_id    END,
                    tw_vc_id     = CASE WHEN json_array_length(nw_team_json)>0 THEN nw_vc_id     ELSE tw_vc_id     END,
                    nw_team_json = '[]', nw_cap_id = NULL, nw_vc_id = NULL
                WHERE week_no = (SELECT MAX(week_no) FROM user_selections u2
                                 WHERE u2.display_name = user_selections.display_name)
            """)
            con.execute("INSERT OR REPLACE INTO meta (key,value) VALUES ('_last_rollover',?)", (lmd.isoformat(),))
            con.execute("INSERT OR REPLACE INTO meta (key,value) VALUES ('_saved',?)", (datetime.now(timezone.utc).isoformat(),))
        return {"ok": True, "rolled": True}


GoldenDB = DatabaseManager
