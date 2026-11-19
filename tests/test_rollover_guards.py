"""The weekly rollover may only run for a competition that is ACTIVE and whose season has STARTED (2026-11-19).

Why this exists: on scratch data the old endpoint moved an UPCOMING competition (week-1 anchor two months away) from week 1 to week 2 with one
authorised call, and a second forced call to week 3. Three of the four ways to trigger it were also unsafe: a browser timer (admin only, whichever
competition was selected), a red "Simulate rollover" admin button (a real write, no confirmation) and the scheduler token.

1. the pure rule (logic.rollover_engine.rollover_refusal): no database, no Flask
2. the browser timer and the red button must not come back (file guards)
3. the real route on scratch data (needs Flask and TEST_DATABASE_URL; skipped otherwise), run in a subprocess"""
import json
import os
import re
import subprocess
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from logic.rollover_engine import rollover_refusal  # noqa: E402

NOW = datetime(2027, 1, 25, 14, 5, tzinfo=timezone.utc)
ANCHOR = "2027-01-18T14:00:00+00:00"


def refusal(**kw):
    base = dict(slug="sa20_2027", status="active", week1_anchor_iso=ANCHOR, now=NOW, current_week=2, force=False, confirm="", is_admin=False)
    base.update(kw)
    return rollover_refusal(**base)


class WhenARolloverMayRun(unittest.TestCase):
    def test_an_active_competition_whose_season_has_started_may_roll(self):
        self.assertIsNone(refusal())

    def test_status_is_compared_case_insensitively(self):
        self.assertIsNone(refusal(status="Active"))
        self.assertIsNone(refusal(status=" active "))

    def test_an_upcoming_competition_never_rolls(self):
        r = refusal(status="upcoming")
        self.assertEqual(r["http"], 200)                       # a harmless no-op: a scheduler must not retry it
        self.assertIn("not active", r["reason"]); self.assertIn("sa20_2027", r["reason"]); self.assertIn("upcoming", r["reason"])

    def test_a_completed_competition_never_rolls(self):
        self.assertEqual(refusal(status="completed")["http"], 200)

    def test_an_unknown_status_is_refused(self):
        for s in ("", None, "paused"):
            self.assertEqual(refusal(status=s)["http"], 200, s)

    def test_the_season_must_have_started(self):
        r = refusal(now=datetime(2026, 11, 23, 14, 0, tzinfo=timezone.utc))      # the Monday that worried us: two months early
        self.assertEqual(r["http"], 200); self.assertIn("has not started", r["reason"]); self.assertIn("2027-01-18", r["reason"])

    def test_the_anchor_instant_itself_counts_as_started(self):
        self.assertIsNone(refusal(now=datetime(2027, 1, 18, 14, 0, tzinfo=timezone.utc)))
        self.assertIsNotNone(refusal(now=datetime(2027, 1, 18, 13, 59, 59, tzinfo=timezone.utc)))

    def test_a_missing_or_unreadable_start_date_is_refused_not_guessed(self):
        for a in ("", None, "not a date"):
            r = refusal(week1_anchor_iso=a)
            self.assertEqual(r["http"], 200, a); self.assertIn("no usable week-1 start date", r["reason"])

    def test_a_start_date_without_a_zone_is_read_as_utc(self):
        self.assertIsNone(refusal(week1_anchor_iso="2027-01-18T14:00:00"))
        self.assertIsNotNone(refusal(week1_anchor_iso="2027-01-25T14:30:00"))

    def test_status_is_checked_before_the_start_date(self):
        self.assertIn("not active", refusal(status="upcoming", week1_anchor_iso="2099-01-01T00:00:00+00:00")["reason"])


class WhenARolloverIsForced(unittest.TestCase):
    def test_the_scheduler_token_alone_is_not_enough(self):
        r = refusal(force=True, is_admin=False)
        self.assertEqual(r["http"], 403); self.assertIn("signed-in admin", r["reason"])

    def test_an_admin_must_confirm_which_week_it_moves_to(self):
        r = refusal(force=True, is_admin=True)
        self.assertEqual(r["http"], 400)
        self.assertIn("from week 2 to week 3", r["reason"]); self.assertIn("confirm=sa20_2027:3", r["reason"])

    def test_a_confirmation_for_another_week_is_refused(self):
        for c in ("sa20_2027:2", "sa20_2027:4", "other:3", "3", ""):
            self.assertEqual(refusal(force=True, is_admin=True, confirm=c)["http"], 400, c)

    def test_the_exact_confirmation_goes_through(self):
        self.assertIsNone(refusal(force=True, is_admin=True, confirm="sa20_2027:3"))
        self.assertIsNone(refusal(force=True, is_admin=True, confirm="  sa20_2027:3 "))

    def test_force_does_not_bypass_the_status_or_the_start_date(self):
        self.assertEqual(refusal(force=True, is_admin=True, confirm="sa20_2027:3", status="upcoming")["http"], 200)
        self.assertEqual(refusal(force=True, is_admin=True, confirm="sa20_2027:3", now=datetime(2026, 11, 23, tzinfo=timezone.utc))["http"], 200)


class TheBrowserAndTheAdminScreenCannotRollOver(unittest.TestCase):
    def read(self, *p):
        with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
            return f.read()

    def test_the_browser_no_longer_schedules_or_sends_a_rollover(self):
        js = self.read("Static", "ipl_glue.js")
        for needle in ("/api/rollover", "_executeRollover", "_scheduleNextRollover", "IplRollover", "ROLLOVER_HOUR", "IplApi.rollover"):
            self.assertTrue(needle not in js, 'the browser code still contains ' + needle)
        self.assertIsNone(re.search(r"rollover\s*:\s*function", js), "an API method called rollover is back")

    def test_the_admin_screen_has_no_rollover_button(self):
        html = self.read("templates", "index.html")
        for needle in ("rollover-btn", "_simulateRollover", "IplApi.rollover", "Simulate Monday", "Force-trigger"):
            self.assertTrue(needle not in html, 'the page still contains ' + needle)

    def test_the_route_asks_the_rule_before_doing_anything(self):
        src = self.read("routes.py")
        i = src.index('@bp.route("/api/rollover"')
        body = src[i:src.index('@bp.route("/api/matches-status"')]
        self.assertIsNotNone(re.search(r"refusal\s*=\s*rollover_refusal\(", body), "the route must call the rule and keep its answer")
        self.assertLess(body.index("rollover_refusal("), body.index("get_users_and_max_weeks"), "the guard must come before any member is touched")
        i_ref = body.index("if refusal:")
        self.assertIn('return jsonify(body),refusal["http"]', body[i_ref:i_ref + 500], "and it must return the refusal")


def _flask_available():
    try:
        import click  # noqa: F401
        import flask  # noqa: F401
        return True
    except Exception:
        return False


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL") and _flask_available(), "needs Flask and TEST_DATABASE_URL (run with `py -3.14`)")
class TheRealRouteOnScratchData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        raw = os.environ["TEST_DATABASE_URL"]
        cls.schema = "t_rollg_" + uuid.uuid4().hex[:8]
        cls.admin = psycopg.connect(raw, autocommit=True)
        cls.admin.execute(f'CREATE SCHEMA "{cls.schema}"')
        env = dict(os.environ, ROLLOVER_TOKEN="demo-token", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        env["DATABASE_URL"] = raw + ("&" if "?" in raw else "?") + "options=" + quote(f"-c search_path={cls.schema}")
        env.pop("HOSTED", None)
        p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tests", "helpers", "rollover_scenario.py")], env=env, capture_output=True, text=True, timeout=300)
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

    def test_an_upcoming_competition_is_not_rolled_even_when_forced(self):
        for key in ("upcoming", "upcoming_forced_by_token", "upcoming_forced_by_admin"):
            self.assertEqual(set(self.out[key]["weeks"].values()), {1}, key)               # nobody moved
        self.assertEqual(self.out["upcoming"]["resp"]["http"], 200)
        self.assertFalse(self.out["upcoming"]["resp"]["body"]["rolled"]); self.assertIn("not active", self.out["upcoming"]["resp"]["body"]["reason"])

    def test_an_active_competition_whose_season_has_not_started_is_not_rolled(self):
        r = self.out["active_future"]
        self.assertEqual(set(r["weeks"].values()), {1}); self.assertIn("has not started", r["resp"]["body"]["reason"])

    def test_a_completed_competition_is_left_alone(self):
        self.assertEqual(set(self.out["completed"]["weeks"].values()), {1}); self.assertFalse(self.out["completed"]["resp"]["body"]["rolled"])

    def test_an_active_started_competition_still_rolls_once(self):
        first, again = self.out["active_first"], self.out["active_again"]
        self.assertTrue(first["resp"]["body"]["rolled"]); self.assertEqual(first["resp"]["body"]["new_week_no"], 2)
        self.assertEqual(set(first["weeks"].values()), {2})
        self.assertFalse(again["resp"]["body"]["rolled"]); self.assertEqual(set(again["weeks"].values()), {2})   # the same deadline: no second roll

    def test_a_forced_rollover_needs_an_admin_and_the_exact_confirmation(self):
        self.assertEqual(self.out["forced_by_token"]["resp"]["http"], 403)
        self.assertEqual(self.out["forced_no_confirm"]["resp"]["http"], 400); self.assertIn("confirm=sa_active:3", self.out["forced_no_confirm"]["resp"]["body"]["error"])
        self.assertEqual(self.out["forced_wrong_confirm"]["resp"]["http"], 400)
        for key in ("forced_by_token", "forced_no_confirm", "forced_wrong_confirm"):
            self.assertEqual(set(self.out[key]["weeks"].values()), {2}, key)               # still week 2
        self.assertTrue(self.out["forced_ok"]["resp"]["body"]["rolled"]); self.assertEqual(set(self.out["forced_ok"]["weeks"].values()), {3})

    def test_a_caller_without_the_token_or_an_admin_session_is_rejected(self):
        self.assertIn(self.out["anonymous"]["resp"]["http"], (401, 403))
        self.assertEqual(set(self.out["anonymous"]["weeks"].values()), {3})

    def test_the_old_meta_keys_belong_to_the_first_competition_only(self):
        m = self.out["meta"]
        self.assertTrue(m["default_comp"].startswith("2026-05-18"))                        # ipl_2026 still reads it
        self.assertEqual(m["other_comp"], "")                                              # SA20 no longer inherits IPL's "last rollover"
        self.assertEqual(m["other_comp_default"], "never")


if __name__ == "__main__":
    unittest.main()
