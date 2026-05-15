# scraper.py — The Cricbuzz Scorecard Reader

> **Post-cleanup note (2026-05-15).** This document is the snapshot that
> drove the cleanup. The Dead Code Audit / Open Questions below describe
> what was **found**. The items listed there have been actioned across
> Waves A–E — see [dead_code_register.md](dead_code_register.md) for the
> current `fixed` / `kept` / `guarded` status of each.


## What it does (business view)

`scraper.py` is the **scorecard reader**. For every match the league
considers "completed", it:

1. Visits the Cricbuzz scorecard page for that match.
2. Pulls out the raw stat line for every batter, bowler, fielder, and
   wicketkeeper.
3. Translates names into the league's player IDs (with the team code
   as a tie-breaker for ambiguous names; auto-creates an `ext_*`
   player if the name is genuinely unknown).
4. Writes the raw stats to the `match_scores` table.
5. Triggers the points pipeline for that one match, so the leaderboard
   updates immediately.

It is a **pure consumer**, not a discoverer. The list of which matches
to scrape (and which Cricbuzz IDs they map to) is read from
`data/schedule.json`. Discovery — finding new IDs to add to that file —
is `logic/cricbuzz_discovery.py`'s job.

The file is **fault-tolerant by design**. Phase 9 added FIX-015 through
FIX-023, a layered defence so a bad scorecard, a missing Cricbuzz ID, a
non-IPL match accidentally linked, or even a corrupt single-player
entry never kills the run.

## Where it sits in the flow

```
   data/schedule.json   ← read in _presync_schedule()
        │
        ▼
   matches table (DB)   ← URLs restored, statuses refreshed
        │
        ▼
   for each "completed" match:
       cricbuzz.com/live-cricket-scorecard/<id>
            │
            ▼
       fetch_scorecard_json → process_cricbuzz_scorecard
            │ (uses logic.fuzzy_match to resolve names)
            ▼
       data/matches/match_NN.json  (cached payload)
            │
            ▼
       _upsert_match() → recalculate_points(match_id) → update_week_points()
```

Triggered three different ways:

- `python scraper.py` — manual CLI run.
- `tasks.start_bg_scrape(match_id, BASE_DIR)` — fired by the Admin tab
  when an admin pastes a Cricbuzz URL (§8.2 in [user_capabilities.md](user_capabilities.md)).
- `tasks.run_discovery_and_scrape()` — the **/api/sync-now** flow
  (§9.1) and the daily 23:55 IST APScheduler job.

## Inputs / Outputs

- **Inputs:**
  - `data/schedule.json` — the canonical "match № ↔ Cricbuzz ID" map.
  - The `matches` table — for current status, scorecard URL, expected
    team pair.
  - The `players` table — read by `_build_player_index()` to feed
    fuzzy matching.
- **Outputs:**
  - `data/matches/match_NN.json` — one cached payload per match (acts
    as the "did we already scrape this?" idempotency flag).
  - `match_scores` table rows (raw per-player stats).
  - `player_match_points` rows (via `db.recalculate_points`).
  - `user_selections.week_pts` updates (via `db.update_week_points`).
  - `players.season_pts` updates at the end of the run.
  - On wrong-scorecard detection: also clears the bad `cricbuzz_id`
    in `schedule.json` so the next discovery can refill the slot.

The return value of `run_full_scrape()` is a stats dict:
`{processed, failed, skipped_non_ipl, no_result_count}`.

## Key business rules it enforces

### 1. The five resilience patches (FIX-015 → FIX-023)
The most important behaviour to understand:

- **FIX-015** — `_auto_add_player()` synthesises an `ext_{cricbuzz_id}`
  player row when a name doesn't resolve, so the innings keeps
  processing instead of crashing.
- **FIX-016** — every `b.get(...)` / `bw.get(...)` uses a safe default;
  a missing key on the Cricbuzz JSON cannot raise `KeyError`.
- **FIX-017** — each batsman and bowler is processed inside its own
  `try / except` (`NON_BLOCKING_ERROR`); one corrupt player row never
  kills the innings.
- **FIX-018** — each *match* is processed inside its own
  `try / except` (`MATCH_FAILED`); one bad scorecard never kills the
  run.
- **FIX-019/021** — `_presync_schedule()` reads `data/schedule.json`
  *before* every scrape and reconciles the DB to it (restores `/00000`
  URLs to real Cricbuzz IDs; updates `status` to `completed` for any
  match whose end-time has passed).
- **FIX-020** — discovery is no longer inline. If a match has `/00000`
  in its URL, the scraper logs an actionable message and **skips
  cleanly** — daily discovery is responsible for filling.
- **FIX-022** — `_reset_url()` clears a wrong `cricbuzz_id` in
  `schedule.json` (not just the DB), so the next discovery can refill
  the slot from the title-keyed merge.
- **FIX-023** — `/00000` URLs no longer trigger a positional-indexing
  fallback. Mismatched IDs are gone for good.

### 2. Wrong-scorecard detection
After fetching a scorecard, the scraper validates two things:
- **All teams in the scorecard must be IPL teams.** If `unknown` teams
  appear (e.g. an English county fixture linked by mistake), the URL
  is reset and `schedule.json` is cleared.
- **Scraped teams must overlap with the scheduled team pair.** If the
  schedule says `SRH vs RCB` but Cricbuzz returned `CSK vs MI`, same
  reset action.

A subtle exception: if the DB's `teams_json` contains non-IPL codes
(legacy bad data), the team-pair check is skipped. The all-IPL check
still runs.

### 3. No-result match handling
If Cricbuzz reports the match as `"no result"`, `"abandoned"`, or
`"cancelled"`, the scraper writes `scores = {}` and counts it as a
**0-point match for everyone**. The match is still marked
`completed` so it appears in history.

### 4. Cache files in `data/matches/`
A successfully scraped match gets its full payload (meta + scores)
written to `data/matches/match_NN.json`. On the next run, if the file
exists and is > 500 bytes, the match is treated as "already scraped"
and skipped. To force a re-scrape, delete that file.

### 5. Strike-rate / economy guards in scoring
`process_cricbuzz_scorecard` only writes the raw stats — the SR and
economy point calculations happen in `logic/scoring_engine.calc_pts`.
But the scraper enforces one rule: **bowling overs are normalised**
using the local `_normalise_overs` (decimal-balls to fractional overs).

## Called by / Calls into

- **Called by:**
  - `tasks._scrape_bg()` — single-match background scrape after a URL paste.
  - `tasks.run_discovery_and_scrape()` — daily auto-sync and `/api/sync-now`.
  - CLI: `python scraper.py`.
- **Calls into:**
  - `db_manager.DatabaseManager` and `_upsert_match`
  - `logic.fuzzy_match` (all five functions)
  - `logic.cricbuzz_discovery` (`load_schedule`, `save_schedule`)
  - `Seed_Matches._auto_count_completed` (lazy import inside
    `_presync_schedule`)
  - Cricbuzz over HTTP via `requests`
  - stdlib: `json`, `math`, `re`, `sqlite3`, `sys`, `time`,
    `datetime`, `pathlib`

## Supports which user capabilities

From [user_capabilities.md](user_capabilities.md):

- **§3 Match Centre / §3.2 Box Score modal** — every per-match point
  that shows up in the hub or modal was first written by this file.
- **§6 Leaderboard** — `week_pts` and `season_pts` updates flow from
  `run_full_scrape` straight into the leaderboard CTE.
- **§8.2 Paste a scorecard URL** — the Admin tab's "Save & Scrape"
  button is wired to `tasks.start_bg_scrape`, which calls into here.
- **§9.1 Refresh button** — second half of the discovery + scrape
  pipeline (after `logic.cricbuzz_discovery` populates schedule.json).

## Dead Code Audit

| Symbol | Lines | Verdict | Notes |
|--------|-------|---------|-------|
| `import unicodedata` | 31 | **DEAD.** | Imported, never used. Added to register as **D13**. |
| `_normalise_overs` | 83–88 | **Live but DUPLICATE** of `logic.scoring_engine._normalise_overs`. The two versions are not bit-for-bit identical — this one wraps the result in `round(_, 4)`. Already tracked as **X3** in the register. | — |
| `_TEAM_PREFIX` dict | 68–72 | **Half-dead.** The dict's *values* (`"c"`, `"d"`, …) are never read. Only `_TEAM_PREFIX.keys()` is used (line 74) to seed `_IPL_TEAMS`. The whole construct could collapse to `_IPL_TEAMS = frozenset({"CSK", "DC", …})`. Added as **D14**. |
| Local import `from Seed_Matches import _auto_count_completed` | 538 | **Live but expensive.** Triggers `Seed_Matches`'s module-level `IPL_2026_SCHEDULE = _load_schedule_tuples()`, which re-parses `schedule.json` on every scrape — for nothing, since scraper doesn't use that constant. See D15 in `Seed_Matches.md`. |
| `fetch_scorecard_json`, `process_cricbuzz_scorecard`, `_parse_dismissal`, `_auto_add_player`, `_extract_meta`, `_reset_url`, `_update_points_for_match`, `_presync_schedule`, `run_full_scrape`, `main` | — | **Live.** | All have callers or are CLI entrypoints. |
| `_match_no_from_id`, `_ID_RE`, `_MATCH_NO_RE`, `_NO_RESULT_STATES`, `HEADERS`, `BASE_DIR`, `MATCHES_DIR`, `SCHEDULE_JSON`, `MAX_RETRIES`, `RETRY_DELAY` | — | **Live.** | All consumed inside the scrape loop. |
| FIX-020 historical comment | 92–94 | **Pure documentation.** Keep — it records why `_fetch_ordered_ids()` was removed. |

**Total dead code in `scraper.py`:** the `unicodedata` import (1 line)
plus the unused values of `_TEAM_PREFIX` (10 dict entries / ~5 lines if
simplified to a frozenset). Modest.

## Open Questions

1. **Cricbuzz HTML parsing is fragile.** `fetch_scorecard_json`
   ([lines 164–200](../scraper.py:164)) hunts for the
   `scorecardApiData` token inside a `self.__next_f.push(...)` block
   and unescapes the JSON manually. The pattern list at line 181 has
   five different terminator strings — the parser is brittle by any
   measure. The first time Cricbuzz changes their Next.js wrapper,
   this stops working silently. Worth adding a minimum-size sanity
   check (the parsed dict should have non-empty `scoreCard`) before
   accepting the result.
2. **Network timing is hard-coded.** `time.sleep(1.5)` between
   matches, `time.sleep(RETRY_DELAY=3)` on errors, `timeout=20` per
   request. None are configurable. If Cricbuzz introduces stricter
   rate-limiting, an operator has no knob to turn.
3. **`run_full_scrape(db=None)` builds a new `DatabaseManager` if
   none given.** That second instance opens its own connection pool —
   subtle but a possible source of "database is locked" pressure under
   concurrent scrapes triggered from the UI. Worth either reusing
   `base.db` or documenting the intent.
4. **No-result counter is informational only.** The `no_result_count`
   in the return dict isn't displayed anywhere. Worth surfacing in
   `/api/sync-now` response so the UI's refresh message can show
   "1 no-result match" rather than counting only `processed`.
5. **`_presync_schedule` does a local import.** Pulling
   `_auto_count_completed` from `Seed_Matches` triggers an unwanted
   side effect (see D15). Better: move `_auto_count_completed` to
   `logic.cricbuzz_discovery` (it's pure logic over a list of tuples)
   and have both `scraper.py` and `Seed_Matches.py` import it from
   there.
