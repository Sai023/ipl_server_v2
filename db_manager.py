"""
BCL Fantasy — DatabaseManager (Postgres / psycopg 3)         Golden File v6.0

INF-1: ported from the SQLite golden file to managed Postgres.
- Connection is a psycopg_pool.ConnectionPool built from a DSN (DATABASE_URL).
  `prepare_threshold=None` keeps it PgBouncer/Neon-pooler safe; rows are dicts.
- `_read()` / `_write()` hand out a pooled connection; psycopg commits the
  block on clean exit and rolls back on exception (one txn per checkout).
- No WAL / PRAGMA / git: `checkpoint()` and `reload_from_disk()` are no-ops kept
  for interface compatibility with callers written for the SQLite era.

The public method surface and semantics are identical to the SQLite golden
file — only the SQL dialect and the connection layer changed.
"""

import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from config import DB_VER  # noqa: F401


_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS competitions (
    slug             TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    format           TEXT NOT NULL DEFAULT 'T20' CHECK (format IN ('T20','ODI')),
    status           TEXT NOT NULL DEFAULT 'upcoming' CHECK (status IN ('upcoming','active','completed')),
    budget_total     DOUBLE PRECISION NOT NULL DEFAULT 100.0,
    xi_size          INTEGER NOT NULL DEFAULT 11,
    max_weeks        INTEGER NOT NULL DEFAULT 10,
    week1_anchor_utc TEXT    NOT NULL DEFAULT '1970-01-01T00:00:00+00:00',
    deadline_hour    INTEGER NOT NULL DEFAULT 14,
    deadline_min     INTEGER NOT NULL DEFAULT 0,
    series_id        TEXT,
    series_slug      TEXT,
    year             INTEGER,
    valid_teams_json TEXT    NOT NULL DEFAULT '[]',
    champion         TEXT,
    closed_at        TEXT,
    created_at       TEXT    NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')
);
CREATE TABLE IF NOT EXISTS players (
    competition_id TEXT    NOT NULL DEFAULT 'ipl_2026',
    id         TEXT    NOT NULL,
    name       TEXT    NOT NULL,
    team       TEXT    NOT NULL,
    price      DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (price >= 0),
    role       TEXT    NOT NULL DEFAULT 'BAT' CHECK (role IN ('BAT','BOWL','AR','WK')),
    season_pts INTEGER NOT NULL DEFAULT 0,
    points     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (competition_id, id)
);
CREATE TABLE IF NOT EXISTS matches (
    competition_id TEXT NOT NULL DEFAULT 'ipl_2026',
    id            TEXT PRIMARY KEY,
    week_no       INTEGER NOT NULL DEFAULT 1 CHECK (week_no >= 1),
    title         TEXT NOT NULL DEFAULT '',
    teams_json    TEXT NOT NULL DEFAULT '[]',
    date_label    TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'upcoming' CHECK (status IN ('upcoming','live','completed')),
    scorecard_url TEXT,
    raw_json      TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS user_selections (
    competition_id   TEXT    NOT NULL DEFAULT 'ipl_2026',
    display_name     TEXT    NOT NULL CHECK (length(display_name) BETWEEN 1 AND 30),
    week_no          INTEGER NOT NULL DEFAULT 1 CHECK (week_no >= 1),
    tw_team_json     TEXT    NOT NULL DEFAULT '[]',
    tw_cap_id        TEXT,
    tw_vc_id         TEXT,
    nw_team_json     TEXT    NOT NULL DEFAULT '[]',
    nw_cap_id        TEXT,
    nw_vc_id         TEXT,
    week_pts         INTEGER NOT NULL DEFAULT 0,
    points_per_match TEXT    NOT NULL DEFAULT '{}',
    PRIMARY KEY (competition_id, display_name, week_no)
);
CREATE TABLE IF NOT EXISTS match_scores (
    competition_id TEXT    NOT NULL DEFAULT 'ipl_2026',
    match_id       TEXT    NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    player_id      TEXT    NOT NULL,
    runs           INTEGER NOT NULL DEFAULT 0 CHECK (runs >= 0),
    balls          INTEGER NOT NULL DEFAULT 0 CHECK (balls >= 0),
    fours          INTEGER NOT NULL DEFAULT 0 CHECK (fours >= 0),
    sixes          INTEGER NOT NULL DEFAULT 0 CHECK (sixes >= 0),
    got_out        INTEGER NOT NULL DEFAULT 0 CHECK (got_out IN (0,1)),
    duck           INTEGER NOT NULL DEFAULT 0 CHECK (duck IN (0,1)),
    overs          DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (overs >= 0),
    runs_conceded  INTEGER NOT NULL DEFAULT 0 CHECK (runs_conceded >= 0),
    wickets        INTEGER NOT NULL DEFAULT 0 CHECK (wickets BETWEEN 0 AND 10),
    maidens        INTEGER NOT NULL DEFAULT 0 CHECK (maidens >= 0),
    lbw_bowled     INTEGER NOT NULL DEFAULT 0 CHECK (lbw_bowled >= 0),
    catches        INTEGER NOT NULL DEFAULT 0 CHECK (catches BETWEEN 0 AND 10),
    stumpings      INTEGER NOT NULL DEFAULT 0 CHECK (stumpings >= 0),
    run_out_direct INTEGER NOT NULL DEFAULT 0 CHECK (run_out_direct >= 0),
    run_out_assist INTEGER NOT NULL DEFAULT 0 CHECK (run_out_assist >= 0),
    played         INTEGER NOT NULL DEFAULT 0 CHECK (played IN (0,1)),
    raw_score_json TEXT    NOT NULL DEFAULT '{}',
    PRIMARY KEY (match_id, player_id)
);
CREATE TABLE IF NOT EXISTS player_match_points (
    competition_id TEXT    NOT NULL DEFAULT 'ipl_2026',
    match_id      TEXT    NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    player_id     TEXT    NOT NULL,
    week_no       INTEGER NOT NULL,
    base_pts      INTEGER NOT NULL DEFAULT 0,
    multiplier    DOUBLE PRECISION NOT NULL DEFAULT 1.0 CHECK (multiplier IN (1.0,1.5,2.0)),
    final_pts     DOUBLE PRECISION NOT NULL DEFAULT 0,
    calculated_at TEXT    NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
    PRIMARY KEY (match_id, player_id)
);
CREATE TABLE IF NOT EXISTS user_match_points (
    competition_id TEXT    NOT NULL DEFAULT 'ipl_2026',
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
CREATE TABLE IF NOT EXISTS members (
    username       TEXT    PRIMARY KEY CHECK (length(username) BETWEEN 1 AND 30),
    passcode_hash  TEXT    NOT NULL,
    must_change    INTEGER NOT NULL DEFAULT 0 CHECK (must_change IN (0,1)),
    is_admin       INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0,1)),
    created_at     TEXT    NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')
);
CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    username    TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')
);
CREATE INDEX IF NOT EXISTS idx_us_name      ON user_selections (display_name);
CREATE INDEX IF NOT EXISTS idx_us_week      ON user_selections (week_no);
CREATE INDEX IF NOT EXISTS idx_ms_match     ON match_scores (match_id);
CREATE INDEX IF NOT EXISTS idx_pmp_player   ON player_match_points (player_id);
CREATE INDEX IF NOT EXISTS idx_pmp_week     ON player_match_points (week_no);
CREATE INDEX IF NOT EXISTS idx_pmp_match_p  ON player_match_points (match_id, player_id);
CREATE INDEX IF NOT EXISTS idx_ump_name     ON user_match_points (display_name);
CREATE INDEX IF NOT EXISTS idx_ump_week     ON user_match_points (week_no);
CREATE INDEX IF NOT EXISTS idx_sess_user    ON sessions (username);
CREATE INDEX IF NOT EXISTS idx_matches_comp ON matches (competition_id);
CREATE INDEX IF NOT EXISTS idx_ms_comp      ON match_scores (competition_id);
CREATE INDEX IF NOT EXISTS idx_pmp_comp     ON player_match_points (competition_id);
CREATE INDEX IF NOT EXISTS idx_ump_comp     ON user_match_points (competition_id);
"""


# ── Leaderboard SQL (Postgres) ──────────────────────────────────────────────
# Two independent CTEs (no cross-join) keep total_pts == SUM of the weekly
# columns. JSON_EACH → jsonb_array_elements_text (LATERAL). Named params.
_LEADERBOARD_SQL = """
WITH
user_totals AS (
    SELECT display_name, COALESCE(SUM(week_pts), 0) AS total_pts
    FROM   user_selections
    WHERE  competition_id = %(comp)s
      AND  (%(week_no)s::integer IS NULL OR week_no = %(week_no)s::integer)
    GROUP  BY display_name
),
match_counts AS (
    SELECT display_name,
           COUNT(DISTINCT CASE WHEN pts > 0 THEN match_id END) AS matches_counted
    FROM   user_match_points
    WHERE  competition_id = %(comp)s
      AND  (%(week_no)s::integer IS NULL OR week_no = %(week_no)s::integer)
    GROUP  BY display_name
),
scored_points AS (
    SELECT us.display_name, pmp.match_id, je.value AS player_id, pmp.base_pts,
           CASE WHEN je.value = us.tw_cap_id THEN ROUND(pmp.base_pts * 2.0)::integer
                WHEN je.value = us.tw_vc_id  THEN ROUND(pmp.base_pts * 1.5)::integer
                ELSE pmp.base_pts END AS awarded_pts
    FROM  user_selections us
    CROSS JOIN LATERAL jsonb_array_elements_text(us.tw_team_json::jsonb) AS je(value)
    INNER JOIN player_match_points pmp
           ON  pmp.player_id = je.value
           AND pmp.week_no   = us.week_no
           AND pmp.competition_id = us.competition_id
    WHERE  us.competition_id = %(comp)s
      AND  (%(week_no)s::integer IS NULL OR us.week_no = %(week_no)s::integer)
),
mvp_data AS (
    SELECT display_name, MAX(awarded_pts) AS mvp_awarded_pts
    FROM   scored_points GROUP BY display_name
),
mvp_resolve AS (
    SELECT sp.display_name, MIN(sp.player_id) AS mvp_player_id, sp.awarded_pts AS mvp_pts
    FROM   scored_points sp
    INNER JOIN mvp_data md
           ON  md.display_name   = sp.display_name
           AND sp.awarded_pts    = md.mvp_awarded_pts
    GROUP  BY sp.display_name, sp.awarded_pts
),
ranked AS (
    SELECT ut.display_name,
           ut.total_pts,
           COALESCE(mc.matches_counted, 0) AS matches_counted,
           COALESCE(mr.mvp_player_id, '')  AS mvp_player_id,
           COALESCE(mr.mvp_pts, 0)         AS mvp_pts,
           DENSE_RANK() OVER (ORDER BY ut.total_pts DESC) AS rank
    FROM  user_totals ut
    LEFT JOIN match_counts mc  USING (display_name)
    LEFT JOIN mvp_resolve  mr  USING (display_name)
),
league_benchmarks AS (
    SELECT ROUND(AVG(total_pts), 1)::double precision AS league_avg,
           MAX(total_pts)           AS top_score,
           COUNT(*)                 AS member_count
    FROM  user_totals
)
SELECT r.rank, r.display_name, r.total_pts, r.matches_counted,
       r.mvp_player_id,
       COALESCE(p.name, r.mvp_player_id) AS mvp_player_name,
       r.mvp_pts,
       lb.league_avg, lb.top_score, lb.member_count
FROM  ranked r
CROSS JOIN league_benchmarks lb
LEFT  JOIN players p ON p.id = r.mvp_player_id AND p.competition_id = %(comp)s
ORDER BY r.rank, r.display_name
"""


def _jloads(s, default):
    if not s: return default
    try:    return json.loads(s)
    except: return default


def _upsert_match(con, m: dict, competition_id: str = "ipl_2026") -> None:
    mid = m.get("id")
    if not mid: return
    raw_copy = {k: v for k, v in m.items() if k != "scores"}
    teams  = m.get("teams", [])
    date   = m.get("date", m.get("date_label", ""))
    wk     = int(m.get("wk", m.get("week_no", 1)))
    title  = m.get("title", "")
    if teams and len(teams) >= 2 and title:
        if teams[0] not in title and teams[1] not in title:
            title = f"{teams[0]} vs {teams[1]}, {title}"
    status = m.get("status", "upcoming")
    con.execute("""
        INSERT INTO matches (competition_id,id,week_no,title,teams_json,date_label,status,raw_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (id) DO UPDATE SET
            competition_id=EXCLUDED.competition_id,
            week_no=EXCLUDED.week_no, title=EXCLUDED.title, teams_json=EXCLUDED.teams_json,
            date_label=EXCLUDED.date_label, status=EXCLUDED.status, raw_json=EXCLUDED.raw_json
    """, (competition_id, mid, wk, title, json.dumps(teams), date, status, json.dumps(raw_copy)))
    for pid, sc in m.get("scores", {}).items():
        if not isinstance(sc, dict): continue
        con.execute("""
            INSERT INTO match_scores (
                competition_id,match_id,player_id,runs,balls,fours,sixes,got_out,duck,
                overs,runs_conceded,wickets,maidens,lbw_bowled,
                catches,stumpings,run_out_direct,run_out_assist,played,raw_score_json
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (match_id,player_id) DO UPDATE SET
                competition_id=EXCLUDED.competition_id,
                runs=EXCLUDED.runs, balls=EXCLUDED.balls, fours=EXCLUDED.fours,
                sixes=EXCLUDED.sixes, got_out=EXCLUDED.got_out, duck=EXCLUDED.duck,
                overs=EXCLUDED.overs, runs_conceded=EXCLUDED.runs_conceded,
                wickets=EXCLUDED.wickets, maidens=EXCLUDED.maidens,
                lbw_bowled=EXCLUDED.lbw_bowled, catches=EXCLUDED.catches,
                stumpings=EXCLUDED.stumpings, run_out_direct=EXCLUDED.run_out_direct,
                run_out_assist=EXCLUDED.run_out_assist, played=EXCLUDED.played,
                raw_score_json=EXCLUDED.raw_score_json
        """, (
            competition_id, mid, pid,
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
    DEFAULT_COMP = "ipl_2026"

    def __init__(self, dsn):
        self._dsn = str(dsn)
        self._pool = ConnectionPool(
            self._dsn, min_size=1, max_size=8, open=True,
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
        )
        self._init_schema()

    # ── Connection helpers ────────────────────────────────────────────────
    @contextmanager
    def _read(self):
        with self._pool.connection() as con:
            yield con

    @contextmanager
    def _write(self):
        # psycopg commits the block on clean exit, rolls back on exception.
        with self._pool.connection() as con:
            yield con

    def reload_from_disk(self) -> None:
        pass  # no-op (Postgres; there is no on-disk file to reopen)

    def checkpoint(self) -> None:
        pass  # no-op (no WAL / git in Postgres mode)

    def _init_schema(self):
        with self._pool.connection() as con:
            for stmt in (s.strip() for s in _SCHEMA.split(";")):
                if stmt:
                    con.execute(stmt)

    # ── Competition resolution ────────────────────────────────────────────
    def _active_slug(self) -> str:
        try:
            with self._read() as con:
                r = con.execute(
                    "SELECT slug FROM competitions WHERE status='active' "
                    "ORDER BY created_at DESC LIMIT 1").fetchone()
                if r: return r["slug"]
                r = con.execute(
                    "SELECT slug FROM competitions ORDER BY created_at LIMIT 1").fetchone()
                if r: return r["slug"]
        except Exception:
            pass
        return self.DEFAULT_COMP

    def _cid(self, competition_id):
        return competition_id or self._active_slug()

    # ── Meta ──────────────────────────────────────────────────────────────
    def get_meta(self, key, default=""):
        with self._read() as con:
            row = con.execute("SELECT value FROM meta WHERE key=%s", (key,)).fetchone()
            return row["value"] if row else default

    def set_meta(self, key, value):
        with self._write() as con:
            con.execute(
                "INSERT INTO meta (key,value) VALUES (%s,%s) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, value))

    def get_meta_comp(self, base_key, competition_id, default=""):
        cid = self._cid(competition_id)
        v = self.get_meta(f"{base_key}:{cid}", None)
        if v is None:
            v = self.get_meta(base_key, default)
        return v

    def set_meta_comp(self, base_key, competition_id, value):
        self.set_meta(f"{base_key}:{self._cid(competition_id)}", value)

    # ── State ─────────────────────────────────────────────────────────────
    def get_state(self, competition_id=None) -> dict:
        cid = self._cid(competition_id)
        with self._read() as con:
            rows = con.execute("""
                SELECT display_name, tw_team_json, tw_cap_id, tw_vc_id,
                       nw_team_json, nw_cap_id, nw_vc_id
                FROM user_selections
                WHERE competition_id = %s
                  AND week_no = (SELECT MAX(week_no) FROM user_selections u2
                                 WHERE u2.display_name = user_selections.display_name
                                   AND u2.competition_id = user_selections.competition_id)
            """, (cid,)).fetchall()
            members = {}
            for r in rows:
                members[r["display_name"]] = {
                    "this_week": {"team": _jloads(r["tw_team_json"],[]), "cap": r["tw_cap_id"], "vc": r["tw_vc_id"]},
                    "next_week": {"team": _jloads(r["nw_team_json"],[]), "cap": r["nw_cap_id"], "vc": r["nw_vc_id"]},
                }
            # Every registered member belongs to every competition. A brand-new competition has no
            # user_selections rows yet; without this the login screen would list nobody (and a signed-in
            # member would be logged out) the moment it is activated.
            for r in con.execute("SELECT username FROM members ORDER BY username").fetchall():
                members.setdefault(r["username"], {
                    "this_week": {"team": [], "cap": None, "vc": None},
                    "next_week": {"team": [], "cap": None, "vc": None},
                })
            match_rows = con.execute(
                "SELECT id,week_no,title,teams_json,date_label,status,raw_json "
                "FROM matches WHERE competition_id=%s ORDER BY week_no,id", (cid,)).fetchall()
            # One query for all match_scores, grouped in Python (was a per-match
            # query = 74 network round-trips to Postgres on every /api/state).
            scores_by_match: dict = {}
            for sr in con.execute(
                "SELECT match_id, player_id, raw_score_json FROM match_scores WHERE competition_id=%s",
                (cid,)).fetchall():
                scores_by_match.setdefault(sr["match_id"], {})[sr["player_id"]] = _jloads(sr["raw_score_json"], {})
            matches = []
            for mr in match_rows:
                base  = _jloads(mr["raw_json"], {})
                entry = {"id": mr["id"], "wk": mr["week_no"], "title": mr["title"],
                         "teams": _jloads(mr["teams_json"],[]), "date": mr["date_label"],
                         "status": mr["status"]}
                for k, v in base.items():
                    if k not in entry: entry[k] = v
                sc = scores_by_match.get(mr["id"])
                if sc:
                    entry["scores"] = sc
                matches.append(entry)
            player_pts_rows = con.execute(
                "SELECT id, season_pts FROM players WHERE competition_id=%s", (cid,)).fetchall()
            player_pts = {r["id"]: r["season_pts"] for r in player_pts_rows}
        return {
            "members":        members,
            "matches":        matches,
            "player_pts":     player_pts,
            "_saved":         self.get_meta_comp("_saved", cid, "never"),
            "_last_rollover": self.get_meta_comp("_last_rollover", cid, ""),
        }

    def upsert_member(self, name: str, data: dict, competition_id=None) -> None:
        cid = self._cid(competition_id)
        with self._write() as con:
            row = con.execute("SELECT COALESCE(MAX(week_no),1) AS wn FROM user_selections "
                              "WHERE competition_id=%s", (cid,)).fetchone()
            current_week = row["wn"] if row else 1
            tw = data.get("this_week") or {}
            nw = data.get("next_week") or {}
            if "this_week" not in data:
                tw = {"team": data.get("team",[]), "cap": data.get("cap"), "vc": data.get("vc")}
                nw = dict(tw)
            con.execute("""
                INSERT INTO user_selections (competition_id,display_name,week_no,tw_team_json,tw_cap_id,tw_vc_id,nw_team_json,nw_cap_id,nw_vc_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (competition_id,display_name,week_no) DO UPDATE SET
                    tw_team_json=EXCLUDED.tw_team_json, tw_cap_id=EXCLUDED.tw_cap_id, tw_vc_id=EXCLUDED.tw_vc_id,
                    nw_team_json=EXCLUDED.nw_team_json, nw_cap_id=EXCLUDED.nw_cap_id, nw_vc_id=EXCLUDED.nw_vc_id
            """, (cid, name, current_week,
                  json.dumps(tw.get("team",[]) or []), tw.get("cap"), tw.get("vc"),
                  json.dumps(nw.get("team",[]) or []), nw.get("cap"), nw.get("vc")))
            con.execute("INSERT INTO meta (key,value) VALUES (%s,%s) "
                        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                        (f"_saved:{cid}", datetime.now(timezone.utc).isoformat()))

    def upsert_match(self, m: dict, competition_id=None) -> None:
        cid = self._cid(competition_id)
        mid = m.get("id")
        with self._write() as con:
            _upsert_match(con, m, cid)
            con.execute("INSERT INTO meta (key,value) VALUES (%s,%s) "
                        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                        (f"_saved:{cid}", datetime.now(timezone.utc).isoformat()))
        if mid and m.get("scores"):
            self.recalculate_points(match_id=mid, competition_id=cid)

    # ── Points calculation ────────────────────────────────────────────────
    def recalculate_points(self, match_id=None, competition_id=None) -> int:
        from logic.scoring_engine import calc_pts
        cid = self._cid(competition_id)
        with self._read() as con:
            if match_id:
                score_rows = con.execute(
                    "SELECT ms.match_id, ms.player_id, ms.raw_score_json, m.week_no "
                    "FROM match_scores ms JOIN matches m ON m.id=ms.match_id "
                    "WHERE ms.match_id=%s AND ms.competition_id=%s", (match_id, cid)).fetchall()
            else:
                score_rows = con.execute(
                    "SELECT ms.match_id, ms.player_id, ms.raw_score_json, m.week_no "
                    "FROM match_scores ms JOIN matches m ON m.id=ms.match_id "
                    "WHERE ms.competition_id=%s", (cid,)).fetchall()
        if not score_rows: return 0
        now_iso = datetime.now(timezone.utc).isoformat()
        params = []
        for row in score_rows:
            base_pts = calc_pts(_jloads(row["raw_score_json"], {}))
            params.append((cid, row["match_id"], row["player_id"], row["week_no"],
                           base_pts, float(base_pts), now_iso))
        with self._write() as con:
            with con.cursor() as cur:
                cur.executemany("""
                    INSERT INTO player_match_points (competition_id,match_id,player_id,week_no,base_pts,multiplier,final_pts,calculated_at)
                    VALUES (%s,%s,%s,%s,%s,1.0,%s,%s)
                    ON CONFLICT (match_id,player_id) DO UPDATE SET
                        competition_id=EXCLUDED.competition_id,
                        week_no=EXCLUDED.week_no, base_pts=EXCLUDED.base_pts,
                        final_pts=EXCLUDED.final_pts, calculated_at=EXCLUDED.calculated_at
                """, params)
        return len(params)

    def update_player_season_pts(self, competition_id=None) -> int:
        cid = self._cid(competition_id)
        with self._write() as con:
            con.execute("UPDATE players SET season_pts = 0 WHERE competition_id=%s", (cid,))
            con.execute("""
                UPDATE players SET season_pts = (
                    SELECT COALESCE(SUM(pmp.base_pts), 0)
                    FROM player_match_points pmp
                    WHERE pmp.player_id = players.id
                      AND pmp.competition_id = players.competition_id
                ) WHERE competition_id=%s
            """, (cid,))
        with self._read() as con:
            row = con.execute("SELECT COUNT(*) AS n FROM players WHERE season_pts > 0 AND competition_id=%s",
                              (cid,)).fetchone()
            return row["n"] if row else 0

    def update_week_points(self, competition_id=None) -> int:
        cid = self._cid(competition_id)
        with self._read() as con:
            sels = con.execute("""
                SELECT display_name, week_no, tw_team_json, tw_cap_id, tw_vc_id
                FROM user_selections WHERE competition_id=%s
            """, (cid,)).fetchall()
            pmp_map = {}
            for r in con.execute(
                "SELECT player_id, match_id, base_pts FROM player_match_points WHERE competition_id=%s",
                (cid,)).fetchall():
                pmp_map[(r["player_id"], r["match_id"])] = r["base_pts"]
            week_matches: dict = {}
            for r in con.execute(
                "SELECT id, week_no FROM matches WHERE LOWER(status)='completed' AND competition_id=%s",
                (cid,)).fetchall():
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

        with self._write() as con:
            with con.cursor() as cur:
                if ump_rows:
                    cur.executemany("""
                        INSERT INTO user_match_points (competition_id, display_name, week_no, match_id, pts)
                        VALUES (%s,%s,%s,%s,%s)
                        ON CONFLICT (display_name, match_id) DO UPDATE SET
                            competition_id=EXCLUDED.competition_id,
                            pts=EXCLUDED.pts, week_no=EXCLUDED.week_no
                    """, [(cid, name, wk, mid, pts) for (name, wk, mid, pts) in ump_rows])
                upd = [(pts, json.dumps(ppm_blobs.get((name, wk), {})), cid, name, wk)
                       for (name, wk), pts in wk_totals.items()]
                if upd:
                    cur.executemany(
                        "UPDATE user_selections SET week_pts=%s, points_per_match=%s "
                        "WHERE competition_id=%s AND display_name=%s AND week_no=%s", upd)
        return len(wk_totals)

    # ── Rollover DAO ──────────────────────────────────────────────────────
    def get_users_and_max_weeks(self, competition_id=None) -> list:
        cid = self._cid(competition_id)
        with self._read() as con:
            rows = con.execute(
                "SELECT display_name, MAX(week_no) AS cur_wk "
                "FROM user_selections WHERE competition_id=%s GROUP BY display_name",
                (cid,)).fetchall()
        return [dict(r) for r in rows]

    def get_selection_row(self, display_name: str, week_no: int, competition_id=None):
        cid = self._cid(competition_id)
        with self._read() as con:
            row = con.execute(
                "SELECT tw_team_json, tw_cap_id, tw_vc_id, nw_team_json, nw_cap_id, nw_vc_id "
                "FROM user_selections WHERE competition_id=%s AND display_name=%s AND week_no=%s",
                (cid, display_name, week_no)).fetchone()
        return dict(row) if row else None

    def insert_rollover_week(self, display_name: str, new_week_no: int,
                              team_json: str, cap_id, vc_id, competition_id=None) -> None:
        cid = self._cid(competition_id)
        with self._write() as con:
            con.execute("""
                INSERT INTO user_selections
                    (competition_id, display_name, week_no,
                     tw_team_json, tw_cap_id, tw_vc_id,
                     nw_team_json, nw_cap_id, nw_vc_id)
                VALUES (%s,%s,%s, %s,%s,%s, %s,%s,%s)
                ON CONFLICT (competition_id, display_name, week_no) DO NOTHING
            """, (cid, display_name, new_week_no,
                  team_json, cap_id, vc_id, team_json, cap_id, vc_id))

    def set_last_rollover(self, iso: str, competition_id=None) -> None:
        cid = self._cid(competition_id)
        with self._write() as con:
            con.execute("INSERT INTO meta (key,value) VALUES (%s,%s) "
                        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                        (f"_last_rollover:{cid}", iso))

    # ── Rebuild / hydrate (JSON archives) ─────────────────────────────────
    def rebuild_scores_and_points(self, json_dir=None, competition_id=None) -> dict:
        from pathlib import Path
        cid = self._cid(competition_id)
        if json_dir is None:
            json_dir = Path(self._dsn).parent  # unused in Postgres; kept for signature
        json_dir = Path(json_dir) if json_dir else None
        with self._write() as con:
            con.execute("DELETE FROM player_match_points WHERE competition_id=%s", (cid,))
            con.execute("DELETE FROM match_scores WHERE competition_id=%s", (cid,))
            con.execute("DELETE FROM user_match_points WHERE competition_id=%s", (cid,))
            con.execute("UPDATE user_selections SET week_pts = 0, points_per_match = '{}' WHERE competition_id=%s", (cid,))
            con.execute("UPDATE players SET season_pts = 0, points = 0 WHERE competition_id=%s", (cid,))
        files_ingested = 0
        if json_dir and json_dir.exists():
            files = sorted(json_dir.glob("*.json"),
                           key=lambda f: int(mm.group(1)) if (mm := re.search(r"(\d+)", f.stem)) else 0)
            with self._write() as con:
                for fp in files:
                    try:
                        with open(fp) as fh: match_data = json.load(fh)
                        if "id" not in match_data: continue
                        _upsert_match(con, match_data, cid); files_ingested += 1
                    except Exception as e:
                        print(f"  [rebuild] skip {fp.name}: {e}")
        pmp_rows      = self.recalculate_points(competition_id=cid)
        week_pts_rows = self.update_week_points(competition_id=cid)
        player_rows   = self.update_player_season_pts(competition_id=cid)
        return {"files_ingested": files_ingested, "pmp_rows": pmp_rows,
                "week_pts_rows": week_pts_rows, "player_pts_rows": player_rows}

    def hydrate_from_json(self, json_dir=None, competition_id=None) -> int:
        from pathlib import Path
        cid = self._cid(competition_id)
        if not json_dir: return 0
        json_dir = Path(json_dir)
        if not json_dir.exists(): return 0
        files = sorted(json_dir.glob("*.json"),
                       key=lambda f: int(mm.group(1)) if (mm := re.search(r"(\d+)", f.stem)) else 0)
        if not files: return 0
        count = 0
        with self._write() as con:
            for fp in files:
                try:
                    with open(fp) as fh: match_data = json.load(fh)
                    if "id" in match_data: _upsert_match(con, match_data, cid); count += 1
                except Exception as e:
                    print(f"  [hydrate] skip {fp.name}: {e}")
        if count:
            self.recalculate_points(competition_id=cid)
            print(f"  [hydrate] Ingested {count} matches")
        return count

    # ── Queries ───────────────────────────────────────────────────────────
    def ping_stats(self, competition_id=None) -> dict:
        cid = self._cid(competition_id)
        with self._read() as con:
            member_count = con.execute(
                "SELECT COUNT(DISTINCT display_name) AS n FROM user_selections WHERE competition_id=%s",
                (cid,)).fetchone()["n"]
            scored_count = con.execute(
                "SELECT COUNT(DISTINCT match_id) AS n FROM match_scores WHERE played=1 AND competition_id=%s",
                (cid,)).fetchone()["n"]
        return {"members": member_count, "matches_scored": scored_count,
                "saved": self.get_meta_comp("_saved", cid, "never")}

    def get_leaderboard(self, week_no=None, competition_id=None) -> dict:
        cid = self._cid(competition_id)
        with self._read() as con:
            rows = con.execute(_LEADERBOARD_SQL, {"week_no": week_no, "comp": cid}).fetchall()
            if week_no is None:
                wk_rows = con.execute(
                    "SELECT display_name, week_no, week_pts FROM user_selections "
                    "WHERE competition_id=%s ORDER BY week_no", (cid,)).fetchall()
            else:
                wk_rows = con.execute(
                    "SELECT display_name, week_no, week_pts FROM user_selections "
                    "WHERE competition_id=%s AND week_no=%s", (cid, week_no)).fetchall()
            weekly = {}
            for wr in wk_rows:
                weekly.setdefault(wr["display_name"], []).append(
                    {"week_no": wr["week_no"], "pts": wr["week_pts"]})
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

    def get_etags(self, competition_id=None) -> dict:
        return {"state": self.get_meta_comp("_saved", competition_id, "never")}

    def get_current_week(self, competition_id=None) -> int:
        cid = self._cid(competition_id)
        with self._read() as con:
            row = con.execute(
                "SELECT COALESCE(MAX(week_no),1) AS wn FROM user_selections WHERE competition_id=%s",
                (cid,)).fetchone()
            return int(row["wn"]) if row else 1

    def get_players(self, competition_id=None) -> list:
        cid = self._cid(competition_id)
        with self._read() as con:
            rows = con.execute(
                "SELECT id,name,team,role,price,season_pts,points "
                "FROM players WHERE competition_id=%s ORDER BY season_pts DESC, name",
                (cid,)).fetchall()
            return [dict(r) for r in rows]

    def get_history(self, name: str, competition_id=None) -> dict:
        cid = self._cid(competition_id)
        with self._read() as con:
            current_week = self.get_current_week(cid)
            rows = con.execute("""
                SELECT week_no,tw_team_json,tw_cap_id,tw_vc_id,nw_team_json,nw_cap_id,nw_vc_id,
                       week_pts,points_per_match
                FROM user_selections WHERE competition_id=%s AND display_name=%s ORDER BY week_no ASC
            """, (cid, name)).fetchall()
        weeks = [
            {"week_no": r["week_no"], "is_current": r["week_no"]==current_week,
             "this_week": {"team": _jloads(r["tw_team_json"],[]), "cap": r["tw_cap_id"], "vc": r["tw_vc_id"]},
             "next_week": {"team": _jloads(r["nw_team_json"],[]), "cap": r["nw_cap_id"], "vc": r["nw_vc_id"]},
             "week_pts": r["week_pts"],
             "points_per_match": _jloads(r["points_per_match"], {})}
            for r in rows
        ]
        return {"name": name, "current_week": current_week, "weeks": weeks, "ok": True}

    def validate_budget(self, player_ids: list, budget: float = 100.0, competition_id=None) -> tuple:
        if not player_ids: return True, 0.0
        cid = self._cid(competition_id)
        with self._read() as con:
            ph   = ",".join(["%s"] * len(player_ids))
            rows = con.execute(f"SELECT id,price FROM players WHERE competition_id=%s AND id IN ({ph})",
                               [cid, *player_ids]).fetchall()
        price_map = {r["id"]: r["price"] for r in rows}
        total = round(sum(price_map.get(pid, 0.0) for pid in player_ids), 1)
        return total <= budget, total

    def validate_selection(self, team: list, cap, vc, budget: float, xi_size: int,
                           competition_id=None) -> tuple:
        """Full server-side validation of a proposed XI (BIZ-1)."""
        if not team:
            return True, None, 0.0
        if len(team) != xi_size:
            return False, f"Need exactly {xi_size} players (got {len(team)})", 0.0
        if len(set(team)) != len(team):
            return False, "Duplicate players are not allowed", 0.0
        cid = self._cid(competition_id)
        with self._read() as con:
            ph   = ",".join(["%s"] * len(team))
            rows = con.execute(
                f"SELECT id, price FROM players WHERE competition_id=%s AND id IN ({ph})",
                [cid, *team]).fetchall()
        price_map = {r["id"]: r["price"] for r in rows}
        missing = [pid for pid in team if pid not in price_map]
        if missing:
            return False, f"Unknown player(s) for this competition: {', '.join(missing[:5])}", 0.0
        total = round(sum(price_map[pid] for pid in team), 1)
        if total > budget:
            return False, f"Budget exceeded: {total:.1f} CR (max {budget:.1f})", total
        if cap and cap not in team:
            return False, "Captain must be one of your selected players", total
        if vc and vc not in team:
            return False, "Vice-captain must be one of your selected players", total
        if cap and vc and cap == vc:
            return False, "Captain and vice-captain must be different players", total
        return True, None, total

    def save_next_week(self, name: str, team: list, cap, vc, competition_id=None) -> dict:
        cid = self._cid(competition_id)
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._write() as con:
            row = con.execute(
                "SELECT COALESCE(MAX(week_no),1) AS wn FROM user_selections WHERE competition_id=%s",
                (cid,)).fetchone()
            current_week = int(row["wn"]) if row else 1
            con.execute("""
                INSERT INTO user_selections
                    (competition_id,display_name,week_no,tw_team_json,tw_cap_id,tw_vc_id,
                     nw_team_json,nw_cap_id,nw_vc_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (competition_id,display_name,week_no) DO UPDATE SET
                    nw_team_json=EXCLUDED.nw_team_json,
                    nw_cap_id=EXCLUDED.nw_cap_id,
                    nw_vc_id=EXCLUDED.nw_vc_id
            """, (cid, name, current_week, "[]", None, None, json.dumps(team), cap, vc))
            con.execute("INSERT INTO meta (key,value) VALUES (%s,%s) "
                        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (f"_saved:{cid}", now_iso))
        return {"week_no": current_week}

    # ── Competition DAO ───────────────────────────────────────────────────
    def list_competitions(self) -> list:
        with self._read() as con:
            rows = con.execute(
                "SELECT slug,name,format,status,budget_total,xi_size,max_weeks,"
                "week1_anchor_utc,deadline_hour,deadline_min,series_id,series_slug,year,"
                "valid_teams_json,champion,closed_at,created_at "
                "FROM competitions ORDER BY created_at").fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["valid_teams"] = _jloads(d.pop("valid_teams_json"), [])
                out.append(d)
            return out

    def get_competition(self, slug: str):
        with self._read() as con:
            r = con.execute("SELECT * FROM competitions WHERE slug=%s", (slug,)).fetchone()
            if not r: return None
            d = dict(r)
            d["valid_teams"] = _jloads(d.get("valid_teams_json"), [])
            return d

    def create_competition(self, slug: str, name: str, **kw) -> None:
        fields = {
            "format":           kw.get("format", "T20"),
            "status":           kw.get("status", "upcoming"),
            "budget_total":     kw.get("budget_total", 100.0),
            "xi_size":          kw.get("xi_size", 11),
            "max_weeks":        kw.get("max_weeks", 10),
            "week1_anchor_utc": kw.get("week1_anchor_utc", "1970-01-01T00:00:00+00:00"),
            "deadline_hour":    kw.get("deadline_hour", 14),
            "deadline_min":     kw.get("deadline_min", 0),
            "series_id":        kw.get("series_id"),
            "series_slug":      kw.get("series_slug"),
            "year":             kw.get("year"),
            "valid_teams_json": json.dumps(kw.get("valid_teams", [])),
            "created_at":       datetime.now(timezone.utc).isoformat(),
        }
        cols = ["slug", "name", *fields.keys()]
        vals = [slug, name, *fields.values()]
        ph   = ",".join(["%s"] * len(cols))
        with self._write() as con:
            con.execute(f"INSERT INTO competitions ({','.join(cols)}) VALUES ({ph})", vals)

    def set_competition_status(self, slug: str, status: str) -> None:
        with self._write() as con:
            con.execute("UPDATE competitions SET status=%s WHERE slug=%s", (status, slug))

    def set_champion(self, slug: str, champion: str) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._write() as con:
            con.execute(
                "UPDATE competitions SET champion=%s, status='completed', closed_at=%s WHERE slug=%s",
                (champion, now_iso, slug))

    # ── Competition data import (admin) ───────────────────────────────────
    # Competition-row columns an import may fill in (anything else is refused).
    # week1_anchor_utc may only be filled in while it is still the never-set 1970 default.
    _IMPORT_COMP_FIELDS = ("valid_teams_json", "series_id", "series_slug", "year", "week1_anchor_utc")

    def import_competition_data(self, slug, *, players=None, matches=None,
                                comp_updates=None, dry_run=True) -> dict:
        """Admin onboarding: upsert a competition's roster and/or fixtures in ONE
        transaction. `players` = [(id,name,team,price,role)], `matches` = row dicts
        from logic.competition_import.parse_schedule(), `comp_updates` = {column:
        value} limited to _IMPORT_COMP_FIELDS.

        Never deletes. Existing players are updated in place (season_pts/points
        untouched); players missing from the file are only reported. Existing
        matches are updated only while 'upcoming' (live/completed ones are locked
        so scored data is never disturbed) and a real Cricbuzz scorecard URL is
        never replaced by the /00000 placeholder. competitions.max_weeks is
        re-derived from the match weeks, and the state ETag (`_saved:<slug>`) is
        bumped so open browsers refresh.

        dry_run=True runs exactly the same statements and then ROLLS BACK, so the
        returned report is precisely what a real import would do. Raises
        LookupError (unknown competition) / ValueError (blocked) with a
        user-readable message."""
        rep = {"players": None, "matches": None, "competition": {}}
        with self._pool.connection() as con:
            comp = con.execute(
                "SELECT slug,status,max_weeks,valid_teams_json,series_id,series_slug,year,week1_anchor_utc "
                "FROM competitions WHERE slug=%s FOR UPDATE", (slug,)).fetchone()
            if not comp:
                raise LookupError(f"competition '{slug}' not found")
            if comp["status"] == "completed":
                raise ValueError("this competition is completed and read-only - reopen it before importing")
            rep["status"] = comp["status"]

            if players is not None:
                have = {r["id"]: r for r in con.execute(
                    "SELECT id,name,team,price,role FROM players WHERE competition_id=%s", (slug,)).fetchall()}
                ins, upd, same, changes = [], [], 0, []
                for pid, name, team, price, role in players:
                    old = have.get(pid)
                    if old is None:
                        ins.append((slug, pid, name, team, price, role))
                        continue
                    ch = {}
                    for field, new in (("name", name), ("team", team), ("price", price), ("role", role)):
                        if (abs(float(old[field]) - float(new)) > 1e-9) if field == "price" else (old[field] != new):
                            ch[field] = [old[field], new]
                    if ch:
                        upd.append((name, team, price, role, slug, pid))
                        changes.append({"id": pid, "name": name, "changes": ch})
                    else:
                        same += 1
                with con.cursor() as cur:
                    if ins:
                        cur.executemany(
                            "INSERT INTO players (competition_id,id,name,team,price,role) "
                            "VALUES (%s,%s,%s,%s,%s,%s)", ins)
                    if upd:
                        cur.executemany(
                            "UPDATE players SET name=%s,team=%s,price=%s,role=%s "
                            "WHERE competition_id=%s AND id=%s", upd)
                rep["players"] = {
                    "inserted": len(ins), "updated": len(upd), "unchanged": same,
                    "not_in_file": sorted(set(have) - {p[0] for p in players}),
                    "changes": changes[:50]}

            if matches is not None:
                ids = [m["id"] for m in matches]
                clash = [r["id"] for r in con.execute(
                    "SELECT id FROM matches WHERE id = ANY(%s) AND competition_id <> %s",
                    (ids, slug)).fetchall()]
                if clash:
                    raise ValueError("match id(s) already used by another competition: " + ", ".join(clash[:5]))
                have = {r["id"]: r for r in con.execute(
                    "SELECT id,week_no,title,teams_json,date_label,status,scorecard_url "
                    "FROM matches WHERE competition_id=%s", (slug,)).fetchall()}
                ins, upd, same, locked = [], [], 0, 0
                for m in matches:
                    old = have.get(m["id"])
                    if old is None:
                        ins.append((slug, m["id"], m["week_no"], m["title"], "upcoming",
                                    m["scorecard_url"], m["teams_json"], m["date_label"]))
                    elif old["status"] != "upcoming":
                        locked += 1
                    else:
                        url = m["scorecard_url"] if m["cricbuzz_id"] else old["scorecard_url"]
                        new = (m["week_no"], m["title"], m["teams_json"], m["date_label"], url)
                        cur_v = (old["week_no"], old["title"], old["teams_json"], old["date_label"], old["scorecard_url"])
                        if new != cur_v:
                            upd.append((*new, slug, m["id"]))
                        else:
                            same += 1
                with con.cursor() as cur:
                    if ins:
                        cur.executemany(
                            "INSERT INTO matches (competition_id,id,week_no,title,status,scorecard_url,"
                            "teams_json,date_label) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", ins)
                    if upd:
                        cur.executemany(
                            "UPDATE matches SET week_no=%s,title=%s,teams_json=%s,date_label=%s,scorecard_url=%s "
                            "WHERE competition_id=%s AND id=%s", upd)
                rep["matches"] = {
                    "inserted": len(ins), "updated": len(upd), "unchanged": same, "locked": locked,
                    "not_in_file": len(set(have) - set(ids))}
                # max_weeks is derived from the schedule itself (never hand-set).
                mw = con.execute("SELECT COALESCE(MAX(week_no),1) AS w FROM matches WHERE competition_id=%s",
                                 (slug,)).fetchone()["w"]
                if mw != comp["max_weeks"]:
                    con.execute("UPDATE competitions SET max_weeks=%s WHERE slug=%s", (mw, slug))
                    rep["competition"]["max_weeks"] = [comp["max_weeks"], mw]

            for col, val in (comp_updates or {}).items():
                if col not in self._IMPORT_COMP_FIELDS:
                    raise ValueError(f"import may not change competition field '{col}'")
                if col == "week1_anchor_utc" and not str(comp[col] or "1970").startswith("1970"):
                    raise ValueError("the week-1 date is already set and cannot be changed by an import")
                if comp[col] != val:
                    con.execute(f"UPDATE competitions SET {col}=%s WHERE slug=%s", (val, slug))
                    rep["competition"][col] = [comp[col], val]

            rep["selections_with_teams"] = con.execute(
                "SELECT COUNT(*) AS n FROM user_selections WHERE competition_id=%s "
                "AND (tw_team_json<>'[]' OR nw_team_json<>'[]')", (slug,)).fetchone()["n"]
            if dry_run:
                con.rollback()
            else:
                con.execute("INSERT INTO meta (key,value) VALUES (%s,%s) "
                            "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                            (f"_saved:{slug}", datetime.now(timezone.utc).isoformat()))
        return rep

    def get_championship_tally(self) -> list:
        with self._read() as con:
            rows = con.execute(
                "SELECT champion, COUNT(*) AS titles FROM competitions "
                "WHERE status='completed' AND champion IS NOT NULL AND champion<>'' "
                "GROUP BY champion ORDER BY titles DESC, champion").fetchall()
            return [dict(r) for r in rows]

    # ── Passcode / auth DAO ───────────────────────────────────────────────
    def get_member_auth(self, username: str):
        with self._read() as con:
            r = con.execute(
                "SELECT username, passcode_hash, must_change, is_admin "
                "FROM members WHERE username=%s", (username,)).fetchone()
            return dict(r) if r else None

    def upsert_member_auth(self, username: str, passcode_hash: str,
                           must_change: int = 0, is_admin: int = 0) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._write() as con:
            con.execute("""
                INSERT INTO members (username, passcode_hash, must_change, is_admin, created_at)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (username) DO UPDATE SET
                    passcode_hash = EXCLUDED.passcode_hash,
                    must_change   = EXCLUDED.must_change
            """, (username, passcode_hash, int(must_change), int(is_admin), now_iso))

    def set_passcode(self, username: str, passcode_hash: str, must_change: int = 0) -> None:
        with self._write() as con:
            con.execute("UPDATE members SET passcode_hash=%s, must_change=%s WHERE username=%s",
                        (passcode_hash, int(must_change), username))

    def list_members_admin_view(self) -> list:
        with self._read() as con:
            rows = con.execute(
                "SELECT username, must_change, is_admin, created_at "
                "FROM members ORDER BY username").fetchall()
            return [dict(r) for r in rows]

    # ── Sessions ──────────────────────────────────────────────────────────
    def create_session(self, token: str, username: str, ttl_days: int = 30) -> str:
        expires = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()
        with self._write() as con:
            con.execute("INSERT INTO sessions (token, username, expires_at) VALUES (%s,%s,%s)",
                        (token, username, expires))
        return expires

    def get_session(self, token: str):
        if not token: return None
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._write() as con:
            con.execute("DELETE FROM sessions WHERE expires_at < %s", (now_iso,))
            r = con.execute("SELECT username, expires_at FROM sessions WHERE token=%s", (token,)).fetchone()
            return dict(r) if r else None

    def delete_sessions_for_user(self, username: str) -> int:
        with self._write() as con:
            cur = con.execute("DELETE FROM sessions WHERE username=%s", (username,))
            return cur.rowcount or 0
