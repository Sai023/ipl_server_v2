> **2026-11-19 - read this first.** The browser no longer schedules or sends a weekly rollover (the Monday 14:00 UTC `setTimeout` in `Static/ipl_glue.js`, `IplRollover` and `IplApi.rollover` are gone) and the red admin button "Simulate Monday rollover" is gone. They could roll a competition that had not started (reproduced on scratch data) and only ever worked for an admin who had the page open. `POST /api/rollover` now refuses a competition that is not `active` or whose week-1 anchor has not passed, and a forced rollover needs a signed-in admin plus `confirm=<slug>:<week>` (`logic/rollover_engine.rollover_refusal`, `tests/test_rollover_guards.py`). Nothing runs the rollover automatically until the new server-side catch-up is built (plan: the "Rollover automation plan"). Sections below that describe the browser timer or the button are historical.

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

Called from one place: the `/api/rollover` route handler in `routes.py`.
Since 2026-11-19 that endpoint has ONE legitimate caller: the scheduler token (the GitHub workflow, currently switched off) or a signed-in admin with curl.
The browser timer and the red "Simulate" button were removed (see the note at the top of this file).

Every call first asks `rollover_refusal()` (below) and only then goes through the three helpers.

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

## `rollover_refusal()` - when a rollover may run (v1.1.0, 2026-11-19)

A rollover moves EVERY member to the next week, so before anything else the route asks this pure function. It returns `None` (go ahead) or a refusal:

| Situation | Answer |
|---|---|
| competition status is not `active` (upcoming, completed, unknown) | 200, `rolled:false`, reason "not active" (a scheduler must not retry it) |
| no usable `week1_anchor_utc`, or `now` is before it | 200, `rolled:false`, reason "has no usable week-1 start date" / "the season has not started" |
| `force=1` without a signed-in admin (the scheduler token is not enough) | 403 |
| `force=1` without `confirm=<slug>:<week it would move to>` (or with another week) | 400 naming the exact value to send |

Found by testing: on scratch data an authorised call rolled an UPCOMING competition from week 1 to 2 (and a forced one to 3). Still true and still dormant (nothing calls it any more):
the loop is not one transaction, so a failure half way plus a retry leaves members in different weeks. That, week 1's promotion, fixture-driven lock times and the layered triggers
are the next phases of the rollover automation plan. Also fixed the same day: `db.get_meta_comp` no longer lets every competition inherit the legacy un-suffixed meta keys
(only `ipl_2026` does), so SA20 does not see IPL's `_last_rollover`.

## Dead Code Audit

| Symbol | Verdict |
|--------|---------|
| `last_monday_deadline` | **Live.** Single caller in `routes.py`. |
| `already_rolled` | **Live.** Single caller in `routes.py`. |
| `pick_active_team` | **Live.** Single caller in `routes.py`. |

**No dead code.** All three public functions have exactly one consumer.

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
