"""The lock schedule and the weekly transitions on a real (scratch) database, 2026-11-20 (Phase 1.5 of the SA20 readiness plan).

What these prove, against the real SA20 2027 calendar (week 1 locks Sun 17 Jan 12:30 UTC = 14:30 SAST, weeks 2-6 Monday 14:00 UTC):
  * the schedule is validated and stored, and a week that has already run keeps its lock time
  * "start": at the week-1 lock every member's pre-season draft becomes the XI that plays week 1 (the old rollover never did that), SA20 can activate itself
  * "rollover": at each later lock every draft becomes that week's XI, a member with no draft carries last week's XI, nobody is left out
  * a late or repeated trigger catches up in order and never applies a week twice
  * the lock state the page shows comes from the same ledger

Needs a scratch Postgres: set TEST_DATABASE_URL (a DIRECT Neon/Postgres URL, run with `py -3.14`); the module uses and drops its own schema.
The failure, concurrency, dry-run, undo and whole-season replay tests are in tests/test_rollover_safety.py."""
import json
import os
import sys
import unittest
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "helpers"))
from logic import rollover_plan as rp  # noqa: E402
import rollover_db as R  # noqa: E402
from rollover_db import at, row, team, weeks_of, SEASON, LOCKS, TEAM_A, TEAM_B, TEAM_C  # noqa: E402

S = None
needs_db = unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "set TEST_DATABASE_URL to run the database tests")


def setUpModule():
    global S
    if os.environ.get("TEST_DATABASE_URL"):
        S = R.ScratchDb()


def tearDownModule():
    if S is not None:
        S.close()


class Base(unittest.TestCase):
    @property
    def db(self):
        return S.db

    def applied_weeks(self, rep):
        return [a["to_week"] for a in rep["applied"]]


@needs_db
class TheScheduleStore(Base):
    def test_a_schedule_is_stored_and_read_back_in_week_order(self):
        slug = S.clone(schedule=False)
        self.assertNotIn(f"_saved:{slug}", S.snap(slug)["meta"])
        rep = self.db.set_schedule(slug, list(reversed(SEASON)))
        got = self.db.get_schedule(slug)
        self.assertEqual([r["week_no"] for r in got], [1, 2, 3, 4, 5, 6])
        self.assertEqual([rp.parse_utc(r["lock_at_utc"]) for r in got], [LOCKS[k] for k in range(1, 7)])
        self.assertEqual((rep["weeks"], rep["changed_weeks"], rep["already_run"], rep["dry_run"]), (6, [1, 2, 3, 4, 5, 6], [], False))
        self.assertIn(f"_saved:{slug}", S.snap(slug)["meta"], "open browsers must be told to refresh their deadline")

    def test_a_bad_schedule_is_refused_and_nothing_is_stored(self):
        slug = S.clone()
        before = S.snap(slug)
        gap = [r for r in SEASON if r["week_no"] != 3]
        inverted = [dict(r) for r in SEASON]; inverted[2]["lock_at_utc"] = inverted[0]["lock_at_utc"]
        late = [dict(r) for r in SEASON]; late[1]["lock_at_utc"] = late[1]["first_match_utc"]
        for bad, words in ((gap, "none missing"), (inverted, "at or before"), (late, "before its first match")):
            with self.assertRaises(ValueError) as cm:
                self.db.set_schedule(slug, bad)
            self.assertIn(words, str(cm.exception))
        self.assertEqual(S.snap(slug), before)

    def test_unknown_completed_and_too_long_are_refused(self):
        with self.assertRaises(LookupError):
            self.db.set_schedule("nope", SEASON)
        done = S.clone(); S.sql(f"UPDATE competitions SET status='completed' WHERE slug='{done}'")
        with self.assertRaises(ValueError) as cm:
            self.db.set_schedule(done, SEASON)
        self.assertIn("completed", str(cm.exception))
        short = S.clone(schedule=False); S.sql(f"UPDATE competitions SET max_weeks=3 WHERE slug='{short}'")
        with self.assertRaises(ValueError) as cm:
            self.db.set_schedule(short, SEASON)
        self.assertIn("6 weeks but the competition has 3", str(cm.exception))
        self.assertEqual(self.db.get_schedule(short), [])

    def test_a_dry_run_changes_nothing_and_reports_what_a_real_call_does(self):
        slug = S.clone(schedule=False)
        before = S.snap(slug)
        dry = self.db.set_schedule(slug, SEASON, auto_activate=True, dry_run=True)
        self.assertEqual(S.snap(slug), before)
        self.assertFalse(self.db.get_auto_activate(slug))
        real = self.db.set_schedule(slug, SEASON, auto_activate=True)
        self.assertTrue(dry.pop("dry_run")); self.assertFalse(real.pop("dry_run"))
        self.assertEqual(dry, real)
        self.assertNotEqual(S.snap(slug), before)
        self.assertTrue(self.db.get_auto_activate(slug))

    def test_auto_activation_changes_only_when_asked(self):
        slug = S.clone(schedule=False)
        self.assertFalse(self.db.get_auto_activate(slug))
        self.db.set_schedule(slug, SEASON); self.assertFalse(self.db.get_auto_activate(slug))                  # None = leave it as it is
        self.db.set_schedule(slug, SEASON, auto_activate=True); self.assertTrue(self.db.get_auto_activate(slug))
        self.db.set_schedule(slug, SEASON); self.assertTrue(self.db.get_auto_activate(slug))                   # still on
        self.db.set_schedule(slug, SEASON, auto_activate=False); self.assertFalse(self.db.get_auto_activate(slug))

    def test_a_week_that_has_run_keeps_its_lock_time_and_later_weeks_may_still_move(self):
        slug = S.clone()
        self.assertEqual(self.applied_weeks(self.db.reconcile_competition(slug, at(1))), [1])
        earlier = [dict(r) for r in SEASON]; earlier[0]["lock_at_utc"] = rp.iso(LOCKS[1] - timedelta(minutes=10))
        with self.assertRaises(ValueError) as cm:
            self.db.set_schedule(slug, earlier)
        self.assertIn("week 1 has already run", str(cm.exception))
        with self.assertRaises(ValueError):
            self.db.set_schedule(slug, [r for r in SEASON if r["week_no"] != 1])                                # week 1 cannot be dropped either
        moved = [dict(r) for r in SEASON]; moved[2]["lock_at_utc"] = rp.iso(LOCKS[3] - timedelta(days=1))
        rep = self.db.set_schedule(slug, moved)
        self.assertEqual((rep["changed_weeks"], rep["already_run"]), ([3], [1]))
        self.assertEqual(self.db.set_schedule(slug, moved)["changed_weeks"], [])                                # the same again changes nothing
        self.assertEqual(rp.parse_utc(self.db.get_schedule(slug)[2]["lock_at_utc"]), LOCKS[3] - timedelta(days=1))

    def test_a_shorter_schedule_leaves_no_leftover_weeks(self):
        slug = S.clone()
        self.db.set_schedule(slug, [r for r in SEASON if r["week_no"] <= 4])
        self.assertEqual([r["week_no"] for r in self.db.get_schedule(slug)], [1, 2, 3, 4])


@needs_db
class TheStartOfTheSeason(Base):
    """Week 1: the pre-season draft becomes the XI that plays week 1."""

    def test_nothing_happens_before_the_lock(self):
        slug = S.clone()
        before = S.snap(slug)
        rep = self.db.reconcile_competition(slug, at(1, seconds=-1), triggered_by="test")
        self.assertEqual(rep["applied"], []); self.assertIn("nothing is due yet", rep["skipped"]); self.assertIn(rp.iso(LOCKS[1]), rep["skipped"])
        self.assertEqual(S.snap(slug), before)

    def test_at_the_lock_every_draft_becomes_the_week_1_team(self):
        slug = S.clone()
        rep = self.db.reconcile_competition(slug, at(1), triggered_by="test")
        self.assertEqual(self.applied_weeks(rep), [1]); self.assertNotIn("error", rep)
        a = rep["applied"][0]
        self.assertEqual((a["kind"], a["members"], a["summary"], a["activated"], a["season_complete"]), ("start", 3, {"promoted": 2, "created_empty": 1}, False, False))
        s = S.snap(slug)
        sai, moe, buddy = row(s, "Sai", 1), row(s, "Moe", 1), row(s, "Buddy", 1)
        self.assertEqual((team(sai), sai["tw_cap_id"], sai["tw_vc_id"]), (TEAM_A, "p1", "p2"))
        self.assertEqual((team(moe), moe["tw_cap_id"], moe["tw_vc_id"]), (TEAM_B, "p5", "p6"))
        self.assertEqual((team(sai, "nw"), team(moe, "nw")), (TEAM_A, TEAM_B), "the draft stays: it pre-fills the draft for week 2")
        self.assertEqual((team(buddy), team(buddy, "nw"), buddy["tw_cap_id"]), ([], [], None), "Buddy never drafted: an empty XI, but a row, so nobody is left out")
        self.assertEqual(s["meta"][f"_last_rollover:{slug}"], at(1).isoformat()); self.assertEqual(s["meta"][f"_saved:{slug}"], at(1).isoformat())

    def test_the_ledger_records_what_happened_and_what_it_replaced(self):
        slug = S.clone()
        self.db.reconcile_competition(slug, at(1, minutes=7), triggered_by="watchdog")
        (e,) = self.db.get_rollover_log(slug)
        self.assertEqual((e["kind"], e["to_week"], e["triggered_by"], e["members"]), ("start", 1, "watchdog", 3))
        self.assertEqual(rp.parse_utc(e["lock_at_utc"]), LOCKS[1]); self.assertEqual(rp.parse_utc(e["applied_at"]), at(1, minutes=7)); self.assertIsNone(e["undone_at"])
        self.assertEqual((e["detail"]["before_status"], e["detail"]["activated"], e["detail"]["redo"]), ("active", False, False))
        by = {m["name"]: m for m in e["detail"]["members"]}
        self.assertEqual((by["Sai"]["action"], by["Sai"]["before"]["tw_team_json"], by["Sai"]["team_size"]), ("promoted", "[]", 11))
        self.assertEqual((by["Buddy"]["action"], by["Buddy"]["team_size"]), ("created_empty", 0))

    def test_a_second_call_changes_nothing(self):
        slug = S.clone()
        self.db.reconcile_competition(slug, at(1))
        after = S.snap(slug)
        rep = self.db.reconcile_competition(slug, at(1, minutes=5))
        self.assertEqual(rep["applied"], []); self.assertIn("nothing is due yet", rep["skipped"]); self.assertIn(rp.iso(LOCKS[2]), rep["skipped"])
        self.assertEqual(S.snap(slug), after)

    def test_a_member_who_only_has_an_active_team_keeps_it(self):
        slug = S.clone()
        S.sql(f"UPDATE user_selections SET tw_team_json='{json.dumps(TEAM_B)}', tw_cap_id='p5', tw_vc_id='p6', nw_team_json='[]', nw_cap_id=NULL, nw_vc_id=NULL "
              f"WHERE competition_id='{slug}' AND display_name='Moe'")
        rep = self.db.reconcile_competition(slug, at(1))
        self.assertEqual(rep["applied"][0]["summary"], {"promoted": 1, "kept": 1, "created_empty": 1})
        moe = row(S.snap(slug), "Moe", 1)
        self.assertEqual((team(moe), moe["tw_cap_id"], moe["tw_vc_id"]), (TEAM_B, "p5", "p6"))

    def test_a_player_who_is_not_in_the_roster_is_a_warning_not_a_blocker(self):
        slug = S.clone()
        S.sql(f"""UPDATE user_selections SET nw_team_json='["p1","zz9"]' WHERE competition_id='{slug}' AND display_name='Sai'""")
        rep = self.db.reconcile_competition(slug, at(1))
        self.assertEqual(self.applied_weeks(rep), [1])
        self.assertTrue(any("zz9" in w and "Sai" in w for w in rep["applied"][0]["warnings"]), rep["applied"][0]["warnings"])
        self.assertEqual(team(row(S.snap(slug), "Sai", 1)), ["p1", "zz9"])

    def test_an_upcoming_competition_that_does_not_start_itself_is_left_alone_and_stays_locked(self):
        slug = S.clone(status="upcoming")
        before = S.snap(slug)
        rep = self.db.reconcile_competition(slug, at(1, hours=1))
        self.assertEqual(rep["applied"], []); self.assertIn("does not start itself", rep["skipped"])
        self.assertEqual(S.snap(slug), before)
        st = self.db.get_lock_state(slug, at(1, hours=1))
        self.assertTrue(st["locked"]); self.assertEqual(st["awaiting"], {"kind": "start", "to_week": 1})

    def test_an_upcoming_competition_that_may_start_itself_activates_at_the_lock(self):
        slug = S.clone(status="upcoming", auto=True)
        self.assertEqual(self.db.reconcile_competition(slug, at(1, seconds=-1))["applied"], [])
        rep = self.db.reconcile_competition(slug, at(1), triggered_by="auto")
        a = rep["applied"][0]
        self.assertEqual((a["kind"], a["activated"]), ("start", True))
        self.assertEqual(S.snap(slug)["status"], "active")
        self.assertEqual(self.db.get_rollover_log(slug)[0]["detail"]["before_status"], "upcoming")

    def test_force_applies_the_next_week_early_only_when_it_is_that_week_and_the_status_allows(self):
        slug = S.clone()
        before = S.snap(slug)
        wrong = self.db.reconcile_competition(slug, at(1, days=-5), force_next=True, expect_week=2)
        self.assertEqual(wrong["applied"], []); self.assertIn("is 1, not 2", wrong["skipped"]); self.assertEqual(S.snap(slug), before)
        right = self.db.reconcile_competition(slug, at(1, days=-5), force_next=True, expect_week=1, max_steps=1)
        self.assertEqual(self.applied_weeks(right), [1])
        nope = S.clone(status="upcoming")
        r = self.db.reconcile_competition(nope, at(1, days=-5), force_next=True)
        self.assertEqual(r["applied"], []); self.assertIn("does not start itself", r["skipped"])

    def test_it_refuses_to_start_a_competition_that_is_already_further_on(self):
        slug = S.clone()
        S.sql(f"INSERT INTO user_selections (competition_id,display_name,week_no) VALUES ('{slug}','Sai',3)")
        before = S.snap(slug)
        rep = self.db.reconcile_competition(slug, at(1))
        self.assertEqual(rep["applied"], []); self.assertIn("already has selections for week 3", rep["error"])
        self.assertEqual(S.snap(slug), before)

    def test_a_competition_with_no_schedule_or_a_finished_one_is_skipped(self):
        bare = S.clone(schedule=False)
        self.assertEqual(self.db.reconcile_competition(bare, at(3))["skipped"], "this competition has no lock schedule")
        done = S.clone(status="completed")
        rep = self.db.reconcile_competition(done, at(3))
        self.assertEqual(rep["applied"], []); self.assertIn("the season is over", rep["skipped"]); self.assertIn("has ended", rep["skipped"])
        with self.assertRaises(LookupError):
            self.db.reconcile_competition("nope", at(3))

    def test_points_for_week_1_exist_only_once_the_start_has_run(self):
        slug = S.clone()
        S.sql(f"INSERT INTO matches (competition_id,id,week_no,title,status) VALUES ('{slug}','{slug}m1',1,'Opener','completed');\n"
              f"INSERT INTO player_match_points (competition_id,match_id,player_id,week_no,base_pts,multiplier,final_pts) VALUES "
              f"('{slug}','{slug}m1','p1',1,10,1.0,10),('{slug}','{slug}m1','p5',1,4,1.0,4)")
        self.db.update_week_points(competition_id=slug)
        s = S.snap(slug)
        self.assertEqual([r["week_pts"] for r in s["rows"]], [0, 0], "before the start nobody has a team in week 1")        # the old design: week 1 never scored
        self.db.reconcile_competition(slug, at(1))
        s = S.snap(slug)
        pts = {r["display_name"]: r["week_pts"] for r in s["rows"] if r["week_no"] == 1}
        self.assertEqual(pts, {"Sai": 24, "Moe": 8, "Buddy": 0})     # Sai: p1 captain 10x2 + p5 4; Moe: p5 captain 4x2; Buddy: no team


@needs_db
class TheWeeklyRollovers(Base):
    def started(self, **kw):
        slug = S.clone(**kw)
        self.assertEqual(self.applied_weeks(self.db.reconcile_competition(slug, at(1))), [1])
        return slug

    def test_the_draft_becomes_the_next_weeks_team_and_earlier_weeks_stay_as_they_were(self):
        slug = self.started()
        self.db.save_next_week("Sai", TEAM_C, "p12", "p13", competition_id=slug)            # Sai changes his mind during week 1
        week1 = S.snap(slug)
        rep = self.db.reconcile_competition(slug, at(2), triggered_by="test")
        self.assertEqual(self.applied_weeks(rep), [2]); a = rep["applied"][0]
        self.assertEqual((a["kind"], a["members"], a["summary"]), ("rollover", 3, {"promoted": 2, "empty": 1}))
        s = S.snap(slug)
        sai2, moe2, buddy2 = row(s, "Sai", 2), row(s, "Moe", 2), row(s, "Buddy", 2)
        self.assertEqual((team(sai2), sai2["tw_cap_id"], sai2["tw_vc_id"]), (TEAM_C, "p12", "p13"))
        self.assertEqual((team(sai2, "nw"), sai2["nw_cap_id"]), (TEAM_C, "p12"), "this week's team also pre-fills the draft for the week after")
        self.assertEqual((team(moe2), team(buddy2)), (TEAM_B, []))
        for name in R.MEMBERS:                                                               # week 1 is exactly as it was
            self.assertEqual(row(s, name, 1), row(week1, name, 1))
        self.assertEqual(s["meta"][f"_last_rollover:{slug}"], at(2).isoformat())

    def test_no_draft_means_last_weeks_team_carries_forward(self):
        slug = self.started()
        self.db.save_next_week("Moe", [], None, None, competition_id=slug)                  # Moe clears his draft
        rep = self.db.reconcile_competition(slug, at(2))
        self.assertEqual(rep["applied"][0]["summary"], {"promoted": 1, "carried": 1, "empty": 1})
        moe2 = row(S.snap(slug), "Moe", 2)
        self.assertEqual((team(moe2), moe2["tw_cap_id"], moe2["tw_vc_id"]), (TEAM_B, "p5", "p6"))

    def test_a_member_who_joined_late_and_has_no_row_is_not_left_out(self):
        slug = self.started()
        self.db.upsert_member_auth("Newbie", "hash", 0, 0)
        try:
            rep = self.db.reconcile_competition(slug, at(2))
            self.assertEqual(rep["applied"][0]["members"], 4)
            s = S.snap(slug)
            self.assertEqual(weeks_of(s, "Newbie"), [2]); self.assertEqual(team(row(s, "Newbie", 2)), [])
            self.assertEqual(row(s, "Newbie", 2)["tw_cap_id"], None)
        finally:
            S.sql("DELETE FROM members WHERE username='Newbie'")

    def test_repeating_the_call_is_harmless(self):
        slug = self.started()
        self.db.reconcile_competition(slug, at(2))
        done = S.snap(slug)
        for later in (at(2, seconds=1), at(2, hours=5), at(3, seconds=-1)):
            rep = self.db.reconcile_competition(slug, later)
            self.assertEqual(rep["applied"], [], later); self.assertIn("nothing is due yet", rep["skipped"])
        self.assertEqual(S.snap(slug), done)

    def test_a_late_trigger_catches_up_in_order_and_the_next_call_carries_on(self):
        slug = S.clone()
        late = at(3, hours=3)                                                             # nothing ran for days: weeks 1, 2 and 3 are all overdue
        rep = self.db.reconcile_competition(slug, late, triggered_by="late", max_steps=2)
        self.assertEqual(self.applied_weeks(rep), [1, 2], "one call applies at most max_steps weeks, in order")
        rep = self.db.reconcile_competition(slug, late, triggered_by="late")
        self.assertEqual(self.applied_weeks(rep), [3])
        s = S.snap(slug)
        for name in R.MEMBERS:
            self.assertEqual(weeks_of(s, name), [1, 2, 3], name)
        self.assertEqual([(e["to_week"], rp.parse_utc(e["lock_at_utc"])) for e in s["log"]], [(1, LOCKS[1]), (2, LOCKS[2]), (3, LOCKS[3])])
        self.assertEqual(team(row(s, "Sai", 3)), TEAM_A); self.assertEqual(team(row(s, "Moe", 3)), TEAM_B)
        self.assertEqual(self.db.get_lock_state(slug, late)["applied_to"], 3)

    def test_the_whole_season_in_one_late_call_and_then_the_picks_close(self):
        slug = S.clone()
        end = at(6, days=1)
        rep = self.db.reconcile_competition(slug, end, triggered_by="very-late")
        self.assertEqual(self.applied_weeks(rep), [1, 2, 3, 4, 5, 6]); self.assertNotIn("error", rep)
        self.assertEqual([a["season_complete"] for a in rep["applied"]], [False] * 5 + [True])
        s = S.snap(slug)
        for name in R.MEMBERS:
            self.assertEqual(weeks_of(s, name), [1, 2, 3, 4, 5, 6], name)
        st = self.db.get_lock_state(slug, end)
        self.assertTrue(st["locked"] and st["season_over"]); self.assertIn("final week is under way", st["reason"])
        again = self.db.reconcile_competition(slug, end)
        self.assertEqual(again["applied"], []); self.assertIn("the season is over", again["skipped"])
        self.assertEqual(S.snap(slug), s)

    def test_two_competitions_never_interfere(self):
        a, b = S.clone(), S.clone()
        b_before = S.snap(b)
        self.db.reconcile_competition(a, at(2, hours=1))
        self.assertEqual(S.snap(b), b_before)
        self.assertEqual([e["to_week"] for e in self.db.get_rollover_log(a)], [1, 2])
        self.assertEqual(self.db.get_rollover_log(b), [])


@needs_db
class TheLockStateOnTheDatabase(Base):
    def test_it_follows_the_schedule_and_the_ledger(self):
        self.assertIsNone(self.db.get_lock_state(S.clone(schedule=False)), "no schedule: the old rules apply")
        self.assertIsNone(self.db.get_lock_state("nope"))
        slug = S.clone()
        s = self.db.get_lock_state(slug, at(1, seconds=-1))
        self.assertFalse(s["locked"]); self.assertEqual(s["next_lock"], LOCKS[1]); self.assertIsNone(s["awaiting"]); self.assertEqual((s["applied_to"], s["held"]), (0, []))
        s = self.db.get_lock_state(slug, at(1))
        self.assertTrue(s["locked"]); self.assertEqual(s["awaiting"], {"kind": "start", "to_week": 1}); self.assertIn("until the rollover has run", s["reason"])
        self.db.reconcile_competition(slug, at(1))
        s = self.db.get_lock_state(slug, at(1, minutes=1))
        self.assertFalse(s["locked"]); self.assertEqual((s["next_lock"], s["applied_to"]), (LOCKS[2], 1))


@needs_db
class TheOpsRecords(Base):
    def test_a_heartbeat_keeps_one_row_per_source_with_the_latest_detail(self):
        self.db.touch_heartbeat("test:zeta", "first"); self.db.touch_heartbeat("test:zeta", "second"); self.db.touch_heartbeat("test:alpha", "x" * 900)
        beats = {b["source"]: b for b in self.db.get_heartbeats()}
        self.assertEqual(beats["test:zeta"]["detail"], "second"); self.assertEqual(len(beats["test:alpha"]["detail"]), 500)
        self.assertEqual([b["source"] for b in self.db.get_heartbeats() if b["source"].startswith("test:")], ["test:alpha", "test:zeta"])
        self.assertIsNotNone(rp.parse_utc(beats["test:zeta"]["last_at"]))


@needs_db
class TheBootDdlIsAdditive(Base):
    """The tables are created when the app boots after the deploy. Nothing may be dropped, altered or rewritten, and booting twice must be harmless."""

    def test_every_boot_statement_only_creates_what_is_missing_or_adds_the_one_nullable_column(self):
        import re
        from db_manager import _SCHEMA
        stmts = [s.strip() for s in _SCHEMA.split(";") if s.strip()]
        self.assertGreater(len(stmts), 20)
        alters = [s for s in stmts if s.upper().startswith("ALTER")]
        self.assertEqual(alters, ["ALTER TABLE user_selections ADD COLUMN IF NOT EXISTS nw_touched_at TEXT"], "the ONLY change to an existing table: one nullable column")
        for s in stmts:
            if s not in alters:
                self.assertRegex(s, r"^CREATE (TABLE|INDEX) IF NOT EXISTS ", s[:70])
        new = re.findall(r"CREATE TABLE IF NOT EXISTS (competition_weeks|rollover_log|ops_heartbeat)\b", _SCHEMA)
        self.assertEqual(sorted(new), ["competition_weeks", "ops_heartbeat", "rollover_log"])

    def test_booting_on_a_database_from_the_previous_release_adds_only_the_touched_column(self):
        """The deploy rehearsal: a database shaped like production when 2.18.0 is live (this release's boot DDL minus the one ALTER), with data in every
        table including the Phase 1.5 ones, is booted by this release. Every table keeps its rows; no table changes except user_selections, which gains
        one nullable column that is empty for every existing row."""
        import re
        import uuid
        import psycopg
        from urllib.parse import quote
        from db_manager import _SCHEMA, DatabaseManager
        raw = os.environ["TEST_DATABASE_URL"]
        schema = "t_upg_" + uuid.uuid4().hex[:8]
        dsn = raw + ("&" if "?" in raw else "?") + "options=" + quote(f"-c search_path={schema}")
        old_ddl = [s for s in (x.strip() for x in _SCHEMA.split(";")) if s and not re.search(r"\bnw_touched_at\b", s)]
        admin = psycopg.connect(raw, autocommit=True)
        admin.execute(f'CREATE SCHEMA "{schema}"')
        con = psycopg.connect(dsn, autocommit=True)
        db = None
        try:
            for s in old_ddl:
                con.execute(s)
            con.execute("INSERT INTO competitions (slug,name,status,max_weeks) VALUES ('ipl_2026','IPL 2026','completed',10),('sa20_2027','SA20','upcoming',6)")
            con.execute("INSERT INTO members (username,passcode_hash,is_admin) VALUES ('Sai','h',1),('Moe','h',0)")
            con.execute("INSERT INTO user_selections (competition_id,display_name,week_no,tw_team_json,nw_team_json) VALUES ('ipl_2026','Sai',3,'[\"a\"]','[\"b\"]'),('sa20_2027','Sai',1,'[]','[\"c\"]')")
            con.execute("INSERT INTO meta (key,value) VALUES ('_saved:sa20_2027','2026-11-19T10:00:00+00:00')")
            con.execute("INSERT INTO sessions (token,username,expires_at) VALUES ('tok','Sai','2027-01-01T00:00:00+00:00')")
            con.execute("INSERT INTO competition_weeks (competition_id,week_no,lock_at_utc,first_match_utc) VALUES ('sa20_2027',1,'2027-01-17T12:30:00+00:00','2027-01-17T13:30:00+00:00')")
            con.execute("INSERT INTO rollover_log (competition_id,kind,to_week,lock_at_utc,applied_at,triggered_by,members) VALUES ('ipl_2026','start',1,'x','y','test',3)")
            con.execute("INSERT INTO ops_heartbeat (source,last_at,detail) VALUES ('rollover:admin','2026-11-20T09:00:00+00:00','sa20_2027: nothing due')")
            shape = lambda: con.execute("SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns WHERE table_schema=%s "   # noqa: E731
                                        "ORDER BY table_name, ordinal_position", (schema,)).fetchall()
            cols_before = shape()
            tables = sorted({r[0] for r in cols_before})
            names = {t: [r[1] for r in cols_before if r[0] == t] for t in tables}
            rows_of = lambda t: con.execute(f"SELECT {', '.join(names[t])} FROM {t} ORDER BY 1, 2").fetchall()                                         # noqa: E731
            data_before = {t: rows_of(t) for t in tables}
            db = DatabaseManager(dsn)                                                                  # the boot after the deploy
            cols_after = shape()
            self.assertEqual(sorted({r[0] for r in cols_after}), tables, "no table was added or removed")
            self.assertEqual([r for r in cols_after if r[0] != "user_selections"], [r for r in cols_before if r[0] != "user_selections"], "no other table changed a column")
            self.assertEqual([r for r in cols_after if r[0] == "user_selections"], [r for r in cols_before if r[0] == "user_selections"] + [("user_selections", "nw_touched_at", "text", "YES")],
                             "user_selections gained exactly one nullable text column, at the end")
            self.assertEqual({t: rows_of(t) for t in tables}, data_before, "no existing value in any table was touched")
            self.assertEqual(con.execute("SELECT count(*) FROM user_selections WHERE nw_touched_at IS NOT NULL").fetchone()[0], 0, "the new column is empty for every existing row")
            self.assertEqual(len(db.get_schedule("sa20_2027")), 1, "the Phase 1.5 schedule survived the boot")
            self.assertIsNone(db.get_history("Sai", competition_id="sa20_2027")["weeks"][0]["next_week_touched_at"], "an old draft reads as untouched")
        finally:
            try:
                if db is not None:
                    db._pool.close()
            finally:
                con.close()
                admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
                admin.close()

    def test_booting_twice_changes_nothing_and_the_new_tables_exist(self):
        from db_manager import DatabaseManager
        slug = S.clone()
        self.db.reconcile_competition(slug, at(1))
        before = S.snap(slug)
        raw = os.environ["TEST_DATABASE_URL"]
        from urllib.parse import quote
        second = DatabaseManager(raw + ("&" if "?" in raw else "?") + "options=" + quote(f"-c search_path={S.schema}"))
        try:
            self.assertEqual(S.snap(slug), before)
            with second._read() as con:
                tables = {r["table_name"] for r in con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s", (S.schema,)).fetchall()}
            self.assertTrue({"competition_weeks", "rollover_log", "ops_heartbeat"} <= tables)
        finally:
            second._pool.close()


if __name__ == "__main__":
    unittest.main()
