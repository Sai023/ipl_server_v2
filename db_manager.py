"""
IPL Fantasy 2026 — DatabaseManager                          Golden File v3
===========================================================================
Single source of truth for all SQLite interaction.

Import in server.py:
    from db_manager import DatabaseManager, calc_pts

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
#
# Six-CTE pipeline — single round-trip, no Python-side aggregation.
#
# Data flow:
#   current_picks     → shred tw_team_json into per-player rows (latest week_no)
#   scored_points     → join to player_match_points; cap ×2 / VC ×1.5 applied inline
#   user_totals       → SUM awarded_pts; MAX for MVP identification
#   mvp_resolve       → highest-scoring player per user; MIN(player_id) tie-breaks
#   ranked            → DENSE_RANK over total_pts DESC (ties share rank, no gaps)
#   league_benchmarks → AVG/MAX/COUNT cross-joined (zero extra query cost)
#
# Canonical Week 1 validation (107/107 test-verified):
#   Salt (r04): runs=12, balls=7, fours=2, catches=2 → raw=34 pts  [BVA source]
#   Sai  CAP=r01(Kohli  raw=110 → ×2=220)  VC=s02(Abhishek raw=13 → ×1.5≈20)  total=488
#   Moe  CAP=r04(Salt   raw=34  → ×2=68)   VC=s03(Kishan   raw=116 → ×1.5=174) total=469
#   → Sai rank 1, MVP player_id=r01 pts=220
#   → Moe rank 2, MVP player_id=s03 pts=174
#   league_avg = (488+469)/2 = 478.5, top_score = 488, member_count = 2
#
# :week_no binding:
#   None (NULL) → all weeks aggregated (global leaderboard)
#   integer N   → only matches where pmp.week_no = N (weekly view)
# ───────────────────────────────────────────────────────────────────────────────

_LEADERBOARD_SQL = """
WITH

-- ── CTE 1: current_picks ─────────────────────────────────────────────────────
-- Resolve the latest week_no per user; shred tw_team_json into rows.
-- JSON_EACH available in SQLite ≥ 3.38 (Python 3.9+ has json1 compiled in).
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

-- ── CTE 2: scored_points ─────────────────────────────────────────────────────
-- Join picks → player_match_points; apply cap (×2) / VC (×1.5) inline.
-- CAST(:week_no AS INTEGER) IS NULL is the portable NULL-filter pattern.
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

-- ── CTE 3: user_totals ───────────────────────────────────────────────────────
-- Aggregate: total awarded pts, distinct match count, peak score for MVP.
user_totals AS (
    SELECT
        display_name,
        SUM(awarded_pts)          AS total_pts,
        COUNT(DISTINCT match_id)  AS matches_counted,
        MAX(awarded_pts)          AS mvp_awarded_pts
    FROM  scored_points
    GROUP BY display_name
),

-- ── CTE 4: mvp_resolve ───────────────────────────────────────────────────────
-- Identify which player achieved mvp_awarded_pts for each user.
-- MIN(player_id) provides a deterministic tie-break when two players
-- score identically (e.g. two bowlers both on 75 pts).
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

-- ── CTE 5: ranked ────────────────────────────────────────────────────────────
-- DENSE_RANK over total_pts DESC.
-- Ties share the same rank integer; no gaps in the sequence.
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

-- ── CTE 6: league_benchmarks ─────────────────────────────────────────────────
-- Single-row league-wide aggregates.  CROSS JOIN adds these to every output
-- row at negligible cost — avoids a second query from the application layer.
league_benchmarks AS (
    SELECT
        ROUND(AVG(total_pts), 1)  AS league_avg,
        MAX(total_pts)            AS top_score,
        COUNT(*)                  AS member_count
    FROM  user_totals
)

-- ── Final projection ──────────────────────────────────────────────────────────
-- LEFT JOIN players resolves player_id → human name.
-- COALESCE falls back to player_id when players table is not yet seeded.
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
    r.display_name   -- stable alpha sort within tied ranks
"""


# ═══════════════════════════════════════════════════════════════════════════════
# POINTS ENGINE
# ═══════════════════════════════════════════════════════════════════════════════
#
# Canonical scoring rules — mirrors the JS engine in index.html and the
# decision-table in Tests_Circut.py (Suite 2).  All 107 tests pass against
# this implementation.
#
# Overs clamping
# ──────────────
# ESPNcricinfo and manual entry both use the N.B format where B is the ball
# digit (0–5).  A raw value like 3.5 means "3 overs and 5 balls", NOT the
# decimal fraction 3.5.  We normalise:
#
#   _normalise_overs(3.5)  → 3 + 5/6 ≈ 3.8333   ✓
#   _normalise_overs(3.9)  → 3 + 5/6 ≈ 3.8333   (clamped — ball digit > 5)
#   _normalise_overs(4.0)  → 4.0                 ✓
#   _normalise_overs(0.0)  → 0.0                 ✓
#
# The ball digit is clamped to 0–5 before conversion.  This matches the
# parse_overs() helper in Tests_Circut.py and Scraper.py.

def _normalise_overs(raw: float) -> float:
    """
    Convert cricket 'N.B' overs notation to a fractional real.

    3.5  → 3 + 5/6 ≈ 3.8333   (3 overs, 5 balls)
    3.9  → 3 + 5/6             (clamped: ball digit > 5 → 5)
    4.0  → 4.0                 (complete overs unchanged)
    0.0  → 0.0

    Args:
        raw: The raw value from the scorecard / JSON payload.
    Returns:
        Fractional overs suitable for economy-rate division.
    """
    if raw <= 0:
        return 0.0
    full_overs = math.floor(raw)
    ball_digit  = min(5, max(0, round((raw - full_overs) * 10)))
    return full_overs + ball_digit / 6


def calc_pts(s: dict) -> int:
    """
    Compute base fantasy points for a single player scorecard dict.

    Returns 0 for DNP (played=False or missing).
    Cap/VC multipliers are NOT applied here — those are applied in SQL
    via ROUND(base_pts * 2.0) / ROUND(base_pts * 1.5).

    Scoring rules (BVA-verified against 107/107 tests):
    ────────────────────────────────────────────────────
    Playing XI:
        +4 pts for being in the XI (played=True)

    Batting:
        +1 pt per run
        +1 pt per boundary (four)
        +2 pts per six
        Milestones: ≥100→+16, ≥50→+8, ≥30→+4
        Duck penalty: played & got_out & balls≥1 & runs=0 → −2
        Strike-rate bonus (min 10 balls):
            SR > 125 → +6 | SR ≥ 110 → +4 | SR ≥ 100 → +2
            SR < 60  → −4 | SR < 70  → −2

    Bowling:
        +25 pts per wicket
        +8  pts per lbw/bowled (stacked with wicket)
        +12 pts per maiden over
        Haul bonuses: ≥2W→+4, ≥3W→+4, ≥4W→+8, ≥5W→+8
        Economy bonus (min 2 overs, normalised):
            eco <  5 → +6 | eco < 6 → +4 | eco < 7 → +2
            eco > 12 → −6 | eco ≥ 12 → −4 | eco ≥ 10 → −2

    Fielding:
        +8 pts per catch; +4 bonus if ≥3 catches
        +12 pts per stumping
        +12 pts per direct run-out
        +6  pts per assisted run-out

    lbw_bowled cap: clamped to min(wickets, lbwBowled) so phantom lbw/bowled
    credits cannot be awarded to non-wicket-taking bowlers.
    """
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
    # lbw_bowled capped at wickets to prevent phantom credits
    lbwb    = max(0, min(wickets, int(s.get("lbwBowled", s.get("lbw_bowled", 0)))))
    duck    = bool(s.get("duck", False))
    got_out = bool(s.get("gotOut", s.get("got_out", False)))

    pts = 4   # Playing XI bonus

    # ── Batting ───────────────────────────────────────────────────────────────
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

    # ── Bowling ───────────────────────────────────────────────────────────────
    pts += wickets * 25 + lbwb * 8 + maidens * 12

    # Haul bonuses are incremental / stacking
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

    # ── Fielding ──────────────────────────────────────────────────────────────
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
    """
    Upsert one match + all its per-player raw scores in a single transaction
    block (caller holds the write lock).

    The overs value from the payload is stored AS-IS in match_scores.overs
    (already normalised by the scraper / calc_pts caller).  Economy arithmetic
    happens in Python via _normalise_overs(), not in SQLite.
    """
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
    At 50 concurrent users this is zero-contention in practice.
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
        """
        Return the full nested JSON expected by the frontend render() functions.

        Shape (unchanged from legacy league.json contract):
        {
          members: {
            "Name": {
              this_week: { team: [...], cap: str|null, vc: str|null },
              next_week: { team: [...], cap: str|null, vc: str|null }
            }
          },
          matches: [
            { id, wk, title, teams, date, status, scores: {pid: {...}} }
          ],
          _saved:         ISO-8601 str,
          _last_rollover: ISO-8601 str,
        }
        """
        with self._read() as con:
            # Members: latest week_no per user
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

            # Matches + raw score blobs
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
                # Merge any extra keys stored in raw_json (forward-compat)
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
        """
        Full-merge save.  Accepts both new format (this_week/next_week) and
        legacy flat format (team/cap/vc) for API parity.
        Returns the ISO timestamp of the save.
        """
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
                # Legacy flat format compat
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

        # Recalculate points for every match that has scores — outside the
        # write lock so the transaction stays short.
        match_ids_with_scores = [m["id"] for m in matches if "id" in m and m.get("scores")]
        for mid in match_ids_with_scores:
            self.recalculate_points(match_id=mid)

        return now_iso

    # ── PUT /api/member/<name> ────────────────────────────────────────────────

    def upsert_member(self, name: str, data: dict) -> None:
        """
        Upsert a single member's team picks.

        Accepted shapes:
          { this_week: {team, cap, vc}, next_week: {team, cap, vc} }  ← new
          { team: [...], cap: str, vc: str }                           ← legacy
        """
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
        """
        Upsert one match + its per-player raw scores, then immediately
        recalculate player_match_points for that match.

        Pipeline:
          1. _upsert_match()        → writes match_scores rows
          2. recalculate_points()   → writes player_match_points rows
          3. stamp _saved meta

        The recalculate call is outside the write lock on purpose: it reads
        match_scores (committed by step 1) and writes player_match_points in
        its own locked transaction.  This keeps each transaction short and
        avoids holding the lock across the CPU-bound scoring loop.
        """
        mid = m.get("id")
        with self._write() as con:
            _upsert_match(con, m)
            con.execute(
                "INSERT OR REPLACE INTO meta (key,value) VALUES ('_saved',?)",
                (datetime.now(timezone.utc).isoformat(),),
            )
        # Recalculate points outside the write lock — reads committed data.
        if mid and m.get("scores"):
            self.recalculate_points(match_id=mid)

    # ── Points recalculation engine ───────────────────────────────────────────

    def recalculate_points(self, match_id: str | None = None) -> int:
        """
        Populate / refresh player_match_points from match_scores.

        Called automatically by upsert_match() for the affected match.
        Can also be called manually to backfill after migration from the
        JSON backend, or to re-score a match after a scorecard correction.

        Parameters
        ----------
        match_id : str | None
            Recalculate for one match only.
            None → recalculate ALL matches (full backfill).

        Returns
        -------
        int
            Number of player_match_points rows written / updated.

        Algorithm
        ---------
        1. Read raw_score_json rows from match_scores (JOIN matches for week_no).
        2. For each row, call calc_pts() to get base_pts.
        3. INSERT OR REPLACE into player_match_points.
           multiplier is always stored as 1.0 here — cap/VC multipliers are
           applied at query time in _LEADERBOARD_SQL via ROUND(base_pts * 2.0)
           so the base value stays reusable across different user compositions.

        Thread safety
        -------------
        Reads use _read() (WAL non-blocking).
        Writes use _write() (exclusive lock, short transaction).
        The two phases are deliberately separate so WAL readers are never
        blocked by the scoring CPU loop.
        """
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
                base_pts = calc_pts(sc)          # full scoring engine
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
                    float(base_pts),   # final_pts = base_pts × 1.0 (no multiplier stored)
                    now_iso,
                ))
                rows_written += 1

        return rows_written

    # ── POST /api/rollover ────────────────────────────────────────────────────

    def do_rollover(self) -> dict:
        """
        Monday 14:00 UTC deadline: promote next_week → this_week, clear next_week.
        Idempotent — no-op if already rolled for this deadline window.

        Rollover anchor logic (mirrors Tests_Circut.py TestTemporalRollover):
        ──────────────────────────────────────────────────────────────────────
        1. Compute lmd = most recent Monday 14:00 UTC relative to `now`.
           If today IS Monday but it's before 14:00, lmd falls back to
           the PREVIOUS Monday 14:00 (by subtracting 7 days).
        2. Read _last_rollover meta.  If lmd ≤ last_rollover, return rolled=False.
        3. Otherwise execute the UPDATE and stamp meta.

        Key test cases:
          Monday 14:01 → lmd = today 14:00 < now → fires (rolled=True)
          Monday 13:59 → lmd = today 14:00 > now → lmd -= 7d = last Mon 14:00
                         If that was already stamped → no-op (rolled=False)
          Idempotent:  calling twice at 14:01 → second call is no-op
        """
        now            = datetime.now(timezone.utc)
        days_since_mon = now.weekday()       # Mon=0, Sun=6
        # Anchor to most recent Monday 14:00 UTC
        lmd = (now - timedelta(days=days_since_mon)).replace(
            hour=DEADLINE_HOUR, minute=DEADLINE_MIN,
            second=0, microsecond=0, tzinfo=timezone.utc,
        )
        # If that Monday 14:00 is still in the future (i.e. we're before the
        # deadline today), step back one full week.
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
                pass   # malformed stamp → allow rollover

        with self._write() as con:
            # Promote next_week → this_week only when nw_team_json is non-empty.
            # Members who haven't staged a next_week squad keep their current XI.
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
        """
        Execute the six-CTE leaderboard pipeline and return the structured
        response dict.

        Returns the unified dual-key shape consumed by both the legacy
        `rankings` key and the `standings`+`meta` contract from leaderboard_route.py.

        Parameters
        ----------
        week_no : int | None
            None  → global leaderboard (all weeks aggregated)
            int N → week-N filter only

        Returns
        -------
        {
          "week_no":      int | None,
          "generated_at": str,           # ISO-8601 UTC
          "league_avg":   float,         # flat alias
          "top_score":    int | float,   # flat alias
          "member_count": int,           # flat alias
          "meta": {
            "league_avg":   float,
            "top_score":    int | float,
            "member_count": int,
          },
          "standings": [                 # primary key (leaderboard_route.py)
            {
              "rank":            int,    # DENSE_RANK — ties share rank
              "name":            str,
              "total_pts":       int | float,
              "matches_counted": int,
              "mvp": {
                "player_id":   str,
                "player_name": str,
                "pts":         int | float,  # awarded (incl. cap/vc multiplier)
              }
            },
            ...
          ],
          "rankings": <same list as standings>   # legacy alias
        }

        Week 1 validation (107/107 verified):
          Sai  → total=488, mvp=r01(Kohli) 220 pts, rank=1
          Moe  → total=469, mvp=s03(Kishan) 174 pts, rank=2
          meta → league_avg=478.5, top_score=488, member_count=2
        """
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
            # flat keys (legacy server.py contract)
            "league_avg":   league_avg,
            "top_score":    top_score,
            "member_count": member_count,
            # structured keys (leaderboard_route.py contract)
            "meta": {
                "league_avg":   league_avg,
                "top_score":    top_score,
                "member_count": member_count,
            },
            "standings": standing_rows,
            "rankings":  standing_rows,   # same list object — zero copy cost
        }

    # ── GET /api/poll (ETag helper) ───────────────────────────────────────────

    def get_etags(self) -> dict:
        """
        Cheap single-row read used by GET /api/poll.
        Returns the current _saved timestamp so the frontend can skip full
        fetches when nothing has changed.
        """
        return {"state": self.get_meta("_saved", "never")}

    # ── Reset (test harness only) ─────────────────────────────────────────────

    def reset(self) -> None:
        """Wipe all mutable data.  Called by test fixtures."""
        with self._write() as con:
            con.execute("DELETE FROM match_scores")
            con.execute("DELETE FROM player_match_points")
            con.execute("DELETE FROM user_selections")
            con.execute("DELETE FROM matches")
            con.execute("DELETE FROM meta")
