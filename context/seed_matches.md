# Seed_Matches.py — The Schedule Synchroniser

> **Post-cleanup note (2026-05-15).** This document is the snapshot that
> drove the cleanup. The Dead Code Audit / Open Questions below describe
> what was **found**. The items listed there have been actioned across
> Waves A–E — see [dead_code_register.md](dead_code_register.md) for the
> current `fixed` / `kept` / `guarded` status of each.


## What it does (business view)

`Seed_Matches.py` is the **schedule synchroniser**. It bridges
`data/schedule.json` (the league's source-of-truth for "which match
plays when and where on Cricbuzz") with the `matches` table in the
database.

Two jobs:

1. **Sync direction `schedule.json → matches table`** — read the
   JSON, compute week numbers from match dates, insert or update each
   row in the `matches` table. Idempotent: nothing is written unless
   something actually changed.
2. **Optional Cricbuzz discovery before the sync** — when run without
   `--no-live`, the script first asks `logic.cricbuzz_discovery` to
   hunt for fresh Cricbuzz IDs and update `schedule.json` in place.

It is **not** the scraper, **not** the discovery engine, and **not** a
schema definition. It is the thin glue layer that decides "which row
needs updating, which is fine, which is brand new".

The `v4.0` history is the key context: discovery was extracted into
`logic/cricbuzz_discovery.py` in Phase 9. This file is now mostly a
shim that knows how to read JSON, compute week numbers, and write rows.

## Where it sits in the flow

```
   ┌─── manual: python Seed_Matches.py             ─── default: discover + sync
   ├─── manual: python Seed_Matches.py --no-live   ─── sync only (GH Actions path)
   ├─── manual: python Seed_Matches.py --verify ID ─── standalone scorecard check
   └─── auto:  init_db._auto_seed_if_needed()      ─── first-boot subprocess

                              │
                              ▼
       logic.cricbuzz_discovery.run_discovery()     (skipped when --no-live)
                              │
                              ▼
                    data/schedule.json
                              │
                              ▼
                     seed_to_db(schedule, completed)
                              │
                              ▼
                       matches table
                              │
                              ▼
            scraper.py consumes matches.scorecard_url
```

## Inputs / Outputs

- **Inputs:**
  - `data/schedule.json` — the canonical league schedule (74 matches,
    week labels, Cricbuzz IDs where known).
  - Optionally, **Cricbuzz HTTP** (skipped with `--no-live`).
  - `argparse` CLI options: `--completed N`, `--no-live`, `--force`
    (no-op for back-compat), `--debug`, `--verify CB_ID`.
- **Outputs:**
  - The `matches` table is brought into sync with `schedule.json` —
    `INSERT` for new rows, `UPDATE` for changed `status` / `URL` /
    `week_no`. Existing real Cricbuzz URLs are **never** overwritten
    with placeholder `/00000`.
  - Console summary: inserted / updated counts, with-ID / without-ID
    counts, week breakdown.
  - When run with `--verify`, prints whether a Cricbuzz scorecard ID
    actually returns a payload (standalone diagnostic — doesn't touch
    the DB).

## Key business rules it enforces

### 1. Week-number calculator
Weeks roll over on **Monday at 14:00 IST**. `_week_no_for_match()`
([Seed_Matches.py:74-89](../Seed_Matches.py:74)) converts a date +
time string into a week number, anchored on `SEASON_WEEK1_END =
Mar 30 2026 14:00 IST`:
- Anything before that anchor → Week 1.
- Anything after → 2 + (days since anchor) // 7.

Note: this is **IST**, while the server-side rollover engine
(`logic.rollover_engine`) operates on **UTC**. The scheduled rollover
happens at 14:00 UTC, not 14:00 IST. The week-number calculation here
is a separate concept — *when did this match fall* — and uses a
different baseline. See Open Question 1.

### 2. Idempotent sync
For each match in `schedule.json`:
- If the row doesn't exist → `INSERT`.
- If it exists but `title` or `teams_json` differs → **always refresh
  them** ([Seed_Matches.py:199-202](../Seed_Matches.py:199)).
- If `status`, `URL`, or `week_no` changed → also write those.
- If everything matches → no write.

**Special rule:** never overwrite a real Cricbuzz URL with a
`/00000` placeholder. If the JSON has `cricbuzz_id=null` but the DB
already has the right URL, the DB wins.

### 3. Title-to-teams_json extraction
The seeder also normalises `teams_json` from the title string
([Seed_Matches.py:183-184](../Seed_Matches.py:183)):
`"SRH vs RCB, 1st Match"` → `["SRH", "RCB"]`. The Admin tab uses this
to render `"M1 · SRH vs RCB"` even when the scraper hasn't run yet.

### 4. Auto-completed detection
`_auto_count_completed(schedule)` ([Seed_Matches.py:130-144](../Seed_Matches.py:130))
counts matches whose **end-time (start + 4h)** has passed in IST. Used
by both `seed_to_db` and (via lazy import) by
`scraper._presync_schedule`.

### 5. Backward-compat exports
The module-level constants `IPL_2026_SCHEDULE`, `_week_no_for_match`,
and `_auto_count_completed` are **declared public** in the docstring
([Seed_Matches.py:27-31](../Seed_Matches.py:27)) for older code that
still imports them. As of v4.0, only `_auto_count_completed` is
actually imported elsewhere — see Dead Code Audit.

### 6. `--verify` is standalone
`verify_scorecard_url(cricbuzz_id)` makes a single HTTP call to
Cricbuzz, checks the page is a real scorecard (presence of
`scorecardApiData` and `batsmenData`), and prints PASS/FAIL. It
never touches the DB or `schedule.json` — pure operator diagnostic.

## Called by / Calls into

- **Called by:**
  - Operator: `python Seed_Matches.py [args]` directly.
  - `init_db._auto_seed_if_needed()` via `subprocess.run`.
  - `daily_sync.yml` step 5 (`python Seed_Matches.py --no-live`).
  - **Lazy import** from `scraper._presync_schedule`
    (`from Seed_Matches import _auto_count_completed`) on every
    scrape run. This is the *only* in-process consumer.
- **Calls into:**
  - `logic.cricbuzz_discovery` (`load_schedule`, `run_discovery`,
    `CRICBUZZ_DISCOVERY_VER`).
  - `sqlite3`, `requests`, stdlib (`argparse`, `json`, `re`,
    `datetime`, `pathlib`, `sys`).

## Supports which user capabilities

From [user_capabilities.md](user_capabilities.md):

- **§8 Admin tab** — every row the admin sees was last written here.
  The "M1 · SRH vs RCB" label, the week number, the "🟢 Completed" /
  "🕑 Upcoming" status pill — all set by `seed_to_db`.
- **§3 Match Centre** — same data; the hub reads from the same rows.
- **§9.1 Refresh** — when an admin clicks Refresh, the
  `/api/sync-now` flow runs `logic.cricbuzz_discovery.run_discovery()`
  (which updates `schedule.json`) followed by the scraper (which
  consumes the DB). But the **scraper itself calls** `_presync_schedule`,
  which re-syncs DB rows from schedule.json before scraping. So
  `seed_to_db()`'s logic is partly duplicated inside the scraper —
  see Open Question 2.

## Dead Code Audit

| Symbol | Lines | Verdict | Notes |
|--------|-------|---------|-------|
| `IPL_2026_SCHEDULE = _load_schedule_tuples()` (module-level) | 127 | **DEAD — and worse, runs work nobody needs.** Loaded on every import; nobody imports it. The docstring at line 27 claims it's "backward-compat for scraper.py", but as of FIX-021 the scraper reads `schedule.json` itself. The only `from Seed_Matches import ...` line in the project is `from Seed_Matches import _auto_count_completed` ([scraper.py:538](../scraper.py:538)) — which still triggers this evaluation as a side-effect. Added as **D15**. |
| `_load_schedule_tuples()` | 96–121 | **Live.** Called by `main()` (line 311) and by `IPL_2026_SCHEDULE`'s initialiser (line 127, dead). After D15 cleanup, this stays alive — used by `main`. |
| `_week_no_for_match()` | 74–89 | **Live, but only inside this file.** Used by `seed_to_db()` (line 169). Listed as a "backward-compat export" in the docstring; no external consumer found. Could become a private `_` helper after the docstring is corrected. |
| `_auto_count_completed()` | 130–144 | **Live across modules.** `scraper._presync_schedule` lazy-imports it on every scrape. |
| `seed_to_db()` | 151–229 | **Live.** Called by `main()`. |
| `verify_scorecard_url()` | 236–253 | **Live (CLI-only).** Reached via `--verify` flag. |
| `main()` | 260–329 | **Live.** CLI entry. |
| Docstring claims about "backward-compat exports" | 27–31 | **STALE.** Two of the three listed exports (`IPL_2026_SCHEDULE`, `_week_no_for_match`) are not consumed externally. Tracked as **S8**. |

**Total dead code in `Seed_Matches.py`:** the `IPL_2026_SCHEDULE`
module-level computation (1 line of code, but triggers a `schedule.json`
parse on every `from Seed_Matches import ...` call elsewhere). Modest
in lines, real in cost.

## Open Questions

1. **Week-number anchor uses IST; rollover uses UTC.** The
   `SEASON_WEEK1_END = Mar 30 2026 14:00 IST` constant
   ([Seed_Matches.py:67](../Seed_Matches.py:67)) anchors the
   week-number calculation. The rollover engine
   ([logic/rollover_engine.py](../logic/rollover_engine.py)) uses
   `DEADLINE_HOUR=14` UTC. These should agree on "when does Week N
   become Week N+1", but they don't — `14:00 IST` is `08:30 UTC`,
   five and a half hours earlier than the actual rollover. For
   matches that start between 08:30-14:00 UTC on a Monday, the week
   number printed in the Admin tab is one *higher* than the week the
   rollover engine puts the match in. This is a real data-integrity
   smell. Tracked as **S9** (potential bug, not just doc).
2. **Schedule sync is half-duplicated.** `seed_to_db` (here) and
   `scraper._presync_schedule` both read `schedule.json` and write
   rows to the `matches` table. They overlap on URL restoration and
   status updates but differ on title / `teams_json` enrichment
   (only `seed_to_db` does that). On every scrape, both run. Worth
   either consolidating into one function (probably in
   `logic.cricbuzz_discovery` or a new `match_sync.py`) or
   documenting the split clearly.
3. **The `--force` flag is a no-op** ([line 268](../Seed_Matches.py:268))
   accepted for backward compat. Today it logs nothing, does nothing.
   Either implement (force re-write all rows) or remove. README still
   says "Run `python Seed_Matches.py --force`" as a fix for
   "0 completed matches" — that advice is currently misleading.
   Tracked as S10.
4. **Title regex is permissive.** `^([A-Z]+)\s+vs\s+([A-Z]+)`
   ([line 183](../Seed_Matches.py:183)) accepts anything that looks
   like upper-case-letters-vs-upper-case-letters. If `schedule.json`
   ever has a malformed title like `"TBD vs TBD, 1st Match"`, the
   seeder will happily write `teams_json = ["TBD", "TBD"]` to the DB.
   Worth either restricting to the 10 IPL teams or accepting it as
   documented behaviour for playoff TBDs.
5. **`verify_scorecard_url` and the rest of the file share no
   dependencies.** It's a self-contained diagnostic with its own
   user-agent string. Worth moving to a tiny `cli/verify.py` so this
   file stays focused on schedule sync.
