# Documentation Audit — SKILL.md / README.md vs Reality

> **Post-cleanup note (2026-05-15).** This document is the snapshot that
> drove the cleanup. The Dead Code Audit / Open Questions below describe
> what was **found**. The items listed there have been actioned across
> Waves A–E — see [dead_code_register.md](dead_code_register.md) for the
> current `fixed` / `kept` / `guarded` status of each.


> Findings collected while building `user_capabilities.md`. Each row cites the
> doc claim, the actual code, and a recommended correction.
>
> This list will be **re-checked at the end of every phase**. The aim is to
> drive the docs to match reality, not the other way around.

---

## A. Tab count

| Source | Claim | Reality |
|--------|-------|---------|
| [SKILL.md:177](../SKILL.md:177) | "responsive horizontal-scroll tab nav for 9 tabs on mobile" | **6 tabs** are actually rendered ([index.html:744-754](../templates/index.html:744)): `match-centre`, `team`, `next`, `leaderboard`, `members`, `admin`. |
| [Static/mc_hub.js:7](../Static/mc_hub.js:7) | "9-tab row now scrolls horizontally" | Same — 6 tabs in the live HTML. |

**Recommendation:** update both docs to "6 tabs" or, if a Points/History/Matches
tab was intended, restore them in `index.html`.

---

## B. Points tab — claimed live, actually dead

| Source | Claim | Reality |
|--------|-------|---------|
| [SKILL.md:339](../SKILL.md:339) | `players.points` is "Display in Points tab" | The Points tab is **not in the rendered tab list**. `_buildPointsTab()` exists in [index.html:569](../templates/index.html:569) and [Static/ipl_glue.js:462](../Static/ipl_glue.js:462) but has no entry point. `switchTab("points")` is never called. |

**Recommendation:** either delete the dead UI code and the
`/api/player-points/<n>` endpoint (if no external consumer) — or restore the
tab. Decision goes in Phase 6.

---

## C. History tab — partial drift

| Source | Claim | Reality |
|--------|-------|---------|
| [SKILL.md:158](../SKILL.md:158) | `/api/history/<n>` returns "User's week-by-week team history" | Endpoint is alive and consumed. **However** the *History tab UI* (`_buildHistoryTab` at [index.html:498](../templates/index.html:498)) is no longer rendered — week navigation is now embedded in This Week / Next Week pre-fill logic instead. |

**Recommendation:** keep the endpoint, delete the unused tab builder.

---

## D. Rollover deadline label — wrong time zone in UI

| Source | Claim | Reality |
|--------|-------|---------|
| [README.md:108](../README.md:108) / [SKILL.md:109](../SKILL.md:109) | "Monday 14:00 UTC (= 16:00 SAST)" | Backend agrees (`DEADLINE_HOUR = 14` in [config.py:40](../config.py:40)). |
| **[index.html:491](../templates/index.html:491)** | UI shows `"Locks Mon 14:00 IST"` next to the Next Week label | **Bug.** 14:00 UTC is **19:30 IST**, not 14:00 IST. The user is being shown the UTC time but with an IST suffix. |

**Recommendation:** fix the UI label — either to "Mon 14:00 UTC" or to a
correctly-converted IST/SAST string. This is a user-visible defect, not a
documentation issue, and should be raised against the frontend.

---

## E. `cricbuzz_discovery.py` — missing from SKILL.md architecture diagram

| Source | Claim | Reality |
|--------|-------|---------|
| [SKILL.md §2 Dependency Graph](../SKILL.md) | Lists `scoring_engine`, `rollover_engine`, `fuzzy_match` under `logic/` | [logic/cricbuzz_discovery.py](../logic/cricbuzz_discovery.py) (722 LOC) is **not in the graph** even though it's used by `tasks.py` and the `/api/sync-now` flow. |
| [logic/__init__.py:9-12](../logic/__init__.py:9) | Submodule list in the package docstring | Also omits `cricbuzz_discovery`. |

**Recommendation:** add `cricbuzz_discovery` to both. Also re-examine whether
it really belongs in `logic/` given that it performs HTTP I/O (violates the
"only stdlib, no side effects" rule the package was created to enforce).

---

## F. `_generate_dynamic_player` — omitted from `logic/__init__.py` docstring

| Source | Claim | Reality |
|--------|-------|---------|
| [logic/__init__.py:12](../logic/__init__.py:12) | Lists `_norm`, `_build_player_index`, `_fuzzy_match`, `_fuzzy_fielder` as the public surface of `fuzzy_match` | Missing `_generate_dynamic_player`, which SKILL.md correctly identifies as the v1.1.0 resilience addition. |

**Recommendation:** small one-line patch to the package docstring.

---

## G. Two parallel player resolvers

| Source | Claim | Reality |
|--------|-------|---------|
| [SKILL.md §10 Player Matching](../SKILL.md) | Describes a single 6-tier resolver | There are **two**: `base.resolve_player_id` (used by `routes.py`) and `logic/fuzzy_match._fuzzy_match` (used by `scraper.py`). They share the *concept* but maintain separate nickname maps and tier definitions. |

**Recommendation:** decide which one is canonical, delete the other, or
factor the nickname map and thresholds into a shared module both can import.
Tracked as a structural concern in [base.md](base.md).

---

## H. Duplicate path constants

| Source | Claim | Reality |
|--------|-------|---------|
| [SKILL.md §4.1](../SKILL.md) | `config.py` is "the ground truth" for paths | `BASE_DIR` and `DATA_DIR` are **redefined** in [base.py:39-40](../base.py:39) from `__file__` rather than imported. Same value today, fragile if any file moves. |

**Recommendation:** in `base.py`, replace the re-derivation with
`from config import BASE_DIR, DATA_DIR`.

---

## I. Orphaned files in the project root

| File | Status |
|------|--------|
| `ipl_glue.js` (root, v7.7, 875 lines) | **Orphan.** [index.html:822](../templates/index.html:822) loads `/static/ipl_glue.js` (= [Static/ipl_glue.js](../Static/ipl_glue.js), v7.8, 775 lines). The root copy is older and not served. |
| `A _ Sticky budget _ table picker.html` | **Orphan.** Not referenced anywhere. Looks like a discarded picker prototype. |

**Recommendation:** delete both after Phase 6 confirms no hidden references.

---

## J. README troubleshooting — references fixes for code that has since moved

| Source | Claim | Reality |
|--------|-------|---------|
| [README.md:218](../README.md:218) | "v7.7 _patchXiGrid()" template injection | Live `Static/ipl_glue.js` is v7.8 ([line 2](../Static/ipl_glue.js:2)). The patch is still present, but the README anchor version is one behind. |
| [README.md:217](../README.md:217) | "Fixed in v13.2" startup wipe bug | `SERVER_VER = "13.3"` ([config.py:47](../config.py:47)). README never mentions 13.3's `start_daily_discovery_scheduler` hook. |

**Recommendation:** README should treat the troubleshooting table as a
versioned list of **what's fixed at the current version**, with old anchors
moved to a CHANGELOG.

---

## K. APP_VERSION drift

| Source | Claim | Reality |
|--------|-------|---------|
| [README.md:6](../README.md:6) | `Current version: 2.1.0-stable` | [config.py:44](../config.py:44) declares `APP_VERSION = "2.3.0"`. |
| [SKILL.md:7](../SKILL.md:7) | `APP_VERSION: 2.2.0-match-centre` | Same — config says 2.3.0. |

**Recommendation:** any doc that prints a version string should read it from
`/api/version` (live) or from `config.APP_VERSION` (build-time), not from a
hard-coded literal in the doc.

---

## L. The `Phase 9 → Daily auto-sync` model is undocumented in README

- [config.py:71-79](../config.py:71) describes Phase 9's daily auto-sync
  architecture in detail.
- [.github/workflows/daily_sync.yml:1-23](../.github/workflows/daily_sync.yml:1)
  documents the two-tier model (local discovery + cloud scrape) in its header.
- **README does not mention it at all.** A user reading README would still
  believe the only refresh path is "run `scraper.py` manually".

**Recommendation:** add a "Daily Sync" section to README pointing at the
APScheduler job in `tasks.py` and the cloud workflow.

---

---

## M. `apply_multiplier` and `debug_calc_pts` are dead code

| Source | Claim | Reality |
|--------|-------|---------|
| [SKILL.md:82, 115](../SKILL.md) | `apply_multiplier` and `debug_calc_pts` are part of `logic/scoring_engine.py`'s public API | **Neither function is imported anywhere.** `routes.py` reimplements the multiplier with an inline ternary at [routes.py:226, 398](../routes.py:226). The `/api/audit-scores/<n>` endpoint that `debug_calc_pts` was written for does its own inline trace. |

About **200 lines of dead code** in
[logic/scoring_engine.py:127-329](../logic/scoring_engine.py:127). See
[scoring_engine.md](scoring_engine.md) Dead Code Audit for the full picture.

**Recommendation:** either refactor the two callers to use the helpers
(saves ~30 lines in `routes.py` and gives the audit endpoint a free
per-component breakdown), or delete the dead functions.

---

## N. Duplicate `_normalise_overs` implementations

| Source | Claim | Reality |
|--------|-------|---------|
| [db_manager.py:8](../db_manager.py:8) (comment) | "`_normalise_overs` re-export removed (server.py now imports from logic/)" | True for `server.py`, but **`scraper.py` has its own private copy** at [scraper.py:83-88](../scraper.py:83). It wraps the result in `round(_, 4)`; the logic-engine version does not. |

The two implementations are not bit-for-bit identical. If the rule
for "balls within an over" ever changes, two places need editing.

**Recommendation:** delete the scraper's copy, import from
`logic.scoring_engine`.

---

## Summary

14 drift findings spanning:

- **3 user-visible defects** (Points tab unreachable, History tab unreachable, IST/UTC label bug — item D is the most urgent).
- **2 file orphans** safe to delete.
- **3 architectural smells** worth tracking (duplicate resolvers, duplicate path constants, duplicate `_normalise_overs`).
- **1 dead-code cluster** (~200 lines in `scoring_engine.py`).
- **5 doc-only updates** that don't change behaviour but improve onboarding.

The context files produced in Phases 2-7 will refer back to this audit
whenever they describe a feature that's affected.
