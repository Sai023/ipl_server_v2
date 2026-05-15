# cricbuzz_discovery.py — The Daily "Where Are Tomorrow's Match IDs?" Hunter

## What it does (business view)

Every IPL match has a numeric **Cricbuzz match ID** (e.g. `149618`).
Without that ID, the scraper has no scorecard to pull and the league
can't score the match. `cricbuzz_discovery.py` is the **daily ID
hunter**: it visits Cricbuzz, finds every fresh match ID it can, and
slots them into the league's `schedule.json` file — the single
source of truth for "which Cricbuzz page maps to which league match".

It runs three different ways:

1. **Daily auto-sync** — once a day at 23:55 IST, the in-server
   APScheduler job (in `tasks.py`) calls `run_discovery()`. This is
   the routine refresh.
2. **Manual admin trigger** — the **Refresh** button in the header
   (§9.1 in [user_capabilities.md](user_capabilities.md)) hits
   `POST /api/sync-now`, which also calls `run_discovery()`.
3. **Bootstrap / re-seed** — `Seed_Matches.py` calls `run_discovery()`
   when first populating the schedule, or when an operator asks for a
   fresh resolve.

This is the **only** part of the project that hits the Cricbuzz website
to find IDs. The scraper itself is a pure consumer of `schedule.json` —
it never discovers, only fetches.

## Where it sits in the flow

```
schedule.json
    ↑ write (atomic, tempfile-then-rename)
    │
cricbuzz_discovery.run_discovery()
    ├── load_schedule(...)         ← read current state
    ├── resolve_series_id(year)    ← find IPL{year} series number on cricbuzz.com
    ├── fetch_series_matches(sid)  ← HTTP + 3-strategy ID extraction
    ├── merge_discoveries(...)     ← team-pair keyed merge into schedule
    └── save_schedule(...)         ← atomic write back

  ↑ called by:
  ├── Seed_Matches.py        (manual / bootstrap)
  ├── tasks.run_discovery_and_scrape()  (daily 23:55 IST + Refresh button)
  └── python -m logic.cricbuzz_discovery  (CLI debug)
```

`scraper.py` reads `schedule.json` to find URLs to scrape, but it does
not call `run_discovery` — the discovery half and the scrape half are
deliberately split so that GitHub Actions (which can't reach Cricbuzz
because its egress IPs are blocked) can still run the scrape half from
a `schedule.json` checked into the repo.

## Inputs / Outputs

### `load_schedule(path) → dict` / `save_schedule(path, data)`
- Read and write the league's `data/schedule.json`. Save is **atomic**:
  serialises to a tempfile in the same directory, then `os.replace()`s —
  no half-written JSON if the process is killed mid-write.
- Also used by `scraper.py` to update `schedule.json` when it detects a
  wrong Cricbuzz ID via FIX-022 `_reset_url`.

### `resolve_series_id(year, debug=False) → str | None`
- Finds the current Cricbuzz series ID for IPL `year` by scraping a
  rotating list of Cricbuzz listing pages
  ([cricbuzz_discovery.py:161-167](../logic/cricbuzz_discovery.py:161)):
  homepage → schedule pages → legacy URL.
- Returns the series ID as a string (e.g. `"9241"` for IPL 2026), or
  `None` if every URL fails — caller then falls back to the cached
  value in `schedule.json`.
- 404 responses are treated as **permanent failures** (no retry); only
  transient errors and 5xx get exponential backoff.

### `fetch_series_matches(series_id, slug, debug=False) → list[dict]`
- Three-strategy scraping of the series page:
  1. **JSON API** — `/api/cricket-series/<sid>/matches`. Often fails;
     cheap to try.
  2. **Next.js hydration JSON** — embedded in `self.__next_f.push(...)`
     blocks in the page HTML.
  3. **Regex** — direct match URL patterns
     (`/live-cricket-scorecard/...`, `/live-cricket-scores/...`,
     `/cricket-scores/...`).
- Runs *both* the nextjs and regex extractors against **four** page
  variants (`/matches`, overview, `/results`, `/points-table`), then
  deduplicates by Cricbuzz ID. Roughly doubles coverage versus
  scraping only one page.
- Returns a list of `{cb_match_id, title, source}` dicts.

### `merge_discoveries(schedule, discovered, debug=False) → (schedule, stats)`
- Slots discovered IDs into the league's schedule by matching
  **team-pair** (a `frozenset({"SRH", "RCB"})`) rather than position.
- Two safety rules:
  - **Existing-IDs dedup** ([line 487-509](../logic/cricbuzz_discovery.py:487))
    — an ID already used on any match row is filtered out of the
    discovery queue, so M1 (SRH vs RCB) can never be reassigned to M67
    (SRH vs RCB again). This was a previously-shipped bug.
  - **Chronological pop** within a team-pair queue — the Nth time
    SRH/RCB appears in discovery order maps to the Nth unfilled
    SRH/RCB slot in the schedule.
- Returns the merged schedule plus a stats dict
  (`filled`, `already_had_id`, `unfilled_known`, `unfilled_playoff`,
  `surplus_discoveries`, `dedup_skipped`, `discovered_total`).
- **Does not mutate** the input dict — produces a copy.

### `run_discovery(schedule_path, year=2026, debug=False, dry_run=False) → dict`
- The orchestrator: load → resolve → fetch → merge → save. Always
  returns a stats dict with `ok`, `error`, `written`, etc. — never
  raises, so a scheduled job can keep firing even when Cricbuzz is
  down or behind Cloudflare.

## Key business rules it enforces

1. **`schedule.json` is the source of truth, never the database.** All
   ID changes go through this file, then `Seed_Matches.py` syncs the
   file into the `matches` table. This keeps the DB rebuildable from
   the JSON.
2. **Series IDs change between seasons.** v1.2.0 found this the hard
   way: IPL 2026 is `9241`, not `9237` (which was the 2025 ID hardcoded
   in older revisions). `resolve_series_id` is the dynamic resolver
   that catches the change.
3. **No positional indexing.** Match `M3` is `M3` because the league
   defined it so; the third row in Cricbuzz's discovery list is
   irrelevant. Team-pair keying makes the merge order-independent.
4. **Discovery is one-way idempotent.** Already-filled slots are never
   overwritten by discovery (`already_had` short-circuit). The only
   way to *change* an ID is for `scraper.py` to call `save_schedule()`
   with a different value (FIX-022).
5. **Playoff slots are TBD until the round-robin ends.** Matches with
   fewer than 2 teams in the schedule (semi-finals before W9) are
   tracked as `unfilled_playoff` and skipped — no discovery candidate
   is forced onto them.

## Called by / Calls into

- **Called by:**
  - `tasks.py` — `run_discovery` (in `run_discovery_and_scrape`)
  - `Seed_Matches.py` — `run_discovery` and `load_schedule`
  - `scraper.py` — `load_schedule` and `save_schedule`
  - CLI: `python -m logic.cricbuzz_discovery` (the `__main__` block)
- **Calls into:** stdlib (`hashlib`, `json`, `os`, `random`, `re`, `time`,
  `datetime`, `pathlib`, `tempfile`) **plus `requests`**. **Imports `requests`**
  — see Open Questions.

## Supports which user capabilities

From [user_capabilities.md](user_capabilities.md):

- **§3 Match Centre** — every match card needs a Cricbuzz ID before it
  can show points. Discovery is what supplies the ID.
- **§8.2 Admin Tab — paste a URL** — admins only need to paste a URL
  manually when discovery couldn't find it. The "Missing IDs" filter
  reflects discovery gaps.
- **§9.1 Refresh button** — directly triggers discovery via
  `/api/sync-now`. The 75-second auto-refresh wait in the UI is sized
  for one discovery + scrape cycle to finish.
- **§10.2 Daily auto-sync (implicit)** — the 23:55 IST APScheduler job
  silently refreshes IDs overnight; the user never sees the trigger.

## Dead Code Audit

| Symbol | Verdict |
|--------|---------|
| `CRICBUZZ_DISCOVERY_VER` | **Live.** Imported by `tasks.py` and `Seed_Matches.py`. |
| `IST`, `_now_ist_iso` | **Live** (internal). Used by `save_schedule` (via `merge_discoveries`) and `run_discovery` for `last_updated`. |
| `IPL_TEAMS` | **Live** (internal). Used by `_extract_teams_from_title`. |
| `_USER_AGENTS`, `_RETRY_ATTEMPTS`, `_RETRY_BASE_SEC`, `_REQUEST_TIMEOUT`, `_hdrs`, `_is_cloudflare`, `_fetch_html`, `_SERIES_LOOKUP_URLS` | **Live** (internal). All used by the HTTP code paths. |
| `load_schedule`, `save_schedule` | **Live.** Used by `scraper.py`, `Seed_Matches.py`, and internally. |
| `resolve_series_id`, `_strategy_api`, `_strategy_nextjs`, `_strategy_regex`, `_extract_teams_from_title`, `merge_discoveries`, `fetch_series_matches`, `run_discovery` | **Live.** Used by `run_discovery` and/or external callers. |
| `__main__` block | **Live** (operator tool). `python -m logic.cricbuzz_discovery` runs an ad-hoc discovery. |

**No dead code.** Every function and constant has a caller.

The previous "legacy" cleanups are now in their final state — the v1.1.0
changelog notes the removal of the dead `/api/html/...` URL from
`_strategy_api`, and v1.2.0 simplified the retry path. There's nothing
left to prune.

## Open Questions

1. **Does `cricbuzz_discovery.py` belong in `logic/`?** The package's
   docstring rule is *"zero project imports; only stdlib is permitted"*.
   This file imports `requests` (third-party) and performs HTTP I/O
   with retries and timeouts — by any reasonable definition, that's
   "side effects". Sibling files (`scoring_engine`, `rollover_engine`,
   `fuzzy_match`) are genuinely pure. Two options:
   - Keep here and update the package docstring to say "logic + curated
     I/O for discovery".
   - Move to a new `ingestion/` package alongside `scraper.py` and
     `tasks.py`, leaving `logic/` for the pure layer.
   Tracked as [docs_audit.md item E](docs_audit.md).
2. **Cricbuzz blocks GitHub Actions IPs.** The whole reason for the
   two-tier daily-sync model is that this file cannot run in cloud CI.
   That fact is documented in [daily_sync.yml:8-10](../.github/workflows/daily_sync.yml:8)
   but **not in README** — see Phase 7 (the operations docs).
3. **Cloudflare detection is a string match.** `_is_cloudflare(html)`
   greps for `"cf-browser-verification"` and `"Just a moment"`. If
   Cricbuzz changes the challenge page wording, the detector goes
   blind and we'll try to extract IDs from the challenge page itself.
   No-op practically (the regexes won't match), but the debug logs
   will become misleading. Add a `len(html) < 5000 and "title" in
   html.lower()` heuristic as a backup signal?
4. **`series_id` resolution probes the *homepage* first.** This is
   correct today (verified 2026-05-14 per the docstring) but it's a
   fragile contract — Cricbuzz changing their homepage layout would
   silently break daily discovery. Worth a smoke test that asserts
   `resolve_series_id(2026)` returns a non-`None` string at least
   weekly. Could be scheduled via the same APScheduler in `tasks.py`.
5. **No backoff between page fetches in `fetch_series_matches`.** Four
   page hits in immediate succession with rotating User-Agents — fine
   for Cricbuzz today, but a polite 1-2s `time.sleep()` between them
   would reduce the chance of triggering a rate-limit during a
   recovery storm (e.g. when a misconfigured cron fires the daily job
   every 5 minutes by accident).
