"""Phase 2 (2026-11-22, release 2.28.0): the ops core on the REAL routes and the real database (needs Flask and TEST_DATABASE_URL; skipped otherwise). Three scenarios, each
in a SUBPROCESS on its own scratch schema:
  ops_core_scenario.py    who may call, the tick applies what is due once (and four at once apply it once), the status card's data, a failing competition does not
                          stop the others, the alert state is one atomic row, the new modules are listed
  ops_alerts_scenario.py  the alerts through a fake Telegram: one message per incident, a reminder, resolved, an event, a failed send is raised again, the test alert
  ops_drills_scenario.py  "kill the clock": each trigger ALONE lands the handover, all of them together land it once, the server's own clock loop
"""
import json
import os
import subprocess
import sys
import unittest
import uuid
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _flask_available():
    try:
        import click  # noqa: F401
        import flask  # noqa: F401
        return True
    except Exception:
        return False


NEEDS = unittest.skipUnless(os.environ.get("TEST_DATABASE_URL") and _flask_available(), "needs Flask and TEST_DATABASE_URL (run with `py -3.14`)")


def run_scenario(script, prefix):
    """(result dict or None, stderr tail): the scenario runs in a subprocess against a fresh scratch schema that is dropped afterwards."""
    import psycopg
    raw = os.environ["TEST_DATABASE_URL"]
    schema = prefix + uuid.uuid4().hex[:8]
    admin = psycopg.connect(raw, autocommit=True)
    try:
        admin.execute(f'CREATE SCHEMA "{schema}"')
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        env["DATABASE_URL"] = raw + ("&" if "?" in raw else "?") + "options=" + quote(f"-c search_path={schema}")
        for k in ("HOSTED", "OPS_TOKEN", "ROLLOVER_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_API_BASE", "PUBLIC_BASE_URL", "MOVED_TO_URL", "OPS_APP_CLOCK"):
            env.pop(k, None)
        p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tests", "helpers", script)], env=env, capture_output=True, text=True, timeout=1500)
        marker = "@@RESULT@@"
        out = json.loads(p.stdout.split(marker)[1]) if marker in p.stdout else None
        return out, (p.stderr or "")[-2500:]
    finally:
        try:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            admin.close()


@NEEDS
class TheCoreOnScratchData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out, cls.err = run_scenario("ops_core_scenario.py", "t_opsc_")

    def setUp(self):
        self.assertIsNotNone(self.out, "the scenario script did not finish:\n" + self.err)

    def r(self, key):
        return self.out[key]

    # A  who may call
    def test_a_nobody_gets_in_without_proof(self):
        for key, http, reason in (("a_no_credentials", 401, "auth_required"), ("a_wrong_token", 401, "session_ended"), ("a_member", 403, "admin_only"),
                                  ("a_status_no_credentials", 401, "auth_required"), ("a_status_member", 403, "admin_only"),
                                  ("a_test_alert_clock_token", 401, "session_ended"), ("a_test_alert_member", 403, "admin_only")):
            r = self.r(key)
            self.assertEqual((r["http"], r["body"]["reason"]), (http, reason), key)
        self.assertEqual(self.r("a_tick_is_post_only")["http"], 405)

    def test_a_the_clock_token_the_scheduler_token_and_an_admin_get_in(self):
        for key, source in (("a_ops_token", "supabase"), ("a_scheduler_token", "github"), ("a_admin", "admin"), ("a_scheduler_still_works", "clock")):
            r = self.r(key)
            self.assertEqual(r["http"], 200, key)
            self.assertTrue(r["body"]["ok"], key)
            self.assertEqual(r["body"]["source"], source, key)
        for key in ("a_status_ops_token", "a_status_admin"):
            self.assertEqual(self.r(key)["http"], 200, key)
            self.assertTrue(self.r(key)["body"]["ok"], key)

    def test_a_an_unset_token_matches_nothing(self):
        """With both tokens unset, an empty bearer (an empty equals an empty!) and any other bearer are refused like every other stranger."""
        self.assertEqual(self.r("a_empty_vs_unset")["http"], 401)
        self.assertEqual(self.r("a_anything_vs_unset")["http"], 401)

    def test_a_guesses_are_counted_and_a_good_token_is_not(self):
        self.assertEqual(self.r("a_guesses"), [401, 401, 401, 401, 429, 429, 429])
        self.assertEqual(self.r("a_a_good_token_is_not_counted"), 200)

    # B  the tick applies what is due, once
    def test_b_the_tick_applies_what_is_due_and_nothing_else(self):
        rep = self.r("b_tick1")
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["source"], "supabase")
        self.assertEqual(sorted((a["competition"], a["kind"], a["to_week"], a["activated"], a["members"]) for a in rep["applied"]),
                         [("c_auto", "start", 1, True, 2), ("c_due", "start", 1, False, 2)])
        self.assertEqual(rep["errors"], [])
        led = self.r("b_ledger_after_1")
        self.assertEqual(led["c_due"], [{"to_week": 1, "kind": "start", "triggered_by": "tick:supabase"}])
        self.assertEqual(led["c_auto"], [{"to_week": 1, "kind": "start", "triggered_by": "tick:supabase"}])
        for slug in ("c_future", "c_manual", "c_none"):
            self.assertEqual(led[slug], [], slug)
        st = self.r("b_status_after_1")
        self.assertEqual((st["c_due"], st["c_auto"], st["c_manual"], st["c_future"], st["c_none"]), ("active", "active", "upcoming", "active", "active"),
                         "the one that starts itself became active; the one that does not stays upcoming")

    def test_b_a_competition_that_cannot_start_is_reported_not_forced(self):
        rep = self.r("b_tick1")
        self.assertEqual((rep["health"], rep["mode"], rep["next_tick_in_s"], rep["alerts_sent"]), ("red", "busy", 60, 0))

    def test_b_a_repeat_changes_nothing(self):
        self.assertEqual(self.r("b_tick2")["applied"], [])
        self.assertTrue(self.r("b_tick2")["ok"])
        self.assertEqual(self.r("b_ledger_after_2"), self.r("b_ledger_after_1"))
        self.assertEqual(self.r("b_tick3_pc")["applied"], [])

    def test_b_every_clock_leaves_its_own_heartbeat(self):
        hb = self.r("b_heartbeats_after_1")
        self.assertTrue(hb["tick:supabase"].startswith("applied "), hb["tick:supabase"])
        self.assertIn("c_due week 1", hb["tick:supabase"])                     # the heartbeat names WHAT was applied, not just a number
        self.assertIn("c_auto week 1", hb["tick:supabase"])
        for source in ("tick:github", "tick:admin", "tick:clock"):
            self.assertEqual(hb[source], "nothing due", source)
        self.assertIn("tick:pc", self.r("b_heartbeat_sources"))
        nb = self.r("b_no_beat")
        self.assertEqual((nb["before"], nb["rep"], nb["after"]), (None, True, None), "beat=False leaves no heartbeat")

    def test_b_the_competitions_as_the_clock_reads_them(self):
        by = {c["slug"]: c for c in self.r("b_comps")}
        self.assertEqual(by["c_due"]["handled"], [1])
        self.assertEqual(by["c_future"]["handled"], [])
        self.assertTrue(by["c_auto"]["auto_activate"])
        self.assertFalse(by["c_manual"]["auto_activate"])
        self.assertEqual((by["c_none"]["n_rows"], by["c_manual"]["n_rows"]), (0, 3))
        self.assertEqual(by["c_manual"]["status"], "upcoming")

    def test_b_the_newest_handovers_with_what_each_did(self):
        by = {r["slug"]: r for r in self.r("b_recent")}
        self.assertEqual(sorted(by), ["c_auto", "c_due"])
        self.assertEqual((by["c_auto"]["kind"], by["c_auto"]["activated"], by["c_auto"]["members"], by["c_auto"]["triggered_by"]), ("start", True, 2, "tick:supabase"))
        self.assertEqual(by["c_due"]["summary"], {"promoted": 2})
        self.assertFalse(by["c_due"]["activated"])

    def test_b_a_week_an_admin_undid_is_held_and_never_applied_again_by_a_trigger(self):
        self.assertEqual([a for a in self.r("b_undo_tick")["applied"] if a["competition"] == "c_due"], [], "a held week is not applied again by the tick")
        self.assertEqual(self.r("b_undo_ledger"), [{"to_week": 1, "kind": "start", "triggered_by": "tick:supabase"}], "the ledger row is kept")
        self.assertEqual(self.r("b_undo_recent"), ["c_auto"], "an undone handover is no longer in force")
        self.assertEqual(self.r("b_undo_comp"), {"handled": [], "held": [1]})
        self.assertIsNone(self.r("b_undo_state")["due"])
        self.assertIn("undone by an admin", self.r("b_undo_state")["blocked"])

    # C  four at once
    def test_c_four_ticks_at_the_same_moment_apply_a_handover_once(self):
        self.assertEqual(self.r("c_applied_total"), 1)
        self.assertTrue(self.r("c_all_ok"))
        self.assertEqual(len(self.r("c_ledger")), 1)

    # D  over HTTP with the real clock
    def test_d_a_clock_names_itself_and_may_not_pose_as_the_server(self):
        pc = self.r("d_pc")
        self.assertEqual((pc["http"], pc["body"]["source"]), (200, "pc"))
        self.assertIn(("h_late", "start", 1), [(a["competition"], a["kind"], a["to_week"]) for a in pc["body"]["applied"]])
        self.assertEqual(self.r("d_app_is_not_claimable")["body"]["source"], "clock")
        self.assertEqual(self.r("d_odd_source")["body"]["source"], "clock")
        self.assertEqual(self.r("d_admin_source")["body"]["source"], "admin")
        self.assertEqual(self.r("d_ledger_h_late"), [{"to_week": 1, "kind": "start", "triggered_by": "tick:pc"}])
        hb = self.r("d_heartbeats")
        self.assertNotIn("tick:app", hb)
        self.assertEqual(hb["tick:pc"], "applied h_late week 1")

    def test_d_the_status_card_has_what_it_needs(self):
        for key in ("d_status", "d_status_token"):
            s = self.r(key)
            self.assertEqual(s["http"], 200, key)
            b = s["body"]
            self.assertEqual(sorted(b), ["address", "alerts", "clocks", "health", "now", "ok", "rhythm", "states", "tokens"], key)
            self.assertEqual(b["alerts"], {"telegram": "not_configured", "open_incidents": []})
            self.assertEqual(b["tokens"], {"ops_token": True, "scheduler_token": True})
            self.assertEqual(b["address"], {"public_base_url": "http://localhost", "source": "request"})
            self.assertEqual(b["health"]["level"], "red")
            self.assertTrue(any("Needs An Admin" in r and "does not start itself" in r for r in b["health"]["reasons"]), b["health"]["reasons"])
        b = self.r("d_status")["body"]
        by = {s["slug"]: s for s in b["states"]}
        self.assertEqual(by["h_late"]["last_run"]["triggered_by"], "tick:pc")
        self.assertEqual(by["h_late"]["last_run"]["kind"], "start")
        self.assertIsNone(by["c_future"]["last_run"])
        self.assertIsNone(by["c_manual"]["due"])
        self.assertIn("does not start itself", by["c_manual"]["blocked"])
        sources = {c["source"] for c in b["clocks"]}
        self.assertTrue({"admin", "clock", "github", "pc", "supabase"} <= sources, sources)
        self.assertTrue({"race0", "race1", "race2", "race3"} <= sources, "every clock that ever ticked is listed")
        self.assertTrue(all(c["age_s"] is not None and c["age_s"] < 900 for c in b["clocks"]))

    def test_d_a_competition_that_fails_does_not_stop_the_others(self):
        t = self.r("d_failing_tick")
        self.assertEqual(t["http"], 500)
        b = t["body"]
        self.assertFalse(b["ok"])
        self.assertEqual(b["code"], 500)
        self.assertEqual(len(b["errors"]), 1)
        self.assertTrue(b["errors"][0].startswith("h_1_bad: ValueError"), b["errors"])
        self.assertEqual(b["error"], b["errors"][0])
        self.assertEqual([(a["competition"], a["to_week"]) for a in b["applied"]], [("h_2_ok", 1)], "the healthy one in the same tick was applied, though the failing one came first")
        self.assertEqual(len(self.r("d_ledger_h_ok")), 1)
        self.assertEqual(self.r("d_ledger_h_bad"), [], "the failing one left nothing behind")
        hb = self.r("d_heartbeat_after_failure")
        self.assertTrue(hb.startswith("applied h_2_ok week 1 | ERROR h_1_bad: ValueError"), hb)

    def test_d_the_card_shows_the_failing_competition_as_late(self):
        b = self.r("d_status_after_failure")["body"]
        by = {s["slug"]: s for s in b["states"]}
        self.assertEqual(by["h_1_bad"]["due"], {"kind": "start", "to_week": 1})
        self.assertGreaterEqual(by["h_1_bad"]["overdue_s"], 600)
        self.assertEqual(b["health"]["level"], "red")
        self.assertTrue(any("Broken League" in r and "late" in r for r in b["health"]["reasons"]), b["health"]["reasons"])

    # E  the alert state is one atomic row
    def test_e_eight_writers_lose_nothing(self):
        self.assertEqual(self.r("e_announced"), list(range(8)))
        self.assertEqual(self.r("e_returns_the_messages"), ["x", "y"])

    # F
    def test_f_the_new_modules_are_listed(self):
        self.assertEqual(self.r("f_modules"), {"ops_plan": "1.0.0", "ops_notify": "1.0.0", "origin": "1.0.0"})


BASE_URL = "https://league.example.org"


@NEEDS
class TheAlertsOnScratchData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out, cls.err = run_scenario("ops_alerts_scenario.py", "t_opsa_")

    def setUp(self):
        self.assertIsNotNone(self.out, "the scenario script did not finish:\n" + self.err)

    def r(self, key):
        return self.out[key]

    def test_1_one_message_when_it_starts(self):
        s = self.r("s1_first")
        self.assertEqual((s["rep"]["ok"], s["rep"]["alerts_sent"], s["rep"]["health"]), (True, 1, "red"))
        self.assertEqual(len(s["sent"]), 1)
        self.assertRegex(s["sent"][0], r"^RED: Alpha League locked at \d\d:\d\d UTC but it is upcoming and does not start itself, so picks stay locked\. Open: " + BASE_URL + "$")

    def test_1_nothing_more_for_half_an_hour(self):
        for key in ("s1_a_minute_later", "s1_twenty_nine_minutes"):
            s = self.r(key)
            self.assertEqual((s["sent"], s["rep"]["alerts_sent"]), ([], 0), key)

    def test_1_a_reminder_after_half_an_hour(self):
        s = self.r("s1_half_an_hour")
        self.assertEqual(len(s["sent"]), 1)
        self.assertTrue(s["sent"][0].startswith("STILL FAILING (30 min): RED: Alpha League locked at"), s["sent"][0])
        self.assertTrue(s["sent"][0].endswith("Open: " + BASE_URL))

    def test_1_when_it_is_fixed_it_says_resolved_and_that_the_week_is_live(self):
        s = self.r("s1_fixed")
        self.assertEqual(s["applied"], [["a_comp", 1, True]])
        self.assertEqual(s["sent"], ["RESOLVED: Alpha League is no longer blocked.", "OK: Alpha League is live. Week 1 locked: 2 members, 0 with no XI."])
        self.assertEqual((self.r("s1_quiet_again")["sent"], self.r("s1_quiet_again")["rep"]["alerts_sent"]), ([], 0))
        self.assertEqual(self.r("s1_state")["incidents"], {})
        self.assertEqual(self.r("s1_state")["announced"], ["a_comp:1"])

    def test_2_a_send_that_fails_does_not_fail_the_tick_and_is_raised_again(self):
        f = self.r("s2_failed_send")
        self.assertEqual((f["rep"]["ok"], f["rep"]["alerts_sent"], f["sent"], f["attempts"]), (True, 0, [], [500]))
        self.assertEqual(self.r("s2_state_after_failure"), [], "an incident that was never delivered is not remembered as sent")
        r = self.r("s2_retry")
        self.assertEqual((r["rep"]["alerts_sent"], r["attempts"]), (1, [200]))
        self.assertTrue(r["sent"][0].startswith("RED: Beta League locked at"), r["sent"])
        self.assertEqual(self.r("s2_state_after_retry"), ["blocked:b_comp:1"])

    def test_3_the_test_alert(self):
        ok = self.r("s3_ok")
        self.assertEqual((ok["http"], ok["body"]["ok"], ok["body"]["configured"], ok["body"]["detail"]), (200, True, True, "sent"))
        self.assertEqual(self.r("s3_what_telegram_got"), ["TEST: an alert from the Ops card, asked for by Sai."])
        bad = self.r("s3_telegram_refuses")["body"]
        self.assertEqual((bad["ok"], bad["configured"], bad["detail"]), (False, True, "Telegram answered 401: Unauthorized"))
        off = self.r("s3_not_configured")["body"]
        self.assertEqual((off["ok"], off["configured"]), (False, False))
        self.assertIn("not set up", off["detail"])

    def test_4_without_telegram_nothing_is_recorded_so_what_is_wrong_is_raised_once_it_is_set_up(self):
        s = self.r("s4_not_configured")
        self.assertEqual((s["rep"]["alerts_sent"], s["sent"], s["attempts"]), (0, [], []))
        self.assertTrue(self.r("s4_state_unchanged"))
        c = self.r("s4_configured_now")
        self.assertEqual((c["rep"]["alerts_sent"], len(c["sent"])), (1, 1))
        self.assertTrue(c["sent"][0].startswith("RED: Gamma League locked at"), c["sent"])
        self.assertEqual(self.r("s4_state"), ["blocked:b_comp:1", "blocked:c_comp:1"])

    def test_5_a_clock_that_goes_quiet_in_a_busy_window_is_reported_and_heard_again(self):
        q = self.r("s5_quiet_clock")
        self.assertEqual(q["sent"], ["ORANGE: no tick from pc for 5 min. Open: " + BASE_URL])
        h = self.r("s5_heard_again")
        self.assertEqual(h["sent"], ["RESOLVED: pc is ticking again."])

    def test_what_telegram_was_sent_is_clean(self):
        self.assertFalse(self.r("tg_token_in_any_text"), "the bot token must never be part of a message")
        self.assertEqual(self.r("chat_ids"), ["-100555"])
        self.assertEqual(self.r("paths"), ["/bot987654321:AAH-alerts-test-token-01/sendMessage"])


@NEEDS
class KillTheClockDrills(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out, cls.err = run_scenario("ops_drills_scenario.py", "t_opsd_")

    def setUp(self):
        self.assertIsNotNone(self.out, "the scenario script did not finish:\n" + self.err)

    def r(self, key):
        return self.out[key]

    def test_each_trigger_alone_lands_the_handover(self):
        self.assertEqual(self.r("d1_http"), 200)
        self.assertEqual(self.r("d1_ledger"), [{"to_week": 1, "triggered_by": "tick:supabase"}], "an external clock alone")
        self.assertEqual(self.r("d2_ledger"), [{"to_week": 1, "triggered_by": "tick:app"}], "the server's own clock alone")
        self.assertIn("d2", self.r("d2_heartbeat") or "")
        self.assertEqual(self.r("d3_http"), 200)
        self.assertEqual(self.r("d3_ledger"), [{"to_week": 1, "triggered_by": "tick:admin"}], "an admin alone")
        self.assertEqual(self.r("d4_http"), 200)
        self.assertEqual(self.r("d4_ledger"), [{"to_week": 1, "triggered_by": "scheduler"}], "the older scheduler route alone")

    def test_every_trigger_at_once_lands_it_exactly_once(self):
        self.assertEqual(self.r("d5_codes"), [200, 200, 200, 200, 200])
        self.assertEqual(len(self.r("d5_ledger")), 1)
        self.assertEqual(self.r("d5_ledger")[0]["to_week"], 1)

    def test_the_servers_own_clock_never_sleeps_long_or_busy_and_survives_a_bad_cycle(self):
        self.assertEqual(self.r("l_idle_is_capped"), {"waits": [3, 300, 300], "runs": 2})
        self.assertEqual(self.r("l_busy_is_kept"), {"waits": [3, 60, 60], "runs": 2})
        self.assertEqual(self.r("l_never_busier_than_five_seconds"), {"waits": [3, 5, 5], "runs": 2})
        self.assertEqual(self.r("l_no_answer_means_a_minute"), {"waits": [3, 60, 60, 60], "runs": 3})
        self.assertEqual(self.r("l_a_failing_cycle_does_not_kill_it"), {"waits": [3, 60, 60, 60], "runs": 3})
        self.assertEqual(self.r("l_max_cycles"), {"runs": 3, "waits": 3})
        self.assertEqual(self.r("l_a_stopped_loop_never_runs"), 0)


if __name__ == "__main__":
    unittest.main()
