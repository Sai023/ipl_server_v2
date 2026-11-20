"""The weekly rollover may only run when the stored lock schedule says it is due, and only a signed-in admin may force it (2026-11-19, rewritten 2026-11-20).

Why this exists: on scratch data the old endpoint moved an UPCOMING competition (week-1 anchor two months away) from week 1 to week 2 with one
authorised call, and a second forced call to week 3. Three of the four ways to trigger it were also unsafe: a browser timer (admin only, whichever
competition was selected), a red "Simulate rollover" admin button (a real write, no confirmation) and the scheduler token. Phase 1.5 replaced the
"move everyone one week per call" loop with the lock schedule + ledger (logic/rollover_plan.py, db.reconcile_competition); these tests keep the
original promises true on the new design.

1. the browser timer and the red button must not come back (file guards), and the route must use the ledger, not the old loop
2. the real routes on scratch data (needs Flask and TEST_DATABASE_URL; skipped otherwise), run in a subprocess"""
import json
import os
import re
import subprocess
import sys
import unittest
import uuid
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


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

    def route_body(self):
        src = self.read("routes.py")
        i = src.index('@bp.route("/api/rollover"')
        return src[i:src.index('@bp.route("/api/matches-status"')]

    def test_the_route_applies_handovers_through_the_ledger_and_not_the_old_loop(self):
        body = self.route_body()
        self.assertIn("db.reconcile_competition(", body)
        for gone in ("insert_rollover_week", "get_users_and_max_weeks", "pick_active_team", "set_last_rollover", "rollover_refusal"):
            self.assertNotIn(gone, body, "the old move-everyone-one-week loop must not come back: " + gone)

    def test_a_forced_run_is_previewed_and_confirmed_before_it_is_applied(self):
        body = self.route_body()
        self.assertLess(body.index("dry_run=True"), body.index('request.args.get("confirm"'), "the week a forced run would apply is worked out first ...")
        self.assertLess(body.index("expect_week=expect_week"), body.index("max_steps=1 if force"), "... and the confirmed week is checked inside the transaction")
        self.assertIn("scheduler token is not enough", body)


def _flask_available():
    try:
        import click  # noqa: F401
        import flask  # noqa: F401
        return True
    except Exception:
        return False


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL") and _flask_available(), "needs Flask and TEST_DATABASE_URL (run with `py -3.14`)")
class TheRealRoutesOnScratchData(unittest.TestCase):
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
        p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tests", "helpers", "rollover_scenario.py")], env=env, capture_output=True, text=True, timeout=900)
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

    def body(self, key):
        return self.out[key]["resp"]["body"]

    def weeks(self, key):
        return set(self.out[key]["weeks"].values())

    # ── nothing rolls unless the schedule says so ───────────────────────────────────────────────────────────────
    def test_a_competition_with_no_lock_schedule_is_never_rolled(self):
        r = self.out["unscheduled"]
        self.assertEqual(r["resp"]["http"], 200); self.assertFalse(r["resp"]["body"]["rolled"]); self.assertIn("no lock schedule", r["resp"]["body"]["reason"])
        self.assertEqual((self.weeks("unscheduled"), r["ledger"]), ({1}, []))

    def test_an_upcoming_competition_that_does_not_start_itself_is_not_rolled_even_when_forced(self):
        for key in ("upcoming", "upcoming_forced"):
            self.assertEqual((self.weeks(key), self.out[key]["ledger"], self.out[key]["status"]), ({1}, [], "upcoming"), key)
            self.assertEqual(self.out[key]["resp"]["http"], 200); self.assertFalse(self.body(key)["rolled"]); self.assertIn("does not start itself", self.body(key)["reason"])

    def test_a_competition_whose_first_lock_is_a_month_away_is_not_rolled(self):
        self.assertEqual((self.weeks("future"), self.out["future"]["ledger"]), ({1}, [])); self.assertIn("nothing is due yet", self.body("future")["reason"])

    def test_a_completed_competition_is_left_alone(self):
        self.assertEqual((self.weeks("completed"), self.out["completed"]["ledger"]), ({1}, [])); self.assertFalse(self.body("completed")["rolled"])
        self.assertIn("the season is over", self.body("completed")["reason"])

    # ── what a due rollover does ─────────────────────────────────────────────────────────────────────────────────
    def test_an_upcoming_competition_that_starts_itself_activates_at_the_lock_and_only_once(self):
        first, again = self.out["auto_first"], self.out["auto_again"]
        b = first["resp"]["body"]
        self.assertTrue(b["rolled"]); self.assertEqual(b["new_week_no"], 1); self.assertEqual([a["kind"] for a in b["applied"]], ["start"]); self.assertTrue(b["applied"][0]["activated"])
        self.assertEqual((first["status"], first["ledger"], set(first["weeks"].values())), ("active", [1], {1}))
        self.assertFalse(again["resp"]["body"]["rolled"]); self.assertEqual(again["ledger"], [1])

    def test_overdue_weeks_are_caught_up_in_order_in_one_call_and_a_second_call_does_nothing(self):
        first, again = self.out["catchup_first"], self.out["catchup_again"]
        b = first["resp"]["body"]
        self.assertEqual((b["rolled"], b["new_week_no"], [a["to_week"] for a in b["applied"]]), (True, 2, [1, 2]))
        self.assertEqual((first["ledger"], set(first["weeks"].values())), ([1, 2], {2}))
        self.assertFalse(again["resp"]["body"]["rolled"]); self.assertEqual((again["ledger"], set(again["weeks"].values())), ([1, 2], {2}))

    def test_a_dry_run_reports_what_would_happen_and_changes_nothing(self):
        p, real = self.out["dry_preview"], self.out["dry_real"]
        b = p["resp"]["body"]
        self.assertTrue(b["dry_run"]); self.assertFalse(b["rolled"]); self.assertEqual([a["to_week"] for a in b["applied"]], [1, 2])
        self.assertEqual((p["ledger"], set(p["weeks"].values())), ([], {1}), "nothing was written")
        self.assertTrue(real["resp"]["body"]["rolled"]); self.assertEqual((real["ledger"], set(real["weeks"].values())), ([1, 2], {2}))

    # ── who may force it ─────────────────────────────────────────────────────────────────────────────────────────
    def test_a_forced_rollover_needs_an_admin_and_the_exact_confirmation(self):
        self.assertEqual(self.out["forced_by_token"]["resp"]["http"], 403); self.assertIn("signed-in admin", self.body("forced_by_token")["error"])
        self.assertEqual(self.out["forced_no_confirm"]["resp"]["http"], 400); self.assertIn("confirm=sa_catchup:3", self.body("forced_no_confirm")["error"])
        self.assertEqual(self.out["forced_wrong_confirm"]["resp"]["http"], 400)
        for key in ("forced_by_token", "forced_no_confirm", "forced_wrong_confirm"):
            self.assertEqual((self.weeks(key), self.out[key]["ledger"]), ({2}, [1, 2]), key)            # still week 2
        ok = self.out["forced_ok"]["resp"]["body"]
        self.assertEqual((ok["rolled"], ok["new_week_no"], [a["to_week"] for a in ok["applied"]]), (True, 3, [3]), "exactly the confirmed week, though its lock is ten days away")
        self.assertEqual((self.weeks("forced_ok"), self.out["forced_ok"]["ledger"]), ({3}, [1, 2, 3]))

    def test_a_caller_without_the_token_or_an_admin_session_is_rejected(self):
        self.assertIn(self.out["anonymous"]["resp"]["http"], (401, 403))
        self.assertEqual((self.weeks("anonymous"), self.out["anonymous"]["ledger"]), ({3}, [1, 2, 3]))

    # ── the lock the members feel ────────────────────────────────────────────────────────────────────────────────
    def test_picking_stays_locked_from_the_lock_until_the_handover_has_run(self):
        before = self.out["lock_state_before"]
        self.assertTrue(before["managed"] and before["locked"]); self.assertEqual(before["awaiting"], {"kind": "start", "to_week": 1})
        r = self.out["lock_save_awaiting"]
        self.assertEqual(r["http"], 423); self.assertEqual(r["body"]["reason"], "locked"); self.assertIn("until the rollover has run", r["body"]["error"])

    def test_after_the_handover_the_next_week_opens_and_the_countdown_moves_on(self):
        self.assertTrue(self.out["lock_roll"]["resp"]["body"]["rolled"])
        after = self.out["lock_state_after"]
        self.assertTrue(after["managed"]); self.assertFalse(after["locked"]); self.assertIsNone(after["awaiting"]); self.assertEqual(after["applied_to"], 1)
        self.assertGreater(after["next_lock_utc"], self.out["lock_state_before"]["next_lock_utc"])
        r = self.out["lock_save_open"]
        self.assertEqual(r["http"], 200); self.assertTrue(r["body"]["ok"])
        self.assertEqual(self.out["saved_team"], [f"p{i}" for i in range(11, 0, -1)], "the save landed")

    def test_a_competition_with_no_schedule_still_follows_the_old_rules(self):
        self.assertEqual(self.out["unsched_save_open"]["http"], 200)                                     # pre-season: drafting is open
        d = self.out["unsched_state"]
        self.assertNotIn("managed", d); self.assertTrue(d["next_lock_utc"])

    # ── the admin "schedule" action (the real SA20 fixtures) ─────────────────────────────────────────────────────
    LOCKS = [(1, "2027-01-17T12:30:00+00:00"), (2, "2027-01-18T14:00:00+00:00"), (3, "2027-01-25T14:00:00+00:00"),
             (4, "2027-02-01T14:00:00+00:00"), (5, "2027-02-08T14:00:00+00:00"), (6, "2027-02-15T14:00:00+00:00")]

    def test_the_schedule_action_is_admin_only_and_a_dry_run_by_default(self):
        self.assertEqual(self.out["sched_not_admin"]["http"], 403)
        r = self.out["sched_dry"]
        self.assertEqual(r["http"], 200); b = r["body"]
        self.assertTrue(b["ok"] and b["dry_run"]); self.assertEqual(b["status"], "upcoming")
        self.assertEqual([(w["week_no"], w["lock_utc"]) for w in b["weeks"]], self.LOCKS)
        self.assertEqual([w["matches"] for w in b["weeks"]], [1, 8, 7, 7, 7, 4], "the fixture weeks as the import puts them")
        self.assertEqual(b["weeks"][0]["lock_sast"], "Sun 17 Jan 2027 14:30 SAST"); self.assertEqual(b["weeks"][1]["lock_sast"], "Mon 18 Jan 2027 16:00 SAST")
        self.assertEqual((b["changed_weeks"], b["already_run"], b["auto_activate"]), ([1, 2, 3, 4, 5, 6], [], True), "reports what it WOULD do")
        self.assertEqual(self.out["sched_after_dry"], {"weeks": [], "auto": False}, "and wrote nothing")

    def test_without_stored_fixtures_the_schedule_can_be_previewed_but_warns(self):
        b = self.out["sched_before_import"]["body"]
        self.assertTrue(b["ok"] and b["dry_run"]); self.assertTrue(any("no matches are stored" in w for w in b["warnings"]), b["warnings"])

    def test_saving_the_schedule_stores_the_locks_and_the_auto_activate_flag_and_a_repeat_changes_nothing(self):
        r = self.out["sched_real"]
        self.assertEqual(r["http"], 200); self.assertFalse(r["body"]["dry_run"])
        self.assertEqual(self.out["sched_after_real"], {"weeks": [list(x) for x in self.LOCKS], "auto": True})
        again = self.out["sched_again"]["body"]
        self.assertEqual(again["changed_weeks"], []); self.assertTrue(again["auto_activate"], "leaving auto_activate out keeps the setting")
        self.assertEqual(self.out["sched_after_again"], self.out["sched_after_real"])

    def test_a_schedule_that_disagrees_with_the_stored_matches_is_refused_and_writes_nothing(self):
        r = self.out["sched_mismatch"]
        self.assertEqual(r["http"], 409); self.assertIn("sa20_sched_m05", r["body"]["error"]); self.assertIn("stored in week 3", r["body"]["error"])
        self.assertEqual(self.out["sched_after_mismatch"], self.out["sched_after_real"])

    def test_the_schedule_action_rejects_bad_requests(self):
        self.assertEqual(self.out["sched_missing_file"]["http"], 400); self.assertIn("fixture list", self.out["sched_missing_file"]["body"]["error"])
        self.assertEqual(self.out["sched_bad_flag"]["http"], 400); self.assertIn("true or false", self.out["sched_bad_flag"]["body"]["error"])

    # ── bookkeeping ──────────────────────────────────────────────────────────────────────────────────────────────
    def test_every_trigger_leaves_a_heartbeat(self):
        beats = self.out["heartbeats"]
        self.assertIn("rollover:scheduler", beats); self.assertIn("rollover:admin", beats)
        self.assertIn("applied week", beats["rollover:admin"])

    def test_the_old_meta_keys_belong_to_the_first_competition_only(self):
        m = self.out["meta"]
        self.assertTrue(m["default_comp"].startswith("2026-05-18"))                        # ipl_2026 still reads it
        self.assertEqual(m["other_comp"], "")                                              # SA20 no longer inherits IPL's "last rollover"
        self.assertEqual(m["other_comp_default"], "never")


if __name__ == "__main__":
    unittest.main()
