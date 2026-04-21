"""
IPL Fantasy 2026 — Database Initialiser                     init_db v1.0.0
===========================================================================
Phase 1 — System Architect.  Strict relocation; zero logic changes.

What lives here
---------------
_SCHEMA         — relocated from db_manager.py (authoritative copy).
                  db_manager.py retains its own copy for DatabaseManager._init_schema();
                  full schema consolidation is deferred to a later migration phase.
_SEED_VERSION   — version guard for history seeding.
_HISTORY_SEED   — weekly team/cap/vc tuples, relocated from server.py.
_auto_seed_*    — the three startup seed functions, relocated verbatim from server.py.
run_all_sync()  — single public entry point called by server.py on startup.

Dependency graph (Phase 1)
--------------------------
  server.py  ──import──>  init_db.py  ──import──>  DatabaseManager (db_manager.py)
"""

import json as _json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from db_manager import DatabaseManager  # noqa: F401 — used by run_all_sync caller

# ── Version ───────────────────────────────────────────────────────────────────

INIT_DB_VER = "1.0.0"

VERSION_MAP = {
    "1.0.0": "Phase 1 — Relocated _SCHEMA + _auto_seed_* from server.py / db_manager.py",
}

# ── Paths (same derivation as server.py — both files sit in BASE_DIR) ─────────

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH  = DATA_DIR / "fantasy.db"
DATA_DIR.mkdir(exist_ok=True)

# ── Schema — relocated from db_manager.py (authoritative copy) ────────────────
# Note: db_manager.py retains its own copy for DatabaseManager._init_schema().
# Full schema consolidation (removing the db_manager.py copy) is a later phase.

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

# ── History seed data — relocated verbatim from server.py ─────────────────────
# RULE: Every week MUST have its own explicit variable — never alias W3=W2.
# To add W5: define _SAI_W5_TEAM/_MOE_W5_TEAM, add to _HISTORY_SEED, bump _SEED_VERSION.

_SEED_VERSION = "2026.v8.w3w4-defined"

_SAI_W1_TEAM = ["k04","k19","s04","s05","s07","r01","r03","r11","m04","m07","m12"]
_SAI_W1_CAP  = "k04"
_SAI_W1_VC   = "s05"

_SAI_W2_TEAM = ["d22","p10","c12","c02","g03","rr14","rr11","l11","c09","p03","s04"]
_SAI_W2_CAP  = "c09"
_SAI_W2_VC   = "rr11"

_SAI_W3_TEAM = ["d22","p10","c12","c02","g03","rr14","rr11","l11","c09","p03","s04"]
_SAI_W3_CAP  = "c09"
_SAI_W3_VC   = "rr11"

_SAI_W4_TEAM = ["d22","p10","c12","c02","g03","rr14","rr11","l11","c09","p03","s04"]
_SAI_W4_CAP  = "c09"
_SAI_W4_VC   = "rr11"

# W5+ — add here when teams are known:
# _SAI_W5_TEAM = [...]
# _SAI_W5_CAP  = "..."
# _SAI_W5_VC   = "..."

_MOE_W1_TEAM = ["k04","m04","m07","m17","r02","r03","r12","s01","s04","k07","r16"]
_MOE_W1_CAP  = "r03"
_MOE_W1_VC   = "s04"

_MOE_W2_TEAM = ["m03","r05","k09","r16","p07","c11","rr04","s05","m11","s04","l01"]
_MOE_W2_CAP  = "l01"
_MOE_W2_VC   = "s04"

_MOE_W3_TEAM = ["m03","r05","k09","r16","p07","c11","rr04","s05","m11","s04","l01"]
_MOE_W3_CAP  = "l01"
_MOE_W3_VC   = "s04"

_MOE_W4_TEAM = ["m03","r05","k09","r16","p07","c11","rr04","s05","m11","s04","l01"]
_MOE_W4_CAP  = "l01"
_MOE_W4_VC   = "s04"

# W5+ — add here when teams are known:
# _MOE_W5_TEAM = [...]
# _MOE_W5_CAP  = "..."
# _MOE_W5_VC   = "..."

_HISTORY_SEED = [
    ("Sai", 1, _SAI_W1_TEAM, _SAI_W1_CAP, _SAI_W1_VC),
    ("Moe", 1, _MOE_W1_TEAM, _MOE_W1_CAP, _MOE_W1_VC),
    ("Sai", 2, _SAI_W2_TEAM, _SAI_W2_CAP, _SAI_W2_VC),
    ("Moe", 2, _MOE_W2_TEAM, _MOE_W2_CAP, _MOE_W2_VC),
    ("Sai", 3, _SAI_W3_TEAM, _SAI_W3_CAP, _SAI_W3_VC),
    ("Moe", 3, _MOE_W3_TEAM, _MOE_W3_CAP, _MOE_W3_VC),
    ("Sai", 4, _SAI_W4_TEAM, _SAI_W4_CAP, _SAI_W4_VC),
    ("Moe", 4, _MOE_W4_TEAM, _MOE_W4_CAP, _MOE_W4_VC),
]


# ── DB connection helper — local copy for seed functions ──────────────────────
# server.py retains its own _db_con() for route handlers; this one is used only
# by _auto_seed_history_if_needed() which runs at startup before Flask is live.

def _db_con():
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


# ── Auto-seed functions — relocated verbatim from server.py ───────────────────
# No logic changes. Strict-scope Phase 1 relocation only.

def _auto_seed_if_needed():
    seed=BASE_DIR/"Seed_Matches.py"
    if not seed.exists(): seed=BASE_DIR/"seed_matches.py"
    if not seed.exists(): return
    try:
        con=sqlite3.connect(str(DB_PATH),timeout=30); con.execute("PRAGMA busy_timeout=30000")
        n=con.execute("SELECT COUNT(*) FROM matches").fetchone()[0]; con.close()
    except: return
    if n>0: return
    print("\n  [startup] No match data — running seed script ...")
    try: subprocess.run([sys.executable,str(seed)],cwd=str(BASE_DIR),timeout=60); print("  [startup] Done.\n")
    except Exception as e: print(f"  [startup] Could not run: {e}\n")


def _auto_seed_players_if_needed():
    seed=BASE_DIR/"Seed_Players.py"
    if not seed.exists(): seed=BASE_DIR/"seed_players.py"
    if not seed.exists(): return
    try:
        con=sqlite3.connect(str(DB_PATH),timeout=30); con.execute("PRAGMA busy_timeout=30000")
        n=con.execute("SELECT COUNT(*) FROM players").fetchone()[0]; con.close()
    except: return
    if n>0: return
    print("\n  [startup] No player data — running Seed_Players.py ...")
    try: subprocess.run([sys.executable,str(seed)],cwd=str(BASE_DIR),timeout=60); print("  [startup] Done.\n")
    except Exception as e: print(f"  [startup] Could not run: {e}\n")


def _auto_seed_history_if_needed():
    """v11.7/v12.5: Versioned history seed with draft preservation."""
    try:
        con = _db_con()
        ver_row = con.execute("SELECT value FROM meta WHERE key='_seed_version'").fetchone()
        stored_ver = ver_row["value"] if ver_row else None
        if stored_ver == _SEED_VERSION:
            print("  [startup] Season history up-to-date.")
            con.close(); return

        print(f"  [startup] Seed version ({stored_ver!r} \u2192 {_SEED_VERSION!r}) \u2014 re-seeding history...")
        seeded_names = list(set(name for name, _, _, _, _ in _HISTORY_SEED))
        max_seed_wk  = max(wk for _, wk, _, _, _ in _HISTORY_SEED)
        nw_backups = {}; extra_weeks = {}

        for uname in seeded_names:
            row = con.execute(
                "SELECT nw_team_json,nw_cap_id,nw_vc_id FROM user_selections "
                "WHERE display_name=? AND week_no=?", (uname, max_seed_wk)
            ).fetchone()
            if row:
                nw_t = row["nw_team_json"]
                tw_row = con.execute(
                    "SELECT tw_team_json FROM user_selections WHERE display_name=? AND week_no=?",
                    (uname, max_seed_wk)
                ).fetchone()
                stale_tw = tw_row["tw_team_json"] if tw_row else "[]"
                if nw_t and nw_t != "[]" and nw_t != stale_tw:
                    nw_backups[uname] = (nw_t, row["nw_cap_id"], row["nw_vc_id"])
            extras = con.execute(
                "SELECT week_no,tw_team_json,tw_cap_id,tw_vc_id,nw_team_json,nw_cap_id,nw_vc_id "
                "FROM user_selections WHERE display_name=? AND week_no>?",
                (uname, max_seed_wk)
            ).fetchall()
            if extras: extra_weeks[uname] = [dict(r) for r in extras]

        for uname in seeded_names:
            con.execute("DELETE FROM user_selections WHERE display_name=? AND week_no<=?",
                        (uname, max_seed_wk))

        seeded = []
        for name, week_no, team, cap, vc in _HISTORY_SEED:
            if week_no == max_seed_wk and name in nw_backups:
                nw_team, nw_cap, nw_vc = nw_backups[name]
            else:
                nw_team = _json.dumps(team); nw_cap = cap; nw_vc = vc
            con.execute("""
                INSERT OR REPLACE INTO user_selections
                (display_name,week_no,tw_team_json,tw_cap_id,tw_vc_id,nw_team_json,nw_cap_id,nw_vc_id)
                VALUES (?,?,?,?,?,?,?,?)
            """, (name, week_no, _json.dumps(team), cap, vc, nw_team, nw_cap, nw_vc))
            seeded.append(f"{name}/W{week_no}")

        for uname, rows in extra_weeks.items():
            for r in rows:
                con.execute("""
                    INSERT OR IGNORE INTO user_selections
                    (display_name,week_no,tw_team_json,tw_cap_id,tw_vc_id,nw_team_json,nw_cap_id,nw_vc_id)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (uname, r["week_no"], r["tw_team_json"], r["tw_cap_id"], r["tw_vc_id"],
                      r["nw_team_json"], r["nw_cap_id"], r["nw_vc_id"]))

        con.execute("INSERT OR REPLACE INTO meta (key,value) VALUES ('_seed_version',?)", (_SEED_VERSION,))
        con.execute("INSERT OR REPLACE INTO meta (key,value) VALUES ('_saved',?)",
                    (datetime.now(timezone.utc).isoformat(),))
        con.commit(); con.close()
        print(f"  [startup] History re-seeded: {', '.join(seeded)}")
        if nw_backups: print(f"  [startup] Preserved nw drafts for: {', '.join(nw_backups)}")
        if extra_weeks:
            restored = [f"{u}/W{r['week_no']}" for u,rs in extra_weeks.items() for r in rs]
            print(f"  [startup] Restored extra weeks: {', '.join(restored)}")
    except Exception as e:
        print(f"  [startup] Could not seed history: {e}")


# ── Public entry point ────────────────────────────────────────────────────────

def run_all_sync(db=None):
    """
    Single startup call for server.py (Phase 1).
    Replaces the three individual _auto_seed_* calls that previously
    lived inline in server.py's __main__ block.

    Execution order is preserved: players → matches → history.
    `db` (a DatabaseManager instance) is accepted for forward-compat but is
    not used in Phase 1 — the seed functions each open their own connections.
    """
    _auto_seed_players_if_needed()
    _auto_seed_if_needed()
    _auto_seed_history_if_needed()
