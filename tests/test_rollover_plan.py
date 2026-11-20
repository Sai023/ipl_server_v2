"""The weekly lock schedule and which transition is due (logic/rollover_plan.py, 2026-11-20, Phase 1.5 of the SA20 readiness plan).

Sai's decisions (2026-11-19): week 1 locks 60 minutes before the opener, weeks 2 onward lock on Monday at 14:00 UTC, SA20 activates itself at the
week-1 lock, and a member with no draft carries last week's XI. Everything here is pure (no database): the engine decides WHAT is due; the database
layer (tests/test_rollover_core.py) applies it.

1. building the schedule from the real SA20 calendar and from awkward ones
2. validating a schedule
3. which transition is due, in which status
4. whether picking is locked
5. a seeded replay of the whole SA20 season: random trigger times, long outages and failed attempts, against an independent oracle"""
import json
import os
import random
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from logic import rollover_plan as rp  # noqa: E402

UTC = timezone.utc
with open(os.path.join(ROOT, "tests", "fixtures", "sa20_2027_calendar.json"), encoding="utf-8") as _f:
    SA20 = json.load(_f)["matches"]


def dt(s):
    return datetime.fromisoformat(s)


def first_matches(matches=SA20):
    out = {}
    for m in matches:
        t = dt(m["start_utc"])
        out[m["week_no"]] = min(t, out.get(m["week_no"], t))
    return out


# What Sai decided, written out by hand (UTC), so the engine is checked against the decision and not against itself.
EXPECTED_LOCKS = {1: "2027-01-17T12:30:00+00:00", 2: "2027-01-18T14:00:00+00:00", 3: "2027-01-25T14:00:00+00:00",
                  4: "2027-02-01T14:00:00+00:00", 5: "2027-02-08T14:00:00+00:00", 6: "2027-02-15T14:00:00+00:00"}


def sa20_rows():
    built = rp.build_schedule(first_matches())
    assert not built["errors"], built["errors"]
    return built["rows"]


class TheSa20Schedule(unittest.TestCase):
    def test_the_calendar_has_six_weeks_of_34_matches(self):
        self.assertEqual(len(SA20), 34); self.assertEqual(sorted({m["week_no"] for m in SA20}), [1, 2, 3, 4, 5, 6])

    def test_week_1_locks_60_minutes_before_the_opener_and_the_others_on_monday_1400_utc(self):
        rows = sa20_rows()
        self.assertEqual({r["week_no"]: r["lock_at_utc"] for r in rows}, EXPECTED_LOCKS)

    def test_the_week_1_lock_is_1430_south_african_time_on_the_opening_sunday(self):
        lock = dt(sa20_rows()[0]["lock_at_utc"])
        self.assertEqual(lock.astimezone(timezone(timedelta(hours=2))).strftime("%a %d %b %H:%M"), "Sun 17 Jan 14:30")
        self.assertEqual(dt(first_matches()[1].isoformat()) - lock, timedelta(minutes=60))

    def test_every_later_lock_is_a_monday_at_1400_utc_and_before_its_weeks_first_match(self):
        for r in sa20_rows()[1:]:
            lock = dt(r["lock_at_utc"])
            self.assertEqual((lock.weekday(), lock.hour, lock.minute, lock.second), (0, 14, 0, 0), r)
            self.assertLess(lock, dt(r["first_match_utc"]) - timedelta(minutes=30), r)

    def test_the_schedule_is_sound(self):
        self.assertEqual(rp.validate_schedule(sa20_rows(), max_weeks=6), [])

    def test_week_2_locks_only_90_minutes_before_its_first_ball(self):
        rows = {r["week_no"]: r for r in sa20_rows()}
        self.assertEqual(dt(rows[2]["first_match_utc"]) - dt(rows[2]["lock_at_utc"]), timedelta(minutes=90))

    def test_the_first_match_of_each_week_is_kept_with_the_row(self):
        for r in sa20_rows():
            self.assertEqual(dt(r["first_match_utc"]), first_matches()[r["week_no"]])


class BuildingASchedule(unittest.TestCase):
    def test_a_first_match_on_monday_afternoon_locks_the_same_day(self):
        b = rp.build_schedule({1: "2027-01-17T13:30:00Z", 2: "2027-01-18T14:30:00Z"})
        self.assertEqual(b["rows"][1]["lock_at_utc"], "2027-01-18T14:00:00+00:00"); self.assertEqual(b["errors"], [])

    def test_a_first_match_less_than_30_minutes_after_monday_1400_locks_a_week_earlier_and_is_reported(self):
        b = rp.build_schedule({1: "2027-01-10T13:30:00Z", 2: "2027-01-18T14:29:00Z"})
        self.assertEqual(b["rows"][1]["lock_at_utc"], "2027-01-11T14:00:00+00:00")           # the previous Monday: still a valid answer ...
        b = rp.build_schedule({1: "2027-01-17T13:30:00Z", 2: "2027-01-18T14:29:00Z"})
        self.assertTrue(any("before week 1 has even started" in e for e in b["errors"]), b["errors"])   # ... unless it falls before week 1 began

    def test_a_week_with_no_matches_is_an_error_not_a_guess(self):
        b = rp.build_schedule({1: "2027-01-17T13:30:00Z", 3: "2027-01-26T15:30:00Z"})
        self.assertTrue(any("none missing" in e for e in b["errors"]), b["errors"])

    def test_an_unreadable_first_match_is_an_error(self):
        b = rp.build_schedule({1: "2027-01-17T13:30:00Z", 2: "not a time"})
        self.assertTrue(any("week 2" in e and "no usable time" in e for e in b["errors"]), b["errors"])

    def test_nothing_to_build_from(self):
        for empty in (None, {}):
            self.assertTrue(rp.build_schedule(empty)["errors"])
        self.assertEqual(rp.build_schedule({})["rows"], [])

    def test_a_bad_week_number_is_reported(self):
        self.assertTrue(rp.build_schedule({0: "2027-01-17T13:30:00Z"})["errors"])
        self.assertTrue(rp.build_schedule({"1": "2027-01-17T13:30:00Z"})["errors"])

    def test_the_lead_for_week_1_can_be_changed(self):
        b = rp.build_schedule({1: "2027-01-17T13:30:00Z"}, week1_lead=timedelta(hours=2))
        self.assertEqual(b["rows"][0]["lock_at_utc"], "2027-01-17T11:30:00+00:00")

    def test_times_with_other_zones_are_converted_to_utc(self):
        b = rp.build_schedule({1: "2027-01-17T19:00:00+05:30"})
        self.assertEqual(b["rows"][0]["lock_at_utc"], "2027-01-17T12:30:00+00:00")

    def test_monday_lock_on_or_before_for_every_weekday(self):
        this_monday = datetime(2027, 1, 18, 14, 0, tzinfo=UTC)                  # Monday 18 Jan 2027, 14:00 UTC
        for day in range(7):
            for hh, mm in ((0, 0), (13, 59), (14, 0), (23, 59)):
                moment = datetime(2027, 1, 18 + day, hh, mm, tzinfo=UTC)
                want = this_monday if moment >= this_monday else this_monday - timedelta(days=7)
                self.assertEqual(rp.monday_lock_on_or_before(moment), want, moment)


class ValidatingASchedule(unittest.TestCase):
    def rows(self):
        return [dict(r) for r in sa20_rows()]

    def test_empty(self):
        self.assertEqual(rp.validate_schedule([]), ["the schedule has no weeks"]); self.assertTrue(rp.validate_schedule(None))

    def test_a_duplicate_week(self):
        r = self.rows(); r.append(dict(r[0]))
        self.assertTrue(any("appears twice" in e for e in rp.validate_schedule(r)))

    def test_a_missing_week(self):
        r = self.rows(); del r[2]
        self.assertTrue(any("none missing" in e for e in rp.validate_schedule(r)))

    def test_a_week_that_does_not_start_at_one(self):
        r = self.rows()[1:]
        self.assertTrue(any("none missing" in e for e in rp.validate_schedule(r)))

    def test_bad_week_numbers(self):
        for bad in (0, -1, 1.5, "2", True, None):
            self.assertTrue(rp.validate_schedule([{"week_no": bad, "lock_at_utc": "2027-01-17T12:30:00Z"}]), bad)

    def test_locks_that_are_not_in_order(self):
        r = self.rows(); r[2]["lock_at_utc"], r[3]["lock_at_utc"] = r[3]["lock_at_utc"], r[2]["lock_at_utc"]
        self.assertTrue(any("locks at or before week" in e for e in rp.validate_schedule(r)))

    def test_two_weeks_locking_at_the_same_moment(self):
        r = self.rows(); r[3]["lock_at_utc"] = r[2]["lock_at_utc"]
        self.assertTrue(any("at or before" in e for e in rp.validate_schedule(r)))

    def test_a_lock_too_close_to_its_first_match(self):
        r = self.rows(); r[0]["lock_at_utc"] = "2027-01-17T13:15:00+00:00"                  # 15 minutes before the opener
        self.assertTrue(any("less than 30 minutes" in e for e in rp.validate_schedule(r)))

    def test_a_lock_before_the_previous_week_has_even_started(self):
        r = self.rows(); r[1]["lock_at_utc"] = "2027-01-17T13:00:00+00:00"
        self.assertTrue(any("before week 1 has even started" in e for e in rp.validate_schedule(r)))

    def test_an_unreadable_lock(self):
        for bad in (None, "", "soon"):
            r = self.rows(); r[1]["lock_at_utc"] = bad
            self.assertTrue(any("week 2" in e and "unreadable" in e for e in rp.validate_schedule(r)), bad)

    def test_more_weeks_than_the_competition_has(self):
        self.assertTrue(any("6 weeks but the competition has 5" in e for e in rp.validate_schedule(self.rows(), max_weeks=5)))

    def test_first_match_times_are_optional(self):
        r = [{"week_no": 1, "lock_at_utc": "2027-01-17T12:30:00Z"}, {"week_no": 2, "lock_at_utc": "2027-01-18T14:00:00Z"}]
        self.assertEqual(rp.validate_schedule(r), [])


ROWS = None


def rows():
    global ROWS
    if ROWS is None:
        ROWS = sa20_rows()
    return ROWS


def at(s):
    return dt(s)


class WhatIsDue(unittest.TestCase):
    L1, L2, L3 = at(EXPECTED_LOCKS[1]), at(EXPECTED_LOCKS[2]), at(EXPECTED_LOCKS[3])

    def due(self, now, handled=(), status="active", **kw):
        return rp.next_due(rows(), handled, now, status, max_weeks=6, **kw)

    def test_nothing_is_due_before_the_first_lock(self):
        self.assertIsNone(self.due(self.L1 - timedelta(seconds=1), status="upcoming", auto_activate=True))
        self.assertIsNone(self.due(self.L1 - timedelta(days=30), status="active"))

    def test_the_start_is_due_at_the_first_lock_instant_for_an_active_competition(self):
        t = self.due(self.L1, status="active")
        self.assertEqual((t["kind"], t["to_week"], t["lock_at"]), ("start", 1, self.L1))

    def test_an_upcoming_competition_starts_itself_only_when_it_is_set_to_activate_itself(self):
        self.assertEqual(self.due(self.L1, status="upcoming", auto_activate=True)["kind"], "start")
        self.assertIsNone(self.due(self.L1, status="upcoming", auto_activate=False))
        self.assertIsNone(self.due(self.L1 + timedelta(days=9), status="upcoming", auto_activate=False))

    def test_an_upcoming_competition_never_rolls_over_before_it_has_started(self):
        t = self.due(self.L2, handled=(), status="upcoming", auto_activate=True)
        self.assertEqual((t["kind"], t["to_week"]), ("start", 1), "week 1 comes first, however late it is")
        self.assertIsNone(self.due(self.L2, handled={1}, status="upcoming", auto_activate=True), "a rollover needs an active competition")

    def test_a_completed_competition_never_does_anything(self):
        self.assertIsNone(self.due(self.L3, status="completed")); self.assertIsNone(self.due(self.L3, status="completed", force=True))

    def test_an_unknown_status_does_nothing(self):
        for s in ("", None, "paused"):
            self.assertIsNone(self.due(self.L3, status=s))

    def test_transitions_come_one_at_a_time_in_order(self):
        far = at("2027-03-01T00:00:00+00:00")
        handled = set()
        order = []
        while True:
            t = self.due(far, handled=handled)
            if t is None:
                break
            order.append((t["kind"], t["to_week"])); handled.add(t["to_week"])
        self.assertEqual(order, [("start", 1)] + [("rollover", w) for w in range(2, 7)])

    def test_a_handled_transition_is_never_offered_again(self):
        self.assertEqual(self.due(self.L3, handled={1})["to_week"], 2)
        self.assertEqual(self.due(self.L3, handled={1, 2})["to_week"], 3)
        self.assertIsNone(self.due(self.L3 - timedelta(seconds=1), handled={1, 2}))

    def test_a_lock_in_the_future_blocks_everything_after_it(self):
        self.assertIsNone(self.due(self.L2 - timedelta(minutes=1), handled={1}))

    def test_force_ignores_the_lock_time_but_not_the_status(self):
        t = self.due(self.L1 - timedelta(days=5), status="active", force=True)
        self.assertEqual((t["kind"], t["to_week"]), ("start", 1))
        self.assertEqual(self.due(self.L1 - timedelta(days=5), handled={1, 2, 3}, status="active", force=True)["to_week"], 4)
        self.assertIsNone(self.due(self.L1 - timedelta(days=5), status="upcoming", force=True), "an upcoming competition that does not activate itself")
        self.assertEqual(self.due(self.L1 - timedelta(days=5), status="upcoming", force=True, auto_activate=True)["kind"], "start")

    def test_weeks_beyond_the_competitions_length_are_never_due(self):
        far = at("2027-03-01T00:00:00+00:00")
        self.assertIsNone(rp.next_due(rows(), {1, 2, 3, 4, 5}, far, "active", max_weeks=5))
        self.assertEqual(rp.next_due(rows(), {1, 2, 3, 4, 5}, far, "active", max_weeks=6)["to_week"], 6)
        self.assertIsNone(rp.next_due(rows(), {1, 2, 3, 4, 5, 6}, far, "active", max_weeks=6))

    def test_an_unreadable_lock_time_is_never_guessed(self):
        r = [dict(x) for x in rows()]; r[1]["lock_at_utc"] = "soon"
        self.assertIsNone(rp.next_due(r, {1}, at("2027-03-01T00:00:00+00:00"), "active", max_weeks=6))

    def test_now_may_be_a_string_or_a_naive_datetime(self):
        self.assertEqual(rp.next_due(rows(), (), EXPECTED_LOCKS[1], "active")["to_week"], 1)
        self.assertEqual(rp.next_due(rows(), (), datetime(2027, 1, 17, 12, 30), "active")["to_week"], 1)

    def test_no_schedule_no_transitions(self):
        self.assertIsNone(rp.next_due([], (), self.L3, "active")); self.assertEqual(rp.schedule_transitions([]), [])


class TheLockState(unittest.TestCase):
    def state(self, now, handled=(), status="active", **kw):
        return rp.lock_state(rows(), handled, now, status, max_weeks=6, **kw)

    def test_before_the_first_lock_picking_is_open_and_the_countdown_points_at_week_1(self):
        s = self.state(at(EXPECTED_LOCKS[1]) - timedelta(seconds=1), status="upcoming")
        self.assertFalse(s["locked"]); self.assertEqual(s["next_lock"], at(EXPECTED_LOCKS[1])); self.assertIsNone(s["awaiting"]); self.assertEqual(s["applied_to"], 0)

    def test_at_the_lock_instant_picking_locks_until_the_transition_has_run(self):
        s = self.state(at(EXPECTED_LOCKS[1]), status="upcoming")
        self.assertTrue(s["locked"]); self.assertEqual(s["awaiting"], {"kind": "start", "to_week": 1}); self.assertIn("until the rollover has run", s["reason"])
        self.assertEqual(s["next_lock"], at(EXPECTED_LOCKS[1]), "the countdown still shows the lock that passed, so the page shows it locked at once")

    def test_after_the_transition_the_next_week_is_open_and_the_countdown_moves_on(self):
        s = self.state(at(EXPECTED_LOCKS[1]) + timedelta(minutes=1), handled={1})
        self.assertFalse(s["locked"]); self.assertEqual(s["next_lock"], at(EXPECTED_LOCKS[2])); self.assertEqual(s["applied_to"], 1)

    def test_a_missed_week_keeps_everything_locked_until_it_is_caught_up(self):
        s = self.state(at(EXPECTED_LOCKS[4]) + timedelta(hours=1), handled={1, 2})
        self.assertTrue(s["locked"]); self.assertEqual(s["awaiting"]["to_week"], 3)

    def test_after_the_last_week_the_season_is_over_and_picking_is_locked_for_good(self):
        s = self.state(at("2027-02-20T00:00:00+00:00"), handled={1, 2, 3, 4, 5, 6})
        self.assertTrue(s["locked"]); self.assertTrue(s["season_over"]); self.assertIsNone(s["next_lock"]); self.assertEqual(s["applied_to"], 6)
        self.assertIn("final week is under way", s["reason"]); self.assertNotIn("complete", s["reason"])     # the last matches may still be to play

    def test_a_completed_competition_is_locked(self):
        s = self.state(at(EXPECTED_LOCKS[1]) - timedelta(days=30), status="completed")
        self.assertTrue(s["locked"]); self.assertTrue(s["season_over"]); self.assertIn("has ended", s["reason"])

    def test_an_unreadable_lock_fails_closed(self):
        r = [dict(x) for x in rows()]; r[0]["lock_at_utc"] = "soon"
        s = rp.lock_state(r, (), at("2027-01-01T00:00:00+00:00"), "upcoming", max_weeks=6)
        self.assertTrue(s["locked"]); self.assertIsNone(s["next_lock"])

    def test_applied_to_counts_the_unbroken_run_from_week_1(self):
        self.assertEqual(self.state(at("2027-03-01T00:00:00+00:00"), handled={1, 2, 4})["applied_to"], 2)

    def test_the_tuple_form_matches(self):
        now = at(EXPECTED_LOCKS[2]) + timedelta(seconds=5)
        self.assertEqual(rp.scheduled_selections_locked(rows(), {1}, now, "active", max_weeks=6), (True, self.state(now, handled={1})["reason"]))
        self.assertEqual(rp.scheduled_selections_locked(rows(), {1}, at(EXPECTED_LOCKS[2]) - timedelta(seconds=5), "active", max_weeks=6), (False, ""))


class ReplayingTheWholeSeason(unittest.TestCase):
    """Time passes; something wakes the system up at random moments (sometimes not for days) and some attempts to apply a transition fail. The engine
    is checked against an oracle written separately from it."""

    def replay(self, seed, fail_p=0.2):
        rng = random.Random(seed)
        r = rows()
        locks = {x["week_no"]: at(x["lock_at_utc"]) for x in r}
        handled, applied, status = set(), [], "upcoming"           # applied: (to_week, tick time)
        ticks, fails = [], []
        t = at("2027-01-10T00:00:00+00:00"); end = at("2027-02-25T00:00:00+00:00")
        while True:
            ticks.append(t)
            while True:
                nd = rp.next_due(r, handled, t, status, max_weeks=6, auto_activate=True)
                if nd is None:
                    break
                if rng.random() < fail_p and t < end:
                    fails.append((t, nd["to_week"])); break                  # the attempt failed: nothing was recorded, a later tick retries
                self.assertNotIn(nd["to_week"], handled, "applied twice")
                handled.add(nd["to_week"]); applied.append((nd["to_week"], t))
                if nd["kind"] == "start":
                    status = "active"
            if t >= end:
                break
            gap = rng.expovariate(1 / 1200.0)                                 # mostly every 20 minutes ...
            if rng.random() < 0.03:
                gap += rng.uniform(3600, 3 * 86400)                           # ... with the odd outage of up to three days
            t = min(t + timedelta(seconds=gap), end)
        return locks, applied, ticks, fails, handled, status

    def test_every_week_is_applied_once_in_order_and_never_early(self):
        for seed in range(40):
            locks, applied, ticks, fails, handled, status = self.replay(seed)
            self.assertEqual([w for w, _ in applied], [1, 2, 3, 4, 5, 6], "seed %d" % seed)
            for w, when in applied:
                self.assertGreaterEqual(when, locks[w], "week %d applied before its lock (seed %d)" % (w, seed))
            self.assertEqual(handled, {1, 2, 3, 4, 5, 6}); self.assertEqual(status, "active")

    def test_a_week_is_applied_at_the_first_tick_after_its_lock_that_does_not_fail(self):
        for seed in range(40):
            locks, applied, ticks, fails, _h, _s = self.replay(seed)
            failed = {}
            for when, w in fails:
                failed.setdefault(w, set()).add(when)
            for w, when in applied:
                eligible = [x for x in ticks if x >= locks[w] and x not in failed.get(w, ())]
                # a week can also wait for the week before it (a failed attempt blocks the later ones at that tick): never LATER than the first tick
                # at which the week before it had been applied and this week was not failing
                prev_done = max([a for ww, a in applied if ww == w - 1] or [ticks[0]])
                eligible = [x for x in eligible if x >= prev_done]
                self.assertEqual(when, eligible[0], "week %d (seed %d)" % (w, seed))

    def test_picking_is_locked_exactly_while_a_lock_has_passed_and_its_transition_has_not_run(self):
        for seed in range(25):
            locks, applied, ticks, fails, _h, _s = self.replay(seed)
            rng = random.Random(1000 + seed)
            r = rows()
            start = at("2027-01-10T00:00:00+00:00")
            for _ in range(400):
                u = start + timedelta(seconds=rng.uniform(0, 46 * 86400))
                done = {w for w, when in applied if when <= u}
                status = "active" if 1 in done else "upcoming"
                s = rp.lock_state(r, done, u, status, max_weeks=6)
                # the oracle: the first week not applied yet, and whether its lock has passed
                pending = [w for w in range(1, 7) if w not in done]
                if not pending:
                    self.assertTrue(s["locked"])
                else:
                    self.assertEqual(s["locked"], locks[pending[0]] <= u, "seed %d at %s" % (seed, u))
                    self.assertEqual(s["next_lock"], locks[pending[0]])

    def test_after_an_outage_that_spans_several_locks_everything_is_caught_up_in_order_at_the_next_tick(self):
        r = rows(); handled, status, order = set(), "upcoming", []
        t = at("2027-02-10T00:00:00+00:00")                                   # nobody has run anything since before the season: weeks 1-5 are all overdue
        while True:
            nd = rp.next_due(r, handled, t, status, max_weeks=6, auto_activate=True)
            if nd is None:
                break
            order.append(nd["to_week"]); handled.add(nd["to_week"]); status = "active"
        self.assertEqual(order, [1, 2, 3, 4, 5])
        self.assertEqual(rp.lock_state(r, handled, t, status, max_weeks=6)["next_lock"], at(EXPECTED_LOCKS[6]))


if __name__ == "__main__":
    unittest.main()
