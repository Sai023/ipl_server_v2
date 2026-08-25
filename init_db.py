"""
IPL Fantasy 2026 — Database Initialiser                     init_db v2.0.0
===========================================================================
Startup bootstrap for the members/passcode layer. The schema itself is
owned by db_manager.py — DatabaseManager._init_schema() is the
authoritative definition.

INF-1 (Postgres cutover)
------------------------
The persistent data now lives in managed Postgres (DATABASE_URL), not a
SQLite file committed to git. Consequences for this module:

  * The players/matches "seed if empty" checks now query Postgres. On the
    live deploy the tables are populated by the one-time ETL, so these are
    no-ops. If Postgres is somehow empty we *warn* — we no longer shell out
    to the SQLite Seed_*.py scripts (those wrote to the old local file and
    are kept only as manual/SA20 tooling until they are ported).
  * The members/passcode backfill is idempotent and now runs against
    Postgres, so the "ensure Sai stays admin" safety-net keeps working.

What lives here
---------------
_auto_seed_*    — the startup checks.
run_all_sync()  — single public entry point called by server.py on startup.
"""

import hashlib as _hashlib
import psycopg
from psycopg.rows import dict_row
from datetime import datetime, timezone

from config import DATA_DIR, DATABASE_URL

# Passcode backfill constants — kept here (not imported from base) so init_db
# can run before the Flask app is constructed (base.py creates the app at
# import time, which depends on the DB schema already existing).
_DEFAULT_PASSCODE  = "1234"
_BACKFILL_VERSION  = "v1"      # bump if backfill semantics change
_DEFAULT_ADMIN     = "Sai"     # seeded as is_admin=1 if present

DATA_DIR.mkdir(exist_ok=True)


# ── DB connection helper — Postgres (INF-1) ──────────────────────────────────
# Runs at startup before Flask is live, so it opens its own short-lived
# connection rather than borrowing base.py's pool.

def _db_con():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


# ── Data presence checks (Postgres) ──────────────────────────────────────────
# Post-INF-1 the source of truth is Postgres, seeded by the ETL. These no
# longer subprocess-launch the SQLite Seed_*.py scripts; they only warn if
# the expected data is missing so an empty-DB deploy is loud, not silent.

def _auto_seed_players_if_needed():
    try:
        con = _db_con()
        n = con.execute("SELECT COUNT(*) AS n FROM players").fetchone()["n"]
        con.close()
    except Exception as e:
        print(f"  [startup] Could not check players table: {e}")
        return
    if n > 0:
        return
    print("  [startup] WARNING: Postgres has no players — run the ETL/seed "
          "before serving.")


def _auto_seed_if_needed():
    try:
        con = _db_con()
        n = con.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"]
        con.close()
    except Exception as e:
        print(f"  [startup] Could not check matches table: {e}")
        return
    if n > 0:
        return
    print("  [startup] WARNING: Postgres has no matches — run the ETL/seed "
          "before serving.")


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
                "UPDATE members SET is_admin=1 WHERE username=%s AND is_admin=0",
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
                "SELECT 1 FROM members WHERE username=%s", (uname,)
            ).fetchone()
            if existing:
                continue
            is_admin = 1 if uname == _DEFAULT_ADMIN else 0
            con.execute("""
                INSERT INTO members (username, passcode_hash, must_change, is_admin, created_at)
                VALUES (%s, %s, 1, %s, %s)
            """, (uname, _hash_passcode_local(_DEFAULT_PASSCODE, uname), is_admin, now_iso))
            seeded.append(uname)

        # Make sure Sai is admin even if seeded with is_admin=0 earlier.
        con.execute(
            "UPDATE members SET is_admin=1 WHERE username=%s AND is_admin=0",
            (_DEFAULT_ADMIN,)
        )

        con.execute("""
            INSERT INTO meta (key,value) VALUES ('_members_backfill_done',%s)
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value
        """, (_BACKFILL_VERSION,))
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
    Single startup call for server.py. Runs the checks in order:
    players → matches → members. Each opens its own connection and is
    idempotent against Postgres.
    """
    _auto_seed_players_if_needed()
    _auto_seed_if_needed()
    _auto_seed_members_if_needed()
