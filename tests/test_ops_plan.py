"""Phase 2 (2026-11-22): logic/ops_plan.py, the pure rules behind the clocks, the Ops card and the admin alerts. No database, no Flask, no network.

  1. competition_ops_state: what one competition needs right now (due, waiting, blocked, how late)
  2. clocks_from_heartbeats, clock_rhythm (every minute near a lock, every 30 minutes otherwise)
  3. health (green / orange / red and why)
  4. evaluate_alerts: one message per incident, "still failing" every 30 minutes, "resolved", events announced once, history never replayed
"""
import copy
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from logic import ops_plan as ops  # noqa: E402

UTC = timezone.utc
LOCK1 = datetime(2027, 1, 17, 12, 30, tzinfo=UTC)          # the SA20 week-1 lock
LOCK2 = datetime(2027, 1, 18, 14, 0, tzinfo=UTC)
LOCK3 = datetime(2027, 1, 25, 14, 0, tzinfo=UTC)
ROWS = [{"week_no": i, "lock_at_utc": lk.isoformat(), "first_match_utc": (lk + timedelta(hours=1)).isoformat()} for i, lk in enumerate((LOCK1, LOCK2, LOCK3), start=1)]


def comp(**kw):
    base = {"slug": "sa20_2027", "name": "SA20 2027", "status": "active", "max_weeks": 3, "rows": ROWS, "handled": (), "held": (), "auto_activate": False}
    base.update(kw)
    return base


def hb(source, at, detail=""):
    return {"source": source, "last_at": at.isoformat(), "detail": detail}


class CompetitionOpsState(unittest.TestCase):
    def st(self, now, **kw):
        return ops.competition_ops_state(comp(**kw), now)

    def test_a_competition_with_no_schedule_is_never_due(self):
        s = self.st(LOCK1 + timedelta(days=9), rows=[])
        self.assertFalse(s["managed"])
        self.assertEqual((s["due"], s["awaiting"], s["blocked"], s["overdue_s"]), (None, None, None, 0))

    def test_before_the_first_lock_nothing_is_due(self):
        s = self.st(LOCK1 - timedelta(hours=5))
        self.assertTrue(s["managed"])
        self.assertEqual((s["due"], s["awaiting"], s["blocked"], s["overdue_s"], s["applied_to"]), (None, None, None, 0, 0))
        self.assertEqual(s["next_lock"], LOCK1.isoformat())

    def test_after_the_lock_the_handover_is_due_and_says_how_late(self):
        s = self.st(LOCK1 + timedelta(seconds=95))
        self.assertEqual(s["due"], {"kind": "start", "to_week": 1})
        self.assertEqual(s["awaiting"], {"kind": "start", "to_week": 1})
        self.assertEqual(s["overdue_s"], 95)
        self.assertIsNone(s["blocked"])

    def test_an_upcoming_competition_that_does_not_start_itself_is_blocked(self):
        s = self.st(LOCK1 + timedelta(minutes=10), status="upcoming")
        self.assertIsNone(s["due"])
        self.assertEqual(s["awaiting"], {"kind": "start", "to_week": 1})
        self.assertIn("does not start itself", s["blocked"])
        self.assertEqual(s["overdue_s"], 600)

    def test_an_upcoming_competition_that_starts_itself_is_due(self):
        s = self.st(LOCK1 + timedelta(minutes=1), status="upcoming", auto_activate=True)
        self.assertEqual(s["due"], {"kind": "start", "to_week": 1})
        self.assertIsNone(s["blocked"])

    def test_a_week_that_ran_moves_the_next_lock_on(self):
        s = self.st(LOCK1 + timedelta(minutes=30), handled={1})
        self.assertEqual((s["due"], s["awaiting"], s["applied_to"]), (None, None, 1))
        self.assertEqual(s["next_lock"], LOCK2.isoformat())
        s = self.st(LOCK2 + timedelta(minutes=3), handled={1})
        self.assertEqual(s["due"], {"kind": "rollover", "to_week": 2})
        self.assertEqual(s["overdue_s"], 180)

    def test_a_week_an_admin_undid_is_held_not_due(self):
        s = self.st(LOCK2 + timedelta(minutes=5), handled={1}, held={2})
        self.assertIsNone(s["due"])
        self.assertIn("undone by an admin", s["blocked"])
        self.assertEqual(s["awaiting"], {"kind": "rollover", "to_week": 2})

    def test_a_finished_season_is_not_due(self):
        s = self.st(LOCK3 + timedelta(days=3), handled={1, 2, 3})
        self.assertTrue(s["season_over"])
        self.assertEqual((s["due"], s["blocked"], s["overdue_s"]), (None, None, 0))
        s = self.st(LOCK1 + timedelta(days=30), status="completed")
        self.assertTrue(s["season_over"])
        self.assertIsNone(s["due"])

    def test_an_unreadable_lock_time_is_never_guessed(self):
        bad = [dict(ROWS[0], lock_at_utc="not a date")] + ROWS[1:]
        s = self.st(LOCK1 + timedelta(hours=3), rows=bad)
        self.assertIsNone(s["due"])
        self.assertIsNone(s["awaiting"])

    def test_the_name_falls_back_to_the_slug_and_the_input_is_untouched(self):
        c = comp(name=None)
        before = copy.deepcopy(c)
        s = ops.competition_ops_state(c, LOCK1)
        self.assertEqual(s["name"], "sa20_2027")
        self.assertEqual(c, before)


class ClocksAndRhythm(unittest.TestCase):
    def test_only_tick_rows_are_clocks(self):
        now = LOCK1
        got = ops.clocks_from_heartbeats([hb("tick:supabase", now - timedelta(seconds=41), "ok"), hb("rollover:scheduler", now, "x"), hb("tick:app", now - timedelta(seconds=12)),
                                          {"source": "tick:pc", "last_at": "garbage", "detail": None}], now)
        self.assertEqual([c["source"] for c in got], ["app", "pc", "supabase"])
        self.assertEqual({c["source"]: c["age_s"] for c in got}, {"app": 12, "pc": None, "supabase": 41})
        self.assertEqual(got[2]["detail"], "ok")
        self.assertEqual(ops.clocks_from_heartbeats(None, now), [])

    def rhythm(self, now, **kw):
        return ops.clock_rhythm([ops.competition_ops_state(comp(**kw), now)], now)

    def test_far_from_a_lock_the_clock_is_slow(self):
        r = self.rhythm(LOCK1 - timedelta(hours=48, seconds=1))
        self.assertEqual((r["mode"], r["next_tick_in_s"]), ("idle", 1800))

    def test_within_48_hours_of_a_lock_the_clock_is_fast(self):
        r = self.rhythm(LOCK1 - timedelta(hours=47, minutes=59))
        self.assertEqual((r["mode"], r["next_tick_in_s"]), ("busy", 60))
        self.assertIn("within 48 hours", r["reason"])
        self.assertEqual(self.rhythm(LOCK1 - timedelta(hours=48))["mode"], "busy", "from exactly 48 hours before")

    def test_a_waiting_handover_is_always_fast(self):
        r = self.rhythm(LOCK1 + timedelta(days=2))                       # long overdue, still waiting: the clock must not slow down
        self.assertEqual(r["mode"], "busy")
        self.assertIn("waiting", r["reason"])

    def test_after_the_handover_the_clock_slows_down_until_the_next_lock_is_near(self):
        self.assertEqual(self.rhythm(LOCK1 + timedelta(minutes=5), handled={1})["mode"], "busy")          # the Monday lock is 25 hours away
        self.assertEqual(self.rhythm(LOCK2 + timedelta(minutes=5), handled={1, 2})["mode"], "idle")        # the next one is a week away

    def test_a_finished_or_unmanaged_competition_never_makes_the_clock_fast(self):
        self.assertEqual(self.rhythm(LOCK1 - timedelta(hours=1), status="completed")["mode"], "idle")
        self.assertEqual(self.rhythm(LOCK1 - timedelta(hours=1), rows=[])["mode"], "idle")
        self.assertEqual(self.rhythm(LOCK3 + timedelta(days=1), handled={1, 2, 3})["mode"], "idle")

    def test_an_upcoming_competition_counts_too(self):
        self.assertEqual(self.rhythm(LOCK1 - timedelta(hours=20), status="upcoming", auto_activate=True)["mode"], "busy")

    def test_no_competitions_at_all_is_idle(self):
        self.assertEqual(ops.clock_rhythm([], LOCK1)["mode"], "idle")


class Health(unittest.TestCase):
    def snap(self, now, heartbeats=(), **kw):
        return ops.build_snapshot([comp(**kw)], list(heartbeats), now)

    def test_quiet_is_green(self):
        h = self.snap(LOCK1 - timedelta(days=5))["health"]
        self.assertEqual((h["level"], h["reasons"]), ("green", []))

    def test_a_handover_is_late_only_after_two_minutes(self):
        self.assertEqual(self.snap(LOCK1 + timedelta(seconds=119))["health"]["level"], "green")
        h = self.snap(LOCK1 + timedelta(seconds=120))["health"]
        self.assertEqual(h["level"], "red")
        self.assertIn("week 1 handover is 2 min late", h["reasons"][0])

    def test_a_lock_that_cannot_run_is_red_after_two_minutes(self):
        h = self.snap(LOCK1 + timedelta(minutes=5), status="upcoming")["health"]
        self.assertEqual(h["level"], "red")
        self.assertIn("does not start itself", h["reasons"][0])
        self.assertEqual(self.snap(LOCK1 + timedelta(seconds=60), status="upcoming")["health"]["level"], "green")
        self.assertEqual(self.snap(LOCK1 + timedelta(seconds=119), status="upcoming")["health"]["level"], "green")
        self.assertEqual(self.snap(LOCK1 + timedelta(seconds=120), status="upcoming")["health"]["level"], "red", "the same two-minute line as a late handover")

    def test_a_clock_is_silent_from_exactly_three_minutes(self):
        now = LOCK1 - timedelta(hours=2)
        self.assertEqual(self.snap(now, [hb("tick:pc", now - timedelta(seconds=180))])["health"]["level"], "orange")
        self.assertEqual(self.snap(now, [hb("tick:pc", now - timedelta(seconds=179))])["health"]["level"], "green")

    def test_a_silent_clock_is_orange_in_a_busy_window_only(self):
        now = LOCK1 - timedelta(hours=2)                                  # busy
        quiet = [hb("tick:supabase", now - timedelta(seconds=240))]
        h = self.snap(now, quiet)["health"]
        self.assertEqual(h["level"], "orange")
        self.assertIn("no tick from supabase for 4 min", h["reasons"][0])
        self.assertEqual(self.snap(now, [hb("tick:supabase", now - timedelta(seconds=179))])["health"]["level"], "green")
        later = LOCK1 - timedelta(days=10)                                # idle: 30-minute ticks are normal, so quiet is not a problem
        self.assertEqual(self.snap(later, [hb("tick:supabase", later - timedelta(hours=1))])["health"]["level"], "green")

    def test_the_in_server_clock_is_never_expected_to_tick(self):
        now = LOCK1 - timedelta(hours=2)
        self.assertEqual(self.snap(now, [hb("tick:app", now - timedelta(hours=3))])["health"]["level"], "green")

    def test_red_outranks_orange_and_lists_both(self):
        now = LOCK1 + timedelta(minutes=10)
        h = self.snap(now, [hb("tick:supabase", now - timedelta(minutes=9))])["health"]
        self.assertEqual(h["level"], "red")
        self.assertEqual(len(h["reasons"]), 2)
        self.assertIn("late", h["reasons"][0])
        self.assertIn("no tick from supabase", h["reasons"][1])

    def test_a_finished_competition_is_never_a_problem(self):
        self.assertEqual(self.snap(LOCK1 + timedelta(days=40), status="completed")["health"]["level"], "green")
        self.assertEqual(self.snap(LOCK3 + timedelta(days=3), handled={1, 2, 3})["health"]["level"], "green")

    def test_the_snapshot_carries_the_parts(self):
        s = self.snap(LOCK1 - timedelta(hours=1), [hb("tick:pc", LOCK1 - timedelta(hours=1, seconds=30))])
        self.assertEqual(sorted(s), ["clocks", "health", "now", "rhythm", "states"])
        self.assertEqual(s["clocks"][0]["source"], "pc")
        self.assertEqual(s["rhythm"]["mode"], "busy")


class Alerts(unittest.TestCase):
    def snap(self, now, heartbeats=(), **kw):
        return ops.build_snapshot([comp(**kw)], list(heartbeats), now)

    def run_at(self, now, state, applied=(), **kw):
        return ops.evaluate_alerts(self.snap(now, **kw), list(applied), state, now)

    def test_nothing_to_say_says_nothing(self):
        msgs, st = self.run_at(LOCK1 - timedelta(days=3), {})
        self.assertEqual(msgs, [])
        self.assertEqual(st, {"incidents": {}, "announced": []})

    def test_a_late_handover_is_one_message_then_a_reminder_every_half_hour_then_resolved(self):
        t0 = LOCK1 + timedelta(minutes=3)
        msgs, st = self.run_at(t0, {})
        self.assertEqual([(m["kind"], m["severity"]) for m in msgs], [("new", "red")])
        self.assertEqual(msgs[0]["key"], "late:sa20_2027:1")
        self.assertIn("RED: SA20 2027 week 1 handover is 3 min late (it was due at 12:30 UTC).", msgs[0]["text"])
        msgs, st = self.run_at(t0 + timedelta(minutes=29), st)
        self.assertEqual(msgs, [], "no repeat inside half an hour")
        msgs, st = self.run_at(t0 + timedelta(minutes=30), st)
        self.assertEqual([m["kind"] for m in msgs], ["still"])
        self.assertTrue(msgs[0]["text"].startswith("STILL FAILING (30 min): RED: SA20 2027 week 1 handover is 33 min late"))
        msgs, st = self.run_at(t0 + timedelta(minutes=45), st)
        self.assertEqual(msgs, [], "the reminder clock restarts from the last message")
        msgs, st = self.run_at(t0 + timedelta(minutes=60), st)
        self.assertEqual([m["kind"] for m in msgs], ["still"])
        # the handover runs: the incident clears
        msgs, st = self.run_at(t0 + timedelta(minutes=61), st, handled={1})
        self.assertEqual([(m["kind"], m["severity"], m["text"]) for m in msgs], [("resolved", "ok", "RESOLVED: SA20 2027 week 1 handover has now run.")])
        self.assertEqual(st["incidents"], {})
        msgs, _ = self.run_at(t0 + timedelta(minutes=62), st, handled={1})
        self.assertEqual(msgs, [])

    def test_a_blocked_competition_is_an_incident_with_its_own_resolution(self):
        t0 = LOCK1 + timedelta(minutes=4)
        msgs, st = self.run_at(t0, {}, status="upcoming")
        self.assertEqual(msgs[0]["key"], "blocked:sa20_2027:1")
        self.assertIn("locked at 12:30 UTC but it is upcoming and does not start itself", msgs[0]["text"])
        msgs, st = self.run_at(t0 + timedelta(minutes=1), st, status="upcoming", auto_activate=True)       # an admin turns auto-activation on: now it is simply late
        self.assertEqual(sorted((m["kind"], m["key"]) for m in msgs), [("new", "late:sa20_2027:1"), ("resolved", "blocked:sa20_2027:1")])

    def test_a_silent_clock_is_an_incident(self):
        now = LOCK1 - timedelta(hours=2)
        msgs, st = self.run_at(now, {}, heartbeats=[hb("tick:supabase", now - timedelta(minutes=5))])
        self.assertEqual([(m["key"], m["severity"]) for m in msgs], [("silent:supabase", "orange")])
        self.assertEqual(msgs[0]["text"], "ORANGE: no tick from supabase for 5 min.")
        msgs, st = self.run_at(now + timedelta(seconds=30), st, heartbeats=[hb("tick:supabase", now + timedelta(seconds=20))])
        self.assertEqual([(m["kind"], m["text"]) for m in msgs], [("resolved", "RESOLVED: supabase is ticking again.")])

    def test_several_incidents_come_out_in_a_fixed_order(self):
        now = LOCK1 + timedelta(minutes=10)
        msgs, _ = self.run_at(now, {}, heartbeats=[hb("tick:pc", now - timedelta(minutes=9)), hb("tick:supabase", now - timedelta(minutes=8))])
        self.assertEqual([m["key"] for m in msgs], ["late:sa20_2027:1", "silent:pc", "silent:supabase"])

    def test_a_handover_that_ran_is_announced_once(self):
        row = {"slug": "sa20_2027", "name": "SA20 2027", "kind": "rollover", "to_week": 3, "applied_at": (LOCK3 + timedelta(seconds=7)).isoformat(), "members": 3,
               "summary": {"promoted": 1, "carried": 1, "empty": 1}, "activated": False}
        now = LOCK3 + timedelta(minutes=1)
        msgs, st = self.run_at(now, {}, applied=[row], handled={1, 2, 3})
        self.assertEqual([(m["kind"], m["severity"], m["text"]) for m in msgs], [("event", "ok", "OK: SA20 2027 week 3 is live. 3 members, 1 carried over, 1 empty XIs.")])
        self.assertEqual(st["announced"], ["sa20_2027:3"])
        msgs, st = self.run_at(now + timedelta(minutes=1), st, applied=[row], handled={1, 2, 3})
        self.assertEqual(msgs, [])

    def test_the_announcement_counts_every_kind_of_step(self):
        """Carried = carried + kept; empty = empty + created_empty (a member who never drafted gets an empty XI made for them)."""
        base = {"slug": "sa20_2027", "name": "SA20 2027", "applied_at": (LOCK3 + timedelta(seconds=7)).isoformat(), "activated": False}
        now = LOCK3 + timedelta(minutes=1)
        row = dict(base, kind="rollover", to_week=3, members=8, summary={"promoted": 1, "carried": 1, "kept": 2, "empty": 1, "created_empty": 3})
        self.assertEqual(self.run_at(now, {}, applied=[row], handled={1, 2, 3})[0][0]["text"], "OK: SA20 2027 week 3 is live. 8 members, 3 carried over, 4 empty XIs.")
        start = dict(base, kind="start", to_week=1, members=5, activated=True, summary={"promoted": 3, "empty": 1, "created_empty": 1})
        self.assertEqual(self.run_at(now, {}, applied=[start], handled={1, 2, 3})[0][0]["text"], "OK: SA20 2027 is live. Week 1 locked: 5 members, 2 with no XI.")

    def test_the_week_one_start_says_the_competition_is_live(self):
        row = {"slug": "sa20_2027", "name": "SA20 2027", "kind": "start", "to_week": 1, "applied_at": (LOCK1 + timedelta(seconds=4)).isoformat(), "members": 3,
               "summary": {"promoted": 2, "kept": 0, "empty": 1}, "activated": True}
        msgs, _ = self.run_at(LOCK1 + timedelta(minutes=1), {}, applied=[row], handled={1})
        self.assertEqual(msgs[0]["text"], "OK: SA20 2027 is live. Week 1 locked: 3 members, 1 with no XI.")

    def test_a_handover_is_news_for_exactly_a_day(self):
        def row(age):
            return {"slug": "c", "name": "C", "kind": "rollover", "to_week": 2, "applied_at": (LOCK2 - age).isoformat(), "members": 1, "summary": {}, "activated": False}
        self.assertEqual(len(self.run_at(LOCK2, {}, applied=[row(timedelta(hours=24))], handled={1})[0]), 1, "exactly 24 hours old is still news")
        self.assertEqual(self.run_at(LOCK2, {}, applied=[row(timedelta(hours=24, seconds=1))], handled={1})[0], [], "a second older is history")

    def test_history_is_remembered_but_never_announced(self):
        old = {"slug": "ipl_2026", "name": "IPL 2026", "kind": "rollover", "to_week": 9, "applied_at": (LOCK1 - timedelta(days=30)).isoformat(), "members": 3,
               "summary": {}, "activated": False}
        msgs, st = self.run_at(LOCK1 - timedelta(days=2), {}, applied=[old])
        self.assertEqual(msgs, [])
        self.assertEqual(st["announced"], ["ipl_2026:9"])

    def test_the_announced_list_is_bounded(self):
        rows = [{"slug": "c", "name": "C", "kind": "rollover", "to_week": i, "applied_at": (LOCK1 - timedelta(days=60 - i)).isoformat(), "members": 1, "summary": {}, "activated": False}
                for i in range(1, 61)]
        _, st = self.run_at(LOCK1, {}, applied=rows)
        self.assertEqual(len(st["announced"]), ops.ANNOUNCED_KEEP)
        self.assertEqual(st["announced"][-1], "c:60")

    def test_the_state_that_is_passed_in_is_never_changed(self):
        t0 = LOCK1 + timedelta(minutes=3)
        _, st = self.run_at(t0, {})
        frozen = copy.deepcopy(st)
        self.run_at(t0 + timedelta(minutes=40), st, handled={1})
        self.assertEqual(st, frozen)

    def test_a_message_that_did_not_get_out_is_raised_again(self):
        """After a failed send the caller takes the new incident and the event back (forget_unsent), so the next call raises them again."""
        row = {"slug": "sa20_2027", "name": "SA20 2027", "kind": "rollover", "to_week": 2, "applied_at": (LOCK2 + timedelta(seconds=5)).isoformat(), "members": 3,
               "summary": {"carried": 3}, "activated": False}
        now = LOCK2 + timedelta(minutes=3)
        snap = self.snap(now, handled={1}, rows=[dict(r) for r in ROWS], held={2})                # week 2 held: blocked, an incident of its own
        msgs, st = ops.evaluate_alerts(snap, [row], {}, now)
        kinds = sorted(m["kind"] for m in msgs)
        self.assertEqual(kinds, ["event", "new"])
        again = ops.forget_unsent(st, msgs)
        self.assertEqual(again, {"incidents": {}, "announced": []})
        msgs2, _ = ops.evaluate_alerts(snap, [row], again, now)
        self.assertEqual(sorted(m["kind"] for m in msgs2), ["event", "new"], "both come out again")

    def test_a_reminder_and_a_resolved_are_not_retried(self):
        t0 = LOCK1 + timedelta(minutes=3)
        _, st = self.run_at(t0, {})
        msgs, st2 = self.run_at(t0 + timedelta(minutes=31), st)
        self.assertEqual([m["kind"] for m in msgs], ["still"])
        self.assertEqual(ops.forget_unsent(st2, msgs), st2, "a reminder that failed is simply left: the next one is half an hour away")
        msgs, st3 = self.run_at(t0 + timedelta(minutes=32), st2, handled={1})
        self.assertEqual([m["kind"] for m in msgs], ["resolved"])
        self.assertEqual(ops.forget_unsent(st3, msgs), st3)

    def test_forgetting_never_changes_what_it_was_given(self):
        st = {"incidents": {"late:x:1": {"since": "a", "last_sent": "a", "severity": "red", "resolved": "r"}}, "announced": ["x:1", "x:2"]}
        frozen = copy.deepcopy(st)
        out = ops.forget_unsent(st, [{"kind": "new", "key": "late:x:1"}, {"kind": "event", "key": "applied:x:2"}, {"kind": "event", "key": "applied:never:9"}])
        self.assertEqual(st, frozen)
        self.assertEqual(out, {"incidents": {}, "announced": ["x:1"]})
        self.assertEqual(ops.forget_unsent(None, None), {"incidents": {}, "announced": []})

    def test_a_message_names_people_by_display_name_only(self):
        t0 = LOCK1 + timedelta(minutes=3)
        for text in [m["text"] for m in self.run_at(t0, {})[0] + self.run_at(t0, {}, status="upcoming")[0]]:
            self.assertNotIn("@", text)
            self.assertNotIn("http", text.lower())
            self.assertNotIn("token", text.lower())


if __name__ == "__main__":
    unittest.main()
