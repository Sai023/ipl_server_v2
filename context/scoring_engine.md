# scoring_engine.py — The Official Fantasy Scoring Rulebook

> **Post-cleanup note (2026-05-15).** This document is the snapshot that
> drove the cleanup. The Dead Code Audit / Open Questions below describe
> what was **found**. The items listed there have been actioned across
> Waves A–E — see [dead_code_register.md](dead_code_register.md) for the
> current `fixed` / `kept` / `guarded` status of each.


## What it does (business view)

`scoring_engine.py` is the **single source of truth for "how do cricket
statistics become fantasy points"**. Given one player's raw match line
(runs, balls, wickets, catches, etc.), it returns a fantasy-point total
that matches what every member sees on their leaderboard.

Two principles, both critical:

1. **No multipliers here.** This file calculates the **base** points only.
   The captain ×2 / vice-captain ×1.5 boost is applied separately by the
   caller (today that's an inline expression in `routes.py` and `scraper.py`).
2. **Pure function, no dependencies.** It takes a dict in, returns a number
   out. It cannot touch the database, log anything, or call any other part
   of the project. This makes it trivially testable and impossible to
   regress accidentally from a route or DAO change.

## Where it sits in the flow

Pure-logic leaf — called whenever the system needs a fantasy-point number:

```
scraper.py  →  db.recalculate_points()  →  calc_pts()
                                             ↑
routes.py /api/audit-scores  ─────────────────┘
```

## Inputs / Outputs

- **Input:** a `dict` with the raw per-player match line. Keys it understands
  (camelCase and snake_case both accepted): `played`, `runs`, `balls`,
  `fours`, `sixes`, `wickets`, `overs`, `runsConceded`/`runs_conceded`,
  `maidens`, `catches`, `stumpings`, `runOutDirect`/`run_out_direct`,
  `runOutAssist`/`run_out_assist`, `lbwBowled`/`lbw_bowled`, `duck`,
  `gotOut`/`got_out`.
- **Output:** an integer — the player's base fantasy points for that match.

## Key business rules it enforces

These are the actual rules of the IPL Fantasy 2026 league.

### Participation
- Appearing in the XI → **+4 pts**.

### Batting
- **+1** per run, **+1 bonus** per four, **+2 bonus** per six.
- Milestones (one only, the highest that applies):
  - 100+ runs → **+16**
  - 50+ runs → **+8**
  - 30+ runs → **+4**
- **Duck penalty** (`got_out` AND 0 runs AND faced ≥1 ball) → **−2**.
- **Strike rate bonus** (only if ≥10 balls faced):
  - SR > 125 → +6
  - SR ≥ 110 → +4
  - SR ≥ 100 → +2
  - SR < 60 → −4
  - SR < 70 → −2

### Bowling
- **+25** per wicket; **+8** per LBW/bowled bonus (clamped to ≤ wickets).
- **+12** per maiden over.
- Wicket-haul bonuses (cumulative):
  - 2 wickets → +4
  - 3 wickets → +4
  - 4 wickets → +8
  - 5 wickets → +8
- **Economy bonus** (only if ≥2 overs bowled):
  - Eco > 12 → −6
  - Eco ≥ 11 → −4
  - Eco ≥ 10 → −2
  - Eco < 5 → +6
  - Eco < 6 → +4
  - Eco < 7 → +2

### Fielding
- **+8** per catch; **+4 bonus** for 3+ catches.
- **+12** per stumping; **+12** per direct run-out; **+6** per assist.

### Sanity clamps
- `runs`, `balls`, `wickets` ≤ 10, etc. — defensive clamps so a bad
  scorecard value can't push the score into nonsense territory.
- `fours` capped at `runs` (can't have more boundary-runs than total runs).
- `lbw_bowled` capped at `wickets`.

### Overs normalisation
- Cricbuzz reports overs as a decimal where the fractional part is
  **balls** (e.g. `3.5` means "3 overs and 5 balls"). The
  `_normalise_overs()` helper converts that to a proper fraction
  (`3 + 5/6 ≈ 3.833`) before any economy calculation.

### Captain / Vice-Captain multipliers
- `CAP_MULT = 2.0`, `VC_MULT = 1.5` are declared here as the league
  constants — but **the file does not apply them itself**. Callers
  multiply manually using the inline pattern
  `mult = CAP_MULT if pid == cap else (VC_MULT if pid == vc else 1.0)`.

## Called by / Calls into

- **Called by:**
  - `db_manager.py` — `recalculate_points` calls `calc_pts()` for every
    `(match_id, player_id)` row when rebuilding `player_match_points`.
  - `routes.py /api/audit-scores/<n>` — calls `calc_pts()` to recompute
    each weekly score from scratch and compare it against the stored
    `week_pts` (catches stored-vs-computed mismatch).
  - `scraper.py` — imports nothing from here directly. It has its own
    near-duplicate `_normalise_overs` at [scraper.py:83](../scraper.py:83).
- **Calls into:** `math` (stdlib only). Zero project imports.

## Supports which user capabilities

From [user_capabilities.md](user_capabilities.md):

- **§3 Match Centre** — every per-match point shown in the hub or modal
  was produced by `calc_pts()`.
- **§4.1 This Week locked XI** — implicit, via the score totals shown.
- **§5.4 Season-points badges** — implicit, via `season_pts`.
- **§6 Leaderboard** — the entire ranking is `SUM(week_pts)` where each
  `week_pts` is built from `calc_pts()` outputs.

## Dead Code Audit

| Symbol | Verdict | Notes |
|--------|---------|-------|
| `calc_pts` | **Live.** | Called by `db_manager.recalculate_points` and `routes.api_audit_scores`. |
| `_normalise_overs` | **Live** (internal). Used inside `calc_pts` and `debug_calc_pts`. | But see Open Question 1 — `scraper.py` has its own copy. |
| `CAP_MULT`, `VC_MULT` | **Live.** | Imported by `routes.py` ([routes.py:60](../routes.py:60)). |
| `apply_multiplier` | **DEAD.** Never imported anywhere. | [scoring_engine.py:127-146](../logic/scoring_engine.py:127). The two places that *should* use it (`routes.api_audit_scores` line 398, `routes.api_player_points` line 226) instead use an inline ternary. ~20 lines of code with zero callers. |
| `debug_calc_pts` | **DEAD.** Never imported anywhere. | [scoring_engine.py:151-329](../logic/scoring_engine.py:151). ~180 lines explicitly written as a step-by-step audit tracer for `/api/audit-scores/<n>`. The audit endpoint **bypasses it** and reimplements the trace inline. The SKILL.md docstring promotes `debug_calc_pts` as a public API but no real consumer exists. |

**Significant dead code: ~200 lines (apply_multiplier + debug_calc_pts).**

## Open Questions

1. **Duplicate `_normalise_overs` in `scraper.py`.** [scraper.py:83-88](../scraper.py:83)
   has a near-identical copy that wraps the result in `round(_, 4)` — the
   logic-engine version doesn't. Two consequences:
   - The scraper's stored `overs` value can differ in the 4th decimal place
     from what `calc_pts` would internally compute for the same input.
   - If the scoring rule for overs changes, two places need editing.
   Recommendation: delete the scraper's copy, import from `logic.scoring_engine`.
2. **Reinstate `debug_calc_pts` — or delete it.** Decide by use-case:
   - If the audit endpoint is meant to be the canonical "show me the trace"
     view, refactor it to call `debug_calc_pts` (saves ~30 lines in
     `routes.py` and gets per-component breakdown for free).
   - If the inline trace in `routes.py` is the canonical version, delete
     `debug_calc_pts` (saves ~180 lines here).
   Tracked alongside [docs_audit.md item B](docs_audit.md) since SKILL.md
   advertises `debug_calc_pts` as a public API.
3. **Reinstate `apply_multiplier` — or delete it.** Two inline copies of the
   same expression exist in `routes.py` (lines 226, 398). A helper that does
   one thing right is cheaper to read than two ternaries. Same trade-off as
   above.
4. **Add unit tests.** This is the only file where the cricket *rules* live.
   It's pure, deterministic, and currently has zero tests in the repo.
   Worth adding a small `test_scoring_engine.py` with the two worked
   examples already in the docstrings.
