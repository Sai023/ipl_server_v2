"""
BCL Fantasy - Ops plan                                       ops_plan v1.0.0
===========================================================================
Phase 2 of the SA20 readiness plan: what the clocks, the watchdog and the Ops card need to know, and when an admin has to be told.

Pure functions only: stdlib plus logic.rollover_plan (also pure), zero other project imports. The database, the HTTP routes and Telegram are somebody else's job
(db_manager, routes, ops_notify); this module only DECIDES.

Vocabulary
----------
state       what ONE managed competition needs right now (competition_ops_state): is a handover due, is a lock past with nothing allowed to run, how late is it.
clock       anything that calls POST /api/ops/tick: "app" (the server's own clock: a thread that ticks soon after boot, so a visit that wakes a sleeping service
            catches a missed handover up at once, and then at the rhythm), "supabase", "pc", "github". Each leaves a heartbeat "tick:<source>"; the heartbeat
            table is how the Ops card and the watchdog know a clock is alive.
busy        the windows in which the clocks tick every minute (from 48 h before a lock until its handover has run); otherwise they tick every 30 minutes. The
            tick answer carries the rhythm, so it is a setting of the server, not a schedule baked into a clock (Render's free tier has 750 hours a month: an
            always-on minute clock would use about 744 of them).
incident    a condition that stays true until fixed (a late handover, a competition that is locked but cannot start, a clock that went silent). One message
            when it starts, "still failing" at most every 30 minutes, "resolved" when it clears.
event       something that happened once (a week went live, a competition switched itself on): one message, never repeated.

Public API
----------
competition_ops_state(comp, now) -> dict
    `comp` = {slug, name, status, max_weeks, rows, handled, held, auto_activate} (rows = the stored lock schedule, handled = weeks in force, held = weeks an admin undid).
    Returns {slug, name, status, managed, applied_to, next_lock, season_over, due, awaiting, blocked, overdue_s}.
clocks_from_heartbeats(heartbeats, now) -> [{source, last_at, age_s, detail}]       only the "tick:<source>" rows, newest information first by source name
clock_rhythm(states, now) -> {"mode": "busy"|"idle", "next_tick_in_s", "reason"}
health(states, clocks, now, rhythm) -> {"level": "green"|"orange"|"red", "reasons": [...]}
build_snapshot(comps, heartbeats, now) -> {"now", "states", "clocks", "rhythm", "health"}
forget_unsent(state, messages) -> new_state          after a failed send, so a new incident or an event is raised again by the next call
evaluate_alerts(snapshot, applied, state, now) -> (messages, new_state)
    `applied` = the newest ledger rows [{slug, name, kind, to_week, applied_at, members, summary, activated}]; `state` = what the last call returned (or {}).
    Messages are {"key", "kind": "new"|"still"|"resolved"|"event", "severity": "red"|"orange"|"ok", "text"}; texts carry display names only, never an email or a secret.
"""

from datetime import timedelta

from logic import rollover_plan as rp

LATE_AFTER_S = 120               # a handover still not done this long after its lock is RED (the plan: "2 min after a lock")
CLOCK_SILENT_AFTER_S = 180       # inside a busy window, a clock that has ticked before and has been quiet this long is ORANGE (the plan: "3 minutes")
BUSY_BEFORE_LOCK_S = 48 * 3600   # the clocks tick every minute from this long before a lock until its handover has run (a lock that has passed is "waiting")
TICK_BUSY_S = 60
TICK_IDLE_S = 30 * 60
STILL_FAILING_EVERY_S = 30 * 60  # a failing condition is repeated at most this often
EVENT_MAX_AGE_S = 24 * 3600      # a handover older than this is never announced (turning alerts on must not replay the history)
ANNOUNCED_KEEP = 40              # how many announced events are remembered

TICK_PREFIX = "tick:"


def _minutes(seconds):
    return max(0, int(seconds // 60))


def _hhmm(iso_text):
    d = rp.parse_utc(iso_text)
    return d.strftime("%H:%M") if d else "?"


def competition_ops_state(comp, now):
    """What one competition needs right now. A competition with no stored lock schedule is not managed: nothing is ever due for it."""
    now = rp.parse_utc(now)
    status = (comp.get("status") or "").strip().lower()
    slug = comp["slug"]
    out = {"slug": slug, "name": comp.get("name") or slug, "status": status, "managed": False, "applied_to": 0, "next_lock": None,
           "season_over": False, "due": None, "awaiting": None, "blocked": None, "overdue_s": 0}
    rows = comp.get("rows") or []
    if not rows:
        return out
    out["managed"] = True
    handled, held = set(comp.get("handled") or ()), set(comp.get("held") or ())
    max_weeks = comp.get("max_weeks")
    ls = rp.lock_state(rows, handled, now, status, max_weeks=max_weeks)
    out["applied_to"], out["season_over"], out["awaiting"] = ls["applied_to"], ls["season_over"], ls["awaiting"]
    if ls["next_lock"]:
        out["next_lock"] = rp.iso(ls["next_lock"])
    t = rp.next_due(rows, handled, now, status, max_weeks=max_weeks, auto_activate=bool(comp.get("auto_activate")))
    if t is not None and t["to_week"] in held:
        t = None                                                   # an admin undid this week: no trigger applies it again by itself
    if t is not None:
        out["due"] = {"kind": t["kind"], "to_week": t["to_week"]}
    if ls["awaiting"]:
        if ls["next_lock"]:
            out["overdue_s"] = max(0, int((now - ls["next_lock"]).total_seconds()))
        if out["due"] is None:
            week = ls["awaiting"]["to_week"]
            if week in held:
                out["blocked"] = "week %d was undone by an admin and is held, so picks stay locked until it is applied again" % week
            elif status == "upcoming":
                out["blocked"] = "it is upcoming and does not start itself, so picks stay locked"
            else:
                out["blocked"] = "week %d may not run while it is %s" % (week, status or "in an unknown state")
    return out


def clocks_from_heartbeats(heartbeats, now):
    """The clocks (rows named tick:<source>) with their age. Other heartbeats (who last asked for a rollover) are not clocks."""
    now = rp.parse_utc(now)
    out = []
    for h in heartbeats or []:
        source = str(h.get("source") or "")
        if not source.startswith(TICK_PREFIX):
            continue
        at = rp.parse_utc(h.get("last_at"))
        out.append({"source": source[len(TICK_PREFIX):], "last_at": rp.iso(at) if at else None,
                    "age_s": max(0, int((now - at).total_seconds())) if at else None, "detail": h.get("detail") or ""})
    return sorted(out, key=lambda c: c["source"])


def clock_rhythm(states, now):
    """How often the clocks should tick: every minute while a handover is waiting or a lock is less than 48 hours away, every 30 minutes otherwise."""
    now = rp.parse_utc(now)
    for s in states:
        if not s["managed"] or s["status"] not in ("active", "upcoming") or s["season_over"]:
            continue
        if s["due"] or s["awaiting"]:
            return {"mode": "busy", "next_tick_in_s": TICK_BUSY_S, "reason": "%s: a handover is waiting" % s["name"]}
        lock = rp.parse_utc(s["next_lock"])
        if lock and now >= lock - timedelta(seconds=BUSY_BEFORE_LOCK_S):
            return {"mode": "busy", "next_tick_in_s": TICK_BUSY_S, "reason": "%s: the next lock is within 48 hours" % s["name"]}
    return {"mode": "idle", "next_tick_in_s": TICK_IDLE_S, "reason": "no lock within 48 hours"}


def _silent_clocks(clocks, rhythm):
    """The clocks that have ticked before, are not the in-server one, and have been quiet too long while the rhythm is busy."""
    if rhythm["mode"] != "busy":
        return []
    return [c for c in clocks if c["source"] != "app" and c["age_s"] is not None and c["age_s"] >= CLOCK_SILENT_AFTER_S]


def health(states, clocks, now, rhythm):
    """green / orange / red, with the reasons. RED: a handover is late (or a lock has passed that cannot run). ORANGE: a clock has gone quiet."""
    red, orange = [], []
    for s in states:
        if not s["managed"] or s["status"] == "completed" or s["season_over"]:
            continue
        if s["due"] and s["overdue_s"] >= LATE_AFTER_S:
            red.append("%s: the week %d handover is %d min late" % (s["name"], s["due"]["to_week"], _minutes(s["overdue_s"])))
        elif s["blocked"] and s["overdue_s"] >= LATE_AFTER_S:
            red.append("%s: locked %d min ago but %s" % (s["name"], _minutes(s["overdue_s"]), s["blocked"]))
    for c in _silent_clocks(clocks, rhythm):
        orange.append("no tick from %s for %d min" % (c["source"], _minutes(c["age_s"])))
    return {"level": "red" if red else ("orange" if orange else "green"), "reasons": red + orange}


def forget_unsent(state, messages):
    """After a send that failed: a new incident is taken back (so the next call raises it again) and an event is un-announced (so the next call announces it again).
    A reminder or a "resolved" is not retried (the next reminder is half an hour away, and a resolved message is not worth a loop). Returns a new state."""
    incidents = {k: dict(v) for k, v in ((state or {}).get("incidents") or {}).items()}
    announced = list((state or {}).get("announced") or [])
    for m in messages or []:
        if m.get("kind") == "new":
            incidents.pop(m["key"], None)
        elif m.get("kind") == "event":
            key = m["key"][len("applied:"):] if str(m["key"]).startswith("applied:") else m["key"]
            announced = [a for a in announced if a != key]
    return {"incidents": incidents, "announced": announced}


def build_snapshot(comps, heartbeats, now):
    now = rp.parse_utc(now)
    states = [competition_ops_state(c, now) for c in comps]
    clocks = clocks_from_heartbeats(heartbeats, now)
    rhythm = clock_rhythm(states, now)
    return {"now": rp.iso(now), "states": states, "clocks": clocks, "rhythm": rhythm, "health": health(states, clocks, now, rhythm)}


def _conditions(snapshot):
    """Every incident that is true right now: {key: (severity, text, resolved_text, label)}."""
    now = rp.parse_utc(snapshot["now"])
    out = {}
    for s in snapshot["states"]:
        if not s["managed"] or s["status"] == "completed" or s["season_over"]:
            continue
        if s["due"] and s["overdue_s"] >= LATE_AFTER_S:
            w = s["due"]["to_week"]
            out["late:%s:%d" % (s["slug"], w)] = (
                "red", "RED: %s week %d handover is %d min late (it was due at %s UTC)." % (s["name"], w, _minutes(s["overdue_s"]), _hhmm(s["next_lock"])),
                "%s week %d handover has now run." % (s["name"], w))
        elif s["blocked"] and s["overdue_s"] >= LATE_AFTER_S:
            w = (s["awaiting"] or {}).get("to_week", 0)
            out["blocked:%s:%d" % (s["slug"], w)] = (
                "red", "RED: %s locked at %s UTC but %s." % (s["name"], _hhmm(s["next_lock"]), s["blocked"]),
                "%s is no longer blocked." % s["name"])
    for c in _silent_clocks(snapshot["clocks"], snapshot["rhythm"]):
        out["silent:%s" % c["source"]] = (
            "orange", "ORANGE: no tick from %s for %d min." % (c["source"], _minutes(c["age_s"])), "%s is ticking again." % c["source"])
    return out


def _event_message(row):
    """The one-line announcement of a handover that has run (names and counts only)."""
    s = row.get("summary") or {}
    carried = s.get("carried", 0) + s.get("kept", 0)
    empty = s.get("empty", 0) + s.get("created_empty", 0)
    members = row.get("members") or 0
    name = row.get("name") or row.get("slug")
    if row.get("kind") == "start" and row.get("activated"):
        return "OK: %s is live. Week 1 locked: %d members, %d with no XI." % (name, members, empty)
    return "OK: %s week %d is live. %d members, %d carried over, %d empty XIs." % (name, row.get("to_week") or 0, members, carried, empty)


def evaluate_alerts(snapshot, applied, state, now):
    """What an admin must be told now, and the state to remember for the next call. `state` is never changed; a new dict is returned."""
    now = rp.parse_utc(now)
    old = state or {}
    incidents = {k: dict(v) for k, v in (old.get("incidents") or {}).items()}
    announced = list(old.get("announced") or [])
    messages = []
    now_iso = rp.iso(now)

    current = _conditions(snapshot)
    for key in sorted(current):
        severity, text, resolved = current[key]
        inc = incidents.get(key)
        if inc is None:
            incidents[key] = {"since": now_iso, "last_sent": now_iso, "severity": severity, "resolved": resolved}
            messages.append({"key": key, "kind": "new", "severity": severity, "text": text})
            continue
        sent = rp.parse_utc(inc.get("last_sent")) or now
        if (now - sent).total_seconds() >= STILL_FAILING_EVERY_S:
            since = rp.parse_utc(inc.get("since")) or now
            inc["last_sent"] = now_iso
            inc["resolved"] = resolved
            messages.append({"key": key, "kind": "still", "severity": severity,
                             "text": "STILL FAILING (%d min): %s" % (_minutes((now - since).total_seconds()), text)})
    for key in sorted(set(incidents) - set(current)):
        inc = incidents.pop(key)
        messages.append({"key": key, "kind": "resolved", "severity": "ok", "text": "RESOLVED: " + (inc.get("resolved") or key)})

    for row in sorted(applied or [], key=lambda r: (str(r.get("applied_at") or ""), str(r.get("slug") or ""), r.get("to_week") or 0)):
        key = "%s:%s" % (row.get("slug"), row.get("to_week"))
        if key in announced:
            continue
        announced.append(key)
        at = rp.parse_utc(row.get("applied_at"))
        if at is None or (now - at).total_seconds() > EVENT_MAX_AGE_S:
            continue                                               # remembered, but not announced: it is history, not news
        messages.append({"key": "applied:" + key, "kind": "event", "severity": "ok", "text": _event_message(row)})
    return messages, {"incidents": incidents, "announced": announced[-ANNOUNCED_KEEP:]}
