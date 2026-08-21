"""
IPL Fantasy 2026 — Database Initialiser                     init_db v1.0.0
===========================================================================
Startup seeder for players, matches, and history. The schema itself is
owned by db_manager.py — DatabaseManager._init_schema() is the
authoritative definition. This file only does the "is the DB empty?
then run the seed scripts" check on boot.

What lives here
---------------
_auto_seed_*    — the three startup seed functions.
run_all_sync()  — single public entry point called by server.py on startup.
"""

import hashlib as _hashlib
import json as _json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR, DB_PATH

# Passcode backfill constants — kept here (not imported from base) so init_db
# can run before the Flask app is constructed (base.py creates the app at
# import time, which depends on the DB schema already existing).
_DEFAULT_PASSCODE  = "1234"
_BACKFILL_VERSION  = "v1"      # bump if backfill semantics change
_DEFAULT_ADMIN     = "Sai"     # seeded as is_admin=1 if present

DATA_DIR.mkdir(exist_ok=True)

# ── BASE_DIR is kept local: seed subprocess calls use cwd=str(BASE_DIR) ──────────
BASE_DIR = Path(__file__).resolve().parent

# ── DB connection helper — local copy for seed functions ──────────────────────
# server.py retains its own _db_con() for route handlers; this one is used only
# by _auto_seed_members_if_needed() which runs at startup before Flask is live.
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


# ── Members / passcode backfill ───────────────────────────────────────────────
# Idempotent: gated by meta key `_members_backfill_done`. Inserts a members row
# for every existing display_name in user_selections with passcode "1234" and
# must_change=1, forcing a passcode change on next login. Seeds Sai as admin.
# Runs at every boot but is a no-op once the meta flag is set.

def _hash_passcode_local(passcode, username):
    return _hashlib.sha256(f"{username}:{passcode}".encode("utf-8")).hexdigest()


def _auto_seed_members_if_needed():
    try:
        con = _db_con()
        flag = con.execute(
            "SELECT value FROM meta WHERE key='_members_backfill_done'"
        ).fetchone()
        if flag and flag["value"] == _BACKFILL_VERSION:
            # Even if backfill already ran, make sure Sai is_admin remains true.
            # Cheap insurance against a manual DB edit dropping the flag.
            con.execute(
                "UPDATE members SET is_admin=1 WHERE username=? AND is_admin=0",
                (_DEFAULT_ADMIN,)
            )
            con.commit(); con.close()
            return

        usernames = [r["display_name"] for r in con.execute(
            "SELECT DISTINCT display_name FROM user_selections"
        ).fetchall()]

        seeded = []
        now_iso = datetime.now(timezone.utc).isoformat()
        for uname in usernames:
            existing = con.execute(
                "SELECT 1 FROM members WHERE username=?", (uname,)
            ).fetchone()
            if existing:
                continue
            is_admin = 1 if uname == _DEFAULT_ADMIN else 0
            con.execute("""
                INSERT INTO members (username, passcode_hash, must_change, is_admin, created_at)
                VALUES (?, ?, 1, ?, ?)
            """, (uname, _hash_passcode_local(_DEFAULT_PASSCODE, uname), is_admin, now_iso))
            seeded.append(uname)

        # Make sure Sai is admin even if seeded with is_admin=0 earlier.
        con.execute(
            "UPDATE members SET is_admin=1 WHERE username=? AND is_admin=0",
            (_DEFAULT_ADMIN,)
        )

        con.execute(
            "INSERT OR REPLACE INTO meta (key,value) VALUES ('_members_backfill_done',?)",
            (_BACKFILL_VERSION,)
        )
        con.commit(); con.close()
        if seeded:
            print(f"  [startup] Seeded passcode '{_DEFAULT_PASSCODE}' "
                  f"(must_change=1) for: {', '.join(seeded)}")
        else:
            print("  [startup] Members backfill: no new users to seed.")
    except Exception as e:
        print(f"  [startup] Members backfill failed: {e}")


# ── Public entry point ────────────────────────────────────────────────────────

def run_all_sync():
    """
    Single startup call for server.py. Runs the seed checks in order:
    players → matches → members. Each function opens its own
    connection and is idempotent.
    """
    _auto_seed_players_if_needed()
    _auto_seed_if_needed()
    _auto_seed_members_if_needed()
