# Dead Code Register — Pending Cleanup

> Living catalog of every dead symbol, duplicate, or orphan file found
> while building the context files. **Nothing here is deleted yet.**
> This is the workbook for the final cleanup phase — after all Phase 0–7
> context docs are reviewed, we walk this list together and decide what
> goes.
>
> Update rule: every Phase X context file that finds dead code adds a row
> here. Never delete a row — change its **Status** instead, so we keep
> the history.

## Status legend

| Status | Meaning |
|--------|---------|
| `open` | Found, not yet reviewed by the user. |
| `confirmed` | User reviewed and agreed to delete in cleanup pass. |
| `keep` | User decided to keep — reason recorded. |
| `refactor` | User decided to restore via refactor (e.g. wire helper back in). |
| `fixed` | Removed / refactored in the cleanup pass. |

---

## 1. Dead functions / symbols

| ID | File | Lines | Symbol | What it is | Phase | Recommendation | Status |
|----|------|-------|--------|------------|-------|----------------|--------|
| D1 | [logic/scoring_engine.py](../logic/scoring_engine.py) | 127–146 | `apply_multiplier` | Pure function for C×2 / VC×1.5 multiplier. Zero callers — `routes.py` reimplemented inline. | 2 | **DONE (Wave D).** Deleted (~20 lines). After D2 refactor (routes.api_audit_scores now uses debug_calc_pts which handles the multiplier internally), no caller remains. The `CAP_MULT` and `VC_MULT` constants are still exported. | fixed |
| D2 | [logic/scoring_engine.py](../logic/scoring_engine.py) | 151–329 | `debug_calc_pts` | Step-by-step audit tracer written for `/api/audit-scores/<n>`. | 2 | **DONE (Wave D).** Restored as the canonical audit path. `routes.api_audit_scores` rewritten to call it — saves ~20 lines in routes.py, the response now includes a `steps` per-component breakdown for free. Accuracy verified: Sai W1-W2 computed via refactored path == stored week_pts (1601 == 1601, bit-identical). PowerShell `Audit_Scores.ps1` still works (raw-stat keys unchanged). | fixed |
| D3 | [templates/index.html](../templates/index.html) | 25, 122–133, 569–612 | `_buildPointsTab`, `_loadPoints`, `_ptsData`, `_ptsLoading` | Points tab UI + data fetch. | 0 | **DONE (Wave B1).** Function + state + bootstrap reference + `switchTab` branch all removed. ~50 lines off index.html. | fixed |
| D4 | [Static/ipl_glue.js](../Static/ipl_glue.js) | 462 onwards | `_buildPointsTab` (frontend copy) | Same dead Points tab builder. | 0 | **DONE (Wave B1).** Override deleted (~104 lines). Plus orphan `_playerMap`/`_loadPlayerMap` (~12 lines) and `IplApi.getPlayerPoints`. | fixed |
| D5 | [templates/index.html](../templates/index.html) | 498–515 | `_buildHistoryTab` | History tab UI builder. **`_historyData` still consumed elsewhere.** | 0 | **DONE (Wave B2).** Both inline and ipl_glue.js override removed. `_loadHistory`/`_historyData`/`/api/history/<n>` all preserved. | fixed |
| D6 | [db_manager.py:860](../db_manager.py:860) | 860 | `GoldenDB = DatabaseManager` | Module-level alias. Never imported or referenced anywhere. | 3 | **DONE.** Deleted. Tested: db_manager imports OK, GoldenDB no longer accessible, all other methods intact. | fixed |
| D7 | [db_manager.py:853-857](../db_manager.py:853) | 853–857 | `DatabaseManager.reset()` | Wipes 6 tables. No callers anywhere. | 3 | **DONE.** Deleted. Tested alongside D6. | fixed |
| D8 | [db_manager.py:550-572](../db_manager.py:550) | 550–572 | `DatabaseManager.update_player_points()` | Sets `players.points` — cap/vc-weighted column. | 3 | **DONE (Wave B1).** Method deleted; call site in `update_week_points` removed. `players.points` column kept in schema (no consumer; cheap insurance for one season). | fixed |
| D9 | [init_db.py:42-137](../init_db.py:42) | 42–137 (~95 lines) | `init_db._SCHEMA` | Duplicate of `db_manager.py:59-154`. Never executed in this file. | 3 | **DONE.** Deleted entire `_SCHEMA` block. `db_manager.py` remains the authoritative schema. Tested: `_SCHEMA` no longer accessible, init_db imports cleanly, `run_all_sync` callable. | fixed |
| D10 | [init_db.py:30](../init_db.py:30) | 30 | `from db_manager import DatabaseManager` (in init_db) | Marked `# noqa: F401` with a misleading comment claiming it's used by `run_all_sync` — but the caller does not use it. | 3 | **DONE.** Deleted. | fixed |
| D11 | [init_db.py:31](../init_db.py:31) | 31 | `from config import ... INIT_DB_VER, VERSION_MAP` | Marked `# noqa: F401`; not used. | 3 | **DONE.** Trimmed to `from config import DATA_DIR, DB_PATH`. | fixed |
| D12 | [init_db.py:321](../init_db.py:321) | 321 | `run_all_sync(db=None)` parameter | Argument accepted but never read inside the function. Caller passes a real `DatabaseManager` for no reason. | 3 | **DONE.** Dropped the parameter. Updated the caller in `server.py:348` to `init_db.run_all_sync()` (no args). Tested: signature shows zero params, server.py parses cleanly. | fixed |
| D13 | [scraper.py:31](../scraper.py:31) | 31 | `import unicodedata` | Imported, never used in the file. (Used in `logic/fuzzy_match.py` but that import is separate.) | 4 | **DONE.** Deleted. | fixed |
| D14 | [scraper.py:68-72](../scraper.py:68) | 68–72 | `_TEAM_PREFIX` dict | The dict's **values** (the prefix letters) are never read; only `_TEAM_PREFIX.keys()` is consumed (line 74) to build `_IPL_TEAMS`. | 4 | **DONE.** Collapsed to direct `_IPL_TEAMS = frozenset({"CSK","DC","GT","KKR","LSG","MI","PBKS","RCB","RR","SRH"})`. Tested: all 10 teams present, scraper imports OK. | fixed |
| D15 | [Seed_Matches.py:127](../Seed_Matches.py:127) | 127 | `IPL_2026_SCHEDULE` module-level constant | Computed on every `import Seed_Matches`; nobody imports it. | 4 | **DONE.** Deleted the module-level assignment. `_load_schedule_tuples()` retained — still called by `main()` and by scraper's lazy `_auto_count_completed` import. Tested: scraper-side lazy import still works, `_auto_count_completed` produces sensible output on real schedule. | fixed |
| D16 | [tasks.py:40](../tasks.py:40) | 40 | `TASKS_VER` import | Marked `# noqa: F401`; not referenced anywhere in `tasks.py`. | 4 | **DONE.** Trimmed from import line. | fixed |
| D17 | [routes.py:127-136](../routes.py:127) | 127–136 | `api_save_state` + `IplApi.saveState` + `db.save_state` | Exposed but never called. | 5 | **DONE (Wave B4).** Route handler + client method + DAO method all deleted (~80 lines). | fixed |
| D18 | [routes.py:351-361](../routes.py:351) | 351–361 | `api_match` + `IplApi.saveMatch` | Superseded by `/api/update-match-url`. | 5 | **DONE (Wave B4).** Route + client deleted. `db.upsert_match` kept (still used by `/api/update-match-url`). | fixed |
| D19 | [routes.py:996-1001](../routes.py:996) | 996–1001 | `api_seed_history` + `IplApi.seedHistory` | Never called. `init_db._auto_seed_history_if_needed` runs at startup. | 5 | **DONE (Wave B4).** Route + client deleted. Also removed now-orphaned `import init_db` from routes.py (cascade). | fixed |
| D20 | [routes.py:263-294](../routes.py:263) | 263–294 (~32 lines) | `api_debug_points` handler | Zero consumers anywhere. | 5 | **DONE (Wave B4).** Deleted (~46 lines incl. block). | fixed |
| D21 | [routes.py:253-261](../routes.py:253) | 253–261 | `api_user_match_points` + `db.get_user_match_points` + `IplApi.getUserMatchPoints` | Consumed only by the dead Matches tab. | 5 | **DONE (Wave B3).** All three removed. | fixed |
| D22 | [server.py:49](../server.py:49) | 49 | `from pathlib import Path` | Never used in `server.py`. | 5 | **DONE.** Deleted. | fixed |
| D23 | [server.py:51](../server.py:51) | 51 | `from flask import render_template` | Never used in `server.py`. `render_template` lives in `base.py` and `routes.py`. | 5 | **DONE.** Deleted. | fixed |
| D24 | [Static/ipl_glue.js:569-583+](../Static/ipl_glue.js:569) | 569+ | `_buildMatchesTab`, `_loadUserMatchPoints`, `_umpData` | Third dead tab. | 5 | **DONE (Wave B3).** Block removed (~65 lines). Orphaned `_umpData=null` listener also cleaned. | fixed |
| D25 | [Static/ipl_glue.js:766-775](../Static/ipl_glue.js:766) | 766–775 | `_mcData`, `_mcLoading` globals + `_buildMatchCentreTab` stub | Leftover v7.6 scaffold. | 6 | **DONE.** Deleted both globals and the stub. Tested: balanced braces/parens, all IplApi exports intact. | fixed |
| D26 | [Static/ipl_glue.js:619-676](../Static/ipl_glue.js:619) | 619–676 (~58 lines) | `_injectStatsToPicker`, `_pickerObserver`, `_setupPickerObserver` | **Inert MutationObserver.** Watched `#app` for selectors that never matched any live row. | 6 | **DONE.** Deleted the function, observer var, setup function, DOMContentLoaded hook. Trimmed the `ipl:state-updated` listener to only reset `_umpData`. Tested: ~58 lines off, 723 lines total. `_loadPlayerMap`/`_playerMap` retained for now — cascades naturally when Wave B removes `_buildPointsTab`. | fixed |
| D27 | [templates/index.html:498-515](../templates/index.html:498) | 498–515 | Inline `_buildHistoryTab` (shadowed by override) | Both dead. | 6 | **DONE (Wave B2).** Inline version + override both deleted. | fixed |
| D28 | [templates/index.html:552-560](../templates/index.html:552) | 552–560 (~9 lines) | `_buildMatchesCard(matchList)` | Different from D24's `_buildMatchesTab` — also dead. | 6 | **DONE (Wave B3).** Deleted. | fixed |
| **Phase 7** | — | — | **No new dead code.** All four operations files (`daily_sync.yml`, `Audit_Scores.ps1`, `setup_cloudflare.ps1`, `cloudflared.exe`) are fully live. | 7 | — | — |

## 2. Duplicate logic

| ID | Files | Symbol / topic | Phase | Recommendation | Status |
|----|-------|----------------|-------|----------------|--------|
| X1 | [base.py:98](../base.py:98) `resolve_player_id` vs [logic/fuzzy_match.py:122](../logic/fuzzy_match.py:122) `_fuzzy_match` | Two parallel player resolvers with different thresholds (0.40 vs 0.45) and different nickname maps (`_SEMANTIC_MAP` in base only). | 1 | **Partially fixed.** `_SEMANTIC_MAP` moved to `logic/fuzzy_match.py` as the single source; `base.py` now imports it. The two resolvers still exist (intentional — UI is permissive at 0.40, scraper is strict at 0.45 because Cricbuzz names are formal). Drift risk eliminated. Scenario tested: same dict object, all UI nicknames still resolve correctly, scraper behaviour unchanged. | partial-fixed |
| X2 | [base.py:39-40](../base.py:39) vs [config.py:29-31](../config.py:29) | `BASE_DIR` and `DATA_DIR` re-derived from `__file__` in `base.py` instead of imported from `config.py`. | 1 | **DONE (Wave D).** `base.py` now imports both from `config`. `STATIC_DIR` still derives locally (config doesn't export it). Bonus: dropped the now-unused `from pathlib import Path` import too. Tested: `base.BASE_DIR is config.BASE_DIR` → True (same object). | fixed |
| X3 | [scraper.py:83-88](../scraper.py:83) vs [logic/scoring_engine.py:39-53](../logic/scoring_engine.py:39) | Duplicate `_normalise_overs` — scraper's version wraps in `round(_, 4)`, logic-engine version doesn't. Not bit-for-bit identical. | 2 | **DONE.** Deleted the local copy from `scraper.py`; `_normalise_overs` is now imported from `logic.scoring_engine`. Verified mathematical equivalence across 126 Cricbuzz-realistic inputs (max delta ~3.3e-5, invisible). Also removed the now-orphan `import math` from scraper. Scenario tested: identity check, full input grid, scraper.main / run_full_scrape still callable. | fixed |

## 3. Orphan files

| ID | Path | Phase | Recommendation | Status |
|----|------|-------|----------------|--------|
| O1 | `ipl_glue.js` (project root, v7.7, 875 lines) | 0 | **DONE.** Deleted. Live `/static/ipl_glue.js` (v7.8) unaffected. | fixed |
| O2 | `A _ Sticky budget _ table picker.html` | 0 | **DONE.** Deleted. | fixed |

## 4. Stale documentation (no code change needed, but tracks intent)

| ID | File | Issue | Phase | Recommendation | Status |
|----|------|-------|-------|----------------|--------|
| S1 | [logic/__init__.py:9-12](../logic/__init__.py:9) | Docstring omits `cricbuzz_discovery` and `_generate_dynamic_player`. | 1 | One-line patch each. | open |
| S2 | [logic/rollover_engine.py:51](../logic/rollover_engine.py:51) | Docstring says "14 for 14:00 SAST" — should be "14 for 14:00 UTC". | 2 | One-word fix. | open |
| S3 | [templates/index.html:491, 309, 317, 562, 563](../templates/index.html:491) | UI strings said `"Locks Mon 14:00 IST"` (line 491) and `"Simulate Monday 2:00 PM Rollover"` (other lines, ambiguous timezone). Actual deadline is 14:00 UTC. | 0 | **DONE.** All four user-visible labels now read `14:00 UTC`. Scenario tested: no `\bIST\b` left in template, `<script>` balance preserved, no JS behaviour changed (rollover timer already uses UTC). | fixed |
| S4 | [README.md:6](../README.md:6), [SKILL.md:7](../SKILL.md:7) | APP_VERSION drift: README says 2.1.0-stable, SKILL.md says 2.2.0-match-centre, config.py says 2.3.0. | 0 | Generate the line at build time or fetch from `/api/version`. | open |
| S5 | [SKILL.md:177](../SKILL.md:177), [Static/mc_hub.js:7](../Static/mc_hub.js:7) | Claim "9 tabs" — there are 6 in `index.html`. | 0 | Update comments to "6 tabs". | open |
| S6 | [SKILL.md:339](../SKILL.md:339) | Claims `players.points` is "Display in Points tab" — that tab is dead (D3/D4). | 0 | After D3/D4 cleanup is confirmed, fix or drop the row. | open |
| S7 | [init_db.py:9-11, 38-40](../init_db.py:9) | Docstring comments call `db_manager.py`'s schema the "later consolidation target"; today `db_manager.py` is already authoritative and `init_db.py`'s schema is dead. | 3 | Update comment after D9 cleanup. | open |
| S8 | [Seed_Matches.py:27-31](../Seed_Matches.py:27) | Docstring claims `IPL_2026_SCHEDULE` and `_week_no_for_match` are "backward-compat exports for scraper.py". Today neither is imported externally — only `_auto_count_completed` is. | 4 | Update the docstring to list only the actual public surface, or remove the section entirely. | open |
| S9 | [Seed_Matches.py:67](../Seed_Matches.py:67) | Week-number calculation anchors on `Mar 30 14:00 IST`, but the in-server rollover engine runs at `14:00 UTC` (= 19:30 IST). For Monday matches in the 14:00-19:30 IST window, the week number is one ahead of where the rollover places the result. | 4 | **Investigated and guarded.** Probed all 8 Monday matches in `schedule.json` — **every one starts at 19:30 IST exactly**, none in the danger window. Bug is **latent, not active**. Added `_audit_monday_match_schedule()` to [server.py](../server.py:159) that fires a startup WARNING if a future schedule update lands a Monday match between 14:00-19:30 IST. Three scenarios tested (live, synthetic-bad, missing file). Full UTC migration deferred to Wave D. | guarded |
| S10 | [Seed_Matches.py:268](../Seed_Matches.py:268), [README.md](../README.md) | `--force` flag is accepted but is a no-op. README troubleshooting still suggests using it. | 4 | Either implement `--force` (rewrite all rows) or remove the flag and the README advice. | open |
| S11 | [Static/mc_hub.js:7, 40-44](../Static/mc_hub.js:7) | Comments claim *"all 9 tabs remain accessible without wrapping or clipping"* — there are 6 tabs. Parallel to S5 (SKILL.md version of the same drift). | 6 | One-word fix in two places. | open |
| S12 | [README.md:218](../README.md:218) | README troubleshooting references `_patchXiGrid` template injection as the v7.7 fix. The live Static/ipl_glue.js is v7.8 and the patch was **removed** — the live solution is inline rendering in `_buildNwSquad` (index.html:343). | 6 | Update the README entry after O1 deletion is confirmed. | open |
| S13 | [.github/workflows/daily_sync.yml](../.github/workflows/daily_sync.yml), [setup_cloudflare.ps1](../setup_cloudflare.ps1) | README has no "Daily Sync" section explaining the two-tier discovery (local) + scrape (cloud) model. Same for the tunnel: README mentions `--tunnel cloudflare` but never explains the `cloudflared.exe` vendor vs `setup_cloudflare.ps1` choice. | 7 | Add two short sections to README. | open |
| S14 | `SKILL.md` (root, deleted) + `.claude/skills/ipl-fantasy-sync/SKILL.md` (rewritten) | Both files were severely stale — advertising dead `apply_multiplier`/`debug_calc_pts`, claiming "9 tabs", listing wrong version numbers, referring to dead endpoints, missing Match Centre and cricbuzz_discovery. | Cleanup | **DONE.** Root `SKILL.md` deleted. `.claude/` version rewritten as a ~25-line pointer to `context/`. Scenario tested: only code-side reference is one historical line in `config.py` VERSION_MAP (changelog only, non-load-bearing). | fixed |

## 5. Endpoints with no UI consumer (to verify in Phase 5 / 7)

| ID | Endpoint | Source | Phase | Action | Status |
|----|----------|--------|-------|--------|--------|
| E1 | `GET /api/player-points/<n>` | [routes.py](../routes.py) | 0 | UI consumer was the dead Points tab. | confirmed | **DONE (Wave B1).** Route + handler deleted. Also dropped now-orphan `import collections` from routes.py. | fixed |
| E2 | `GET /api/history/<n>` | [routes.py](../routes.py) | 0 | Tab UI dead but endpoint still consumed by login draft pre-fill, Members tab. | confirmed-keep | **KEPT** (intentional). | kept |

## Status after Wave A + Wave C + Wave B + Wave D (2026-05-15)

**Fixed:** D1, D2, D3, D4, D5, D6, D7, D8, D9, D10, D11, D12, D13, D14, D15, D16, D17, D18, D19, D20, D21, D22, D23, D24, D25, D26, D27, D28, E1, O1, O2, S3, S14, X2, X3.

**Guarded / partial-fixed:** S9 (startup audit in place; full UTC migration deferred), X1 (nickname map unified; thresholds remain different by design).

**Kept (intentional):** E2 `/api/history/<n>`, `db.upsert_match` (live consumer remains).

**Additional one-off bug fixes during the cleanup:**
- M51/M52/M53 schedule drift between schedule.json and matches table (root cause: `_reset_url` didn't restore title/teams_json, and `_presync_schedule` had duplicate sync logic that missed those fields). Fixed by routing `_presync_schedule` through `seed_to_db`, which now also refreshes `date_label` and uses ASCII-safe prints.
- M53 had a wrong CB ID (152152 = a future "59th Match" in Preview state). Added a Preview-state guard to the scraper so future mismatches auto-recover via `_reset_url`. M53's CB ID cleared; awaits operator entry.
- Admin tab display number bug — `parseInt('ipl26_m51'.replace(/\D/g,''))` = 2651, not 51. Replaced with anchored `/_m(\d+)$/` regex.
- UTF-8 stdout reconfigure at server boot — eliminates `UnicodeEncodeError` cascades on Windows cp1252 consoles. The boot-time `_audit_monday_match_schedule`, `seed_to_db`, and scraper progress prints all now survive emoji output.

**Still open (Wave E candidates — doc-only):**

- **S1, S2, S5, S6, S7, S8, S10, S11, S12, S13** — README + docstring touchups.
- **S4** — APP_VERSION drift in README (now `2.3.0` in config).
- **S9 full migration** — multi-file UTC anchor change, deferred. Guard is active.

## Final running totals (after Phase 7 — register is complete)

- **Dead code identified:** ~720+ lines across the whole project.
  - Phase 0: D3 + D4 + D5 (~70 lines, Points/History tabs)
  - Phase 2: D1 + D2 (~200 lines, scoring_engine helpers)
  - Phase 3: D6 + D7 + D9 + D10 + D11 (~130 lines, mostly init_db `_SCHEMA`)
  - Phase 4: D13 + D14 + D15 + D16 (~15 lines)
  - Phase 5: D17 + D18 + D19 + D20 + D21 + D22 + D23 + D24 (~160 lines)
  - Phase 6: D25 + D26 + D27 + D28 (~135 lines)
  - Phase 7: **No new dead code** — operations layer is clean.
  - Conditional: D8 (~30 lines, `update_player_points`) — depends on D3 outcome
- **Dead HTTP endpoints:** 6 confirmed (E1/D17–D21).
- **Dead UI tabs:** 3 confirmed (Points D3, History D5, Matches D24).
- **Orphan files:** 2 (O1, O2) — see [orphans.md](orphans.md).
- **Duplicate-logic clusters:** 4 (X1–X3 + `_audit_player_id_coverage` parallel).
- **Stale-doc fixes pending:** 13 (S1–S13).
- **Suspected data-integrity bug:** S9 — week-number anchor in IST vs
  rollover in UTC.

The dead-code register is now **complete**. Next step: cleanup phase.
You review the register, set each `open` row to `confirmed` / `keep`
/ `refactor`, then a single coordinated pass executes the deletions
and cascades. **Nothing is deleted until that review.**
