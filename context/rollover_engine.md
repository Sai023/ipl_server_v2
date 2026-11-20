> **2026-11-20 - read this first.** The weekly handover is now driven by a stored **lock schedule** and a **ledger** ([rollover_plan.md](rollover_plan.md)): `POST /api/rollover` applies every overdue week, in order, one all-or-nothing transaction each, and a competition with no schedule is never rolled. `rollover_refusal()` (below, historical) was **removed** in v1.2.0: its rules (only an active competition, forced = admin + `confirm`) moved into `rollover_plan.next_due` and the route. This module keeps `last_monday_deadline`, `already_rolled` and `selections_locked` **only for a competition that has no stored schedule**, and `pick_active_team`, which `db_manager.reconcile_competition` uses to turn a draft into the XI. 2026-11-19 (earlier): the browser timer (`setTimeout` in `Static/ipl_glue.js`, `IplRollover`, `IplApi.rollover`) and the red admin button "Simulate Monday rollover" are gone; sections below that describe them are historical.

# rollover_engine.py — The Weekly Lock-In Clock

## What it does (business view)

`rollover_engine.py` answers three time-keeping questions for the league:

1. **"When was the most recent Monday at 14:00 UTC?"** That's the moment
   every member's "next week draft" is supposed to lock into "this week".
2. **"Have we already processed that rollover?"** Idempotency guard — if
   the server has bounced twice on a Monday afternoon, we mustn't roll
   the season forward twice.
3. **"Which team should we promote into this week — the new draft, or
   keep the old one?"** If a member never set a next-week draft, we
   carry their current XI forward unchanged.

That's the entire file. Three small pure functions, ~60 lines of real code,
no I/O, no project imports.

## Where it sits in the flow

Since 2026-11-20: `pick_active_team` is called by `db_manager._apply_transition`; `last_monday_deadline` by `routes.api_get_state` and `already_rolled` / `selections_locked` by `routes.api_save_next_week` and `api_get_state`, all three only for a competition with no stored lock schedule (`db.get_lock_state` returns `None`). The weekly handover itself is `POST /api/rollover` -> `db.reconcile_competition` ([rollover_plan.md](rollover_plan.md)).

## Inputs / Outputs

### `last_monday_deadline(now, deadline_hour, deadline_min) → datetime`
- **In:** current UTC time, the deadline hour, the deadline minute.
- **Out:** a UTC-aware `datetime` for the most recent Monday at
  `deadline_hour:deadline_min` that is **at or before** `now`. Spans
  week boundaries correctly: a query on Sunday returns *last* Monday,
  not next.

### `already_rolled(last_rollover_iso, lmd) → bool`
- **In:** the ISO string stored in the `meta` table under
  `_last_rollover`, plus the deadline returned by the function above.
- **Out:** `True` if the recorded rollover is at or after `lmd` — i.e.
  we've already processed this Monday's deadline.
- Bad ISO strings are silently treated as "haven't rolled yet" — the
  caller is then free to attempt the roll.

### `pick_active_team(nw_team_json, nw_cap, nw_vc, tw_team_json, tw_cap, tw_vc, jloads) → (team_json, cap_id, vc_id)`
- **In:** the member's next-week draft and their current this-week XI,
  plus a JSON loader function (passed in rather than imported — preserves
  the "no project imports" rule).
- **Out:** the triple of `(team_json, cap, vc)` to write as **both** the
  new `tw_*` and new `nw_*` columns.
- **Rule:** if the next-week draft is **empty** (`[]`), carry this-week
  forward. Otherwise, promote the draft.

## Key business rules it enforces

1. **One league heartbeat at 14:00 UTC on Mondays.** The whole season is
   timed against this single tick. `DEADLINE_HOUR` is owned by `config.py`;
   `rollover_engine.py` is given the value, never decides it.
2. **Idempotency.** Reading `_last_rollover` and comparing to `lmd`
   prevents double-processing. This is what makes a browser-triggered
   rollover safe even if a tab in another time zone fires the same
   request a few minutes later.
3. **No-draft fallback is "carry forward, don't blank out".** If a member
   forgets to set their next-week draft, their old XI continues to score —
   they aren't dropped to zero players for the new week.

## Called by / Calls into

- **Called by:** `routes.py` ([routes.py:59-60](../routes.py:59),
  [routes.py:960, 962, 978](../routes.py:960)).
- **Calls into:** `datetime`, `timezone`, `timedelta` from stdlib. Zero
  project imports.

## Supports which user capabilities

From [user_capabilities.md](user_capabilities.md):

- **§4.2 / §8.5 Dev Tools — Simulate Monday rollover** — REMOVED 2026-11-19 (a forced rollover is now `curl` only, with an admin session and `confirm=<slug>:<week>`).
- **§10.2 Auto-rollover at Monday 14:00 UTC** — the browser timer was REMOVED 2026-11-19; the server alone will own it (rollover automation plan).

## `rollover_refusal()` - REMOVED in v1.2.0 (2026-11-20)

v1.1.0 (2026-11-19) added a pure rule that stopped a rollover of an upcoming / not-yet-started competition and demanded an admin + `confirm=<slug>:<week>` for a forced one. Its job is now done by the schedule: `rollover_plan.next_due` only returns a transition that is due and allowed for the competition's status (an upcoming competition only *starts*, and only with auto-activation), and the route keeps the forced rules (signed-in admin, `confirm=<slug>:<week the forced run would apply>`, checked inside the transaction). The same afternoon's other fix stays: `db.get_meta_comp` no longer lets every competition inherit the legacy un-suffixed meta keys (only `ipl_2026` does).

## Dead Code Audit

| Symbol | Verdict |
|--------|---------|
| `last_monday_deadline` | **Live**, only for a competition with no stored lock schedule (`routes.api_get_state`). |
| `already_rolled` | **Live**, only inside `selections_locked` (the no-schedule rule). |
| `selections_locked` | **Live**, only for a competition with no stored lock schedule (`routes.api_save_next_week`). |
| `pick_active_team` | **Live**: `db_manager._apply_transition`. |

If every competition is one day given a schedule at creation, the first three can go together.

## Open Questions

1. **The docstring of `last_monday_deadline` has a wrong time-zone label.**
   [rollover_engine.py:51](../logic/rollover_engine.py:51) says
   *"`deadline_hour` — hour of the Monday lock (14 for 14:00 SAST)"*.
   That's wrong. 14:00 in the code is **UTC** (= 16:00 SAST). Fixing
   the docstring is a one-line change. Tracked under
   [docs_audit.md item D](docs_audit.md) alongside the user-visible IST
   mislabel in `index.html`.
2. **`pick_active_team` takes `jloads` as a callable.** The "no project
   imports" rule of the `logic/` package means it can't `from base import
   _jloads`, so it asks the caller to inject a JSON loader. This is fine,
   but worth noting that if a caller injects a *different* loader (one
   that returns `[None, None]` instead of `[]` for empty), the
   "carry-forward" branch breaks silently. A defensive `isinstance(_,
   list) and _ != []` would make the rule explicit.
3. **No unit tests.** Like `scoring_engine.py`, this is the perfect file
   to test in isolation — pure functions, three branches each. A few
   parametrised tests would lock in the Monday-deadline edge cases
   (Sunday-just-before-midnight, exact-on-Monday-14:00, etc.).
