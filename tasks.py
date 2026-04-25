"""
IPL Fantasy 2026 — Background Tasks                        tasks v1.0.0
===========================================================================
Phase 3 — DevOps.

What lives here
---------------
All threading and background-scheduling logic that was previously inline
in server.py has been extracted into this module.

Key change vs the old server.py approach
-----------------------------------------
The old /api/update-match-url route spawned a subprocess::

    subprocess.run([sys.executable, "scraper.py"], ...)

tasks.py replaces that with a direct in-process call::

    scraper.run_full_scrape(db)

This means the background scrape shares the same Python process, avoids
subprocess overhead, and lets DatabaseManager handle all DB writes through
its own WAL-safe connection pool.

Public API
----------
start_bg_scrape(match_id, base_dir)
    Spawn a named daemon thread to run a full scrape after a match URL
    update. Called by server.py's /api/update-match-url route.
"""

import re
import threading
from pathlib import Path

from config import DB_PATH, TASKS_VER  # noqa: F401
from db_manager import DatabaseManager
import scraper as _scraper


def _scrape_bg(match_id: str, base_dir: Path) -> None:
    """
    Daemon thread target: delete the stale JSON cache for one match,
    then run a full scrape via scraper.run_full_scrape().

    Replaces the inline _scrape_bg closure that previously lived inside
    server.py's api_update_match_url(), which used subprocess.run().

    Parameters
    ----------
    match_id : str
        Internal match ID (e.g. 'ipl26_m04'). Used to derive the JSON
        cache filename to delete before re-scraping.
    base_dir : Path
        Project root (BASE_DIR from server.py). Used to resolve the JSON
        cache path: base_dir / "data" / "matches" / "match_NN.json".
    """
    try:
        mno_m = re.search(r'_m(\d+)', match_id, re.IGNORECASE)
        if mno_m:
            jp = base_dir / "data" / "matches" / f"match_{mno_m.group(1).zfill(2)}.json"
            if jp.exists():
                jp.unlink()
                print(f"  [tasks] Cleared stale cache: {jp.name}")

        db     = DatabaseManager(DB_PATH)
        result = _scraper.run_full_scrape(db)
        print(
            f"  [tasks] Scrape complete \u2014 "
            f"{result['processed']} ok, {result['failed']} failed"
        )
    except RuntimeError as e:
        print(f"  [tasks] Scrape aborted for {match_id}: {e}")
    except Exception as e:
        print(f"  [tasks] Background scrape failed for {match_id}: {e}")


def start_bg_scrape(match_id: str, base_dir: Path) -> None:
    """
    Spawn a named daemon thread to scrape match data after a URL update.

    The thread is a daemon so it will not block process shutdown if the
    server is stopped while a scrape is in progress.

    Parameters
    ----------
    match_id : str
        Internal match ID (e.g. 'ipl26_m04').
    base_dir : Path
        Project root directory (pass BASE_DIR from server.py).
    """
    t = threading.Thread(
        target=_scrape_bg,
        args=(match_id, base_dir),
        daemon=True,
        name=f"scrape-{match_id}",
    )
    t.start()
    print(f"  [tasks] Background scrape started (thread: {t.name})")
