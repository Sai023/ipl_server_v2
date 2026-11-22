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
from logic.session_policy import SESSION_TTL_DAYS, new_expiry, renewed_expiry
from logic import rollover_plan as _plan
from logic.rollover_engine import pick_active_team as _pick_active_team

# The passcode_hash of a member who signs in with Google only (a member who switched, or one who joined with Google). It can never equal a real passcode hash (those are 64 hex
# characters), so the passcode check can never pass for such a member; the login route also says why ("pin_retired"). Phase 3, 2026-11-21.
NO_PIN = "-"


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
ALTER TABLE user_selections ADD COLUMN IF NOT EXISTS nw_touched_at TEXT;
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
CREATE TABLE IF NOT EXISTS identities (
    provider       TEXT NOT NULL,
    subject        TEXT NOT NULL,
    member         TEXT NOT NULL REFERENCES members (username) ON DELETE CASCADE ON UPDATE CASCADE,
    email          TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
    last_login_at  TEXT,
    PRIMARY KEY (provider, subject),
    UNIQUE (provider, member)
);
CREATE TABLE IF NOT EXISTS competition_weeks (
    competition_id  TEXT    NOT NULL,
    week_no         INTEGER NOT NULL CHECK (week_no >= 1),
    lock_at_utc     TEXT    NOT NULL,
    first_match_utc TEXT,
    PRIMARY KEY (competition_id, week_no)
);
CREATE TABLE IF NOT EXISTS rollover_log (
    id             BIGSERIAL PRIMARY KEY,
    competition_id TEXT    NOT NULL,
    kind           TEXT    NOT NULL CHECK (kind IN ('start','rollover')),
    to_week        INTEGER NOT NULL CHECK (to_week >= 1),
    lock_at_utc    TEXT,
    applied_at     TEXT    NOT NULL,
    triggered_by   TEXT    NOT NULL DEFAULT '',
    members        INTEGER NOT NULL DEFAULT 0,
    detail_json    TEXT    NOT NULL DEFAULT '{}',
    undone_at      TEXT,
    UNIQUE (competition_id, to_week),
    CHECK ((kind = 'start') = (to_week = 1))
);
CREATE TABLE IF NOT EXISTS ops_heartbeat (
    source  TEXT PRIMARY KEY,
    last_at TEXT NOT NULL,
    detail  TEXT NOT NULL DEFAULT ''
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
        if v is None and cid == self.DEFAULT_COMP:
            # The un-suffixed keys (_saved, _last_rollover) are the pre-multi-competition ones: they belong to the FIRST competition only.
            # (Before 2026-11-19 every competition fell back to them, so SA20 inherited IPL's "last rollover".)
            v = self.get_meta(base_key, default)
        return default if v is None else v

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

    # ── Lock schedule and weekly transitions (Phase 1.5, 2026-11-20) ───────────────────────────────────────────────
    # A competition with a stored schedule (competition_weeks) is MANAGED: when each week's XI locks, which transition is due and whether picking is
    # locked all come from logic/rollover_plan.py. Every transition ("start" = the pre-season draft becomes the XI that plays week 1, "rollover" to week
    # k = every member's draft becomes the XI that plays week k) runs in ONE transaction under a row lock on the competition (reconcile_competition), is
    # recorded in rollover_log with what it changed (so it can be undone) and is applied at most once, whoever triggers it and however often.

    def get_schedule(self, slug) -> list:
        with self._read() as con:
            return [dict(r) for r in con.execute(
                "SELECT week_no, lock_at_utc, first_match_utc FROM competition_weeks WHERE competition_id=%s ORDER BY week_no", (slug,)).fetchall()]

    def get_auto_activate(self, slug) -> bool:
        return self.get_meta(f"auto_activate:{slug}", "") == "1"

    @staticmethod
    def _ledger_sets(con, slug):
        """(applied, held): the weeks whose transition is in force, and the weeks an admin undid. A held week is NOT in force (picking stays locked, as the
        competition is between two weeks) but no trigger applies it again by itself: only a forced run does."""
        applied, held = set(), set()
        for r in con.execute("SELECT to_week, undone_at FROM rollover_log WHERE competition_id=%s", (slug,)).fetchall():
            (held if r["undone_at"] else applied).add(r["to_week"])
        return applied, held

    def get_lock_state(self, slug, now=None):
        """The lock state of a managed competition (logic.rollover_plan.lock_state), or None when it has no schedule."""
        now = _plan.parse_utc(now) or datetime.now(timezone.utc)
        with self._read() as con:
            comp = con.execute("SELECT status, max_weeks FROM competitions WHERE slug=%s", (slug,)).fetchone()
            rows = [dict(r) for r in con.execute(
                "SELECT week_no, lock_at_utc, first_match_utc FROM competition_weeks WHERE competition_id=%s ORDER BY week_no", (slug,)).fetchall()]
            applied, held = self._ledger_sets(con, slug)
        if not comp or not rows:
            return None
        state = _plan.lock_state(rows, applied, now, comp["status"], max_weeks=comp["max_weeks"])
        state["held"] = sorted(held)
        return state

    def get_rollover_log(self, slug) -> list:
        with self._read() as con:
            rows = con.execute(
                "SELECT kind, to_week, lock_at_utc, applied_at, triggered_by, members, detail_json, undone_at "
                "FROM rollover_log WHERE competition_id=%s ORDER BY to_week", (slug,)).fetchall()
        out = []
        for r in rows:
            d = dict(r); d["detail"] = _jloads(d.pop("detail_json"), {}); out.append(d)
        return out

    def set_schedule(self, slug, rows, *, auto_activate=None, dry_run=False) -> dict:
        """Store a competition's lock schedule (validated by logic.rollover_plan). `auto_activate` (None = leave as it is) lets the competition start and
        activate itself at its week-1 lock. A week whose transition has already run keeps its lock time. dry_run=True runs the same statements and ROLLS
        BACK, so the report is exactly what a real call would do. Raises LookupError / ValueError with a readable message."""
        with self._pool.connection() as con:
            comp = con.execute("SELECT slug,status,max_weeks FROM competitions WHERE slug=%s FOR UPDATE", (slug,)).fetchone()
            if not comp:
                raise LookupError(f"competition '{slug}' not found")
            if comp["status"] == "completed":
                raise ValueError("this competition is completed and read-only")
            errors = _plan.validate_schedule(rows, max_weeks=comp["max_weeks"])
            if errors:
                raise ValueError("; ".join(errors))
            norm = [{"week_no": r["week_no"], "lock_at_utc": _plan.iso(_plan.parse_utc(r["lock_at_utc"])),
                     "first_match_utc": _plan.iso(_plan.parse_utc(r.get("first_match_utc"))) if _plan.parse_utc(r.get("first_match_utc")) else None}
                    for r in sorted(rows, key=lambda x: x["week_no"])]
            old = {r["week_no"]: r["lock_at_utc"] for r in con.execute(
                "SELECT week_no, lock_at_utc FROM competition_weeks WHERE competition_id=%s", (slug,)).fetchall()}
            handled = sorted(self._ledger_sets(con, slug)[0])
            for wk in handled:
                new = next((r for r in norm if r["week_no"] == wk), None)
                if new is None or _plan.parse_utc(new["lock_at_utc"]) != _plan.parse_utc(old.get(wk)):
                    raise ValueError(f"week {wk} has already run; its lock time cannot change")
            changed = [r["week_no"] for r in norm if _plan.parse_utc(old.get(r["week_no"])) != _plan.parse_utc(r["lock_at_utc"])]
            con.execute("DELETE FROM competition_weeks WHERE competition_id=%s", (slug,))
            for r in norm:
                con.execute("INSERT INTO competition_weeks (competition_id, week_no, lock_at_utc, first_match_utc) VALUES (%s,%s,%s,%s)",
                            (slug, r["week_no"], r["lock_at_utc"], r["first_match_utc"]))
            if auto_activate is not None:
                con.execute("INSERT INTO meta (key,value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                            (f"auto_activate:{slug}", "1" if auto_activate else "0"))
            con.execute("INSERT INTO meta (key,value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                        (f"_saved:{slug}", datetime.now(timezone.utc).isoformat()))   # open browsers refresh their deadline
            report = {"competition": slug, "weeks": len(norm), "schedule": norm, "changed_weeks": changed, "already_run": handled,
                      "auto_activate": (auto_activate if auto_activate is not None else self.get_auto_activate(slug)), "dry_run": bool(dry_run)}
            if dry_run:
                con.rollback()
        return report

    def reconcile_competition(self, slug, now=None, *, triggered_by="app", force_next=False, expect_week=None, dry_run=False, max_steps=12) -> dict:
        """Apply every transition that is due for a managed competition, in order. One transaction per transition (a failure leaves the weeks already
        applied intact and nothing half done), each under a row lock on the competition and recorded once in rollover_log, so it does not matter how many
        triggers call this or how late: it is a catch-up that is safe to repeat.

        `force_next` applies exactly the next transition now whatever its lock time says, and also re-applies a week an admin undid (an explicit
        rehearsal or repair; the status rules still hold). `expect_week` makes that step refuse unless it IS that week (the confirmation an admin typed
        is checked inside the transaction, so a trigger that got there first cannot make a forced run apply a different week). dry_run=True applies
        everything inside one transaction and ROLLS BACK, so the report is exactly what a real run would do.

        Returns {"applied": [...], "skipped": reason or None, "error": text when a step failed (the steps before it are kept), ...}. A transition is
        listed in "applied" only once its transaction has committed. Raises LookupError for an unknown competition."""
        now = _plan.parse_utc(now) or datetime.now(timezone.utc)
        rep = {"competition": slug, "now": now.isoformat(), "dry_run": bool(dry_run), "applied": [], "skipped": None}
        steps = (lambda i: (force_next, expect_week) if i == 0 else (False, None))      # force and the confirmation belong to the first step only
        if dry_run:
            with self._pool.connection() as con:
                try:
                    for i in range(max_steps):
                        f, ew = steps(i)
                        entry = self._reconcile_one(con, slug, now, triggered_by, f, ew, rep)
                        if entry is None:
                            break
                        rep["applied"].append(entry)
                except LookupError:
                    raise
                except Exception as e:                                        # report it, as a real run would
                    rep["error"] = f"{type(e).__name__}: {e}"
                finally:
                    con.rollback()
            return rep
        for i in range(max_steps):
            f, ew = steps(i)
            try:
                with self._pool.connection() as con:
                    entry = self._reconcile_one(con, slug, now, triggered_by, f, ew, rep)
            except LookupError:
                raise
            except Exception as e:                                            # this step is rolled back; the earlier ones stay
                rep["error"] = f"{type(e).__name__}: {e}"
                break
            if entry is None:
                break
            rep["applied"].append(entry)                                      # only now: the transaction has committed
        if rep["applied"]:
            try:
                self.update_week_points(competition_id=slug)                  # the derived points follow the new rows (idempotent)
            except Exception as e:
                rep["points_error"] = f"{type(e).__name__}: {e}"              # the transitions stand; the next scoring pass recomputes the points
        return rep

    def _reconcile_one(self, con, slug, now, triggered_by, force_next, expect_week, rep):
        """Apply the next due transition inside `con` (None when nothing is due or it must not run; the reason goes into rep["skipped"])."""
        comp = con.execute("SELECT slug,status,max_weeks FROM competitions WHERE slug=%s FOR UPDATE", (slug,)).fetchone()
        if not comp:
            raise LookupError(f"competition '{slug}' not found")
        rows = [dict(r) for r in con.execute(
            "SELECT week_no, lock_at_utc, first_match_utc FROM competition_weeks WHERE competition_id=%s ORDER BY week_no", (slug,)).fetchall()]

        def skip(why):
            if not rep["applied"]:
                rep["skipped"] = why
            return None

        if not rows:
            return skip("this competition has no lock schedule")
        handled, held = self._ledger_sets(con, slug)
        auto = (con.execute("SELECT value FROM meta WHERE key=%s", (f"auto_activate:{slug}",)).fetchone() or {}).get("value") == "1"
        t = _plan.next_due(rows, handled, now, comp["status"], max_weeks=comp["max_weeks"], auto_activate=auto, force=force_next)
        if t is None:
            ls = _plan.lock_state(rows, handled, now, comp["status"], max_weeks=comp["max_weeks"])
            if ls["season_over"]:
                return skip("the season is over: " + ls["reason"])
            if (ls["awaiting"] or force_next) and comp["status"] == "upcoming" and not auto:
                return skip("the competition is upcoming and does not start itself (activate it, or turn on auto-activation)")
            if ls["next_lock"]:
                return skip("nothing is due yet: the next lock is " + _plan.iso(ls["next_lock"]))
            return skip(ls["reason"] or "nothing is due")
        if t["to_week"] in held and not force_next:
            return skip(f"week {t['to_week']} was undone by an admin and is held: a forced run applies it again")
        if expect_week is not None and t["to_week"] != expect_week:
            return skip(f"the next week to apply is {t['to_week']}, not {expect_week}")
        return self._apply_transition(con, slug, comp, t, now, triggered_by, redo=t["to_week"] in held)

    def _apply_transition(self, con, slug, comp, t, now, triggered_by, redo=False) -> dict:
        """One transition, inside the caller's transaction (and its row lock on the competition). Returns the report; the same facts, plus what it
        changed, are written to rollover_log so the transition can be undone."""
        now_iso = now.isoformat()
        to_week, kind = t["to_week"], t["kind"]
        names = sorted({r["username"] for r in con.execute("SELECT username FROM members").fetchall()}
                       | {r["display_name"] for r in con.execute(
                           "SELECT DISTINCT display_name FROM user_selections WHERE competition_id=%s", (slug,)).fetchall()})
        known_ids = {r["id"] for r in con.execute("SELECT id FROM players WHERE competition_id=%s", (slug,)).fetchall()}
        detail = {"before_status": comp["status"], "activated": False, "members": [], "inserted": [], "warnings": []}
        cols = "tw_team_json,tw_cap_id,tw_vc_id,nw_team_json,nw_cap_id,nw_vc_id,nw_touched_at"
        if kind == "start":
            ahead = con.execute("SELECT MAX(week_no) AS wn FROM user_selections WHERE competition_id=%s", (slug,)).fetchone()["wn"]
            if ahead is not None and ahead > 1:
                raise ValueError(f"cannot start '{slug}': it already has selections for week {ahead}")
        for name in names:
            if kind == "start":
                row = con.execute(f"SELECT week_no,{cols} FROM user_selections WHERE competition_id=%s AND display_name=%s AND week_no=1 FOR UPDATE",
                                  (slug, name)).fetchone()
            else:
                row = con.execute(f"SELECT week_no,{cols} FROM user_selections WHERE competition_id=%s AND display_name=%s "
                                  "ORDER BY week_no DESC LIMIT 1 FOR UPDATE", (slug, name)).fetchone()
            entry = {"name": name}
            if row is None:                                                           # never drafted anything: an empty XI for the week (0 points)
                con.execute("INSERT INTO user_selections (competition_id, display_name, week_no) VALUES (%s,%s,%s) "
                            "ON CONFLICT (competition_id, display_name, week_no) DO NOTHING", (slug, name, to_week))
                entry.update(action="created_empty", team_size=0, before=None, wrote={"team": "[]", "cap": None, "vc": None})
                detail["inserted"].append(name)
            elif kind == "rollover" and row["week_no"] >= to_week:
                entry.update(action="skipped_ahead", team_size=len(_jloads(row["tw_team_json"], [])), week=row["week_no"])
                detail["warnings"].append(f"{name} was already at week {row['week_no']}")
            else:
                team, cap, vc = _pick_active_team(row["nw_team_json"], row["nw_cap_id"], row["nw_vc_id"],
                                                  row["tw_team_json"], row["tw_cap_id"], row["tw_vc_id"], _jloads)
                ids = _jloads(team, [])
                promoted = bool(_jloads(row["nw_team_json"], []))
                bad = [i for i in ids if i not in known_ids]
                if bad:
                    detail["warnings"].append(f"{name}: {len(bad)} id(s) not in the roster: {', '.join(map(str, bad[:5]))}")
                if kind == "start":
                    entry.update(action="promoted" if promoted else ("kept" if ids else "empty"), team_size=len(ids),
                                 before={"tw_team_json": row["tw_team_json"], "tw_cap_id": row["tw_cap_id"], "tw_vc_id": row["tw_vc_id"], "nw_touched_at": row["nw_touched_at"]})
                    # the pre-season pick becomes the XI that plays week 1; the same draft now pre-fills week 2's, which nobody has confirmed yet
                    con.execute("UPDATE user_selections SET tw_team_json=%s, tw_cap_id=%s, tw_vc_id=%s, nw_touched_at=NULL "
                                "WHERE competition_id=%s AND display_name=%s AND week_no=1", (team, cap, vc, slug, name))
                else:
                    entry.update(action="promoted" if promoted else ("carried" if ids else "empty"), team_size=len(ids), before=None,
                                 wrote={"team": team, "cap": cap, "vc": vc})
                    con.execute("INSERT INTO user_selections (competition_id,display_name,week_no,tw_team_json,tw_cap_id,tw_vc_id,nw_team_json,nw_cap_id,nw_vc_id) "
                                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (competition_id,display_name,week_no) DO NOTHING",
                                (slug, name, to_week, team, cap, vc, team, cap, vc))
                    detail["inserted"].append(name)
            detail["members"].append(entry)
        if kind == "start" and comp["status"] == "upcoming":
            con.execute("UPDATE competitions SET status='active' WHERE slug=%s", (slug,))
            detail["activated"] = True
        before = {}
        for key in (f"_last_rollover:{slug}", f"_saved:{slug}"):
            r = con.execute("SELECT value FROM meta WHERE key=%s", (key,)).fetchone()
            before[key] = r["value"] if r else None
        detail["meta_before"] = before
        for key in (f"_last_rollover:{slug}", f"_saved:{slug}"):
            con.execute("INSERT INTO meta (key,value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, now_iso))
        detail["redo"] = bool(redo)
        cur = con.execute(
            "INSERT INTO rollover_log (competition_id, kind, to_week, lock_at_utc, applied_at, triggered_by, members, detail_json) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (competition_id, to_week) DO UPDATE SET lock_at_utc=EXCLUDED.lock_at_utc, applied_at=EXCLUDED.applied_at, "
            "triggered_by=EXCLUDED.triggered_by, members=EXCLUDED.members, detail_json=EXCLUDED.detail_json, undone_at=NULL "
            "WHERE rollover_log.undone_at IS NOT NULL",                 # only a week an admin undid may be written again; a live one never is
            (slug, kind, to_week, _plan.iso(t["lock_at"]), now_iso, triggered_by, len(names), json.dumps(detail)))
        if cur.rowcount != 1:
            raise RuntimeError(f"week {to_week} of '{slug}' is already in the ledger")
        summary = {}
        for e in detail["members"]:
            summary[e["action"]] = summary.get(e["action"], 0) + 1
        return {"kind": kind, "to_week": to_week, "lock_at": _plan.iso(t["lock_at"]), "applied_at": now_iso, "triggered_by": triggered_by,
                "members": len(names), "summary": summary, "activated": detail["activated"], "warnings": detail["warnings"],
                "season_complete": to_week >= comp["max_weeks"]}

    def undo_transition(self, slug, to_week, *, force=False) -> dict:
        """Take back the LAST transition of a competition: the rows it created are deleted (or week 1's XI is put back as it was), the status and the
        two meta keys are restored, and the ledger row is kept with `undone_at` set so the transition is NOT applied again by itself (it stays held
        until the row is forgotten on purpose). Refused if a later week has run, or (unless `force`) if points have already been worked out for the
        week. Raises LookupError / ValueError with a readable message."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._pool.connection() as con:
            if not con.execute("SELECT 1 FROM competitions WHERE slug=%s FOR UPDATE", (slug,)).fetchone():
                raise LookupError(f"competition '{slug}' not found")
            row = con.execute("SELECT id, kind, to_week, detail_json, undone_at FROM rollover_log WHERE competition_id=%s AND to_week=%s",
                              (slug, to_week)).fetchone()
            if not row:
                raise LookupError(f"week {to_week} of '{slug}' has not been applied")
            if row["undone_at"]:
                raise ValueError(f"week {to_week} of '{slug}' was already undone")
            later = [r["to_week"] for r in con.execute(
                "SELECT to_week FROM rollover_log WHERE competition_id=%s AND to_week>%s AND undone_at IS NULL", (slug, to_week)).fetchall()]
            if later:
                raise ValueError(f"week {later[0]} has run after it: undo that one first")
            detail = _jloads(row["detail_json"], {})
            wrote = {m["name"]: m.get("wrote") for m in detail.get("members", [])}

            def saved_since(name, week):
                """Did this member save a draft into the row the handover created, after it ran? (the row's next-week columns no longer hold what it put there)"""
                w = wrote.get(name)
                r = con.execute("SELECT nw_team_json, nw_cap_id, nw_vc_id FROM user_selections WHERE competition_id=%s AND display_name=%s AND week_no=%s",
                                (slug, name, week)).fetchone() if w else None
                return bool(r) and (r["nw_team_json"], r["nw_cap_id"], r["nw_vc_id"]) != (w["team"], w["cap"], w["vc"])

            def refuse_if_drafts_would_be_lost(names):
                lost = [n for n in names if saved_since(n, to_week if row["kind"] == "rollover" else 1)]
                if lost and not force:
                    raise ValueError(f"{', '.join(lost)} saved a new draft since this week was handed over, and undoing it would delete that draft; pass force to undo anyway")

            if row["kind"] == "rollover":
                if not force and con.execute("SELECT 1 FROM user_match_points WHERE competition_id=%s AND week_no=%s LIMIT 1", (slug, to_week)).fetchone():
                    raise ValueError(f"points have already been worked out for week {to_week}; pass force to undo anyway")
                refuse_if_drafts_would_be_lost(detail.get("inserted", []))
                for name in detail.get("inserted", []):
                    con.execute("DELETE FROM user_selections WHERE competition_id=%s AND display_name=%s AND week_no=%s", (slug, name, to_week))
                    con.execute("DELETE FROM user_match_points WHERE competition_id=%s AND display_name=%s AND week_no=%s", (slug, name, to_week))   # only a forced undo has any
            else:
                refuse_if_drafts_would_be_lost([m["name"] for m in detail.get("members", []) if m.get("action") == "created_empty"])
                for m in detail.get("members", []):
                    if m.get("action") == "created_empty":
                        con.execute("DELETE FROM user_selections WHERE competition_id=%s AND display_name=%s AND week_no=1", (slug, m["name"]))
                    elif m.get("before"):
                        b = m["before"]
                        con.execute("UPDATE user_selections SET tw_team_json=%s, tw_cap_id=%s, tw_vc_id=%s, nw_touched_at=%s WHERE competition_id=%s AND display_name=%s AND week_no=1",
                                    (b["tw_team_json"], b["tw_cap_id"], b["tw_vc_id"], b.get("nw_touched_at"), slug, m["name"]))
                if detail.get("activated"):
                    con.execute("UPDATE competitions SET status=%s WHERE slug=%s", (detail.get("before_status") or "upcoming", slug))
            for key, val in (detail.get("meta_before") or {}).items():
                if val is None:
                    con.execute("DELETE FROM meta WHERE key=%s", (key,))
                else:
                    con.execute("INSERT INTO meta (key,value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, val))
            con.execute("INSERT INTO meta (key,value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (f"_saved:{slug}", now_iso))
            con.execute("UPDATE rollover_log SET undone_at=%s WHERE id=%s", (now_iso, row["id"]))
        self.update_week_points(competition_id=slug)
        return {"competition": slug, "undone_week": to_week, "kind": row["kind"], "undone_at": now_iso}

    def touch_heartbeat(self, source, detail="") -> None:
        """Record that something that should be running (the app, a clock, the PC runner) has just run."""
        with self._write() as con:
            con.execute("INSERT INTO ops_heartbeat (source,last_at,detail) VALUES (%s,%s,%s) ON CONFLICT (source) DO UPDATE SET last_at=EXCLUDED.last_at, detail=EXCLUDED.detail",
                        (source, datetime.now(timezone.utc).isoformat(), str(detail or "")[:500]))

    def get_heartbeats(self) -> list:
        with self._read() as con:
            return [dict(r) for r in con.execute("SELECT source, last_at, detail FROM ops_heartbeat ORDER BY source").fetchall()]

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
                SELECT week_no,tw_team_json,tw_cap_id,tw_vc_id,nw_team_json,nw_cap_id,nw_vc_id,nw_touched_at,
                       week_pts,points_per_match
                FROM user_selections WHERE competition_id=%s AND display_name=%s ORDER BY week_no ASC
            """, (cid, name)).fetchall()
        weeks = [
            {"week_no": r["week_no"], "is_current": r["week_no"]==current_week,
             "this_week": {"team": _jloads(r["tw_team_json"],[]), "cap": r["tw_cap_id"], "vc": r["tw_vc_id"]},
             "next_week": {"team": _jloads(r["nw_team_json"],[]), "cap": r["nw_cap_id"], "vc": r["nw_vc_id"]},
             "next_week_touched_at": r["nw_touched_at"],     # when the member last saved or confirmed ("Keep this XI") this draft; None = not since the row was created
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
        """Full server-side validation of a proposed XI (BIZ-1).
        A full XI must name a captain AND a vice-captain (asked for 2026-11-19: the captain scores double, so a team
        without one is never saved). An empty team still clears the draft. These cheap rules are decided before the
        database is touched."""
        if not team:
            return True, None, 0.0
        if len(team) != xi_size:
            return False, f"Need exactly {xi_size} players (got {len(team)})", 0.0
        if len(set(team)) != len(team):
            return False, "Duplicate players are not allowed", 0.0
        if not cap and not vc:
            return False, "Choose a captain and a vice-captain before saving", 0.0
        if not cap:
            return False, "Choose a captain before saving", 0.0
        if not vc:
            return False, "Choose a vice-captain before saving", 0.0
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
        if cap not in team:
            return False, "Captain must be one of your selected players", total
        if vc not in team:
            return False, "Vice-captain must be one of your selected players", total
        if cap == vc:
            return False, "Captain and vice-captain must be different players", total
        return True, None, total

    def _draft_week(self, con, cid, current_week):
        """The week the draft being saved is FOR: the handover it will be promoted at (inside the caller's transaction). On a lock schedule that is the first
        week whose handover has not run; without one it is the week after the current one (the first week is picked before it starts). None when every
        scheduled week has been handed over (the final week: there is nothing left to pick for)."""
        weeks = [r["week_no"] for r in con.execute("SELECT week_no FROM competition_weeks WHERE competition_id=%s ORDER BY week_no", (cid,)).fetchall()]
        if not weeks:
            return 1 if current_week <= 1 else current_week + 1
        done = {r["to_week"] for r in con.execute("SELECT to_week FROM rollover_log WHERE competition_id=%s AND undone_at IS NULL", (cid,)).fetchall()}
        return next((w for w in weeks if w not in done), None)

    def save_next_week(self, name: str, team: list, cap, vc, competition_id=None, expect_week=None) -> dict:
        """Save the member's draft for the next week (into the row of the competition's CURRENT week) and mark it touched. `expect_week` is the week the page
        says its draft is FOR: when a handover has moved the draft on since the page loaded (including the week-1 start, which does not change the row's
        week), nothing is written and {"conflict": True, "week_no": <current week>, "draft_week": <the week a draft is for now>} comes back, so a stale page
        can never drop a draft into the wrong week. The check and the write happen under a shared lock on the competition row, which a handover holds
        exclusively: a save that races a handover waits for it and is then judged against the new week."""
        cid = self._cid(competition_id)
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._write() as con:
            con.execute("SELECT 1 FROM competitions WHERE slug=%s FOR SHARE", (cid,))
            row = con.execute(
                "SELECT COALESCE(MAX(week_no),1) AS wn FROM user_selections WHERE competition_id=%s",
                (cid,)).fetchone()
            current_week = int(row["wn"]) if row else 1
            if expect_week is not None:
                draft_week = self._draft_week(con, cid, current_week)
                if int(expect_week) != draft_week:
                    return {"conflict": True, "week_no": current_week, "draft_week": draft_week}
            con.execute("""
                INSERT INTO user_selections
                    (competition_id,display_name,week_no,tw_team_json,tw_cap_id,tw_vc_id,
                     nw_team_json,nw_cap_id,nw_vc_id,nw_touched_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (competition_id,display_name,week_no) DO UPDATE SET
                    nw_team_json=EXCLUDED.nw_team_json,
                    nw_cap_id=EXCLUDED.nw_cap_id,
                    nw_vc_id=EXCLUDED.nw_vc_id,
                    nw_touched_at=EXCLUDED.nw_touched_at
            """, (cid, name, current_week, "[]", None, None, json.dumps(team), cap, vc, now_iso))
            con.execute("INSERT INTO meta (key,value) VALUES (%s,%s) "
                        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (f"_saved:{cid}", now_iso))
        return {"week_no": current_week, "touched_at": now_iso}

    def keep_next_week(self, name: str, competition_id=None, expect_week=None) -> dict:
        """The member confirms the draft they already have ("Keep this XI"): nothing about the team changes, the draft is only marked touched (this is what
        lets a reminder know they have seen it). `expect_week` works as for save_next_week. Returns {"week_no", "touched_at"}; {"conflict": True, "week_no",
        "draft_week"} when the draft has moved on; {"nothing": True, "week_no"} when there is no team (draft or XI) to keep."""
        cid = self._cid(competition_id)
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._write() as con:
            con.execute("SELECT 1 FROM competitions WHERE slug=%s FOR SHARE", (cid,))
            current_week = int(con.execute("SELECT COALESCE(MAX(week_no),1) AS wn FROM user_selections WHERE competition_id=%s", (cid,)).fetchone()["wn"])
            if expect_week is not None:
                draft_week = self._draft_week(con, cid, current_week)
                if int(expect_week) != draft_week:
                    return {"conflict": True, "week_no": current_week, "draft_week": draft_week}
            row = con.execute("SELECT tw_team_json, nw_team_json FROM user_selections WHERE competition_id=%s AND display_name=%s AND week_no=%s FOR UPDATE",
                              (cid, name, current_week)).fetchone()
            if not row or not (_jloads(row["tw_team_json"], []) or _jloads(row["nw_team_json"], [])):
                return {"nothing": True, "week_no": current_week}
            con.execute("UPDATE user_selections SET nw_touched_at=%s WHERE competition_id=%s AND display_name=%s AND week_no=%s",
                        (now_iso, cid, name, current_week))
            con.execute("INSERT INTO meta (key,value) VALUES (%s,%s) "
                        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (f"_saved:{cid}", now_iso))
        return {"week_no": current_week, "touched_at": now_iso}

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
        """The member's sign-in facts: username, passcode_hash (NO_PIN when they sign in with Google only), must_change, is_admin, and `google_email`
        (the address of the Google account linked to them, None when none is). One statement."""
        with self._read() as con:
            r = con.execute(
                "SELECT m.username, m.passcode_hash, m.must_change, m.is_admin, i.email AS google_email "
                "FROM members m LEFT JOIN identities i ON i.member = m.username AND i.provider = 'google' "
                "WHERE m.username=%s", (username,)).fetchone()
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
        """Every member for the admin's list. `google_email` is the linked Google address or None (the route masks it; the full address never leaves the server)."""
        with self._read() as con:
            rows = con.execute(
                "SELECT m.username, m.must_change, m.is_admin, m.created_at, i.email AS google_email "
                "FROM members m LEFT JOIN identities i ON i.member = m.username AND i.provider = 'google' "
                "ORDER BY m.username").fetchall()
            return [dict(r) for r in rows]

    # ── Identities: Google sign-in (Phase 3, 2026-11-21) ──────────────────
    def get_identity(self, provider: str, subject: str):
        """{member, email, created_at, last_login_at} for a provider's account id (Google's `sub`), or None."""
        with self._read() as con:
            r = con.execute("SELECT member, email, created_at, last_login_at FROM identities WHERE provider=%s AND subject=%s",
                            (provider, subject)).fetchone()
            return dict(r) if r else None

    def touch_identity(self, provider: str, subject: str, email: str, now_iso: str = None) -> None:
        """A sign-in happened: remember when, and the address Google reports today (an address can change; the `sub` never does)."""
        now_iso = now_iso or datetime.now(timezone.utc).isoformat()
        with self._write() as con:
            con.execute("UPDATE identities SET email=%s, last_login_at=%s WHERE provider=%s AND subject=%s", (email, now_iso, provider, subject))

    def link_identity(self, username: str, provider: str, subject: str, email: str, now_iso: str = None) -> dict:
        """Link a provider account to an existing member AND retire their passcode, in ONE transaction (so a member is never left with neither way in).
        The member row is locked first, so two attempts for the same member are served one after the other.
        Returns {"member", "email", "linked_now"} (linked_now is False when this very account was already linked to them: a harmless repeat).
        Raises LookupError("member") when there is no such member, ValueError("in_use") when the account belongs to another member, and
        ValueError("already_linked") when the member already has a different account of this provider (an admin must unlink it first)."""
        now_iso = now_iso or datetime.now(timezone.utc).isoformat()
        with self._write() as con:
            if not con.execute("SELECT 1 FROM members WHERE username=%s FOR UPDATE", (username,)).fetchone():
                raise LookupError("member")
            made = con.execute("INSERT INTO identities (provider, subject, member, email, created_at, last_login_at) VALUES (%s,%s,%s,%s,%s,%s) "
                               "ON CONFLICT DO NOTHING RETURNING member", (provider, subject, username, email, now_iso, now_iso)).fetchone()
            if not made:
                row = con.execute("SELECT member FROM identities WHERE provider=%s AND subject=%s", (provider, subject)).fetchone()
                if row and row["member"] == username:
                    con.execute("UPDATE identities SET email=%s, last_login_at=%s WHERE provider=%s AND subject=%s", (email, now_iso, provider, subject))
                    return {"member": username, "email": email, "linked_now": False}
                raise ValueError("in_use" if row else "already_linked")
            con.execute("UPDATE members SET passcode_hash=%s, must_change=0 WHERE username=%s", (NO_PIN, username))
            return {"member": username, "email": email, "linked_now": True}

    def unlink_identity(self, username: str, temp_passcode_hash: str, provider: str = "google", keep_token: str = None) -> bool:
        """Admin recovery: remove the member's link, give them the temporary passcode (the hash passed in) that they must change at their next sign-in, and
        end all their sessions, except `keep_token` when given (an admin who unlinks THEMSELVES stays signed in on the device they are using, and picks a new
        passcode there and then). False when they had no link. One transaction."""
        with self._write() as con:
            con.execute("SELECT 1 FROM members WHERE username=%s FOR UPDATE", (username,))
            cur = con.execute("DELETE FROM identities WHERE provider=%s AND member=%s", (provider, username))
            if not cur.rowcount:
                return False
            con.execute("UPDATE members SET passcode_hash=%s, must_change=1 WHERE username=%s", (temp_passcode_hash, username))
            if keep_token:
                con.execute("DELETE FROM sessions WHERE username=%s AND token<>%s", (username, keep_token))
            else:
                con.execute("DELETE FROM sessions WHERE username=%s", (username,))
            return True

    def count_members(self) -> int:
        with self._read() as con:
            return int(con.execute("SELECT COUNT(*) AS n FROM members").fetchone()["n"])

    def create_member_with_identity(self, name: str, provider: str, subject: str, email: str, max_members: int, now_iso: str = None) -> dict:
        """Join: a NEW member whose only way in is this provider account (members.passcode_hash = NO_PIN), created TOGETHER with the identity in ONE transaction, so there is
        never a member nobody can sign in as, nor an account linked to nobody. Joins are serialised by an advisory lock, so the cap and the name check cannot be raced.
        Raises ValueError("name_taken") when a member with this name exists (compared case-insensitively: Sai and sai are the same name), ValueError("in_use") when the
        provider account already belongs to a member, ValueError("full") when the league already has `max_members`. Returns {"member", "email"}."""
        now_iso = now_iso or datetime.now(timezone.utc).isoformat()
        with self._write() as con:
            con.execute("SELECT pg_advisory_xact_lock(%s)", (7430011,))
            if con.execute("SELECT COUNT(*) AS n FROM members").fetchone()["n"] >= int(max_members):
                raise ValueError("full")
            if con.execute("SELECT 1 FROM members WHERE lower(username)=lower(%s)", (name,)).fetchone():
                raise ValueError("name_taken")
            if con.execute("SELECT 1 FROM identities WHERE provider=%s AND subject=%s", (provider, subject)).fetchone():
                raise ValueError("in_use")
            con.execute("INSERT INTO members (username, passcode_hash, must_change, is_admin, created_at) VALUES (%s,%s,0,0,%s)", (name, NO_PIN, now_iso))
            con.execute("INSERT INTO identities (provider, subject, member, email, created_at, last_login_at) VALUES (%s,%s,%s,%s,%s,%s)",
                        (provider, subject, name, email, now_iso, now_iso))
            return {"member": name, "email": email}

    def list_members_for_claim(self) -> list:
        """What the 'Which member are you?' step needs, for every member: {username, linked, has_passcode, must_change}. `linked`: a Google account is already linked
        to them (so their name can never be picked again); `has_passcode`: they still have a passcode of their own to prove who they are with; `must_change`: that
        passcode is only a temporary one (a known value: it must not be enough to take the name). Names and flags only: no hash, no address."""
        with self._read() as con:
            rows = con.execute(
                "SELECT m.username, (i.member IS NOT NULL) AS linked, (m.passcode_hash <> %s) AS has_passcode, (m.must_change <> 0) AS must_change "
                "FROM members m LEFT JOIN identities i ON i.member = m.username AND i.provider = 'google' "
                "ORDER BY lower(m.username), m.username", (NO_PIN,)).fetchall()
            return [dict(r) for r in rows]

    def list_pin_members(self) -> list:
        """The NAMES of the members who can still sign in with a passcode, sorted. The sign-in card offers a passcode chip to these only: a member who joined with Google or has
        switched has no passcode, and a chip for them is a dead end."""
        with self._read() as con:
            rows = con.execute("SELECT username FROM members WHERE passcode_hash <> %s ORDER BY lower(username), username", (NO_PIN,)).fetchall()
            return [r["username"] for r in rows]

    def count_pin_members(self) -> int:
        """How many members can still sign in with a passcode (0 once everybody uses Google: the page then drops the passcode screens)."""
        with self._read() as con:
            return int(con.execute("SELECT COUNT(*) AS n FROM members WHERE passcode_hash <> %s", (NO_PIN,)).fetchone()["n"])

    # ── Sessions ──────────────────────────────────────────────────────────
    def create_session(self, token: str, username: str, ttl_days: int = SESSION_TTL_DAYS) -> str:
        """A new sign-in. It lasts SESSION_TTL_DAYS (60) and is renewed by get_session while it is used (logic/session_policy)."""
        expires = new_expiry(datetime.now(timezone.utc), ttl_days)
        with self._write() as con:
            con.execute("INSERT INTO sessions (token, username, expires_at) VALUES (%s,%s,%s)",
                        (token, username, expires))
        return expires

    def get_session(self, token: str):
        """The session behind a bearer token, or None. Expired rows are deleted first. A session that is being used while fewer than
        SESSION_RENEW_BELOW_DAYS (30) remain is renewed to a full SESSION_TTL_DAYS (60), so a member who opens the app at least once
        in 60 days is never signed out and a session is written about once a month (logic/session_policy; Sai, 2026-11-19)."""
        if not token: return None
        now = datetime.now(timezone.utc)
        with self._write() as con:
            con.execute("DELETE FROM sessions WHERE expires_at < %s", (now.isoformat(),))
            r = con.execute("SELECT username, expires_at FROM sessions WHERE token=%s", (token,)).fetchone()
            if not r:
                return None
            sess = dict(r)
            newer = renewed_expiry(sess["expires_at"], now)
            if newer:
                # the "AND expires_at = old" guard makes two requests renewing at the same moment harmless (the second changes nothing)
                con.execute("UPDATE sessions SET expires_at=%s WHERE token=%s AND expires_at=%s",
                            (newer, token, sess["expires_at"]))
                sess["expires_at"] = newer
            return sess

    def delete_sessions_for_user(self, username: str) -> int:
        with self._write() as con:
            cur = con.execute("DELETE FROM sessions WHERE username=%s", (username,))
            return cur.rowcount or 0

    def delete_session(self, token: str) -> bool:
        """Sign THIS device out for real (the token stops working at once). False when there was no such session."""
        if not token:
            return False
        with self._write() as con:
            return bool(con.execute("DELETE FROM sessions WHERE token=%s", (token,)).rowcount)
