"""Errors carry a machine `reason` (2026-11-20, Phase 1.2 of the SA20 readiness plan): the server says WHY in a word the page can act on
("session_ended", "locked", "rate_limited"...), and the page owns the sentence the member reads. The readable `error` text and the numeric
`code` are unchanged, so nothing that read them breaks.

1. the contract between the server and the page: every reason the server can send either has a sentence in Static/ui.js or is deliberately
   passed through as the server's own text, and the page has no sentence for a reason nobody sends
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

# Reasons the page shows its OWN sentence for (Static/ui.js REASON_COPY) ...
WITH_COPY = {"auth_required", "session_ended", "admin_only", "not_your_team", "rate_limited", "locked", "name_taken", "bad_credentials", "server_error",
             "not_found", "too_large", "bad_request",           # (hardening release, 2026-11-21): HTTP errors on /api/ now say what they are, as JSON
             "google_not_configured", "google_unreachable", "token_invalid", "token_expired", "nonce_invalid", "email_unverified",
             "google_account_in_use", "already_linked", "pin_retired",      # (Google sign-in, Phase 3, 2026-11-21): the page words each of these itself
             "claim_expired", "claim_locked", "member_taken", "temp_passcode"}      # (Google claim, 2026-11-21): the same, for claiming a name with a Google account
# ... and reasons whose server text is already specific and readable ("Choose a captain and a vice-captain before saving"): shown as sent.
PASS_THROUGH = {"name_invalid", "passcode_invalid", "credentials_required", "target_required", "member_not_found", "invalid_team",
                "week_changed", "nothing_to_keep",      # the last two (Phase 1.6): the page answers week_changed with its own dialog, and nothing_to_keep says it in the server's words
                "method_not_allowed",                    # (hardening release) only a wrong call from code can cause it: no member ever needs a sentence for it
                "member_signs_in_with_google", "not_linked"}   # (Google sign-in) admin-facing: the server's own sentence is shown as sent


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


def server_reasons():
    found = set()
    for f in ("routes.py", "base.py"):
        found |= set(re.findall(r'"reason"\s*:\s*"([a-z_]+)"', read(f)))
    return found


def page_reasons():
    block = re.search(r"var REASON_COPY = \{(.*?)\};", read("Static", "ui.js"), re.S)
    assert block, "REASON_COPY not found in Static/ui.js"
    return set(re.findall(r"^\s*([a-z_]+):\s*\"", block.group(1), re.M))


class TheServerAndThePageAgree(unittest.TestCase):
    def test_the_server_sends_exactly_the_reasons_we_know_about(self):
        self.assertEqual(server_reasons(), WITH_COPY | PASS_THROUGH,
                         "a reason was added or removed on the server: decide whether the page needs a sentence for it and update this list")

    def test_the_page_has_a_sentence_for_every_reason_that_needs_one(self):
        self.assertEqual(page_reasons(), WITH_COPY, "Static/ui.js REASON_COPY and the list in this test have drifted apart")

    def test_nothing_is_both_worded_by_the_page_and_passed_through(self):
        self.assertEqual(WITH_COPY & PASS_THROUGH, set())

    def test_the_readable_text_and_the_numeric_code_are_still_sent_next_to_every_reason(self):
        # a reason is only ever added to an existing error body, never instead of "error" / "code"
        for f in ("routes.py", "base.py"):
            for m in re.finditer(r'\{[^{}]*"reason"\s*:\s*"[a-z_]+"[^{}]*\}', read(f)):
                body = m.group(0)
                self.assertIn('"error"', body, body)
                self.assertIn('"code"', body, body)


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
        cls.schema = "t_reas_" + uuid.uuid4().hex[:8]
        cls.admin = psycopg.connect(raw, autocommit=True)
        cls.admin.execute(f'CREATE SCHEMA "{cls.schema}"')
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        env["DATABASE_URL"] = raw + ("&" if "?" in raw else "?") + "options=" + quote(f"-c search_path={cls.schema}")
        env.pop("HOSTED", None)
        p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tests", "helpers", "reasons_scenario.py")], env=env, capture_output=True, text=True, timeout=300)
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

    def check(self, key, http, reason):
        r = self.out[key]
        self.assertIsNotNone(r, key + ": no answer")
        self.assertEqual(r["http"], http, key)
        self.assertEqual(r["body"].get("reason"), reason, key)
        self.assertTrue(r["body"].get("error"), key + ": the readable text is still there")
        self.assertEqual(r["body"].get("code"), http, key + ": the numeric code is still there")

    def test_signing_in(self):
        self.check("no_token", 401, "auth_required")
        self.check("bad_token", 401, "session_ended")
        self.check("login_wrong", 401, "bad_credentials")
        self.check("login_missing", 400, "credentials_required")

    def test_who_may_do_what(self):
        self.check("not_admin", 403, "admin_only")
        self.check("other_team", 403, "not_your_team")

    def test_joining_and_passcodes(self):
        self.check("register_duplicate", 409, "name_taken")
        self.check("register_bad_name", 400, "name_invalid")
        self.check("register_bad_pin", 400, "passcode_invalid")
        self.check("pin_change_bad", 400, "passcode_invalid")
        self.check("reset_no_target", 400, "target_required")
        self.check("reset_unknown", 404, "member_not_found")

    def test_saving_a_team(self):
        self.check("locked", 423, "locked")
        self.assertTrue(self.out["locked"]["body"]["locked"])
        self.check("invalid_team", 422, "invalid_team")

    def test_the_rate_limit_says_so(self):
        r = self.out["rate_limited"]
        self.assertIsNotNone(r, "the limiter never refused: 80 login attempts from one address went through")
        self.assertEqual(r["http"], 429)
        self.assertEqual(r["body"].get("reason"), "rate_limited")


if __name__ == "__main__":
    unittest.main()
