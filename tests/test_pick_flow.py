"""The weekly pick flow on the server (2026-11-20, Phase 1.6 of the SA20 readiness plan): a draft that says whether the member has touched it, saves that
say which week they mean, and "Keep this XI".

Why: a carried-over draft could not be confirmed (so "no changes" could not be measured for reminders), and a save made by a page that had not noticed the
weekly handover landed in the wrong week without a word.

1. the database: `nw_touched_at` is set by a save and by a keep and nowhere else; a save or keep that states the wrong week writes nothing; the week-1 start
   resets the mark (the draft is next week's now) and an undo puts it back
2. the real routes on scratch data (needs Flask and TEST_DATABASE_URL; skipped otherwise), run in a subprocess

Needs a scratch Postgres: set TEST_DATABASE_URL (a DIRECT Neon/Postgres URL, run with `py -3.14`); the module uses and drops its own schema."""
import json
import os
import subprocess
import sys
import unittest
import uuid
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "helpers"))
import rollover_db as R  # noqa: E402
from rollover_db import at, row, team, TEAM_A, TEAM_C  # noqa: E402

S = None
needs_db = unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "set TEST_DATABASE_URL to run the database tests")


def setUpModule():
    global S
    if os.environ.get("TEST_DATABASE_URL"):
        S = R.ScratchDb()


def tearDownModule():
    if S is not None:
        S.close()


def _flask_available():
    try:
        import click  # noqa: F401
        import flask  # noqa: F401
        return True
    except Exception:
        return False


@needs_db
class TheTouchedDraft(unittest.TestCase):
    @property
    def db(self):
        return S.db

    def test_a_save_marks_the_draft_touched_and_the_history_says_when(self):
        slug = S.clone()
        self.assertIsNone(self.db.get_history("Sai", competition_id=slug)["weeks"][0]["next_week_touched_at"], "a draft nobody has saved or kept is untouched")
        r = self.db.save_next_week("Sai", TEAM_C, "p12", "p13", competition_id=slug)
        self.assertEqual(r["week_no"], 1)
        self.assertEqual(self.db.get_history("Sai", competition_id=slug)["weeks"][0]["next_week_touched_at"], r["touched_at"])
        self.assertIn(f"_saved:{slug}", S.snap(slug)["meta"], "other open pages are told something changed")

    def test_a_save_that_states_the_wrong_week_writes_nothing(self):
        slug = S.clone()
        before = S.snap(slug)
        self.assertEqual(self.db.save_next_week("Sai", TEAM_C, "p12", "p13", competition_id=slug, expect_week=2), {"conflict": True, "week_no": 1, "draft_week": 1},
                         "before the season starts a draft is for week 1")
        self.assertEqual(S.snap(slug), before)
        ok = self.db.save_next_week("Sai", TEAM_C, "p12", "p13", competition_id=slug, expect_week=1)
        self.assertEqual(ok["week_no"], 1); self.assertEqual(team(row(S.snap(slug), "Sai", 1), "nw"), TEAM_C)

    def test_keep_marks_the_draft_touched_and_changes_nothing_else(self):
        slug = S.clone()
        before = row(S.snap(slug), "Sai", 1)
        k = self.db.keep_next_week("Sai", competition_id=slug, expect_week=1)
        after = row(S.snap(slug), "Sai", 1)
        self.assertEqual(k["week_no"], 1); self.assertEqual(after["nw_touched_at"], k["touched_at"])
        for col in ("tw_team_json", "tw_cap_id", "tw_vc_id", "nw_team_json", "nw_cap_id", "nw_vc_id", "week_pts"):
            self.assertEqual(after[col], before[col], col)
        self.assertIn(f"_saved:{slug}", S.snap(slug)["meta"])

    def test_keep_refuses_when_there_is_nothing_to_keep_or_the_week_has_moved_on(self):
        slug = S.clone()                                                              # Buddy never drafted: he has no row at all
        before = S.snap(slug)
        self.assertEqual(self.db.keep_next_week("Buddy", competition_id=slug), {"nothing": True, "week_no": 1})
        self.assertEqual(self.db.keep_next_week("Sai", competition_id=slug, expect_week=3), {"conflict": True, "week_no": 1, "draft_week": 1})
        self.assertEqual(S.snap(slug), before)

    def test_after_a_handover_the_new_week_starts_untouched_and_a_keep_touches_that_week(self):
        slug = S.clone()
        self.db.save_next_week("Sai", TEAM_A, "p1", "p2", competition_id=slug)    # a pre-season pick: touched
        self.db.reconcile_competition(slug, at(2))                                  # the start, then week 2
        s = S.snap(slug)
        self.assertIsNone(row(s, "Sai", 1)["nw_touched_at"], "the start made the draft next week's: nobody has confirmed that one")
        self.assertIsNone(row(s, "Sai", 2)["nw_touched_at"], "a new week's row starts untouched")
        self.assertEqual(self.db.keep_next_week("Sai", competition_id=slug, expect_week=2), {"conflict": True, "week_no": 2, "draft_week": 3}, "drafts are for week 3 now")
        k = self.db.keep_next_week("Sai", competition_id=slug, expect_week=3)
        weeks = self.db.get_history("Sai", competition_id=slug)["weeks"]
        self.assertEqual((weeks[1]["next_week_touched_at"], weeks[0]["next_week_touched_at"]), (k["touched_at"], None))

    def test_without_a_lock_schedule_a_draft_is_for_the_week_after_the_current_one_exactly_as_the_page_works_it_out(self):
        slug = S.clone(schedule=False)                                              # the page uses the same rule: the first week is picked before it starts, later ones the week after
        self.assertEqual(self.db.save_next_week("Sai", TEAM_C, "p12", "p13", competition_id=slug, expect_week=1)["week_no"], 1)
        self.assertEqual(self.db.save_next_week("Sai", TEAM_C, "p12", "p13", competition_id=slug, expect_week=2), {"conflict": True, "week_no": 1, "draft_week": 1})
        S.sql(f"INSERT INTO user_selections (competition_id,display_name,week_no) VALUES ('{slug}','Sai',3)")      # the old rollover has moved this member to week 3
        self.assertEqual(self.db.save_next_week("Sai", TEAM_C, "p12", "p13", competition_id=slug, expect_week=4)["week_no"], 3)
        self.assertEqual(self.db.keep_next_week("Sai", competition_id=slug, expect_week=3), {"conflict": True, "week_no": 3, "draft_week": 4})

    def test_a_page_that_was_editing_the_pre_season_pick_cannot_save_it_as_next_weeks_draft_after_the_start(self):
        slug = S.clone()
        self.db.reconcile_competition(slug, at(1))                                  # the start: the row's week is still 1, but drafts are for week 2 now
        before = S.snap(slug)
        self.assertEqual(self.db.save_next_week("Sai", TEAM_C, "p12", "p13", competition_id=slug, expect_week=1), {"conflict": True, "week_no": 1, "draft_week": 2})
        self.assertEqual(self.db.keep_next_week("Sai", competition_id=slug, expect_week=1), {"conflict": True, "week_no": 1, "draft_week": 2})
        self.assertEqual(S.snap(slug), before, "neither wrote anything: a draft stated for the wrong week is never filed under the right one")
        self.assertEqual(self.db.save_next_week("Sai", TEAM_C, "p12", "p13", competition_id=slug, expect_week=2)["week_no"], 1)

    def test_a_save_that_races_a_handover_waits_for_it_and_is_judged_against_the_new_week(self):
        import threading
        import time
        slug = S.clone()
        result = {}
        t = threading.Thread(target=lambda: result.update(r=self.db.save_next_week("Sai", TEAM_C, "p12", "p13", competition_id=slug, expect_week=1)))
        with self.db._pool.connection() as con:                                     # a handover in progress: the competition row held exclusively, the start recorded
            con.execute("SELECT 1 FROM competitions WHERE slug=%s FOR UPDATE", (slug,))
            con.execute("INSERT INTO rollover_log (competition_id,kind,to_week,lock_at_utc,applied_at) VALUES (%s,'start',1,%s,%s)", (slug, R.rp.iso(R.LOCKS[1]), at(1).isoformat()))
            t.start()
            time.sleep(3)
            self.assertTrue(t.is_alive(), "the save is waiting for the handover, not racing it")
        t.join(timeout=60)                                                          # the handover has committed: the save now sees it
        self.assertEqual(result["r"], {"conflict": True, "week_no": 1, "draft_week": 2})
        self.assertEqual(team(row(S.snap(slug), "Sai", 1), "nw"), R.TEAM_A, "and wrote nothing")

    def test_undoing_the_start_puts_the_touched_mark_back(self):
        slug = S.clone(drafts=False)
        saved = self.db.save_next_week("Sai", TEAM_A, "p1", "p2", competition_id=slug)
        self.db.reconcile_competition(slug, at(1))
        self.assertIsNone(row(S.snap(slug), "Sai", 1)["nw_touched_at"])
        self.db.undo_transition(slug, 1)
        self.assertEqual(row(S.snap(slug), "Sai", 1)["nw_touched_at"], saved["touched_at"])


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL") and _flask_available(), "needs Flask and TEST_DATABASE_URL (run with `py -3.14`)")
class TheRealRoutesOnScratchData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        raw = os.environ["TEST_DATABASE_URL"]
        cls.schema = "t_pick_" + uuid.uuid4().hex[:8]
        cls.admin = psycopg.connect(raw, autocommit=True)
        cls.admin.execute(f'CREATE SCHEMA "{cls.schema}"')
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        env["DATABASE_URL"] = raw + ("&" if "?" in raw else "?") + "options=" + quote(f"-c search_path={cls.schema}")
        env.pop("HOSTED", None)
        p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tests", "helpers", "pick_flow_scenario.py")], env=env, capture_output=True, text=True, timeout=900)
        marker = "@@RESULT@@"
        cls.err = (p.stderr or "")[-1500:]
        cls.out = json.loads(p.stdout.split(marker)[1]) if marker in p.stdout else None

    @classmethod
    def tearDownClass(cls):
        try:
            cls.admin.execute(f'DROP SCHEMA "{cls.schema}" CASCADE')
        finally:
            cls.admin.close()

    def setUp(self):
        self.assertIsNotNone(self.out, "the scenario script did not finish:\n" + self.err)

    def test_the_scenario_started_the_season_and_the_start_reset_the_touched_mark(self):
        self.assertEqual(self.out["start_applied"], [1]); self.assertTrue(self.out["pre_season_touched"])
        sai, moe = self.out["after_start"]["Sai"], self.out["after_start"]["Moe"]
        self.assertEqual((sai["week"], sai["touched"], sai["draft_size"], sai["xi_size"]), (1, None, 11, 11))
        self.assertEqual((moe["touched"], moe["draft_size"], moe["xi_size"]), (None, 0, 0))

    def test_keeping_an_xi_marks_it_touched_and_leaves_the_team_alone(self):
        self.assertIsNone(self.out["before_keep"]["touched"])
        r = self.out["keep_ok"]
        self.assertEqual(r["http"], 200); self.assertTrue(r["body"]["ok"]); self.assertEqual(r["body"]["week_no"], 1, "the row week; the XI kept is next week's draft")
        self.assertEqual(self.out["after_keep"]["touched"], r["body"]["touched_at"])
        self.assertEqual(self.out["team_after_keep"], [f"p{i}" for i in range(1, 12)])

    def test_there_is_nothing_to_keep_for_a_member_with_no_team(self):
        r = self.out["keep_nothing"]
        self.assertEqual((r["http"], r["body"]["reason"], r["body"]["code"]), (409, "nothing_to_keep", 409)); self.assertIn("no team to keep", r["body"]["error"])

    def check_refused(self, key, current_week, draft_week):
        r = self.out[key]
        self.assertEqual((r["http"], r["body"]["reason"], r["body"]["current_week"], r["body"]["draft_week"]), (409, "week_changed", current_week, draft_week), key)
        self.assertIn(f"week {current_week} is now being played", r["body"]["error"]); self.assertIn(f"drafts are now for week {draft_week}", r["body"]["error"])

    def test_a_keep_that_states_the_wrong_week_is_refused_and_says_what_a_draft_is_for_now(self):
        # a page that loaded before the week-1 start still thinks its draft is for week 1: the row's week is 1 as well, only the draft moved on
        self.check_refused("keep_wrong_week", 1, 2); self.check_refused("keep_far_week", 1, 2)

    def test_a_save_that_states_the_wrong_week_is_refused_and_writes_nothing(self):
        self.check_refused("save_wrong_week", 1, 2)
        self.assertTrue(self.out["moe_team_unchanged"], "a refused save wrote nothing")

    def test_a_save_that_states_the_right_week_works_and_touches_the_draft(self):
        r = self.out["save_ok"]
        self.assertEqual((r["http"], r["body"]["ok"], r["body"]["week_no"]), (200, True, 1), "the row week is 1; the draft it files is for week 2"); self.assertTrue(r["body"]["touched_at"])
        self.assertEqual(self.out["after_save"]["touched"], r["body"]["touched_at"]); self.assertEqual(self.out["after_save"]["draft_size"], 11)

    def test_a_save_that_does_not_state_its_week_still_works(self):
        self.assertEqual(self.out["save_without_week"]["http"], 200)                     # an old page, or any other client, keeps working

    def test_a_week_that_is_not_a_whole_number_is_a_bad_request(self):
        for key in ("save_bad_week", "keep_bad_week"):
            self.assertEqual(self.out[key]["http"], 400, key); self.assertIn("whole number", self.out[key]["body"]["error"])

    def test_only_the_member_can_keep_their_own_xi(self):
        self.assertEqual((self.out["keep_not_your_team"]["http"], self.out["keep_not_your_team"]["body"]["reason"]), (403, "not_your_team"))
        self.assertEqual((self.out["keep_no_token"]["http"], self.out["keep_no_token"]["body"]["reason"]), (401, "session_ended"))

    def test_a_page_that_missed_the_handover_cannot_write_into_the_new_week(self):
        self.assertEqual(self.out["week2_applied"], [2])
        for key in ("stale_save", "stale_keep"):
            r = self.out[key]
            self.assertEqual((r["http"], r["body"]["reason"], r["body"]["current_week"], r["body"]["draft_week"]), (409, "week_changed", 2, 3), key)
        self.assertEqual(self.out["sai_team_after_stale"], [f"p{i}" for i in range(1, 12)], "the stale save did not replace the carried draft")
        self.assertEqual((self.out["week2_rows"]["Sai"]["week"], self.out["week2_rows"]["Sai"]["touched"]), (2, None), "the new week's draft starts unconfirmed")
        self.assertTrue(self.out["week1_row_still_touched"], "last week's confirmation is history, not erased")

    def test_while_picking_is_locked_neither_a_save_nor_a_keep_gets_through(self):
        for key in ("locked_save", "locked_keep"):
            r = self.out[key]
            self.assertEqual((r["http"], r["body"]["reason"]), (423, "locked"), key); self.assertIn("until the rollover has run", r["body"]["error"])


if __name__ == "__main__":
    unittest.main()
