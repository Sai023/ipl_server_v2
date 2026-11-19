"""Sessions last 60 days and are renewed while they are used (2026-11-19; Sai: "Make sessions last 2 months so it would cover an entire league").

Before this a session was a fixed 30 days from sign-in and was never renewed: someone who signed in on 19 Dec was signed out on Mon 18 Jan, a lock day.

1. the pure policy (logic.session_policy): no database, no Flask
2. the database layer must use it (file guards)
3. the real routes on scratch data (needs Flask and TEST_DATABASE_URL; skipped otherwise), run in a subprocess"""
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
from logic.session_policy import SESSION_RENEW_BELOW_DAYS, SESSION_TTL_DAYS, new_expiry, renewed_expiry  # noqa: E402

NOW = datetime(2027, 1, 18, 14, 0, tzinfo=timezone.utc)      # the first Monday lock of SA20


def at(days=0, **kw):
    return (NOW + timedelta(days=days, **kw)).isoformat()


class TheDecision(unittest.TestCase):
    def test_a_session_lasts_two_months(self):
        self.assertEqual(SESSION_TTL_DAYS, 60)                                             # Sai's decision: two months

    def test_it_is_renewed_only_when_under_a_month_remains(self):
        self.assertEqual(SESSION_RENEW_BELOW_DAYS, 30)

    def test_a_new_session_expires_in_60_days(self):
        self.assertEqual(new_expiry(NOW), at(60))

    def test_an_explicit_length_is_honoured(self):
        self.assertEqual(new_expiry(NOW, ttl_days=1), at(1))

    def test_the_expiry_is_an_iso_utc_string_that_sorts_like_the_database_expects(self):
        s = new_expiry(NOW)
        self.assertTrue(s.endswith("+00:00"), s)
        self.assertEqual(datetime.fromisoformat(s), NOW + timedelta(days=60))

    def test_a_naive_clock_is_read_as_utc(self):
        self.assertEqual(new_expiry(datetime(2027, 1, 18, 14, 0)), at(60))


class WhenASessionIsUsed(unittest.TestCase):
    def test_a_session_with_plenty_left_is_left_alone(self):
        for d in (60, 59, 45, 31):
            self.assertIsNone(renewed_expiry(at(d), NOW), d)                               # no write

    def test_exactly_30_days_left_is_left_alone(self):
        self.assertIsNone(renewed_expiry(at(30), NOW))

    def test_a_moment_under_30_days_is_renewed_to_a_full_60(self):
        self.assertEqual(renewed_expiry(at(30, microseconds=-1), NOW), at(60))

    def test_the_closer_to_the_end_the_same_full_renewal(self):
        for d in (29, 10, 1):
            self.assertEqual(renewed_expiry(at(d), NOW), at(60), d)
        self.assertEqual(renewed_expiry(at(0, seconds=1), NOW), at(60))                    # one second left

    def test_a_session_issued_under_the_old_30_day_rule_is_extended_on_its_next_use(self):
        issued = NOW - timedelta(days=12)
        old_expiry = (issued + timedelta(days=30)).isoformat()                             # 18 days left
        self.assertEqual(renewed_expiry(old_expiry, NOW), at(60))

    def test_a_session_that_is_over_is_never_revived(self):
        for e in (at(0), at(0, seconds=-1), at(-1), at(-400)):
            self.assertIsNone(renewed_expiry(e, NOW), e)

    def test_renewing_never_shortens_a_session(self):
        self.assertIsNone(renewed_expiry(at(20), NOW, ttl_days=10, renew_below_days=30))   # a shorter policy must not cut 20 days to 10

    def test_an_unreadable_expiry_is_left_alone(self):
        for bad in ("", None, "not a date", "2027-13-45", "   "):
            self.assertIsNone(renewed_expiry(bad, NOW), bad)

    def test_a_stored_expiry_without_a_zone_is_read_as_utc(self):
        self.assertEqual(renewed_expiry("2027-01-30T14:00:00", NOW), at(60))

    def test_a_stored_expiry_in_another_zone_is_converted(self):
        self.assertEqual(renewed_expiry("2027-01-30T16:00:00+02:00", NOW), at(60))         # the same instant as 14:00 UTC, 12 days away

    def test_a_naive_now_is_read_as_utc(self):
        self.assertEqual(renewed_expiry(at(5), datetime(2027, 1, 18, 14, 0)), at(60))

    def test_the_thresholds_can_be_changed(self):
        self.assertIsNone(renewed_expiry(at(8), NOW, ttl_days=14, renew_below_days=7))
        self.assertEqual(renewed_expiry(at(6), NOW, ttl_days=14, renew_below_days=7), at(14))


def simulate(start, uses):
    """Sign in at `start`, then open the app at each time in `uses`. Returns (when the member was signed out or None, how many times the row was written)."""
    expiry, writes = new_expiry(start), 0
    for t in uses:
        if datetime.fromisoformat(expiry) <= t:
            return t, writes
        newer = renewed_expiry(expiry, t)
        if newer:
            expiry, writes = newer, writes + 1
    return None, writes


class OverTheWholeSeason(unittest.TestCase):
    START = datetime(2026, 12, 19, 12, 0, tzinfo=timezone.utc)

    def test_the_old_rule_would_have_signed_this_member_out_on_a_lock_day(self):
        self.assertGreater(datetime(2027, 1, 18, 14, 0, tzinfo=timezone.utc), self.START + timedelta(days=30))   # 30 fixed days ended on the morning of the week-2 lock

    def test_a_member_who_checks_in_on_every_lock_is_never_signed_out_during_sa20(self):
        locks = [datetime(2027, 1, 17, 12, 30, tzinfo=timezone.utc)] + [datetime(2027, 1, 18, 14, 0, tzinfo=timezone.utc) + timedelta(days=7 * k) for k in range(0, 5)]
        out, _ = simulate(self.START, locks + [datetime(2027, 2, 21, 20, 0, tzinfo=timezone.utc)])
        self.assertIsNone(out)

    def test_a_member_who_opens_the_app_every_29_days_is_never_signed_out(self):
        out, _ = simulate(self.START, [self.START + timedelta(days=29 * k) for k in range(1, 15)])
        self.assertIsNone(out)

    def test_a_member_who_opens_the_app_every_day_costs_about_one_write_a_month(self):
        out, writes = simulate(self.START, [self.START + timedelta(days=k) for k in range(1, 366)])
        self.assertIsNone(out)
        self.assertTrue(10 <= writes <= 13, writes)

    def test_a_member_away_for_59_days_is_still_signed_in(self):
        out, _ = simulate(self.START, [self.START + timedelta(days=59)])
        self.assertIsNone(out)

    def test_a_member_away_for_61_days_is_signed_out(self):
        out, _ = simulate(self.START, [self.START + timedelta(days=61)])
        self.assertIsNotNone(out)


class TheDatabaseLayerUsesThePolicy(unittest.TestCase):
    def source(self, *p):
        with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
            return f.read()

    def body(self, name):
        src = self.source("db_manager.py")
        i = src.index("    def " + name + "(")
        return src[i:src.index("\n    def ", i + 10)]

    def test_a_new_session_gets_its_length_from_the_policy(self):
        b = self.body("create_session")
        self.assertIn("ttl_days: int = SESSION_TTL_DAYS", b)
        self.assertIn("new_expiry(", b)
        self.assertIsNone(re.search(r"ttl_days\s*:\s*int\s*=\s*\d", b), "a hard-coded session length is back")

    def test_using_a_session_renews_it(self):
        b = self.body("get_session")
        self.assertIn("renewed_expiry(", b)
        self.assertIn("UPDATE sessions SET expires_at", b)
        self.assertLess(b.index("DELETE FROM sessions WHERE expires_at"), b.index("renewed_expiry("), "expired rows must go before a session is renewed")

    def test_no_route_picks_its_own_session_length(self):
        src = self.source("routes.py")
        self.assertNotIn("ttl_days", src)
        self.assertIsNone(re.search(r"create_session\([^()]*,[^()]*,[^()]*\)", src), "a route passes its own length to create_session")

    def test_the_new_module_is_listed_by_the_version_endpoint(self):
        self.assertIn('"session_policy"', self.source("routes.py"))
        self.assertIn("SESSION_POLICY_VER", self.source("config.py"))


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
        cls.schema = "t_sess_" + uuid.uuid4().hex[:8]
        cls.admin = psycopg.connect(raw, autocommit=True)
        cls.admin.execute(f'CREATE SCHEMA "{cls.schema}"')
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        env["DATABASE_URL"] = raw + ("&" if "?" in raw else "?") + "options=" + quote(f"-c search_path={cls.schema}")
        env.pop("HOSTED", None)
        p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tests", "helpers", "session_scenario.py")], env=env, capture_output=True, text=True, timeout=300)
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

    def assertSixtyDays(self, days_left, label=""):
        self.assertTrue(59.9 <= days_left <= 60.01, f"{label}: {days_left} days left")

    def test_registering_and_signing_in_give_a_60_day_session(self):
        self.assertEqual(self.out["register"]["http"], 200); self.assertSixtyDays(self.out["register"]["days_left"], "register")
        self.assertEqual(self.out["login"]["http"], 200); self.assertSixtyDays(self.out["login"]["days_left"], "login")

    def test_a_session_with_plenty_left_is_not_rewritten(self):
        for key in ("fresh", "just_over_30"):
            r = self.out[key]
            self.assertEqual(r["http"], 200, key)
            self.assertEqual(r["before"], r["after"], key + ": the row was rewritten without need")

    def test_a_session_under_30_days_is_renewed_to_60_when_it_is_used(self):
        for key in ("ten_days_left", "just_under_30"):
            r = self.out[key]
            self.assertEqual(r["http"], 200, key)
            self.assertNotEqual(r["before"], r["after"], key); self.assertSixtyDays(r["days_left_after"], key)

    def test_a_session_issued_under_the_old_30_day_rule_is_extended_on_its_first_use(self):
        r = self.out["old_30_day_session"]
        self.assertEqual(r["http"], 200); self.assertSixtyDays(r["days_left_after"], "old session")

    def test_a_finished_session_is_rejected_and_removed_not_revived(self):
        r = self.out["expired"]
        self.assertEqual(r["http"], 401); self.assertIsNone(r["after"], "the expired row should be gone")

    def test_an_unknown_token_is_rejected(self):
        self.assertEqual(self.out["unknown"]["http"], 401)

    def test_other_peoples_finished_sessions_are_still_cleaned_up(self):
        self.assertTrue(self.out["cleanup"]["bystander_gone"])

    def test_changing_the_passcode_issues_a_fresh_60_day_session_and_retires_the_old_one(self):
        r = self.out["passcode_change"]
        self.assertEqual(r["http"], 200); self.assertSixtyDays(r["days_left_new"], "new session")
        self.assertEqual(r["old_token_http"], 401)


if __name__ == "__main__":
    unittest.main()
