"""
IPL Fantasy 2026 — Background Tasks                        tasks v2.0.0
===========================================================================
v2.0.0 — Daily discovery scheduler.
  • run_discovery_and_scrape()  — full pipeline:
        cricbuzz_discovery.run_discovery()  →  scraper.run_full_scrape()
  • start_bg_sync()             — daemon-thread wrapper for /api/sync-now
  • start_daily_discovery_scheduler()
                                — APScheduler BackgroundScheduler firing
                                  daily at 23:55 IST (after last match ends)
  • stop_scheduler()            — clean shutdown helper for atexit
  • _scrape_bg / start_bg_scrape — unchanged from v1.0.0; still triggered
                                  by /api/update-match-url (single-match
                                  Admin URL paste).

v1.0.0 — Initial: _scrape_bg() / start_bg_scrape() extracted from server.py
         (replaced subprocess.run with in-process scraper.run_full_scrape()).

Why APScheduler in-process (not Windows Task Scheduler)
-------------------------------------------------------
Flask server already runs 24/7 (cloudflared tunnel) and prevents Windows
sleep via SetThreadExecutionState. Embedding the daily job inside the same
process means:
  • No wake-from-sleep flakiness (Modern Standby kills wake timers anyway)
  • No separate process to monitor / restart
  • Single log file, single Ctrl+C, single crash domain
GitHub Actions workflow (.github/workflows/daily_sync.yml) stays as a
safety net for when the Windows box is genuinely off.

APScheduler is an OPTIONAL dependency. Server boots without it; the daily
job is simply disabled and a clear warning is logged. /api/sync-now and
start_bg_scrape() never depend on APScheduler.
"""

import re
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import DB_PATH, DATA_DIR, IPL_YEAR
from db_manager import DatabaseManager
from logic.cricbuzz_discovery import run_discovery, CRICBUZZ_DISCOVERY_VER  # noqa: F401
import scraper as _scraper

# ── APScheduler is optional ──────────────────────────────────────────────────
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    _APSCHEDULER_AVAILABLE = True
except ImportError:
    BackgroundScheduler = None  # type: ignore
    CronTrigger         = None  # type: ignore
    _APSCHEDULER_AVAILABLE = False


# ── Schedule constants (edit here to retune) ─────────────────────────────────
DISCOVERY_HOUR_IST = 23   # 23:55 IST = 18:25 UTC, after last match ends (~23:30 IST)
DISCOVERY_MIN_IST  = 55
IST                = timezone(timedelta(hours=5, minutes=30))
SCHEDULE_JSON      = DATA_DIR / "schedule.json"

# Misfire policy: if server was offline at fire time, run on next start
# provided we're within 6h of the missed slot; collapse multiple misses.
_MISFIRE_GRACE_SEC = 6 * 3600

_scheduler: "BackgroundScheduler | None" = None
_scheduler_lock = threading.Lock()


# ════════════════════════════════════════════════════════════════════════════
# v1.0.0 — single-match scrape after URL update (UNCHANGED)
# ════════════════════════════════════════════════════════════════════════════

def _scrape_bg(match_id: str, base_dir: Path) -> None:
    """
    Daemon thread target: delete the stale JSON cache for one match, then
    run scraper.run_full_scrape(). Called by /api/update-match-url.
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
            f"  [tasks] Scrape complete — "
            f"{result['processed']} ok, {result['failed']} failed"
        )
    except RuntimeError as e:
        print(f"  [tasks] Scrape aborted for {match_id}: {e}")
    except Exception as e:
        print(f"  [tasks] Background scrape failed for {match_id}: {e}")


def start_bg_scrape(match_id: str, base_dir: Path) -> None:
    """Spawn a daemon thread to scrape after a URL update."""
    t = threading.Thread(
        target=_scrape_bg,
        args=(match_id, base_dir),
        daemon=True,
        name=f"scrape-{match_id}",
    )
    t.start()
    print(f"  [tasks] Background scrape started (thread: {t.name})")


# ════════════════════════════════════════════════════════════════════════════
# v2.0.0 — full discovery + scrape pipeline
# ════════════════════════════════════════════════════════════════════════════

def run_discovery_and_scrape(debug: bool = False) -> dict:
    """
    Synchronous full sync pipeline. Safe to call from any thread.

    Steps
    -----
    1. logic.cricbuzz_discovery.run_discovery()  →  updates schedule.json
    2. scraper.run_full_scrape()                 →  presyncs DB +
                                                    scrapes new matches +
                                                    recalculates points

    Step 2 runs even if step 1 returns ok=False, because scraper.py can
    still scrape any matches whose IDs are already in schedule.json (e.g.
    last week's discovery succeeded; tonight's Cricbuzz call timed out).

    Returns
    -------
    {
        "started_at": ISO-8601 IST timestamp,
        "discovery":  dict | None     — run_discovery() stats
        "scrape":     dict | None     — run_full_scrape() stats
        "ok":         bool            — true only if scrape completed
        "error":      str | None      — populated only on hard failure
    }

    NEVER raises. The APScheduler job firing nightly cannot be killed by
    a transient Cricbuzz Cloudflare blip or a sqlite lock.
    """
    started = datetime.now(IST).isoformat(timespec="seconds")
    result: dict = {
        "started_at": started,
        "discovery":  None,
        "scrape":     None,
        "ok":         False,
        "error":      None,
    }

    print(f"\n[sync] === Daily discovery+scrape started ({started}) ===")

    # ── Step 1: discovery ──
    disc = None
    try:
        print(f"[sync] Step 1/2: cricbuzz_discovery → {SCHEDULE_JSON.name}")
        disc = run_discovery(SCHEDULE_JSON, year=IPL_YEAR, debug=debug)
        result["discovery"] = disc
        if disc["ok"]:
            print(f"[sync]   ✅ +{disc['filled']} new IDs "
                  f"(had {disc['already_had']}, "
                  f"{disc['unfilled_known']} still unfilled, "
                  f"{disc['unfilled_playoff']} playoff TBD)")
        else:
            print(f"[sync]   ⚠ discovery soft-failed: {disc.get('error')}")
            print(f"[sync]      continuing to scrape with existing schedule.json")
    except Exception as e:
        result["error"] = f"discovery raised: {e}"
        print(f"[sync] ❌ discovery exception: {e}")
        # Don't return — still try the scrape

    # ── Step 2: scrape ──
    try:
        print(f"\n[sync] Step 2/2: scraper.run_full_scrape")
        db = DatabaseManager(DB_PATH)
        sc = _scraper.run_full_scrape(db)
        result["scrape"] = sc
        result["ok"]     = True
        print(f"\n[sync] ✅ Complete — "
              f"discovery: +{(disc or {}).get('filled', 0)} IDs, "
              f"scrape: {sc['processed']} ok / {sc['failed']} failed")
    except Exception as e:
        result["error"] = (result["error"] or "") + f"; scrape raised: {e}"
        print(f"[sync] ❌ scrape exception: {e}")

    return result


def _bg_sync_target(debug: bool) -> None:
    """Daemon-thread target — wraps run_discovery_and_scrape with hard-catch."""
    try:
        run_discovery_and_scrape(debug=debug)
    except Exception as e:                                   # pragma: no cover
        # run_discovery_and_scrape already catches everything; this is the
        # absolute last line of defence so a daemon thread death never
        # silently terminates with an uncaught exception.
        print(f"  [tasks] Background sync uncaught exception: {e}")


def start_bg_sync(debug: bool = False) -> threading.Thread:
    """
    Spawn a daemon thread that runs the full discovery+scrape pipeline.

    Used by:
      • routes.api_sync_now()  (manual Admin tab trigger)
      • server.py startup       (optional one-shot catch-up on boot)

    Returns the thread for introspection; caller never needs to join.
    """
    t = threading.Thread(
        target=_bg_sync_target,
        args=(debug,),
        daemon=True,
        name="ipl-daily-sync",
    )
    t.start()
    print(f"  [tasks] Background sync started (thread: {t.name})")
    return t


# ════════════════════════════════════════════════════════════════════════════
# v2.0.0 — APScheduler daily cron
# ════════════════════════════════════════════════════════════════════════════

def start_daily_discovery_scheduler() -> "BackgroundScheduler | None":
    """
    Start a BackgroundScheduler firing run_discovery_and_scrape() once a day
    at DISCOVERY_HOUR_IST:DISCOVERY_MIN_IST.

    Behaviour
    ---------
    • Idempotent — calling twice returns the existing scheduler instance.
    • Misfire grace = 6h, coalesce = true, max_instances = 1.
      If server was offline at fire time but starts within 6h, the job runs
      once on startup. If offline longer, the missed fire is skipped.
    • Returns None if APScheduler isn't installed — server still boots, the
      daily job is just disabled. /api/sync-now still works for manual runs.

    Wired from
    ----------
    server.py __main__ block, after init_db.run_all_sync() but before
    app.run(). See file 6 in this rewrite.
    """
    global _scheduler

    if not _APSCHEDULER_AVAILABLE:
        print("  [tasks] ⚠ APScheduler not installed — daily auto-sync disabled.")
        print("  [tasks]    Install:  pip install APScheduler")
        print("  [tasks]    /api/sync-now and manual scrapes still work.")
        return None

    with _scheduler_lock:
        if _scheduler is not None and _scheduler.running:
            print("  [tasks] Daily scheduler already running — skipping init")
            return _scheduler

        _scheduler = BackgroundScheduler(
            timezone=IST,
            job_defaults={
                "coalesce":           True,
                "max_instances":      1,
                "misfire_grace_time": _MISFIRE_GRACE_SEC,
            },
        )
        _scheduler.add_job(
            run_discovery_and_scrape,
            trigger=CronTrigger(
                hour=DISCOVERY_HOUR_IST,
                minute=DISCOVERY_MIN_IST,
                timezone=IST,
            ),
            id="ipl-daily-discovery",
            name="IPL daily discovery+scrape",
            replace_existing=True,
        )

        _scheduler.start()
        job = _scheduler.get_job("ipl-daily-discovery")
        nxt = job.next_run_time if job else None
        print(f"  [tasks] ✅ Daily discovery scheduler started")
        print(f"  [tasks]    Fires daily at "
              f"{DISCOVERY_HOUR_IST:02d}:{DISCOVERY_MIN_IST:02d} IST "
              f"(discovery v{CRICBUZZ_DISCOVERY_VER})")
        print(f"  [tasks]    Next run: {nxt.isoformat() if nxt else 'unknown'}")
        return _scheduler


def stop_scheduler() -> None:
    """
    Cleanly shut down the scheduler.

    Intended for atexit / server shutdown. Safe to call multiple times and
    safe to call when the scheduler never started.
    """
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None and _scheduler.running:
            try:
                _scheduler.shutdown(wait=False)
                print("  [tasks] Daily scheduler stopped")
            except Exception as e:                            # pragma: no cover
                print(f"  [tasks] Scheduler shutdown error: {e}")
        _scheduler = None
