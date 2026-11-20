"""
BCL Fantasy - Rollover plan                             rollover_plan v1.0.0
===========================================================================
When each week's XI locks, which transition is due, and whether picks are locked right now.

Pure functions only: stdlib (datetime) only, zero project imports.

2026-11-20, Phase 1.5 of the SA20 readiness plan. Sai's decisions (2026-11-19): week 1 locks 60 minutes before the opener, weeks 2 onward lock on
Monday at 14:00 UTC, and SA20 activates itself at the week-1 lock.

Vocabulary
----------
lock        The moment a week's XI is locked in. Picks are refused from then until the transition for that week has run (fail closed).
transition  What happens at a lock. "start": the pre-season draft becomes the XI that plays week 1. "rollover" to week k >= 2: every member's
            draft becomes the XI that plays week k (a member with no draft carries last week's XI).
schedule    The lock time of every week: [{"week_no": 1, "lock_at_utc": "<ISO>", "first_match_utc": "<ISO or None>"}, ...]
handled     The set of `to_week` numbers whose transition has been applied (or deliberately undone and held).

Public API
----------
parse_utc(value) -> datetime | None
build_schedule(first_match_by_week) -> {"rows": [...], "errors": [...]}
validate_schedule(rows, max_weeks=None) -> [error, ...]
schedule_transitions(rows, max_weeks=None) -> [{"kind", "to_week", "lock_at"}, ...]
next_due(rows, handled, now, status, ...) -> transition | None        what to apply now (the caller loops until None)
lock_state(rows, handled, now, status, ...) -> dict                   is picking locked, and what the countdown points at
scheduled_selections_locked(...) -> (locked, reason)                  the same answer as logic.rollover_engine.selections_locked
"""

from datetime import datetime, timedelta, timezone

WEEK1_LEAD = timedelta(minutes=60)    # week 1 locks this long before the first ball
MIN_GAP = timedelta(minutes=30)       # a lock must come at least this long before the first match of its week
LOCK_WEEKDAY = 0                      # weeks 2 onward lock on Monday ...
LOCK_HOUR, LOCK_MINUTE = 14, 0        # ... at 14:00 UTC (16:00 SAST)


def parse_utc(value):
    """A datetime or an ISO 8601 string as an aware UTC datetime, or None when it is empty or unreadable (a value with no zone is read as UTC)."""
    if isinstance(value, datetime):
        d = value
    else:
        if not value:
            return None
        try:
            d = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def monday_lock_on_or_before(moment):
    """The latest Monday 14:00 UTC at or before `moment`."""
    m = parse_utc(moment)
    c = m.replace(hour=LOCK_HOUR, minute=LOCK_MINUTE, second=0, microsecond=0) - timedelta(days=(m.weekday() - LOCK_WEEKDAY) % 7)
    return c - timedelta(days=7) if c > m else c


def validate_schedule(rows, max_weeks=None):
    """What is wrong with a schedule, as readable sentences ([] when it is sound)."""
    if not rows:
        return ["the schedule has no weeks"]
    errors, seen = [], {}
    for r in rows:
        wk = r.get("week_no") if isinstance(r, dict) else None
        if not isinstance(wk, int) or isinstance(wk, bool) or wk < 1:
            errors.append("a week number must be a whole number >= 1 (got %r)" % (wk,))
        elif wk in seen:
            errors.append("week %d appears twice" % wk)
        else:
            seen[wk] = r
    if errors:
        return errors
    weeks = sorted(seen)
    if weeks != list(range(1, len(weeks) + 1)):
        errors.append("the weeks must be 1..N with none missing (got %s)" % ", ".join(map(str, weeks)))
    if max_weeks is not None and len(weeks) > max_weeks:
        errors.append("the schedule has %d weeks but the competition has %d" % (len(weeks), max_weeks))
    locks = {wk: parse_utc(seen[wk].get("lock_at_utc")) for wk in weeks}
    firsts = {wk: parse_utc(seen[wk].get("first_match_utc")) for wk in weeks}      # None where it is not known
    for wk in weeks:
        if locks[wk] is None:
            errors.append("week %d: the lock time is missing or unreadable" % wk)
    prev = None
    for wk in weeks:
        t = locks[wk]
        if t is None:
            continue
        if prev is not None and t <= prev[1]:
            errors.append("week %d locks at or before week %d" % (wk, prev[0]))
        fm = firsts[wk]
        if fm is not None and t > fm - MIN_GAP:
            errors.append("week %d locks less than %d minutes before its first match" % (wk, MIN_GAP.total_seconds() // 60))
        pf = firsts.get(wk - 1)
        if pf is not None and t <= pf:
            errors.append("week %d locks before week %d has even started" % (wk, wk - 1))
        prev = (wk, t)
    return errors


def build_schedule(first_match_by_week, week1_lead=WEEK1_LEAD, min_gap=MIN_GAP):
    """The lock schedule for a competition from the first match of each week: week 1 locks `week1_lead` before its first match; every later week locks
    on the latest Monday 14:00 UTC that is at least `min_gap` before its first match. Returns {"rows": [...], "errors": [...]}."""
    errors, firsts = [], {}
    for wk, v in (first_match_by_week or {}).items():
        if not isinstance(wk, int) or isinstance(wk, bool) or wk < 1:
            errors.append("a week number must be a whole number >= 1 (got %r)" % (wk,))
            continue
        firsts[wk] = parse_utc(v)
    if not firsts:
        return {"rows": [], "errors": errors or ["there are no matches to build a schedule from"]}
    weeks = sorted(firsts)
    if weeks != list(range(1, len(weeks) + 1)):
        errors.append("the fixtures must cover weeks 1..N with none missing (got %s)" % ", ".join(map(str, weeks)))
    rows = []
    for wk in weeks:
        fm = firsts[wk]
        if fm is None:
            errors.append("week %d: the first match has no usable time" % wk)
            continue
        lock = fm - week1_lead if wk == 1 else monday_lock_on_or_before(fm - min_gap)
        rows.append({"week_no": wk, "lock_at_utc": iso(lock), "first_match_utc": iso(fm)})
    return {"rows": rows, "errors": errors + (validate_schedule(rows) if rows and not errors else [])}


def schedule_transitions(rows, max_weeks=None):
    """The transitions a schedule defines, in order: "start" for week 1, then "rollover" to each later week (up to max_weeks)."""
    out = []
    for r in sorted(rows or [], key=lambda x: x["week_no"]):
        wk = r["week_no"]
        if max_weeks is not None and wk > max_weeks:
            break
        out.append({"kind": "start" if wk == 1 else "rollover", "to_week": wk, "lock_at": parse_utc(r.get("lock_at_utc"))})
    return out


def _allowed(transition, status, auto_activate):
    """May this transition run for a competition in this status? An active one may; an upcoming one only starts, and only if it activates itself."""
    s = (status or "").strip().lower()
    if s == "active":
        return True
    if s == "upcoming":
        return transition["kind"] == "start" and bool(auto_activate)
    return False


def next_due(rows, handled, now, status, max_weeks=None, auto_activate=False, force=False):
    """The next transition to apply now, or None. Transitions run strictly in order, one at a time (the caller applies one, re-reads the state and asks again),
    so a long outage is caught up week by week. `force` ignores the lock time (an admin's explicit rehearsal or repair) but never the status."""
    now = parse_utc(now)
    done = set(handled or ())
    for t in schedule_transitions(rows, max_weeks):
        if t["to_week"] in done:
            continue
        if t["lock_at"] is None:
            return None                                   # an unreadable lock time is never guessed
        if not force and t["lock_at"] > now:
            return None                                   # the next one is still in the future: nothing is due
        return t if _allowed(t, status, auto_activate) else None
    return None


def lock_state(rows, handled, now, status, max_weeks=None):
    """Is picking locked right now, and what is the countdown pointing at?

    Returns {"managed": True, "locked": bool, "reason": str, "next_lock": datetime | None, "awaiting": {"kind", "to_week"} | None,
             "applied_to": int, "season_over": bool}.
    `awaiting` is set when a lock has passed and its transition has not run yet: picking stays locked until it has (fail closed)."""
    now = parse_utc(now)
    done = set(handled or ())
    trans = schedule_transitions(rows, max_weeks)
    applied_to = 0
    for t in trans:
        if t["to_week"] in done:
            applied_to = t["to_week"]
        else:
            break
    out = {"managed": True, "locked": False, "reason": "", "next_lock": None, "awaiting": None, "applied_to": applied_to, "season_over": False}
    if (status or "").strip().lower() == "completed":
        out.update(locked=True, season_over=True, reason="This competition has ended — selections are locked.")
        return out
    pending = next((t for t in trans if t["to_week"] not in done), None)
    if pending is None:
        # Every scheduled week has been handed over, so there is no next week to pick for. The final week may still be under way: say that, not "complete".
        out.update(locked=True, season_over=True, reason="The final week is under way — there is no next week to pick a team for.")
        return out
    lock = pending["lock_at"]
    if lock is None:
        out.update(locked=True, reason="This competition's lock time cannot be read — selections are locked.")    # fail closed
        return out
    out["next_lock"] = lock
    if now >= lock:
        out.update(locked=True, awaiting={"kind": pending["kind"], "to_week": pending["to_week"]},
                   reason="The weekly deadline has passed — selections are locked until the rollover has run.")
    return out


def scheduled_selections_locked(rows, handled, now, status, max_weeks=None):
    """(locked, reason): the same shape as logic.rollover_engine.selections_locked, for a competition that has a lock schedule."""
    s = lock_state(rows, handled, now, status, max_weeks)
    return s["locked"], s["reason"]
