"""
IPL Fantasy 2026 — Database Initialiser                     init_db v1.0.0
===========================================================================
Startup seeder for players, matches, and history. The schema itself is
owned by db_manager.py — DatabaseManager._init_schema() is the
authoritative definition. This file only does the "is the DB empty?
then run the seed scripts" check on boot.

What lives here
---------------
_SEED_VERSION   — version guard for history seeding.
_HISTORY_SEED   — weekly team/cap/vc tuples for W1-W4 (Sai + Moe).
_auto_seed_*    — the three startup seed functions.
run_all_sync()  — single public entry point called by server.py on startup.
"""

import json as _json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR, DB_PATH

DATA_DIR.mkdir(exist_ok=True)

# ── BASE_DIR is kept local: seed subprocess calls use cwd=str(BASE_DIR) ──────────
BASE_DIR = Path(__file__).resolve().parent

# ── History seed data ─────────────────────────────────────────────────────────
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
# DB_PATH is now sourced from config.py — guaranteed same path as scraper.py.

def _db_con():
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


# ── Auto-seed functions — relocated verbatim from server.py ───────────────────
# No logic changes. Strict-scope Phase 1/2 relocation only.

def _auto_seed_if_needed():
    seed=BASE_DIR/"Seed_Matches.py"
    if not seed.exists(): seed=BASE_DIR/"seed_matches.py"
    if not seed.exists(): return
    try:
        con=sqlite3.connect(str(DB_PATH),timeout=30); con.execute("PRAGMA busy_timeout=30000")
        n=con.execute("SELECT COUNT(*) FROM matches").fetchone()[0]; con.close()
    except: return
    if n>0: return
    print("\n  [startup] No match data \u2014 running seed script ...")
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
    print("\n  [startup] No player data \u2014 running Seed_Players.py ...")
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

def run_all_sync():
    """
    Single startup call for server.py. Runs the three auto-seed checks
    in order: players → matches → history. Each function opens its own
    connection and is idempotent.
    """
    _auto_seed_players_if_needed()
    _auto_seed_if_needed()
    _auto_seed_history_if_needed()
