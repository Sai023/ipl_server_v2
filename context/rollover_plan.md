# rollover_plan.py — When Each Week Locks, and the Weekly Handover

> Added 2026-11-20 (Phase 1.5 of the SA20 readiness plan). This is the pure half; the database half is `reconcile_competition` & friends in [db_manager.md](db_manager.md), the HTTP half is `POST /api/rollover` and the admin `schedule` action in [routes.md](routes.md).

## What it does (business view)

The league has a rhythm: every week each member's **draft** for the coming week becomes the **XI that plays**, and from that moment the draft is closed. Until now that rhythm lived in one function that moved everybody one week per call, was triggered by whatever remembered to call it, and **never promoted the pre-season draft to week 1** (so week 1 would have scored nothing for anyone).

`rollover_plan.py` holds the rules of the new rhythm, with no database and no clock of its own:

1. **When does each week lock?** Sai's decisions (2026-11-19): **week 1 locks 60 minutes before the opener; every later week locks on Monday at 14:00 UTC (16:00 SAST)**, the latest such Monday that is at least 30 minutes before the week's first match.
2. **Which handover is due now?** A *transition* is applied once its lock has passed, strictly in order, one at a time; a long outage is caught up week by week.
3. **Is picking locked right now?** From a lock until its handover has run, picking is closed (fail closed). After the handover the next week opens.

## Vocabulary

| Word | Meaning |
|---|---|
| lock | the instant a week's XI is fixed; picks are refused from then until the handover has run |
| transition | what happens at a lock: **start** (week 1: the pre-season draft becomes the XI that plays week 1) or **rollover** to week k ≥ 2 (every member's draft becomes the XI that plays week k) |
| schedule | the lock of every week: `[{week_no, lock_at_utc, first_match_utc}]` (table `competition_weeks`) |
| handled | the weeks whose transition has been applied (the ledger, table `rollover_log`) |
| managed | a competition that has a stored schedule; one without keeps the old Monday-14:00 rules |

## The SA20 2027 schedule (the real fixtures, `tests/fixtures/sa20_2027_calendar.json`)

| Week | Locks (UTC) | In SAST |
|---|---|---|
| 1 | Sun 17 Jan 2027 12:30 | 14:30 (opener 13:30 UTC less 60 min) |
| 2 | Mon 18 Jan 14:00 | 16:00 |
| 3 | Mon 25 Jan 14:00 | 16:00 |
| 4 | Mon 1 Feb 14:00 | 16:00 |
| 5 | Mon 8 Feb 14:00 | 16:00 |
| 6 | Mon 15 Feb 14:00 | 16:00 |

Week 1 (the opener weekend) and week 2 therefore lock 25.5 hours apart: week 1 is the one-off pre-season draft, week 2 is the first Monday lock.

## Inputs / Outputs

- `build_schedule(first_match_by_week) → {"rows": [...], "errors": [...]}` — locks from the first match of each week (week 1 = first match − 60 min, later weeks = `monday_lock_on_or_before(first match − 30 min)`).
- `validate_schedule(rows, max_weeks=None) → [error, ...]` — weeks 1..N with none missing and not more than the competition has; locks readable and strictly increasing; each lock ≥ 30 min before its week's first match; a week never locks before the previous week has started.
- `schedule_transitions(rows, max_weeks=None)` — `start` for week 1, `rollover` for each later week, in order.
- `next_due(rows, handled, now, status, max_weeks, auto_activate, force) → transition | None` — the first unhandled transition, if its lock has passed (`force` ignores the lock time, never the status). Status rules: `active` may run anything; `upcoming` may only *start*, and only if `auto_activate`; `completed` never runs. An unreadable lock time is never guessed.
- `lock_state(rows, handled, now, status, max_weeks) → {managed, locked, reason, next_lock, awaiting, applied_to, season_over}` — `awaiting` is set while a lock has passed and its handover has not run; `next_lock` is then still the lock that passed, so the page shows "locked" at once and the countdown moves on when the handover has run.

## What a transition does (database half)

| | start (week 1) | rollover (week k ≥ 2) |
|---|---|---|
| each member's row | week 1: `tw` (the XI that plays) := the draft `nw` if there is one, else the XI already there | new row for week k: `tw = nw` := the member's draft if there is one, else last week's XI (the *carry*) |
| a member with no row | an empty XI for week 1 (so nobody is left out) | an empty row for week k |
| the draft afterwards | unchanged (it pre-fills the draft for week 2) | the XI that was just promoted (pre-fills the draft for week k+1) |
| competition status | `upcoming` → `active` when `auto_activate` is on (the SA20 plan) | — |
| refuses | if rows for a week beyond 1 already exist | — |

Each transition is **one transaction** under a row lock on the competition, recorded **once** in the ledger with what it replaced (so it can be undone). Two triggers at the same instant, or a retry after a half-finished attempt, can therefore neither apply a week twice nor leave members in different weeks.

## Key business rules it enforces

1. Week 1 locks an hour before the opener, later weeks Monday 14:00 UTC. 2. Handovers run in order and at most once each. 3. An upcoming competition never starts by itself unless `auto_activate` is on (SA20 will: "SA20 auto-activates at the week-1 lock with an alert"). 4. From a lock until its handover has run, nobody can save (fail closed). 5. After the last scheduled week has been handed over, picking is closed for good ("The final week is under way — there is no next week to pick a team for."): there is no week 7.

## Called by / Calls into

- Called by `db_manager.py` (`set_schedule`, `get_lock_state`, `reconcile_competition`, `undo_transition`), `routes.py` (`/api/state` deadline, `/api/save-next-week` lock, the admin `schedule` action). Calls nothing: stdlib `datetime` only.
- Tests: [tests/test_rollover_plan.py](../tests/test_rollover_plan.py) (55 pure tests incl. a 40-seed replay against an independent oracle), [tests/test_rollover_core.py](../tests/test_rollover_core.py) and [tests/test_rollover_safety.py](../tests/test_rollover_safety.py) (database: start/rollover semantics, failure atomicity, four concurrent triggers, dry run, undo, a randomised season), [tests/test_rollover_guards.py](../tests/test_rollover_guards.py) (the real routes).

## Known limits / things to know

- A save that is *in flight* at the lock instant may land on the previous week's draft (the 423 fires from the lock instant; a request already past that check completes). Documented, accepted.
- A competition with **no schedule** is never rolled (`POST /api/rollover` answers a 200 no-op saying why) and keeps the old Monday-14:00 pick lock. There is no "adopt a running competition" path (starting a schedule on a competition already past week 1 is refused): not needed for SA20.
- The old rollover also fuzzy-resolved player names inside the XI it carried; the new one does not (saves already resolve and validate every id; an id missing from the roster is recorded as a warning in the ledger, never blocks).
- `rollover_log.members` and the per-member before-images live in `detail_json`; an *undone* week stays in the ledger marked `undone_at` and is **held**: triggers skip it, picking stays locked, and only a forced run (admin + `confirm`) applies it again. Undo (a database method for now; the Ops card in Phase 2 will call it) refuses to delete a row in which a member has saved a new draft since the handover, unless forced.
- The page learns a handover happened from the state tag (`_saved:<slug>`), which every handover and every schedule change moves.

## Dead Code Audit

None. Every public function is used by `db_manager.py` or `routes.py`; `scheduled_selections_locked` is the tuple form of `lock_state` used by the tests (kept next to `selections_locked` so the two stay comparable).

## Open Questions

1. Triggers: the handover is currently triggered only by `POST /api/rollover` (scheduler token or admin). The redundant triggers (Supabase clock, GitHub cron, a lazy trigger on any request, a watchdog + Telegram alert) are Phase 2 of the plan.
2. A member who joins mid-season gets an empty XI for the weeks they missed (0 points) and a row from the next handover on.
