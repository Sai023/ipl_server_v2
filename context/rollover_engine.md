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
That endpoint is hit by three different triggers:

- The browser's auto-rollover timer at Monday 14:00 UTC
  ([user_capabilities.md §10.2](user_capabilities.md)).
- A user clicking **"▶ Simulate Monday 2:00 PM Rollover"** in the Dev Tools
  panel ([user_capabilities.md §4.2](user_capabilities.md)).
- A manual `curl POST /api/rollover` (admin operation).

All three paths go through the same three helpers below.

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

- **§4.2 Dev Tools — Simulate Monday rollover** — direct line: button →
  `POST /api/rollover?force=1` → `pick_active_team` decides the promotion.
- **§10.2 Auto-rollover at Monday 14:00 UTC** — the browser timer calls
  the same endpoint; `already_rolled()` prevents duplicates if multiple
  clients fire.

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
