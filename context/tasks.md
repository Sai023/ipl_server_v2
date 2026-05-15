# tasks.py — The Background-Work Coordinator

> **Post-cleanup note (2026-05-15).** This document is the snapshot that
> drove the cleanup. The Dead Code Audit / Open Questions below describe
> what was **found**. The items listed there have been actioned across
> Waves A–E — see [dead_code_register.md](dead_code_register.md) for the
> current `fixed` / `kept` / `guarded` status of each.


## What it does (business view)

`tasks.py` is the **only place in the project that runs work in the
background**. Three jobs live here, all wrapped so they cannot kill the
web server:

1. **Single-match scrape** — when an admin pastes a Cricbuzz URL in
   the Admin tab, the API responds *immediately* and a daemon thread
   does the scrape in the background.
2. **Full discovery + scrape pipeline** — when the Refresh button is
   clicked (`/api/sync-now`), or when the daily scheduler fires, both
   `logic.cricbuzz_discovery.run_discovery()` and
   `scraper.run_full_scrape()` run in sequence.
3. **Daily auto-sync at 23:55 IST** — an APScheduler cron job lives
   *inside the Flask process* and fires the pipeline once a day, after
   the last IPL match of the day has ended.

The file is also responsible for **scheduler lifecycle**: idempotent
startup, graceful shutdown on `atexit`, and a clean fallback when
APScheduler isn't installed (the daily job is simply disabled; manual
runs and admin scrapes still work).

## Where it sits in the flow

```
   server.py boot
       ├── tasks.start_daily_discovery_scheduler()  ← starts APScheduler cron
       └── atexit.register(tasks.stop_scheduler)    ← clean shutdown

   /api/update-match-url (Admin Save & Scrape)
       └── tasks.start_bg_scrape(match_id, BASE_DIR)
             └── daemon thread → scraper.run_full_scrape()

   /api/sync-now (Refresh button)
   APScheduler cron @ 23:55 IST
       └── tasks.start_bg_sync()  OR  direct call to
            tasks.run_discovery_and_scrape()
              ├── 1. logic.cricbuzz_discovery.run_discovery()
              └── 2. scraper.run_full_scrape()
```

## Inputs / Outputs

- **Inputs:**
  - `data/schedule.json` (read by `run_discovery` via the discovery engine).
  - The `matches` table (read by the scraper).
  - APScheduler (optional dependency).
- **Outputs:**
  - All the side effects of running discovery + scrape (see those files).
  - Console logging with `[tasks]` / `[sync]` prefixes.
  - Return dicts from `run_discovery_and_scrape`:
    `{started_at, discovery, scrape, ok, error}`.

## Key business rules it enforces

### 1. APScheduler is optional
The file imports APScheduler inside a `try/except ImportError`. If it's
not installed:
- `_APSCHEDULER_AVAILABLE = False`.
- `start_daily_discovery_scheduler()` prints a warning and returns
  `None`.
- The server still boots; `/api/sync-now` and the admin URL paste
  still work. Only the cron is disabled.

### 2. The daily cron fires at 23:55 IST
- IPL match end-times in IST cluster around 23:30 (some run later).
  23:55 catches every match's final scorecard before midnight.
- Misfire grace: **6 hours**. If the server was offline at 23:55 and
  starts up before 05:55 the next morning, the job runs **once** on
  startup. If offline longer, the missed fire is **skipped** (not
  retroactively run for old matches).
- `coalesce=True, max_instances=1` — if APScheduler somehow accumulates
  multiple pending fires, they collapse into one. Never overlapping.

### 3. The pipeline survives partial failures
`run_discovery_and_scrape()` runs **both** steps even if step 1
(discovery) returns `ok=False`. Reasoning baked into the docstring:
*"scraper.py can still scrape any matches whose IDs are already in
schedule.json"*. A Cloudflare blip blocking discovery tonight doesn't
prevent yesterday's match from being scored.

### 4. Daemon threads, never join
- All background work spawns `threading.Thread(..., daemon=True)`.
  When the server process exits, threads die with it — no `join()`
  needed.
- The `_bg_sync_target` and `_scrape_bg` targets each wrap their work
  in a `try/except` so an uncaught exception in `run_full_scrape`
  cannot silently terminate the thread.

### 5. Scheduler is a singleton
The module-level `_scheduler` variable + `_scheduler_lock` ensure that
calling `start_daily_discovery_scheduler()` twice returns the same
running instance (idempotent). Useful in the rare case where a
development reload triggers double initialisation.

## Called by / Calls into

- **Called by:**
  - `server.py:356-357` — boot-time `start_daily_discovery_scheduler()`
    and atexit `stop_scheduler`.
  - `routes.py:1045` — `start_bg_scrape` from `/api/update-match-url`.
  - `routes.py:1089` — `start_bg_sync` from `/api/sync-now`.
- **Calls into:**
  - `db_manager.DatabaseManager` (instantiated fresh per scrape — see
    Open Questions).
  - `scraper` (the whole module, aliased as `_scraper`).
  - `logic.cricbuzz_discovery.run_discovery`.
  - APScheduler if available.
  - stdlib: `threading`, `re`, `datetime`, `pathlib`.

## Supports which user capabilities

From [user_capabilities.md](user_capabilities.md):

- **§8.2 Paste a Cricbuzz scorecard URL** — `start_bg_scrape` is what
  makes "Save & Scrape" return instantly instead of blocking the API.
- **§9.1 Refresh button** — `start_bg_sync` runs the
  discovery + scrape combo asynchronously; the UI's 75-second
  auto-refresh after the click is timed for this pipeline.
- **§10.2 Daily auto-sync (no UI surface)** — the APScheduler cron at
  23:55 IST keeps the league current overnight without anyone
  clicking anything.

## Dead Code Audit

| Symbol | Lines | Verdict |
|--------|-------|---------|
| `TASKS_VER` (imported from config) | 40 | **DEAD.** Marked `# noqa: F401`; not referenced anywhere in this file. Added to register as **D16**. |
| `start_bg_scrape` | 99–108 | **Live.** Called from `routes.py:1045`. |
| `start_bg_sync` | 201–219 | **Live.** Called from `routes.py:1089`. |
| `start_daily_discovery_scheduler` | 226–286 | **Live.** Called from `server.py:356`. |
| `stop_scheduler` | 289–304 | **Live.** Registered with `atexit` in `server.py:357`. |
| `run_discovery_and_scrape` | 115–187 | **Live.** Called by APScheduler job and (indirectly via `_bg_sync_target`) by `start_bg_sync`. |
| `_scrape_bg`, `_bg_sync_target` | 74–96, 190–198 | **Live** (internal thread targets). |
| `CRICBUZZ_DISCOVERY_VER` | 42 | **Live.** Used in the startup log at line 284. |
| `BackgroundScheduler`, `CronTrigger` import shim | 46–53 | **Live.** Pattern intentional — defines `None` fallbacks if APScheduler is missing. |
| `_MISFIRE_GRACE_SEC = 6 * 3600` | 64 | **Live.** Used in job defaults. |
| Module globals `_scheduler`, `_scheduler_lock` | 66–67 | **Live.** Singleton state. |

**Total dead code in `tasks.py`:** one unused import. Tiny.

## Open Questions

1. **Each background scrape opens a fresh `DatabaseManager`.**
   `_scrape_bg` ([line 87](../tasks.py:87)) and `run_discovery_and_scrape`
   ([line 176](../tasks.py:176)) both do `db = DatabaseManager(DB_PATH)`,
   bypassing the `base.db` singleton. This means each background job
   establishes its *own* thread-local connection pool, which is fine
   functionally but inconsistent with the rest of the codebase. Worth
   passing `base.db` in if we want one true singleton.
2. **The 23:55 IST cron time is hardcoded** at module level
   ([lines 57–58](../tasks.py:57)). Moving these into `config.py`
   alongside `DEADLINE_HOUR` would follow the "single source of truth"
   pattern. Comments suggest the time was picked because "last match
   ends ~23:30 IST" — true today, but the IPL schedule could shift.
3. **`run_discovery_and_scrape` swallows exceptions but the return
   dict's `ok` field can be misleading.** If discovery raises and
   scrape succeeds, `ok=True` and `error="discovery raised: ..."`. A
   caller checking only `ok` would miss the discovery failure. Worth
   adding a separate `discovery_ok` / `scrape_ok` pair.
4. **No retry on the APScheduler job itself.** If the 23:55 fire
   produces both a discovery failure and a scrape failure, the job
   doesn't retry until 23:55 the next day. The misfire grace only
   handles the case where the server was *down*, not where the job
   *ran but failed*. Worth considering a "retry once after 30 min on
   `ok=False`" rule — but the existing failure mode is "yesterday's
   matches eventually get scored", so the case for retry is weak.
5. **The atexit handler runs `stop_scheduler` after Flask's own
   shutdown sequence.** Order: Werkzeug stops accepting requests →
   atexit fires `stop_scheduler(wait=False)`. The `wait=False` means
   in-flight discovery/scrape may be killed mid-write. Worth checking
   what SQLite WAL leaves behind in that case (probably fine — WAL is
   crash-safe — but worth a `kill -9` test).
