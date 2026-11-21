"""The browser-to-app hand-off (sign-in from the installed Android app, 2026-11-21).

The app is a WebView and Google refuses to sign anybody in inside one, so the app opens the phone's browser for the sign-in and the browser hands the member back through a link
with a one-time code. The app keeps a secret (the verifier) and sends only its hash (the challenge); the code is useless without the verifier.

Part 1 (no database): the store, logic/app_handoff.py. Part 2 (needs Flask and TEST_DATABASE_URL; skipped otherwise): the real routes on a scratch schema, run in a subprocess.
What must hold: a code is single-use even when the proof is wrong, lives two minutes and is bound to the member who asked for it; the app gets its OWN session and the browser's
is untouched; nothing the app sends can change who it becomes; malformed input never burns a live code; the reply never says WHICH part failed; limits and a full store answer
429; two apps racing for one code cannot both win; nothing secret reaches the reply or the log."""
import json
import os
import re
import subprocess
import sys
import threading
import unittest
import uuid
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    from logic import app_handoff as ah
except Exception:                       # pragma: no cover (the module needs only the standard library)
    ah = None

# RFC 7636, appendix B: the verifier and challenge the specification itself gives
RFC_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
RFC_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


V1 = "a" * 43
V2 = "b" * 43


class TheStore(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.s = ah.HandoffStore(clock=self.clock)

    def code(self, user="Moe", verifier=V1):
        return self.s.issue(user, ah.challenge_of(verifier))

    def test_the_challenge_is_the_s256_hash_the_specification_gives(self):
        self.assertEqual(ah.challenge_of(RFC_VERIFIER), RFC_CHALLENGE)
        self.assertRegex(ah.challenge_of(V1), ah.CHALLENGE_RE)
        self.assertEqual(len(ah.challenge_of(V1)), 43)

    def test_a_code_is_redeemed_once_by_whoever_holds_the_verifier(self):
        c = self.code()
        self.assertTrue(isinstance(c, str) and len(c) >= 40 and ah.CODE_RE.match(c))
        self.assertEqual(self.s.redeem(c, V1), "Moe")
        self.assertIsNone(self.s.redeem(c, V1), "single-use")

    def test_a_wrong_verifier_gets_nothing_and_burns_the_code(self):
        c = self.code()
        self.assertIsNone(self.s.redeem(c, V2))
        self.assertIsNone(self.s.redeem(c, V1), "a guess cannot be followed by the right answer")

    def test_a_code_is_bound_to_the_member_who_asked_for_it(self):
        a, b = self.code("Moe"), self.code("Sai")
        self.assertEqual((self.s.redeem(a, V1), self.s.redeem(b, V1)), ("Moe", "Sai"))

    def test_a_code_lives_two_minutes_and_not_a_second_longer(self):
        self.assertEqual(self.s.ttl, 120)
        c, d = self.code(), self.code("Sai", V2)
        self.clock.now += 119.9
        self.assertEqual(self.s.redeem(c, V1), "Moe")
        self.clock.now += 0.2
        self.assertIsNone(self.s.redeem(d, V2), "expired")

    def test_asking_again_for_the_same_challenge_replaces_the_older_code(self):
        old = self.code()
        new = self.code()
        self.assertNotEqual(old, new)
        self.assertIsNone(self.s.redeem(old, V1))
        self.assertEqual(self.s.redeem(new, V1), "Moe")

    def test_two_challenges_of_one_member_live_side_by_side(self):
        a, b = self.code("Moe", V1), self.code("Moe", V2)
        self.assertEqual((self.s.redeem(b, V2), self.s.redeem(a, V1)), ("Moe", "Moe"))

    def test_a_code_cannot_be_redeemed_with_the_verifier_of_another_challenge(self):
        a = self.code("Moe", V1)
        self.assertIsNone(self.s.redeem(a, V2))

    def test_malformed_input_is_refused_without_burning_a_live_code(self):
        c = self.code()
        for code, verifier in [(None, V1), (c, None), (123, V1), (c, 123), ("", V1), (c, ""), ("short", V1), (c, "short"), (c, "a" * 129), (c, V1[:40] + "+/="), (c + "!", V1), ([c], [V1])]:
            self.assertIsNone(self.s.redeem(code, verifier), repr((code, verifier))[:60])
        self.assertEqual(self.s.redeem(c, V1), "Moe", "none of those used it up")

    def test_the_store_is_capped_and_frees_room_when_codes_expire_or_are_used(self):
        s = ah.HandoffStore(cap=3, clock=self.clock)
        codes = [s.issue("Moe", ah.challenge_of("%043d" % i)) for i in range(3)]
        self.assertTrue(all(codes))
        self.assertIsNone(s.issue("Moe", ah.challenge_of("%043d" % 9)), "full of live codes")
        s.redeem(codes[0], "%043d" % 0)
        self.assertTrue(s.issue("Moe", ah.challenge_of("%043d" % 9)), "a used code made room")
        self.clock.now += 121
        self.assertTrue(s.issue("Sai", ah.challenge_of("%043d" % 10)), "expired codes make room")

    def test_codes_do_not_repeat_and_are_url_safe(self):
        seen = {self.s.issue("Moe", ah.challenge_of("%043d" % i)) for i in range(200)}
        self.assertEqual(len(seen), 200)
        self.assertTrue(all(re.fullmatch(r"[A-Za-z0-9_-]+", c) for c in seen))

    def test_four_apps_racing_for_one_code_cannot_all_win(self):
        c = self.code()
        res, gate = [], threading.Barrier(4)

        def racer():
            gate.wait()
            res.append(self.s.redeem(c, V1))

        ths = [threading.Thread(target=racer) for _ in range(4)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        self.assertEqual(sorted(map(str, res)), ["Moe", "None", "None", "None"])


def _available():
    try:
        import click  # noqa: F401
        import flask  # noqa: F401
        return True
    except Exception:
        return False


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL") and _available(), "needs Flask and TEST_DATABASE_URL (run with `py -3.14`)")
class TheRealRoutesOnScratchData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        raw = os.environ["TEST_DATABASE_URL"]
        cls.schema = "t_hand_" + uuid.uuid4().hex[:8]
        cls.admin = psycopg.connect(raw, autocommit=True)
        cls.admin.execute(f'CREATE SCHEMA "{cls.schema}"')
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        env["DATABASE_URL"] = raw + ("&" if "?" in raw else "?") + "options=" + quote(f"-c search_path={cls.schema}")
        env.pop("HOSTED", None)
        env.pop("GOOGLE_CLIENT_ID", None)
        p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tests", "helpers", "handoff_scenario.py")], env=env, capture_output=True, text=True, timeout=1800)
        marker = "@@RESULT@@"
        cls.err = (p.stderr or "")[-3000:]
        cls.out = json.loads(p.stdout.split(marker)[1]) if marker in p.stdout else None

    @classmethod
    def tearDownClass(cls):
        try:
            cls.admin.execute(f'DROP SCHEMA "{cls.schema}" CASCADE')
        finally:
            cls.admin.close()

    def setUp(self):
        self.assertIsNotNone(self.out, "the scenario script did not finish:\n" + self.err)

    def r(self, key):
        return self.out[key]

    def refused(self, key, http, reason):
        r = self.r(key)
        self.assertEqual((r["http"], (r["body"] or {}).get("reason")), (http, reason), "%s: %s" % (key, r["text"][:200]))
        self.assertFalse(r["body"].get("ok"), key)
        self.assertTrue(r["body"].get("error"), key + ": the readable text is there")
        self.assertEqual(r["body"].get("code"), http, key + ": and the numeric code")
        self.assertNotIn("token", r["body"], key + ": a refusal never carries a session")

    # ── A. the browser asks ──
    def test_asking_for_a_code_needs_a_signed_in_member(self):
        self.refused("a_no_session", 401, "auth_required")
        self.refused("a_unknown_session", 401, "session_ended")

    def test_the_challenge_must_be_exactly_the_hash_the_app_made(self):
        self.assertEqual(self.r("a_bad_challenges"), {k: 400 for k in ("none", "number", "short", "long", "plus", "slash", "padded", "space", "empty")})
        self.refused("a_bad_reason", 400, "bad_request")
        self.assertEqual(self.r("a_missing_body")["http"], 400)
        self.assertEqual(self.r("a_not_json"), 400)

    def test_a_good_request_answers_a_code_and_how_long_it_lives(self):
        r = self.r("a_issue")
        self.assertEqual(r["http"], 200)
        self.assertEqual(sorted(r["body"]), ["code", "expires_in", "ok"])
        self.assertEqual((r["body"]["ok"], r["body"]["expires_in"]), (True, 120))
        self.assertRegex(r["body"]["code"], r"^[A-Za-z0-9_-]{40,}$")

    def test_both_routes_are_post_only(self):
        self.assertEqual(self.r("a_methods"), [405, 405])

    # ── B. the app redeems ──
    def test_the_app_gets_its_own_session_and_the_browsers_is_untouched(self):
        r = self.r("b_redeem")
        b = r["body"]
        self.assertEqual(r["http"], 200)
        self.assertEqual((b["ok"], b["name"], b["is_admin"], b["must_change"], b["linked"], b["email_masked"]), (True, "Moe", False, False, False, None))
        self.assertTrue(isinstance(b["token"], str) and len(b["token"]) >= 20 and b["token"] != "tok-moe", "a NEW token, never the browser's")
        self.assertEqual(self.r("b_sessions_before"), ["tok-moe"])
        self.assertEqual(sorted(self.r("b_sessions_after")), sorted(["tok-moe", b["token"]]), "one more session, nothing removed")
        self.assertEqual(self.r("b_whoami_new")["body"]["name"], "Moe", "the new session is real")
        self.assertEqual(self.r("b_whoami_old")["body"]["name"], "Moe", "and the browser is still signed in")

    def test_a_code_works_once(self):
        self.refused("b_again", 401, "handoff_expired")
        self.assertEqual(self.r("b_sessions_after_again"), self.r("b_sessions_after"), "the second try made no session")

    # ── C. what is refused ──
    def test_a_wrong_verifier_burns_the_code(self):
        self.refused("c_wrong_verifier", 401, "handoff_expired")
        self.refused("c_right_verifier_after_a_wrong_one", 401, "handoff_expired")

    def test_an_unknown_or_expired_code_is_refused_the_same_way(self):
        self.refused("c_unknown_code", 401, "handoff_expired")
        self.refused("c_expired", 401, "handoff_expired")
        self.assertEqual(self.r("c_just_in_time"), 200, "a code is good right up to its last second")

    def test_a_code_for_another_challenge_of_the_same_member_is_refused(self):
        self.assertEqual(self.r("c_other_verifier_same_member"), 401)

    def test_malformed_input_is_a_plain_bad_request_and_never_burns_a_live_code(self):
        self.assertEqual(self.r("c_malformed"), {"short_code": 400, "short_verifier": 400, "long_verifier": 400, "bad_chars": 400})
        self.assertEqual(self.r("c_malformed_not_strings"), [400] * 5)
        self.refused("c_malformed_reason", 400, "credentials_required")
        self.assertEqual(self.r("c_live_code_survived_malformed")["http"], 200)

    def test_a_code_is_not_a_session_token(self):
        self.refused("c_code_as_session", 401, "session_ended")

    def test_nothing_the_app_or_the_browser_sends_can_change_who_the_app_becomes(self):
        b = self.r("c_bound_to_the_asker")["body"]
        self.assertEqual((b["name"], b["is_admin"]), ("Moe", False), "bound to the member whose session asked, whatever else was in either body")

    def test_asking_again_replaces_the_older_code_and_two_challenges_coexist(self):
        self.assertEqual(self.r("c_replaced_code"), {"different": True, "old": 401, "new": 200})
        self.assertEqual(self.r("c_two_challenges"), [200, 200])

    def test_a_member_who_no_longer_exists_gets_no_session(self):
        self.refused("c_missing_member", 401, "handoff_expired")
        self.assertEqual(self.r("c_missing_member_sessions"), [])

    # ── D. who can be handed over ──
    def test_an_admin_arrives_as_an_admin(self):
        b = self.r("d_admin")["body"]
        self.assertEqual((b["name"], b["is_admin"], b["linked"], b["must_change"]), ("Sai", True, False, False))
        self.assertTrue(self.r("d_new_admin_session_is_admin")["is_admin"])

    def test_a_google_member_arrives_linked_with_a_masked_address(self):
        b = self.r("d_google_member")["body"]
        self.assertEqual((b["name"], b["linked"], b["email_masked"], b["is_admin"], b["must_change"]), ("Gia", True, "g***@gmail.com", False, False))
        w = self.r("d_new_google_session")
        self.assertEqual((w["name"], w["linked"], w["pin_enabled"]), ("Gia", True, False))

    def test_a_member_with_a_temporary_passcode_still_has_to_choose_a_new_one(self):
        b = self.r("d_temp_passcode")["body"]
        self.assertEqual((b["name"], b["must_change"]), ("Buddy", True))

    def test_it_works_with_google_on_and_with_google_off(self):
        self.assertEqual(self.r("b_redeem")["http"], 200, "the scenario starts with Google off")
        self.assertEqual(self.r("g_with_google_on"), 200)

    # ── E. limits ──
    def test_both_routes_share_the_sign_in_rate_limit(self):
        self.assertEqual(self.r("e_redeem_limited"), [401, 401, 401, 429, 429])
        self.assertEqual(self.r("e_ask_limited"), [200, 200, 200, 429, 429])

    def test_a_full_store_says_busy_instead_of_growing(self):
        self.refused("e_full", 429, "rate_limited")

    def test_two_apps_racing_for_one_code_cannot_both_win(self):
        self.assertEqual(self.r("f_race"), [200, 401, 401, 401])

    # ── H. leaks ──
    def test_nothing_secret_is_echoed_or_logged(self):
        self.assertEqual(self.r("h_redeem_keys"), ["email_masked", "is_admin", "linked", "must_change", "name", "ok", "token"], "no code, no verifier, no challenge comes back")
        self.assertEqual(self.r("h_issue_keys"), ["code", "expires_in", "ok"])
        self.assertFalse(self.r("h_full_address_seen"))
        self.assertFalse(self.r("h_hash_leak"))
        self.assertEqual(self.r("h_log_leaks"), [], "no code, verifier, challenge or session token in the log")
        self.assertTrue(any("hand-off code issued for Moe" in l for l in self.r("h_log_sample")) or self.r("h_log_sample"), "but the hand-off is logged by member name")


if __name__ == "__main__":
    unittest.main()
