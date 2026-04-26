"""
IPL Fantasy 2026 — DatabaseManager                          Golden File v5.9
===========================================================================
v5.9 (Phase 5 — DAO refactor):
  rollover_season() and do_rollover() removed entirely.
    Rollover logic now lives in server.py api_rollover(), which calls
    logic.rollover_engine helpers and the four new thin DAO methods below.
  _normalise_overs re-export removed (server.py now imports from logic/).
  DEADLINE_HOUR / DEADLINE_MIN imports removed (only rollover methods used them).
  timedelta import removed (only rollover methods used it).
  New DAO methods added to support server.py rollover route:
    get_users_and_max_weeks()
    get_selection_row(display_name, week_no)
    insert_rollover_week(display_name, new_wk, team_json, cap_id, vc_id)
    set_last_rollover(iso)
  DatabaseManager is now a strict DAO: SELECT / INSERT / UPDATE only.
  No business logic lives here; all computation delegated to logic/.
v5.8 (Phase 4): Imported from logic.scoring_engine + logic.rollover_engine.
v5.7: points_per_match, update_player_points(), cap/vc aggregation.
v5.6: user_match_points, season_pts, per-match leaderboard.

Leaderboard fix (post-v5.9):
  _LEADERBOARD_SQL user_totals CTE now sources total_pts from
  SUM(us.week_pts) in user_selections — the authoritative persisted value
  written at scrape time.  Previously used SUM(ump.pts) from
  user_match_points, an ephemeral table cleared on restart, causing W3=0,
  W2 drift, and total ≠ sum(weekly).
"""

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from config import DB_VER  # noqa: F401
from logic.scoring_engine import calc_pts  # used by recalculate_points()


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


# ── Leaderboard SQL ────────────────────────────────────────────────────────
#
# FIX: user_totals.total_pts now reads SUM(us.week_pts) from user_selections.
#
# WHY: user_match_points (ump) is an ephemeral derived table — it is cleared
# on every server restart and only repopulated for matches whose status is
# 'completed' at the time update_week_points() runs.  Any week whose matches
# hadn't settled (e.g. W3) would appear as 0, while weeks with partial
# re-scrapes (W2) would drift.  The result was total_pts ≠ sum(weekly)
# because the two figures were drawn from different sources.
#
# user_selections.week_pts is the authoritative value: written atomically by
# the scraper at the moment each match is processed and never cleared between
# restarts.  It is the same source the weekly breakdown list already reads.
#
# Changes from original:
#   user_totals: SUM(us.week_pts) replaces SUM(ump.pts)       ← total fix
#   user_totals: LEFT JOIN ump now scoped ump.week_no=us.week_no  ← cross-week fix
#   user_totals: WHERE clause added for per-week filter        ← week filter fix
#   scored_points / MVP CTEs: unchanged
#
_LEADERBOARD_SQL = """
WITH
user_totals AS (
    -- SOURCE OF TRUTH: week_pts from user_selections (persisted at scrape time).
    -- user_match_points is retained only for matches_counted granularity.
    SELECT us.display_name,
           COALESCE(SUM(us.week_pts), 0)                                    AS total_pts,
           COUNT(DISTINCT CASE WHEN ump.pts > 0 THEN ump.match_id END)      AS matches_counted
    FROM  user_selections us
    LEFT JOIN user_match_points ump
          ON  ump.display_name = us.display_name
          AND ump.week_no      = us.week_no
    WHERE (CAST(:week_no AS INTEGER) IS NULL
           OR us.week_no = CAST(:week_no AS INTEGER))
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
        migrations = [
            "ALTER TABLE user_selections ADD COLUMN week_pts INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE players ADD COLUMN season_pts INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE players ADD COLUMN points INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE user_selections ADD COLUMN points_per_match TEXT NOT NULL DEFAULT '{}'",
        ]
        for stmt in migrations:
            try:
                con.execute(stmt); con.commit()
            except sqlite3.OperationalError:
                pass
        con.close()

    # ── Meta ──────────────────────────────────────────────────────────────────

    def get_meta(self, key, default=""):
        with self._read() as con:
            row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_meta(self, key, value):
        with self._write() as con:
            con.execute("INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)", (key, value))

    # ── State ─────────────────────────────────────────────────────────────────

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

    # ── Members / Matches ─────────────────────────────────────────────────────

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

    # ── Points Calculation ────────────────────────────────────────────────────

    def recalculate_points(self, match_id=None) -> int:
        """
        Read match_scores, call calc_pts() from logic.scoring_engine for each row,
        and write base_pts into player_match_points.
        """
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
        """Set players.season_pts = SUM(base_pts) from player_match_points."""
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
        """Set players.points = SUM of cap/vc-awarded pts across all user weeks."""
        with self._read() as con:
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
                con.execute("UPDATE players SET points = ? WHERE id = ?", (int(pts or 0), pid))
        return sum(1 for v in pts_map.values() if v and v > 0)

    def update_week_points(self) -> int:
        """Recompute week_pts + points_per_match + user_match_points for all rows."""
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
            week_matches: dict = {}
            for r in con.execute(
                "SELECT id, week_no FROM matches WHERE LOWER(status)='completed'"
            ).fetchall():
                week_matches.setdefault(r["week_no"], []).append(r["id"])

        ump_rows = []; wk_totals = {}; ppm_blobs = {}
        for sel in sels:
            name = sel["display_name"]; wk = sel["week_no"]
            try: ids = json.loads(sel["tw_team_json"] or "[]")
            except Exception: ids = []
            cap = sel["tw_cap_id"]; vc = sel["tw_vc_id"]
            wk_total = 0; match_blob = {}
            for mid in week_matches.get(wk, []):
                match_pts = 0
                for pid in ids:
                    bp = pmp_map.get((pid, mid))
                    if bp is not None:
                        mult = 2.0 if pid == cap else (1.5 if pid == vc else 1.0)
                        match_pts += round(bp * mult)
                ump_rows.append((name, wk, mid, match_pts))
                match_blob[mid] = match_pts; wk_total += match_pts
            wk_totals[(name, wk)] = wk_total; ppm_blobs[(name, wk)] = match_blob

        updated = 0
        with self._write() as con:
            for name, wk, mid, pts in ump_rows:
                con.execute("""
                    INSERT INTO user_match_points (display_name, week_no, match_id, pts)
                    VALUES (?,?,?,?)
                    ON CONFLICT(display_name, match_id) DO UPDATE SET
                        pts=excluded.pts, week_no=excluded.week_no
                """, (name, wk, mid, pts))
            for (name, wk), pts in wk_totals.items():
                ppm_json = json.dumps(ppm_blobs.get((name, wk), {}))
                con.execute(
                    "UPDATE user_selections SET week_pts=?, points_per_match=? "
                    "WHERE display_name=? AND week_no=?",
                    (pts, ppm_json, name, wk)
                )
                updated += 1
        self.update_player_points()
        return updated

    # ── Rollover DAO (Phase 5) ────────────────────────────────────────────────

    def get_users_and_max_weeks(self) -> list:
        """Return [{display_name, cur_wk}] for all users. Used by api_rollover."""
        with self._read() as con:
            rows = con.execute(
                "SELECT display_name, MAX(week_no) AS cur_wk "
                "FROM user_selections GROUP BY display_name"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_selection_row(self, display_name: str, week_no: int) -> dict | None:
        """Return one user_selections row as a dict, or None if not found."""
        with self._read() as con:
            row = con.execute(
                "SELECT tw_team_json, tw_cap_id, tw_vc_id, "
                "nw_team_json, nw_cap_id, nw_vc_id "
                "FROM user_selections WHERE display_name=? AND week_no=?",
                (display_name, week_no),
            ).fetchone()
        return dict(row) if row else None

    def insert_rollover_week(self, display_name: str, new_week_no: int,
                              team_json: str, cap_id, vc_id) -> None:
        with self._write() as con:
            con.execute("""
                INSERT OR IGNORE INTO user_selections
                    (display_name, week_no,
                     tw_team_json, tw_cap_id, tw_vc_id,
                     nw_team_json, nw_cap_id, nw_vc_id)
                VALUES (?,?, ?,?,?, ?,?,?)
            """, (display_name, new_week_no,
                  team_json, cap_id, vc_id,
                  team_json, cap_id, vc_id))

    def set_last_rollover(self, iso: str) -> None:
        """Write _last_rollover timestamp to meta."""
        with self._write() as con:
            con.execute(
                "INSERT OR REPLACE INTO meta (key,value) VALUES ('_last_rollover',?)",
                (iso,)
            )

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_user_match_points(self, display_name: str) -> list:
        with self._read() as con:
            rows = con.execute("""
                SELECT ump.display_name, ump.week_no, ump.match_id, ump.pts,
                       m.title, m.status, m.teams_json
                FROM user_match_points ump
                JOIN matches m ON m.id = ump.match_id
                WHERE ump.display_name = ?
                ORDER BY ump.week_no, ump.match_id
            """, (display_name,)).fetchall()
        return [{"week_no": r["week_no"], "match_id": r["match_id"],
                 "title": r["title"], "status": r["status"],
                 "teams": _jloads(r["teams_json"], []), "pts": r["pts"]}
                for r in rows]

    def rebuild_scores_and_points(self, json_dir=None) -> dict:
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
                        with open(fp) as fh: match_data = json.load(fh)
                        if "id" not in match_data: continue
                        _upsert_match(con, match_data); files_ingested += 1
                    except Exception as e:
                        print(f"  [rebuild] skip {fp.name}: {e}")
        pmp_rows      = self.recalculate_points()
        week_pts_rows = self.update_week_points()
        player_rows   = self.update_player_season_pts()
        return {"files_ingested": files_ingested, "pmp_rows": pmp_rows,
                "week_pts_rows": week_pts_rows, "player_pts_rows": player_rows}

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
            member_count = con.execute(
                "SELECT COUNT(DISTINCT display_name) AS n FROM user_selections"
            ).fetchone()["n"]
            scored_count = con.execute(
                "SELECT COUNT(DISTINCT match_id) AS n FROM match_scores WHERE played=1"
            ).fetchone()["n"]
        return {"members": member_count, "matches_scored": scored_count,
                "saved": self.get_meta("_saved","never")}

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
             "mvp": {"player_id": r["mvp_player_id"],
                     "player_name": r["mvp_player_name"], "pts": r["mvp_pts"]}}
            for r in rows
        ]
        meta = {"league_avg": first["league_avg"], "top_score": first["top_score"],
                "member_count": first["member_count"]}
        return {"week_no": week_no, "generated_at": datetime.now(timezone.utc).isoformat(),
                "league_avg": first["league_avg"], "top_score": first["top_score"],
                "member_count": first["member_count"], "meta": meta,
                "standings": standings, "rankings": standings}

    def get_etags(self) -> dict:
        return {"state": self.get_meta("_saved", "never")}

    def get_current_week(self) -> int:
        with self._read() as con:
            row = con.execute(
                "SELECT COALESCE(MAX(week_no),1) AS wn FROM user_selections"
            ).fetchone()
            return int(row["wn"]) if row else 1

    def get_players(self) -> list:
        with self._read() as con:
            rows = con.execute(
                "SELECT id,name,team,role,price,season_pts,points "
                "FROM players ORDER BY points DESC, season_pts DESC, name"
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
             "points_per_match": _jloads(r["points_per_match"], {})}
            for r in rows
        ]
        return {"name": name, "current_week": current_week, "weeks": weeks, "ok": True}

    def validate_budget(self, player_ids: list, budget: float = 100.0) -> tuple:
        if not player_ids: return True, 0.0
        with self._read() as con:
            ph   = ",".join("?" * len(player_ids))
            rows = con.execute(f"SELECT id,price FROM players WHERE id IN ({ph})",
                               player_ids).fetchall()
        price_map = {r["id"]: r["price"] for r in rows}
        total = round(sum(price_map.get(pid, 0.0) for pid in player_ids), 1)
        return total <= budget, total

    def save_next_week(self, name: str, team: list, cap, vc) -> dict:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._write() as con:
            row = con.execute(
                "SELECT COALESCE(MAX(week_no),1) AS wn FROM user_selections"
            ).fetchone()
            current_week = int(row["wn"]) if row else 1
            con.execute("""
                INSERT INTO user_selections
                    (display_name,week_no,tw_team_json,tw_cap_id,tw_vc_id,
                     nw_team_json,nw_cap_id,nw_vc_id)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(display_name,week_no) DO UPDATE SET
                    nw_team_json=excluded.nw_team_json,
                    nw_cap_id=excluded.nw_cap_id,
                    nw_vc_id=excluded.nw_vc_id
            """, (name, current_week, "[]", None, None, json.dumps(team), cap, vc))
            con.execute("INSERT OR REPLACE INTO meta (key,value) VALUES ('_saved',?)", (now_iso,))
        return {"week_no": current_week}

    def reset(self) -> None:
        with self._write() as con:
            for t in ("match_scores","player_match_points","user_match_points",
                      "user_selections","matches","meta"):
                con.execute(f"DELETE FROM {t}")


GoldenDB = DatabaseManager
