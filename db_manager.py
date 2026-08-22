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

Leaderboard fix (post-v5.9) — fan-out elimination:
  _LEADERBOARD_SQL previously LEFT JOINed user_match_points inside
  user_totals to obtain matches_counted.  Because user_match_points has
  one row per match, each user_selections row (one per week) fanned out
  to N match-rows.  SUM(us.week_pts) then counted each week's total N
  times — e.g. W2 (900 pts, 9 matches) -> 8100, W1 (490 pts, 2 matches)
  -> 980, giving a bogus total of 9080 instead of 1390.

  Fix: two independent CTEs, no cross-join.
    user_totals  — SUM(week_pts) directly from user_selections (no join).
    match_counts — COUNT(DISTINCT match_id) from user_match_points (no join).
  Both are LEFT JOINed in the ranked CTE.  Total is now guaranteed to
  equal the sum of the weekly columns displayed in the UI.

Phase 8 (scouting badges):
  get_state() now includes a `player_pts` dict {id: season_pts} sourced
  directly from the players table.  This allows ipl_glue.js to render
  season_pts badges on Next Week player cards without a separate
  /api/players call.  season_pts is the raw base score (no cap/vc
  multiplier) — correct for scouting form guides.

Syntax fix: get_current_week() row["wn"] had a stray backslash (row[\"wn\"])
  introduced during the fan-out patch, causing SyntaxError on import.
"""

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import DB_VER  # noqa: F401
from logic.scoring_engine import calc_pts  # used by recalculate_points()


_SCHEMA = """
PRAGMA journal_mode  = WAL;
PRAGMA foreign_keys  = ON;

CREATE TABLE IF NOT EXISTS competitions (
    slug             TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    format           TEXT NOT NULL DEFAULT 'T20' CHECK (format IN ('T20','ODI')),
    status           TEXT NOT NULL DEFAULT 'upcoming'
                       CHECK (status IN ('upcoming','active','completed')),
    budget_total     REAL    NOT NULL DEFAULT 100.0,
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
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS players (
    competition_id TEXT    NOT NULL DEFAULT 'ipl_2026',
    id         TEXT    NOT NULL,
    name       TEXT    NOT NULL,
    team       TEXT    NOT NULL,
    price      REAL    NOT NULL DEFAULT 0 CHECK (price >= 0),
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
    status        TEXT NOT NULL DEFAULT 'upcoming'
                  CHECK (status IN ('upcoming','live','completed')),
    scorecard_url TEXT,
    raw_json      TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS user_selections (
    competition_id  TEXT    NOT NULL DEFAULT 'ipl_2026',
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
    competition_id TEXT    NOT NULL DEFAULT 'ipl_2026',
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

-- Passcode feature: per-member auth row. Hash is sha256("<username>:<passcode>").
-- must_change=1 forces the user through the Reset Passcode flow on next login
-- (set by admin reset). is_admin=1 unlocks the Admin tab + /api/admin/* endpoints.
CREATE TABLE IF NOT EXISTS members (
    username       TEXT    PRIMARY KEY CHECK (length(username) BETWEEN 1 AND 30),
    passcode_hash  TEXT    NOT NULL,
    must_change    INTEGER NOT NULL DEFAULT 0 CHECK (must_change IN (0,1)),
    is_admin       INTEGER NOT NULL DEFAULT 0 CHECK (is_admin    IN (0,1)),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Bearer-token sessions for /api/passcode/* and /api/admin/*. Other endpoints
-- still trust ?user=<n> (trust-based league). Tokens expire 30 days after issue.
CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    username    TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_us_name     ON user_selections (display_name);
CREATE INDEX IF NOT EXISTS idx_us_week     ON user_selections (week_no);
CREATE INDEX IF NOT EXISTS idx_ms_match    ON match_scores (match_id);
CREATE INDEX IF NOT EXISTS idx_pmp_player  ON player_match_points (player_id);
CREATE INDEX IF NOT EXISTS idx_pmp_week    ON player_match_points (week_no);
CREATE INDEX IF NOT EXISTS idx_pmp_match_p ON player_match_points (match_id, player_id);
CREATE INDEX IF NOT EXISTS idx_ump_name    ON user_match_points (display_name);
CREATE INDEX IF NOT EXISTS idx_ump_week    ON user_match_points (week_no);
CREATE INDEX IF NOT EXISTS idx_sess_user   ON sessions (username);
CREATE INDEX IF NOT EXISTS idx_matches_comp ON matches (competition_id);
CREATE INDEX IF NOT EXISTS idx_ms_comp      ON match_scores (competition_id);
CREATE INDEX IF NOT EXISTS idx_pmp_comp     ON player_match_points (competition_id);
CREATE INDEX IF NOT EXISTS idx_ump_comp     ON user_match_points (competition_id);
"""


# ── Leaderboard SQL ────────────────────────────────────────────────────────
#
# DESIGN: two independent CTEs, no cross-join between them.
#
# user_totals  — reads ONLY from user_selections.
#   SUM(week_pts) with no join.  One output row per user regardless of
#   how many matches or match-point rows exist.  Cannot fan-out.
#
# match_counts — reads ONLY from user_match_points.
#   COUNT(DISTINCT match_id) with no join to user_selections.
#   Cannot inflate total_pts.
#
# Both are LEFT JOINed in the ranked CTE so a user with no
# user_match_points rows still appears with matches_counted = 0.
#
# Invariant: total_pts == SUM of the weekly pts values shown in the UI,
# because both read exclusively from user_selections.week_pts.
#
# MVP (scored_points CTE) still uses player_match_points for per-player
# awarded points — this is correct and does not affect total_pts.
#
_LEADERBOARD_SQL = """
WITH
user_totals AS (
    -- Pure aggregate from user_selections — one row per user per week.
    -- No join to user_match_points; cannot fan-out.
    SELECT display_name,
           COALESCE(SUM(week_pts), 0) AS total_pts
    FROM   user_selections
    WHERE  competition_id = :comp
      AND  (CAST(:week_no AS INTEGER) IS NULL
            OR week_no = CAST(:week_no AS INTEGER))
    GROUP  BY display_name
),
match_counts AS (
    -- Per-user match count from user_match_points — separate CTE,
    -- no join to user_selections; cannot inflate total_pts.
    SELECT display_name,
           COUNT(DISTINCT CASE WHEN pts > 0 THEN match_id END) AS matches_counted
    FROM   user_match_points
    WHERE  competition_id = :comp
      AND  (CAST(:week_no AS INTEGER) IS NULL
            OR week_no = CAST(:week_no AS INTEGER))
    GROUP  BY display_name
),
scored_points AS (
    SELECT us.display_name, pmp.match_id, je.value AS player_id, pmp.base_pts,
           CASE WHEN je.value = us.tw_cap_id THEN ROUND(pmp.base_pts * 2.0)
                WHEN je.value = us.tw_vc_id  THEN ROUND(pmp.base_pts * 1.5)
                ELSE pmp.base_pts END AS awarded_pts
    FROM  user_selections us, JSON_EACH(us.tw_team_json) AS je
    INNER JOIN player_match_points pmp
           ON  pmp.player_id = je.value
           AND pmp.week_no   = us.week_no
           AND pmp.competition_id = us.competition_id
    WHERE  us.competition_id = :comp
      AND  (CAST(:week_no AS INTEGER) IS NULL
            OR us.week_no = CAST(:week_no AS INTEGER))
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
    SELECT ROUND(AVG(total_pts), 1) AS league_avg,
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
LEFT  JOIN players p ON p.id = r.mvp_player_id AND p.competition_id = :comp
ORDER BY r.rank, r.display_name
"""


def _jloads(s, default):
    if not s: return default
    try:    return json.loads(s)
    except: return default


def _upsert_match(con: sqlite3.Connection, m: dict, competition_id: str = "ipl_2026") -> None:
    mid = m.get("id")
    if not mid: return
    raw_copy = {k: v for k, v in m.items() if k != "scores"}
    teams  = m.get("teams", [])
    date   = m.get("date", m.get("date_label", ""))
    wk     = int(m.get("wk", m.get("week_no", 1)))
    title  = m.get("title", "")
    # Enrich generic Cricbuzz titles ("1st Match") with team names when possible
    # so the DB always stores "SRH vs RCB, 1st Match" style — consistent with schedule.json
    if teams and len(teams) >= 2 and title:
        if teams[0] not in title and teams[1] not in title:
            title = f"{teams[0]} vs {teams[1]}, {title}"
    status = m.get("status", "upcoming")
    con.execute("""
        INSERT INTO matches (competition_id,id,week_no,title,teams_json,date_label,status,raw_json)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            competition_id=excluded.competition_id,
            week_no=excluded.week_no, title=excluded.title, teams_json=excluded.teams_json,
            date_label=excluded.date_label, status=excluded.status, raw_json=excluded.raw_json
    """, (competition_id, mid, wk, title, json.dumps(teams), date, status, json.dumps(raw_copy)))
    for pid, sc in m.get("scores", {}).items():
        if not isinstance(sc, dict): continue
        con.execute("""
            INSERT INTO match_scores (
                competition_id,match_id,player_id,runs,balls,fours,sixes,got_out,duck,
                overs,runs_conceded,wickets,maidens,lbw_bowled,
                catches,stumpings,run_out_direct,run_out_assist,played,raw_score_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(match_id,player_id) DO UPDATE SET
                competition_id=excluded.competition_id,
                runs=excluded.runs, balls=excluded.balls, fours=excluded.fours,
                sixes=excluded.sixes, got_out=excluded.got_out, duck=excluded.duck,
                overs=excluded.overs, runs_conceded=excluded.runs_conceded,
                wickets=excluded.wickets, maidens=excluded.maidens,
                lbw_bowled=excluded.lbw_bowled, catches=excluded.catches,
                stumpings=excluded.stumpings, run_out_direct=excluded.run_out_direct,
                run_out_assist=excluded.run_out_assist, played=excluded.played,
                raw_score_json=excluded.raw_score_json
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
    def __init__(self, path):
        self._path  = str(path)
        self._local = threading.local()
        self._wlock = threading.Lock()
        self._generation = 0   # Phase 2: bumped by reload_from_disk()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con     = getattr(self._local, "con",     None)
        con_gen = getattr(self._local, "con_gen", -1)
        # Phase 2: if reload_from_disk() bumped the generation since this
        # thread last connected, drop the stale handle so we reopen against
        # the freshly-pulled fantasy.db.
        if con is not None and con_gen != self._generation:
            try:
                con.close()
            except Exception:
                pass
            con = None
            self._local.con = None
        if con is None:
            con = sqlite3.connect(self._path, timeout=30, check_same_thread=False,
                                  detect_types=sqlite3.PARSE_DECLTYPES)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode = WAL")
            con.execute("PRAGMA foreign_keys = ON")
            con.execute("PRAGMA busy_timeout  = 30000")
            self._local.con     = con
            self._local.con_gen = self._generation
        return con

    def reload_from_disk(self) -> None:
        """
        Phase 2 (HOSTED mode): Invalidate every thread's cached SQLite
        connection so the next access reopens against whatever fantasy.db
        is on disk. Called after `cloud_sync.pull_latest()` brings new
        commits down.

        Implementation: bump a generation counter. Each thread compares its
        connection's generation in `_connect()` and reopens lazily if it
        falls behind. No iteration over thread-local state needed; the
        next request on each thread does the work.

        Safe to call concurrently with in-flight reads — each thread owns
        its own connection, and the reopen is lazy at the next acquire.
        """
        self._generation += 1

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

    # ── Competition resolution ────────────────────────────────────────────────
    # The "active" competition is the default target when a caller does not
    # name one (back-compat for routes/scripts written before competition_id).
    # Normally exactly one competition has status='active'.
    DEFAULT_COMP = "ipl_2026"

    def _active_slug(self) -> str:
        try:
            with self._read() as con:
                r = con.execute(
                    "SELECT slug FROM competitions WHERE status='active' "
                    "ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
                if r:
                    return r["slug"]
                r = con.execute(
                    "SELECT slug FROM competitions ORDER BY created_at LIMIT 1"
                ).fetchone()
                if r:
                    return r["slug"]
        except Exception:
            pass
        return self.DEFAULT_COMP

    def _cid(self, competition_id):
        """Resolve an optional competition_id to the active competition."""
        return competition_id or self._active_slug()

    # ── Meta ──────────────────────────────────────────────────────────────────

    def get_meta(self, key, default=""):
        with self._read() as con:
            row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_meta(self, key, value):
        with self._write() as con:
            con.execute("INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)", (key, value))

    def get_meta_comp(self, base_key, competition_id, default=""):
        """Per-competition meta read with legacy global-key fallback.
        Writes are namespaced '<base_key>:<slug>'; older single-competition
        DBs stored the bare key, so fall back to it when the scoped key is
        absent (true until the first namespaced write for that competition)."""
        cid = self._cid(competition_id)
        v = self.get_meta(f"{base_key}:{cid}", None)
        if v is None:
            v = self.get_meta(base_key, default)
        return v

    def set_meta_comp(self, base_key, competition_id, value):
        self.set_meta(f"{base_key}:{self._cid(competition_id)}", value)

    def checkpoint(self) -> None:
        """
        Flush WAL frames into the main `fantasy.db` file. Required before
        any `git add data/fantasy.db` in HOSTED mode — without it, fresh
        writes can sit in `fantasy.db-wal` and git captures a pre-write
        snapshot, causing `_push_if_hosted` to return "nothing to commit"
        even when there's a real change to push.

        Real-world bug this fixes: passcode changes and save-next-week
        writes silently failed to push because the WAL hadn't been
        flushed by the time `git add` ran. Moe's change happened to
        coincide with an opportunistic flush and landed; Sai's didn't
        and was lost on the next Render redeploy.

        `TRUNCATE` mode resets the WAL file size to zero after the
        checkpoint, keeping the working directory tidy. Best-effort —
        a checkpoint failure is logged but doesn't raise (the worst
        case is a stale push, not data loss; the next write triggers
        another checkpoint attempt).
        """
        try:
            # Use the thread-local connection; PRAGMA outside a transaction
            # checkpoints all committed pages without acquiring a new lock.
            self._connect().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass  # best-effort, never raise

    # ── State ─────────────────────────────────────────────────────────────────

    def get_state(self, competition_id=None) -> dict:
        """
        Returns the full app state consumed by ipl_glue.js on load.

        Phase 8: includes `player_pts` — a compact {player_id: season_pts}
        dict built from the players table.  Allows Next Week tab to render
        scouting badges without an extra /api/players call.
        season_pts = raw base score (no cap/vc multiplier).
        """
        cid = self._cid(competition_id)
        with self._read() as con:
            rows = con.execute("""
                SELECT display_name, tw_team_json, tw_cap_id, tw_vc_id,
                       nw_team_json, nw_cap_id, nw_vc_id
                FROM user_selections
                WHERE competition_id = ?
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
            match_rows = con.execute(
                "SELECT id,week_no,title,teams_json,date_label,status,raw_json "
                "FROM matches WHERE competition_id=? ORDER BY week_no,id",
                (cid,),
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
                    "SELECT player_id,raw_score_json FROM match_scores "
                    "WHERE match_id=? AND competition_id=?",
                    (mr["id"], cid),
                ).fetchall()
                if score_rows:
                    entry["scores"] = {sr["player_id"]: _jloads(sr["raw_score_json"],{}) for sr in score_rows}
                matches.append(entry)

            # Phase 8: scouting lookup — season_pts survives restarts
            # (not cleared by _rebuild_scores_and_points).
            player_pts_rows = con.execute(
                "SELECT id, season_pts FROM players WHERE competition_id=?", (cid,)
            ).fetchall()
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
                              "WHERE competition_id=?", (cid,)).fetchone()
            current_week = row["wn"] if row else 1
            tw = data.get("this_week") or {}
            nw = data.get("next_week") or {}
            if "this_week" not in data:
                tw = {"team": data.get("team",[]), "cap": data.get("cap"), "vc": data.get("vc")}
                nw = dict(tw)
            con.execute("""
                INSERT INTO user_selections (competition_id,display_name,week_no,tw_team_json,tw_cap_id,tw_vc_id,nw_team_json,nw_cap_id,nw_vc_id)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(competition_id,display_name,week_no) DO UPDATE SET
                    tw_team_json=excluded.tw_team_json, tw_cap_id=excluded.tw_cap_id, tw_vc_id=excluded.tw_vc_id,
                    nw_team_json=excluded.nw_team_json, nw_cap_id=excluded.nw_cap_id, nw_vc_id=excluded.nw_vc_id
            """, (cid, name, current_week,
                  json.dumps(tw.get("team",[]) or []), tw.get("cap"), tw.get("vc"),
                  json.dumps(nw.get("team",[]) or []), nw.get("cap"), nw.get("vc")))
            con.execute("INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)",
                        (f"_saved:{cid}", datetime.now(timezone.utc).isoformat()))

    def upsert_match(self, m: dict, competition_id=None) -> None:
        cid = self._cid(competition_id)
        mid = m.get("id")
        with self._write() as con:
            _upsert_match(con, m, cid)
            con.execute("INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)",
                        (f"_saved:{cid}", datetime.now(timezone.utc).isoformat()))
        if mid and m.get("scores"):
            self.recalculate_points(match_id=mid, competition_id=cid)

    # ── Points Calculation ────────────────────────────────────────────────────

    def recalculate_points(self, match_id=None, competition_id=None) -> int:
        cid = self._cid(competition_id)
        with self._read() as con:
            if match_id:
                score_rows = con.execute(
                    "SELECT ms.match_id, ms.player_id, ms.raw_score_json, m.week_no "
                    "FROM match_scores ms JOIN matches m ON m.id=ms.match_id "
                    "WHERE ms.match_id=? AND ms.competition_id=?",
                    (match_id, cid),
                ).fetchall()
            else:
                score_rows = con.execute(
                    "SELECT ms.match_id, ms.player_id, ms.raw_score_json, m.week_no "
                    "FROM match_scores ms JOIN matches m ON m.id=ms.match_id "
                    "WHERE ms.competition_id=?",
                    (cid,),
                ).fetchall()
        if not score_rows: return 0
        now_iso = datetime.now(timezone.utc).isoformat()
        rows_written = 0
        with self._write() as con:
            for row in score_rows:
                sc       = _jloads(row["raw_score_json"], {})
                base_pts = calc_pts(sc)
                con.execute("""
                    INSERT INTO player_match_points (competition_id,match_id,player_id,week_no,base_pts,multiplier,final_pts,calculated_at)
                    VALUES (?,?,?,?,?,1.0,?,?)
                    ON CONFLICT(match_id,player_id) DO UPDATE SET
                        competition_id=excluded.competition_id,
                        week_no=excluded.week_no, base_pts=excluded.base_pts,
                        final_pts=excluded.final_pts, calculated_at=excluded.calculated_at
                """, (cid, row["match_id"], row["player_id"], row["week_no"],
                       base_pts, float(base_pts), now_iso))
                rows_written += 1
        return rows_written

    def update_player_season_pts(self, competition_id=None) -> int:
        """Set players.season_pts = SUM(base_pts) from player_match_points."""
        cid = self._cid(competition_id)
        with self._write() as con:
            con.execute("UPDATE players SET season_pts = 0 WHERE competition_id=?", (cid,))
            con.execute("""
                UPDATE players SET season_pts = (
                    SELECT COALESCE(SUM(pmp.base_pts), 0)
                    FROM player_match_points pmp
                    WHERE pmp.player_id = players.id
                      AND pmp.competition_id = players.competition_id
                ) WHERE competition_id=?
            """, (cid,))
        with self._read() as con:
            row = con.execute("SELECT COUNT(*) FROM players WHERE season_pts > 0 AND competition_id=?",
                              (cid,)).fetchone()
            return row[0] if row else 0

    def update_week_points(self, competition_id=None) -> int:
        """Recompute week_pts + points_per_match + user_match_points for all rows."""
        cid = self._cid(competition_id)
        with self._read() as con:
            sels = con.execute("""
                SELECT display_name, week_no, tw_team_json, tw_cap_id, tw_vc_id
                FROM user_selections WHERE competition_id=?
            """, (cid,)).fetchall()
            pmp_map = {}
            for r in con.execute(
                "SELECT player_id, match_id, base_pts FROM player_match_points WHERE competition_id=?",
                (cid,)
            ).fetchall():
                pmp_map[(r["player_id"], r["match_id"])] = r["base_pts"]
            week_matches: dict = {}
            for r in con.execute(
                "SELECT id, week_no FROM matches WHERE LOWER(status)='completed' AND competition_id=?",
                (cid,)
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
                    INSERT INTO user_match_points (competition_id, display_name, week_no, match_id, pts)
                    VALUES (?,?,?,?,?)
                    ON CONFLICT(display_name, match_id) DO UPDATE SET
                        competition_id=excluded.competition_id,
                        pts=excluded.pts, week_no=excluded.week_no
                """, (cid, name, wk, mid, pts))
            for (name, wk), pts in wk_totals.items():
                ppm_json = json.dumps(ppm_blobs.get((name, wk), {}))
                con.execute(
                    "UPDATE user_selections SET week_pts=?, points_per_match=? "
                    "WHERE competition_id=? AND display_name=? AND week_no=?",
                    (pts, ppm_json, cid, name, wk)
                )
                updated += 1
        return updated

    # ── Rollover DAO ──────────────────────────────────────────────────────────

    def get_users_and_max_weeks(self, competition_id=None) -> list:
        cid = self._cid(competition_id)
        with self._read() as con:
            rows = con.execute(
                "SELECT display_name, MAX(week_no) AS cur_wk "
                "FROM user_selections WHERE competition_id=? GROUP BY display_name",
                (cid,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_selection_row(self, display_name: str, week_no: int, competition_id=None) -> dict | None:
        cid = self._cid(competition_id)
        with self._read() as con:
            row = con.execute(
                "SELECT tw_team_json, tw_cap_id, tw_vc_id, "
                "nw_team_json, nw_cap_id, nw_vc_id "
                "FROM user_selections WHERE competition_id=? AND display_name=? AND week_no=?",
                (cid, display_name, week_no),
            ).fetchone()
        return dict(row) if row else None

    def insert_rollover_week(self, display_name: str, new_week_no: int,
                              team_json: str, cap_id, vc_id, competition_id=None) -> None:
        cid = self._cid(competition_id)
        with self._write() as con:
            con.execute("""
                INSERT OR IGNORE INTO user_selections
                    (competition_id, display_name, week_no,
                     tw_team_json, tw_cap_id, tw_vc_id,
                     nw_team_json, nw_cap_id, nw_vc_id)
                VALUES (?,?,?, ?,?,?, ?,?,?)
            """, (cid, display_name, new_week_no,
                  team_json, cap_id, vc_id,
                  team_json, cap_id, vc_id))

    def set_last_rollover(self, iso: str, competition_id=None) -> None:
        cid = self._cid(competition_id)
        with self._write() as con:
            con.execute(
                "INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)",
                (f"_last_rollover:{cid}", iso)
            )

    # ── Queries ───────────────────────────────────────────────────────────────

    def rebuild_scores_and_points(self, json_dir=None, competition_id=None) -> dict:
        cid = self._cid(competition_id)
        if json_dir is None:
            json_dir = Path(self._path).parent / cid / "matches"
        json_dir = Path(json_dir)
        with self._write() as con:
            con.execute("DELETE FROM player_match_points WHERE competition_id=?", (cid,))
            con.execute("DELETE FROM match_scores WHERE competition_id=?", (cid,))
            con.execute("DELETE FROM user_match_points WHERE competition_id=?", (cid,))
            con.execute("UPDATE user_selections SET week_pts = 0, points_per_match = '{}' WHERE competition_id=?", (cid,))
            con.execute("UPDATE players SET season_pts = 0, points = 0 WHERE competition_id=?", (cid,))
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
                        _upsert_match(con, match_data, cid); files_ingested += 1
                    except Exception as e:
                        print(f"  [rebuild] skip {fp.name}: {e}")
        pmp_rows      = self.recalculate_points(competition_id=cid)
        week_pts_rows = self.update_week_points(competition_id=cid)
        player_rows   = self.update_player_season_pts(competition_id=cid)
        return {"files_ingested": files_ingested, "pmp_rows": pmp_rows,
                "week_pts_rows": week_pts_rows, "player_pts_rows": player_rows}

    def hydrate_from_json(self, json_dir=None, competition_id=None) -> int:
        cid = self._cid(competition_id)
        if json_dir is None:
            json_dir = Path(self._path).parent / cid / "matches"
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
                    if "id" in match_data: _upsert_match(con, match_data, cid); count += 1
                except Exception as e:
                    print(f"  [hydrate] skip {fp.name}: {e}")
        if count:
            self.recalculate_points(competition_id=cid)
            print(f"  [hydrate] Ingested {count} matches from {json_dir}")
        return count

    def ping_stats(self, competition_id=None) -> dict:
        cid = self._cid(competition_id)
        with self._read() as con:
            member_count = con.execute(
                "SELECT COUNT(DISTINCT display_name) AS n FROM user_selections WHERE competition_id=?",
                (cid,)
            ).fetchone()["n"]
            scored_count = con.execute(
                "SELECT COUNT(DISTINCT match_id) AS n FROM match_scores WHERE played=1 AND competition_id=?",
                (cid,)
            ).fetchone()["n"]
        return {"members": member_count, "matches_scored": scored_count,
                "saved": self.get_meta_comp("_saved", cid, "never")}

    def get_leaderboard(self, week_no=None, competition_id=None) -> dict:
        cid = self._cid(competition_id)
        with self._read() as con:
            rows = con.execute(_LEADERBOARD_SQL, {"week_no": week_no, "comp": cid}).fetchall()
            if week_no is None:
                wk_rows = con.execute(
                    "SELECT display_name, week_no, week_pts FROM user_selections "
                    "WHERE competition_id=? ORDER BY week_no",
                    (cid,)
                ).fetchall()
            else:
                wk_rows = con.execute(
                    "SELECT display_name, week_no, week_pts FROM user_selections "
                    "WHERE competition_id=? AND week_no=?",
                    (cid, week_no)
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

    def get_etags(self, competition_id=None) -> dict:
        return {"state": self.get_meta_comp("_saved", competition_id, "never")}

    def get_current_week(self, competition_id=None) -> int:
        cid = self._cid(competition_id)
        with self._read() as con:
            row = con.execute(
                "SELECT COALESCE(MAX(week_no),1) AS wn FROM user_selections WHERE competition_id=?",
                (cid,)
            ).fetchone()
            return int(row["wn"]) if row else 1

    def get_players(self, competition_id=None) -> list:
        cid = self._cid(competition_id)
        with self._read() as con:
            rows = con.execute(
                "SELECT id,name,team,role,price,season_pts,points "
                "FROM players WHERE competition_id=? ORDER BY season_pts DESC, name",
                (cid,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_history(self, name: str, competition_id=None) -> dict:
        cid = self._cid(competition_id)
        with self._read() as con:
            current_week = self.get_current_week(cid)
            rows = con.execute("""
                SELECT week_no,tw_team_json,tw_cap_id,tw_vc_id,nw_team_json,nw_cap_id,nw_vc_id,
                       week_pts,points_per_match
                FROM user_selections WHERE competition_id=? AND display_name=? ORDER BY week_no ASC
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
            ph   = ",".join("?" * len(player_ids))
            rows = con.execute(f"SELECT id,price FROM players WHERE competition_id=? AND id IN ({ph})",
                               [cid, *player_ids]).fetchall()
        price_map = {r["id"]: r["price"] for r in rows}
        total = round(sum(price_map.get(pid, 0.0) for pid in player_ids), 1)
        return total <= budget, total

    def validate_selection(self, team: list, cap, vc, budget: float, xi_size: int,
                           competition_id=None) -> tuple:
        """Full server-side validation of a proposed XI (BIZ-1).

        Returns (ok: bool, error: str|None, total_cost: float). An empty team is
        allowed (a cleared draft). Enforces, in one DB round-trip:
          - exactly xi_size players,
          - no duplicate players,
          - every player is a real player IN this competition (rejects unknown /
            foreign IDs that would otherwise price as 0.0 and bypass the budget),
          - captain and vice-captain, when set, are in the XI and are distinct,
          - total price <= budget.
        """
        if not team:
            return True, None, 0.0
        if len(team) != xi_size:
            return False, f"Need exactly {xi_size} players (got {len(team)})", 0.0
        if len(set(team)) != len(team):
            return False, "Duplicate players are not allowed", 0.0
        cid = self._cid(competition_id)
        with self._read() as con:
            ph   = ",".join("?" * len(team))
            rows = con.execute(
                f"SELECT id, price FROM players WHERE competition_id=? AND id IN ({ph})",
                [cid, *team]
            ).fetchall()
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
                "SELECT COALESCE(MAX(week_no),1) AS wn FROM user_selections WHERE competition_id=?",
                (cid,)
            ).fetchone()
            current_week = int(row["wn"]) if row else 1
            con.execute("""
                INSERT INTO user_selections
                    (competition_id,display_name,week_no,tw_team_json,tw_cap_id,tw_vc_id,
                     nw_team_json,nw_cap_id,nw_vc_id)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(competition_id,display_name,week_no) DO UPDATE SET
                    nw_team_json=excluded.nw_team_json,
                    nw_cap_id=excluded.nw_cap_id,
                    nw_vc_id=excluded.nw_vc_id
            """, (cid, name, current_week, "[]", None, None, json.dumps(team), cap, vc))
            con.execute("INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)", (f"_saved:{cid}", now_iso))
        return {"week_no": current_week}

    # ── Competition DAO (multi-competition) ─────────────────────────────────

    def list_competitions(self) -> list:
        with self._read() as con:
            rows = con.execute(
                "SELECT slug,name,format,status,budget_total,xi_size,max_weeks,"
                "week1_anchor_utc,deadline_hour,deadline_min,series_id,series_slug,year,"
                "valid_teams_json,champion,closed_at,created_at "
                "FROM competitions ORDER BY created_at"
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["valid_teams"] = _jloads(d.pop("valid_teams_json"), [])
                out.append(d)
            return out

    def get_competition(self, slug: str):
        with self._read() as con:
            r = con.execute("SELECT * FROM competitions WHERE slug=?", (slug,)).fetchone()
            if not r:
                return None
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
        ph   = ",".join("?" * len(cols))
        with self._write() as con:
            con.execute(f"INSERT INTO competitions ({','.join(cols)}) VALUES ({ph})", vals)

    def set_competition_status(self, slug: str, status: str) -> None:
        with self._write() as con:
            con.execute("UPDATE competitions SET status=? WHERE slug=?", (status, slug))

    def set_champion(self, slug: str, champion: str) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._write() as con:
            con.execute(
                "UPDATE competitions SET champion=?, status='completed', closed_at=? WHERE slug=?",
                (champion, now_iso, slug)
            )

    def get_championship_tally(self) -> list:
        with self._read() as con:
            rows = con.execute(
                "SELECT champion, COUNT(*) AS titles FROM competitions "
                "WHERE status='completed' AND champion IS NOT NULL AND champion<>'' "
                "GROUP BY champion ORDER BY titles DESC, champion"
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Passcode / Auth DAO (Phase: Passcodes) ─────────────────────────────

    def get_member_auth(self, username: str) -> dict:
        with self._read() as con:
            r = con.execute(
                "SELECT username, passcode_hash, must_change, is_admin "
                "FROM members WHERE username=?", (username,)
            ).fetchone()
            return dict(r) if r else None

    def upsert_member_auth(self, username: str, passcode_hash: str,
                           must_change: int = 0, is_admin: int = 0) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._write() as con:
            con.execute("""
                INSERT INTO members (username, passcode_hash, must_change, is_admin, created_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(username) DO UPDATE SET
                    passcode_hash = excluded.passcode_hash,
                    must_change   = excluded.must_change
            """, (username, passcode_hash, int(must_change), int(is_admin), now_iso))

    def set_passcode(self, username: str, passcode_hash: str,
                     must_change: int = 0) -> None:
        with self._write() as con:
            con.execute(
                "UPDATE members SET passcode_hash=?, must_change=? WHERE username=?",
                (passcode_hash, int(must_change), username)
            )

    def list_members_admin_view(self) -> list:
        with self._read() as con:
            rows = con.execute(
                "SELECT username, must_change, is_admin, created_at "
                "FROM members ORDER BY username"
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Sessions ──────────────────────────────────────────────────────────

    def create_session(self, token: str, username: str, ttl_days: int = 30) -> str:
        expires = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()
        with self._write() as con:
            con.execute(
                "INSERT INTO sessions (token, username, expires_at) VALUES (?,?,?)",
                (token, username, expires)
            )
        return expires

    def get_session(self, token: str) -> dict:
        if not token:
            return None
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._write() as con:
            # Opportunistic cleanup of expired tokens — keeps the table small.
            con.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso,))
            r = con.execute(
                "SELECT username, expires_at FROM sessions WHERE token=?", (token,)
            ).fetchone()
            return dict(r) if r else None

    def delete_sessions_for_user(self, username: str) -> int:
        with self._write() as con:
            cur = con.execute("DELETE FROM sessions WHERE username=?", (username,))
            return cur.rowcount or 0

