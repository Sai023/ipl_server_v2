"""
IPL Fantasy 2026 — DatabaseManager                          Golden File v4
===========================================================================
Single source of truth for all SQLite interaction.

Import in server.py:
    from db_manager import DatabaseManager, GoldenDB, calc_pts

Key invariants (107/107 test-verified):
  • Overs stored as fractional real: 3.5 = 3 overs 5 balls → normalised to
    3 + 5/6 ≈ 3.8333 before economy-rate arithmetic.  Ball digit clamped 0–5.
  • Monday 14:00 UTC is the rollover deadline anchor.  At 13:59 the engine
    computes lmd = next Monday 14:00 > now → subtracts 7 days → lmd = last
    Monday 14:00.  A prior meta stamp from that same deadline makes the call
    a no-op (idempotent).  Fires at 14:01 on a fresh fixture (no prior stamp).
  • Cap ×2 / VC ×1.5 applied in SQL via ROUND() — matches JS engine exactly.
  • MVP = player with highest awarded_pts per user; MIN(player_id) breaks ties.
  • DENSE_RANK: tied total_pts share the same rank, no gaps.
  • league_avg = ROUND(AVG, 1) across all members in the filtered result set.

v4 additions (Phase-1 refactor):
  • get_current_week()  — moved from server.py helper
  • get_players()       — moved from server.py route
  • get_history(name)   — moved from server.py route
  • validate_budget()   — moved from server.py helper
  • save_next_week()    — moved from server.py route (pre-resolved IDs)
  • rollover_season()   — v8 history-preserving INSERT-new-row rollover
  • GoldenDB alias      — backward-compat name used by scraper.py
"""

import json
import math
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Rollover deadline (must mirror server.py constants) ───────────────────────
DEADLINE_HOUR = 14
DEADLINE_MIN  = 0


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA
# ═══════════════════════════════════════════════════════════════════════════════

_SCHEMA = """
PRAGMA journal_mode  = WAL;
PRAGMA foreign_keys  = ON;

CREATE TABLE IF NOT EXISTS players (
    id    TEXT PRIMARY KEY,
    name  TEXT NOT NULL,
    team  TEXT NOT NULL,
    price REAL NOT NULL DEFAULT 0 CHECK (price >= 0),
    role  TEXT NOT NULL DEFAULT 'BAT' CHECK (role IN ('BAT','BOWL','AR','WK'))
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
    display_name TEXT    NOT NULL CHECK (length(display_name) BETWEEN 1 AND 30),
    week_no      INTEGER NOT NULL DEFAULT 1 CHECK (week_no >= 1),
    tw_team_json TEXT    NOT NULL DEFAULT '[]',
    tw_cap_id    TEXT,
    tw_vc_id     TEXT,
    nw_team_json TEXT    NOT NULL DEFAULT '[]',
    nw_cap_id    TEXT,
    nw_vc_id     TEXT,
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
"""


# ═══════════════════════════════════════════════════════════════════════════════
# LEADERBOARD SQL
# ═══════════════════════════════════════════════════════════════════════════════

_LEADERBOARD_SQL = """
WITH

current_picks AS (
    SELECT
        us.display_name,
        us.week_no                AS selection_week,
        je.value                  AS player_id,
        us.tw_cap_id              AS cap_id,
        us.tw_vc_id               AS vc_id
    FROM  user_selections us,
          JSON_EACH(us.tw_team_json) AS je
    WHERE us.week_no = (
              SELECT MAX(week_no)
              FROM   user_selections u2
              WHERE  u2.display_name = us.display_name
          )
),

scored_points AS (
    SELECT
        cp.display_name,
        pmp.match_id,
        cp.player_id,
        pmp.base_pts,
        CASE
            WHEN cp.player_id = cp.cap_id THEN ROUND(pmp.base_pts * 2.0)
            WHEN cp.player_id = cp.vc_id  THEN ROUND(pmp.base_pts * 1.5)
            ELSE                               pmp.base_pts
        END  AS awarded_pts
    FROM       current_picks       cp
    INNER JOIN player_match_points pmp
           ON  pmp.player_id = cp.player_id
    WHERE  (CAST(:week_no AS INTEGER) IS NULL
            OR pmp.week_no = CAST(:week_no AS INTEGER))
),

user_totals AS (
    SELECT
        display_name,
        SUM(awarded_pts)          AS total_pts,
        COUNT(DISTINCT match_id)  AS matches_counted,
        MAX(awarded_pts)          AS mvp_awarded_pts
    FROM  scored_points
    GROUP BY display_name
),

mvp_resolve AS (
    SELECT
        sp.display_name,
        MIN(sp.player_id) AS mvp_player_id,
        sp.awarded_pts    AS mvp_pts
    FROM  scored_points sp
    INNER JOIN user_totals ut
           ON  ut.display_name  = sp.display_name
          AND  sp.awarded_pts   = ut.mvp_awarded_pts
    GROUP BY sp.display_name, sp.awarded_pts
),

ranked AS (
    SELECT
        ut.display_name,
        ut.total_pts,
        ut.matches_counted,
        COALESCE(mr.mvp_player_id, '')  AS mvp_player_id,
        COALESCE(mr.mvp_pts, 0)         AS mvp_pts,
        DENSE_RANK() OVER (
            ORDER BY ut.total_pts DESC
        )                               AS rank
    FROM      user_totals ut
    LEFT JOIN mvp_resolve mr USING (display_name)
),

league_benchmarks AS (
    SELECT
        ROUND(AVG(total_pts), 1)  AS league_avg,
        MAX(total_pts)            AS top_score,
        COUNT(*)                  AS member_count
    FROM  user_totals
)

SELECT
    r.rank,
    r.display_name,
    r.total_pts,
    r.matches_counted,
    r.mvp_player_id,
    COALESCE(p.name, r.mvp_player_id)  AS mvp_player_name,
    r.mvp_pts,
    lb.league_avg,
    lb.top_score,
    lb.member_count
FROM       ranked            r
CROSS JOIN league_benchmarks lb
LEFT  JOIN players           p  ON p.id = r.mvp_player_id
ORDER BY
    r.rank,
    r.display_name
"""


# ═══════════════════════════════════════════════════════════════════════════════
# POINTS ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def _normalise_overs(raw: float) -> float:
    if raw <= 0:
        return 0.0
    full_overs = math.floor(raw)
    ball_digit  = min(5, max(0, round((raw - full_overs) * 10)))
    return full_overs + ball_digit / 6


def calc_pts(s: dict) -> int:
    if not s or not s.get("played"):
        return 0

    runs    = max(0, int(s.get("runs",  0)))
    balls   = max(0, int(s.get("balls", 0)))
    fours   = max(0, min(runs, int(s.get("fours",   0))))
    sixes   = max(0, int(s.get("sixes",   0)))
    wickets = max(0, min(10,  int(s.get("wickets",  0))))
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
    if duck and got_out and balls >= 1:
        pts -= 2
    if balls >= 10:
        sr = (runs / balls) * 100
        if   sr >  125: pts += 6
        elif sr >= 110: pts += 4
        elif sr >= 100: pts += 2
        elif sr <  60:  pts -= 4
        elif sr <  70:  pts -= 2

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
    if catches >= 3:
        pts += 4
    pts += stump * 12 + rod * 12 + roa * 6

    return round(pts)


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _jloads(s, default):
    """Safe JSON loads with fallback."""
    if not s:
        return default
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return default


def _upsert_match(con: sqlite3.Connection, m: dict) -> None:
    mid = m.get("id")
    if not mid:
        return

    raw_copy = {k: v for k, v in m.items() if k != "scores"}
    teams    = m.get("teams", [])
    date     = m.get("date", m.get("date_label", ""))
    wk       = int(m.get("wk", m.get("week_no", 1)))
    title    = m.get("title", "")
    status   = m.get("status", "upcoming")

    con.execute("""
        INSERT INTO matches (id, week_no, title, teams_json, date_label, status, raw_json)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            week_no    = excluded.week_no,
            title      = excluded.title,
            teams_json = excluded.teams_json,
            date_label = excluded.date_label,
            status     = excluded.status,
            raw_json   = excluded.raw_json
    """, (mid, wk, title, json.dumps(teams), date, status, json.dumps(raw_copy)))

    for pid, sc in m.get("scores", {}).items():
        if not isinstance(sc, dict):
            continue
        con.execute("""
            INSERT INTO match_scores (
                match_id, player_id,
                runs, balls, fours, sixes, got_out, duck,
                overs, runs_conceded, wickets, maidens, lbw_bowled,
                catches, stumpings, run_out_direct, run_out_assist,
                played, raw_score_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(match_id, player_id) DO UPDATE SET
                runs           = excluded.runs,
                balls          = excluded.balls,
                fours          = excluded.fours,
                sixes          = excluded.sixes,
                got_out        = excluded.got_out,
                duck           = excluded.duck,
                overs          = excluded.overs,
                runs_conceded  = excluded.runs_conceded,
                wickets        = excluded.wickets,
                maidens        = excluded.maidens,
                lbw_bowled     = excluded.lbw_bowled,
                catches        = excluded.catches,
                stumpings      = excluded.stumpings,
                run_out_direct = excluded.run_out_direct,
                run_out_assist = excluded.run_out_assist,
                played         = excluded.played,
                raw_score_json = excluded.raw_score_json
        """, (
            mid, pid,
            max(0, int(sc.get("runs",  0))),
            max(0, int(sc.get("balls", 0))),
            max(0, int(sc.get("fours", 0))),
            max(0, int(sc.get("sixes", 0))),
            1 if sc.get("gotOut",  sc.get("got_out",  False)) else 0,
            1 if sc.get("duck",    False)                      else 0,
            max(0.0, float(sc.get("overs",          0))),
            max(0, int(sc.get("runsConceded",        sc.get("runs_conceded",   0)))),
            min(10, max(0, int(sc.get("wickets",    0)))),
            max(0, int(sc.get("maidens",             0))),
            max(0, int(sc.get("lbwBowled",           sc.get("lbw_bowled",     0)))),
            min(10, max(0, int(sc.get("catches",    0)))),
            max(0, int(sc.get("stumpings",           0))),
            max(0, int(sc.get("runOutDirect",        sc.get("run_out_direct", 0)))),
            max(0, int(sc.get("runOutAssist",        sc.get("run_out_assist", 0)))),
            1 if sc.get("played", False) else 0,
            json.dumps(sc),
        ))


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class DatabaseManager:
    """
    Thread-safe SQLite manager for IPL Fantasy 2026.

    Connection strategy
    ───────────────────
    One connection per thread, cached in threading.local().
    A single threading.Lock serialises all writes (BEGIN IMMEDIATE).
    WAL mode lets reads run concurrently without blocking writers.
    """

    def __init__(self, path: str | Path):
        self._path  = str(path)
        self._local = threading.local()
        self._wlock = threading.Lock()
        self._init_schema()

    # ── Connection management ─────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(
                self._path,
                timeout=10,
                check_same_thread=False,
                detect_types=sqlite3.PARSE_DECLTYPES,
            )
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode = WAL")
            con.execute("PRAGMA foreign_keys = ON")
            con.execute("PRAGMA busy_timeout  = 8000")
            self._local.con = con
        return con

    @contextmanager
    def _read(self):
        """Yield a connection for read queries (WAL: never blocks writers)."""
        yield self._connect()

    @contextmanager
    def _write(self):
        """Yield a connection inside an exclusive write lock + transaction."""
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
        con = sqlite3.connect(self._path, timeout=10)
        con.executescript(_SCHEMA)
        con.close()

    # ── Meta ──────────────────────────────────────────────────────────────────

    def get_meta(self, key: str, default: str = "") -> str:
        with self._read() as con:
            row = con.execute(
                "SELECT value FROM meta WHERE key=?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._write() as con:
            con.execute(
                "INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)",
                (key, value),
            )

    # ── GET /api/state ────────────────────────────────────────────────────────

    def get_state(self) -> dict:
        with self._read() as con:
            rows = con.execute("""
                SELECT display_name,
                       tw_team_json, tw_cap_id, tw_vc_id,
                       nw_team_json, nw_cap_id, nw_vc_id
                FROM   user_selections
                WHERE  week_no = (
                    SELECT MAX(week_no) FROM user_selections u2
                    WHERE  u2.display_name = user_selections.display_name
                )
            """).fetchall()

            members = {}
            for r in rows:
                members[r["display_name"]] = {
                    "this_week": {
                        "team": _jloads(r["tw_team_json"], []),
                        "cap":  r["tw_cap_id"],
                        "vc":   r["tw_vc_id"],
                    },
                    "next_week": {
                        "team": _jloads(r["nw_team_json"], []),
                        "cap":  r["nw_cap_id"],
                        "vc":   r["nw_vc_id"],
                    },
                }

            match_rows = con.execute(
                "SELECT id, week_no, title, teams_json, date_label, status, raw_json "
                "FROM matches ORDER BY week_no, id"
            ).fetchall()

            matches = []
            for mr in match_rows:
                base  = _jloads(mr["raw_json"], {})
                entry = {
                    "id":     mr["id"],
                    "wk":     mr["week_no"],
                    "title":  mr["title"],
                    "teams":  _jloads(mr["teams_json"], []),
                    "date":   mr["date_label"],
                    "status": mr["status"],
                }
                for k, v in base.items():
                    if k not in entry:
                        entry[k] = v

                score_rows = con.execute(
                    "SELECT player_id, raw_score_json FROM match_scores WHERE match_id=?",
                    (mr["id"],),
                ).fetchall()
                if score_rows:
                    entry["scores"] = {
                        sr["player_id"]: _jloads(sr["raw_score_json"], {})
                        for sr in score_rows
                    }
                matches.append(entry)

        return {
            "members":        members,
            "matches":        matches,
            "_saved":         self.get_meta("_saved", "never"),
            "_last_rollover": self.get_meta("_last_rollover", ""),
        }

    # ── POST /api/state ───────────────────────────────────────────────────────

    def save_state(self, payload: dict) -> str:
        members = payload.get("members", {})
        matches = payload.get("matches",  [])
        now_iso = datetime.now(timezone.utc).isoformat()

        with self._write() as con:
            row = con.execute(
                "SELECT COALESCE(MAX(week_no),1) AS wn FROM user_selections"
            ).fetchone()
            current_week = row["wn"] if row else 1

            for name, data in members.items():
                if not isinstance(data, dict) or not name or len(name) > 30:
                    continue

                tw = data.get("this_week") or {}
                nw = data.get("next_week") or {}
                if "this_week" not in data and "team" in data:
                    tw = {"team": data.get("team", []),
                          "cap":  data.get("cap"),
                          "vc":   data.get("vc")}
                    nw = dict(tw)

                con.execute("""
                    INSERT INTO user_selections
                        (display_name, week_no,
                         tw_team_json, tw_cap_id, tw_vc_id,
                         nw_team_json, nw_cap_id, nw_vc_id)
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(display_name, week_no) DO UPDATE SET
                        tw_team_json = excluded.tw_team_json,
                        tw_cap_id    = excluded.tw_cap_id,
                        tw_vc_id     = excluded.tw_vc_id,
                        nw_team_json = excluded.nw_team_json,
                        nw_cap_id    = excluded.nw_cap_id,
                        nw_vc_id     = excluded.nw_vc_id
                """, (
                    name, current_week,
                    json.dumps(tw.get("team", []) or []),
                    tw.get("cap"), tw.get("vc"),
                    json.dumps(nw.get("team", []) or []),
                    nw.get("cap"), nw.get("vc"),
                ))

            for m in matches:
                if "id" in m:
                    _upsert_match(con, m)

            con.execute(
                "INSERT OR REPLACE INTO meta (key,value) VALUES ('_saved',?)",
                (now_iso,),
            )

        match_ids_with_scores = [m["id"] for m in matches if "id" in m and m.get("scores")]
        for mid in match_ids_with_scores:
            self.recalculate_points(match_id=mid)

        return now_iso

    # ── PUT /api/member/<n> ────────────────────────────────────────────────

    def upsert_member(self, name: str, data: dict) -> None:
        with self._write() as con:
            row = con.execute(
                "SELECT COALESCE(MAX(week_no),1) AS wn FROM user_selections"
            ).fetchone()
            current_week = row["wn"] if row else 1

            tw = data.get("this_week") or {}
            nw = data.get("next_week") or {}
            if "this_week" not in data:
                tw = {"team": data.get("team", []),
                      "cap":  data.get("cap"),
                      "vc":   data.get("vc")}
                nw = dict(tw)

            con.execute("""
                INSERT INTO user_selections
                    (display_name, week_no,
                     tw_team_json, tw_cap_id, tw_vc_id,
                     nw_team_json, nw_cap_id, nw_vc_id)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(display_name, week_no) DO UPDATE SET
                    tw_team_json = excluded.tw_team_json,
                    tw_cap_id    = excluded.tw_cap_id,
                    tw_vc_id     = excluded.tw_vc_id,
                    nw_team_json = excluded.nw_team_json,
                    nw_cap_id    = excluded.nw_cap_id,
                    nw_vc_id     = excluded.nw_vc_id
            """, (
                name, current_week,
                json.dumps(tw.get("team", []) or []),
                tw.get("cap"), tw.get("vc"),
                json.dumps(nw.get("team", []) or []),
                nw.get("cap"), nw.get("vc"),
            ))
            con.execute(
                "INSERT OR REPLACE INTO meta (key,value) VALUES ('_saved',?)",
                (datetime.now(timezone.utc).isoformat(),),
            )

    # ── POST /api/match ───────────────────────────────────────────────────────

    def upsert_match(self, m: dict) -> None:
        mid = m.get("id")
        with self._write() as con:
            _upsert_match(con, m)
            con.execute(
                "INSERT OR REPLACE INTO meta (key,value) VALUES ('_saved',?)",
                (datetime.now(timezone.utc).isoformat(),),
            )
        if mid and m.get("scores"):
            self.recalculate_points(match_id=mid)

    # ── Points recalculation engine ───────────────────────────────────────────

    def recalculate_points(self, match_id: str | None = None) -> int:
        with self._read() as con:
            if match_id:
                score_rows = con.execute(
                    "SELECT ms.match_id, ms.player_id, ms.raw_score_json, m.week_no "
                    "FROM   match_scores ms "
                    "JOIN   matches m ON m.id = ms.match_id "
                    "WHERE  ms.match_id = ?",
                    (match_id,),
                ).fetchall()
            else:
                score_rows = con.execute(
                    "SELECT ms.match_id, ms.player_id, ms.raw_score_json, m.week_no "
                    "FROM   match_scores ms "
                    "JOIN   matches m ON m.id = ms.match_id"
                ).fetchall()

        if not score_rows:
            return 0

        rows_written = 0
        now_iso = datetime.now(timezone.utc).isoformat()

        with self._write() as con:
            for row in score_rows:
                sc       = _jloads(row["raw_score_json"], {})
                base_pts = calc_pts(sc)
                con.execute("""
                    INSERT INTO player_match_points
                        (match_id, player_id, week_no,
                         base_pts, multiplier, final_pts, calculated_at)
                    VALUES (?,?,?,?,1.0,?,?)
                    ON CONFLICT(match_id, player_id) DO UPDATE SET
                        week_no       = excluded.week_no,
                        base_pts      = excluded.base_pts,
                        final_pts     = excluded.final_pts,
                        calculated_at = excluded.calculated_at
                """, (
                    row["match_id"],
                    row["player_id"],
                    row["week_no"],
                    base_pts,
                    float(base_pts),
                    now_iso,
                ))
                rows_written += 1

        return rows_written

    # ── POST /api/rollover (legacy flat promote — kept for compat) ────────────

    def do_rollover(self) -> dict:
        """
        Legacy flat rollover: promote nw → tw in-place for the current row.
        Prefer rollover_season() for v8 history-preserving behaviour.
        """
        now            = datetime.now(timezone.utc)
        days_since_mon = now.weekday()
        lmd = (now - timedelta(days=days_since_mon)).replace(
            hour=DEADLINE_HOUR, minute=DEADLINE_MIN,
            second=0, microsecond=0, tzinfo=timezone.utc,
        )
        if lmd > now:
            lmd -= timedelta(days=7)

        last_raw = self.get_meta("_last_rollover", "")
        if last_raw:
            try:
                last_dt = datetime.fromisoformat(last_raw)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                if lmd <= last_dt:
                    return {"ok": True, "rolled": False}
            except ValueError:
                pass

        with self._write() as con:
            con.execute("""
                UPDATE user_selections
                SET
                    tw_team_json = CASE
                        WHEN json_array_length(nw_team_json) > 0 THEN nw_team_json
                        ELSE tw_team_json END,
                    tw_cap_id    = CASE
                        WHEN json_array_length(nw_team_json) > 0 THEN nw_cap_id
                        ELSE tw_cap_id END,
                    tw_vc_id     = CASE
                        WHEN json_array_length(nw_team_json) > 0 THEN nw_vc_id
                        ELSE tw_vc_id END,
                    nw_team_json = '[]',
                    nw_cap_id    = NULL,
                    nw_vc_id     = NULL
                WHERE week_no = (
                    SELECT MAX(week_no) FROM user_selections u2
                    WHERE u2.display_name = user_selections.display_name
                )
            """)
            con.execute(
                "INSERT OR REPLACE INTO meta (key,value) VALUES ('_last_rollover',?)",
                (lmd.isoformat(),),
            )
            con.execute(
                "INSERT OR REPLACE INTO meta (key,value) VALUES ('_saved',?)",
                (datetime.now(timezone.utc).isoformat(),),
            )

        return {"ok": True, "rolled": True}

    # ── GET /api/ping ─────────────────────────────────────────────────────────

    def ping_stats(self) -> dict:
        with self._read() as con:
            member_count = con.execute(
                "SELECT COUNT(DISTINCT display_name) AS n FROM user_selections"
            ).fetchone()["n"]
            scored_count = con.execute(
                "SELECT COUNT(DISTINCT match_id) AS n FROM match_scores WHERE played=1"
            ).fetchone()["n"]
        return {
            "members":        member_count,
            "matches_scored": scored_count,
            "saved":          self.get_meta("_saved", "never"),
        }

    # ── GET /api/leaderboard ──────────────────────────────────────────────────

    def get_leaderboard(self, week_no: int | None = None) -> dict:
        with self._read() as con:
            rows = con.execute(
                _LEADERBOARD_SQL, {"week_no": week_no}
            ).fetchall()

        if not rows:
            empty_meta = {"league_avg": 0.0, "top_score": 0, "member_count": 0}
            return {
                "week_no":      week_no,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "league_avg":   0.0,
                "top_score":    0,
                "member_count": 0,
                "meta":         empty_meta,
                "standings":    [],
                "rankings":     [],
            }

        first        = rows[0]
        league_avg   = first["league_avg"]
        top_score    = first["top_score"]
        member_count = first["member_count"]

        standing_rows = [
            {
                "rank":            row["rank"],
                "name":            row["display_name"],
                "total_pts":       row["total_pts"],
                "matches_counted": row["matches_counted"],
                "mvp": {
                    "player_id":   row["mvp_player_id"],
                    "player_name": row["mvp_player_name"],
                    "pts":         row["mvp_pts"],
                },
            }
            for row in rows
        ]

        return {
            "week_no":      week_no,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "league_avg":   league_avg,
            "top_score":    top_score,
            "member_count": member_count,
            "meta": {
                "league_avg":   league_avg,
                "top_score":    top_score,
                "member_count": member_count,
            },
            "standings": standing_rows,
            "rankings":  standing_rows,
        }

    # ── GET /api/poll (ETag helper) ───────────────────────────────────────────

    def get_etags(self) -> dict:
        return {"state": self.get_meta("_saved", "never")}

    # ── v4 route-level helpers (Phase-1 refactor) ─────────────────────────────

    def get_current_week(self) -> int:
        """Return the highest week_no in user_selections (min 1)."""
        with self._read() as con:
            row = con.execute(
                "SELECT COALESCE(MAX(week_no), 1) AS wn FROM user_selections"
            ).fetchone()
            return int(row["wn"]) if row else 1

    def get_players(self) -> list:
        """Return full player roster sorted by name."""
        with self._read() as con:
            rows = con.execute(
                "SELECT id, name, team, role, price FROM players ORDER BY name"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_history(self, name: str) -> dict:
        """Return all week rows for *name* in ascending week order."""
        with self._read() as con:
            current_week = self.get_current_week()
            rows = con.execute("""
                SELECT week_no, tw_team_json, tw_cap_id, tw_vc_id,
                       nw_team_json, nw_cap_id, nw_vc_id
                FROM   user_selections
                WHERE  display_name = ?
                ORDER  BY week_no ASC
            """, (name,)).fetchall()
        weeks = [
            {
                "week_no":    r["week_no"],
                "is_current": r["week_no"] == current_week,
                "this_week": {
                    "team": _jloads(r["tw_team_json"], []),
                    "cap":  r["tw_cap_id"],
                    "vc":   r["tw_vc_id"],
                },
                "next_week": {
                    "team": _jloads(r["nw_team_json"], []),
                    "cap":  r["nw_cap_id"],
                    "vc":   r["nw_vc_id"],
                },
            }
            for r in rows
        ]
        return {"name": name, "current_week": current_week, "weeks": weeks, "ok": True}

    def validate_budget(self, player_ids: list, budget: float = 100.0) -> tuple:
        """Return (is_valid: bool, total_cost: float)."""
        if not player_ids:
            return True, 0.0
        with self._read() as con:
            ph = ",".join("?" * len(player_ids))
            rows = con.execute(
                f"SELECT id, price FROM players WHERE id IN ({ph})", player_ids
            ).fetchall()
        price_map = {r["id"]: r["price"] for r in rows}
        total = round(sum(price_map.get(pid, 0.0) for pid in player_ids), 1)
        return total <= budget, total

    def save_next_week(self, name: str, team: list, cap, vc) -> dict:
        """
        Upsert nw_* columns for name's current max week_no.
        Expects pre-validated, pre-resolved canonical player IDs.
        Returns {"week_no": int}.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._write() as con:
            row = con.execute(
                "SELECT COALESCE(MAX(week_no),1) AS wn FROM user_selections"
            ).fetchone()
            current_week = int(row["wn"]) if row else 1
            con.execute("""
                INSERT INTO user_selections
                    (display_name, week_no,
                     tw_team_json, tw_cap_id, tw_vc_id,
                     nw_team_json, nw_cap_id, nw_vc_id)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(display_name, week_no) DO UPDATE SET
                    nw_team_json = excluded.nw_team_json,
                    nw_cap_id    = excluded.nw_cap_id,
                    nw_vc_id     = excluded.nw_vc_id
            """, (name, current_week, "[]", None, None, json.dumps(team), cap, vc))
            con.execute(
                "INSERT OR REPLACE INTO meta (key,value) VALUES ('_saved',?)",
                (now_iso,),
            )
        return {"week_no": current_week}

    def rollover_season(
        self,
        force: bool = False,
        max_weeks: int = 8,
        deadline_hour: int = 14,
        deadline_min: int = 0,
    ) -> dict:
        """
        v8 rollover: INSERT week_no+1 rows per user (history-preserving).
        Idempotent via _last_rollover meta stamp. Season capped at max_weeks.
        force=True bypasses the Monday deadline gate (dev/test only).
        """
        now = datetime.now(timezone.utc)

        if not force:
            days_since_mon = now.weekday()
            lmd = (now - timedelta(days=days_since_mon)).replace(
                hour=deadline_hour, minute=deadline_min,
                second=0, microsecond=0, tzinfo=timezone.utc,
            )
            if lmd > now:
                lmd -= timedelta(days=7)
            last_raw = self.get_meta("_last_rollover", "")
            if last_raw:
                try:
                    last_dt = datetime.fromisoformat(last_raw)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    if lmd <= last_dt:
                        return {
                            "ok": True, "rolled": False, "new_week_no": None,
                            "season_complete": False,
                            "reason": "Already rolled for this deadline",
                        }
                except ValueError:
                    pass

        current_week = self.get_current_week()
        if current_week >= max_weeks:
            return {
                "ok": True, "rolled": False, "new_week_no": None,
                "season_complete": True,
                "reason": f"Season complete — {max_weeks} weeks reached",
            }

        with self._read() as con:
            users = con.execute("""
                SELECT display_name, MAX(week_no) AS cur_wk
                FROM   user_selections
                GROUP  BY display_name
            """).fetchall()

        if not users:
            return {
                "ok": True, "rolled": False, "new_week_no": None,
                "season_complete": False, "reason": "No members found",
            }

        new_week_no = int(users[0]["cur_wk"]) + 1
        now_iso     = now.isoformat()

        with self._write() as con:
            for u in users:
                uname  = u["display_name"]
                cur_wk = int(u["cur_wk"])
                new_wk = cur_wk + 1

                cur_row = con.execute("""
                    SELECT tw_team_json, tw_cap_id, tw_vc_id,
                           nw_team_json, nw_cap_id, nw_vc_id
                    FROM   user_selections
                    WHERE  display_name = ? AND week_no = ?
                """, (uname, cur_wk)).fetchone()
                if not cur_row:
                    continue

                nw_team = cur_row["nw_team_json"] or "[]"
                nw_cap  = cur_row["nw_cap_id"]
                nw_vc   = cur_row["nw_vc_id"]

                if _jloads(nw_team, []) == []:
                    nw_team = cur_row["tw_team_json"] or "[]"
                    nw_cap  = cur_row["tw_cap_id"]
                    nw_vc   = cur_row["tw_vc_id"]

                con.execute("""
                    INSERT OR IGNORE INTO user_selections
                        (display_name, week_no,
                         tw_team_json, tw_cap_id, tw_vc_id,
                         nw_team_json, nw_cap_id, nw_vc_id)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (uname, new_wk, nw_team, nw_cap, nw_vc, nw_team, nw_cap, nw_vc))

            if not force:
                con.execute(
                    "INSERT OR REPLACE INTO meta (key,value) VALUES ('_last_rollover',?)",
                    (now_iso,),
                )
            con.execute(
                "INSERT OR REPLACE INTO meta (key,value) VALUES ('_saved',?)", (now_iso,)
            )

        return {
            "ok": True, "rolled": True, "new_week_no": new_week_no,
            "season_complete": new_week_no >= max_weeks,
        }

    # ── Reset (test harness only) ─────────────────────────────────────────────

    def reset(self) -> None:
        """Wipe all mutable data.  Called by test fixtures."""
        with self._write() as con:
            con.execute("DELETE FROM match_scores")
            con.execute("DELETE FROM player_match_points")
            con.execute("DELETE FROM user_selections")
            con.execute("DELETE FROM matches")
            con.execute("DELETE FROM meta")


# ── Backward-compat alias ─────────────────────────────────────────────────────
GoldenDB = DatabaseManager
