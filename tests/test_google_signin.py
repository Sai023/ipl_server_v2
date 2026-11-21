"""Google sign-in on the real routes (Phase 3, 2026-11-21; needs Flask, PyJWT, cryptography and TEST_DATABASE_URL; skipped otherwise), run in a subprocess on scratch data.
Google is replaced by a throwaway RSA key pair: the tokens are really signed and really verified.
What must hold: a token is only believed with a nonce WE issued (once); a good Google account that belongs to nobody is a state, not an error; a signed-in member can
switch to Google and their passcode is retired in the same step; nobody can take another member's Google account (also when two try at the same moment); the admin can
undo a link; no address ever appears except masked; sign-out really ends the session; and none of it exists until GOOGLE_CLIENT_ID is set."""
import json
import os
import subprocess
import sys
import unittest
import uuid
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = "1234567890-abcdefghijklmnop.apps.googleusercontent.com"


def _available():
    try:
        import click  # noqa: F401
        import cryptography  # noqa: F401
        import flask  # noqa: F401
        import jwt  # noqa: F401
        return True
    except Exception:
        return False


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL") and _available(), "needs Flask, PyJWT, cryptography and TEST_DATABASE_URL (run with `py -3.14`)")
class TheRealRoutesOnScratchData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        raw = os.environ["TEST_DATABASE_URL"]
        cls.schema = "t_goog_" + uuid.uuid4().hex[:8]
        cls.admin = psycopg.connect(raw, autocommit=True)
        cls.admin.execute(f'CREATE SCHEMA "{cls.schema}"')
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        env["DATABASE_URL"] = raw + ("&" if "?" in raw else "?") + "options=" + quote(f"-c search_path={cls.schema}")
        env.pop("HOSTED", None)
        env.pop("GOOGLE_CLIENT_ID", None)
        p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tests", "helpers", "google_scenario.py")], env=env, capture_output=True, text=True, timeout=1800)
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

    # ── A. configuration and nonces ──
    def test_the_sign_in_screen_can_ask_what_is_set_up(self):
        c = self.r("a_config")["body"]
        self.assertEqual((c["ok"], c["google"], c["pin_enabled"]), (True, {"enabled": True, "client_id": CLIENT}, True))
        off = self.r("a_off_config")["body"]
        self.assertEqual(off["google"], {"enabled": False, "client_id": None})

    def test_nothing_works_until_a_client_id_is_set(self):
        self.refused("a_off_nonce", 503, "google_not_configured")
        self.refused("a_off_google", 503, "google_not_configured")

    def test_every_nonce_is_different_and_long(self):
        a, b = self.r("a_nonce1")["body"], self.r("a_nonce2")["body"]
        self.assertTrue(a["ok"] and b["ok"])
        self.assertNotEqual(a["nonce"], b["nonce"])
        self.assertGreaterEqual(len(a["nonce"]), 24)

    # ── B. signing in ──
    def test_a_good_google_account_that_belongs_to_nobody_is_a_state_not_an_error(self):
        r = self.r("b_unlinked")
        self.assertEqual(r["http"], 200)
        self.assertEqual({k: r["body"][k] for k in ("ok", "linked", "email_masked")}, {"ok": True, "linked": False, "email_masked": "n***@gmail.com"})
        self.assertNotIn("token", r["body"])
        self.assertTrue(self.r("b_sessions_unchanged"), "no session is created for an account nobody has linked")

    def test_a_nonce_works_once(self):
        self.refused("b_replay", 401, "nonce_invalid")

    def test_the_nonce_must_be_one_we_issued_and_the_one_inside_the_token(self):
        self.refused("b_nonce_not_in_token", 401, "nonce_invalid")
        self.refused("b_nonce_never_issued", 401, "nonce_invalid")
        self.refused("b_no_nonce", 401, "nonce_invalid")
        self.refused("b_empty_body", 401, "nonce_invalid")
        self.assertEqual(self.r("b_not_json"), 401)

    def test_bad_tokens_are_refused_with_their_own_reason(self):
        self.refused("b_wrong_audience", 401, "token_invalid")
        self.refused("b_wrong_signature", 401, "token_invalid")
        self.refused("b_expired", 401, "token_expired")
        self.refused("b_unverified_email", 403, "email_unverified")
        self.refused("b_junk", 401, "token_invalid")

    def test_a_refusal_does_not_spend_the_nonce(self):
        r = self.r("b_nonce_survives_refusals")
        self.assertEqual((r["http"], r["body"]["linked"]), (200, False), "after all those refusals the nonce still works for a good token")

    def test_a_google_outage_in_the_middle_does_not_burn_the_nonce(self):
        self.refused("b_outage", 503, "google_unreachable")
        r = self.r("b_after_outage")
        self.assertEqual((r["http"], r["body"]["ok"]), (200, True), "the very same credential and nonce work once Google is back")

    # ── C. switching ──
    def test_switching_needs_a_signed_in_member(self):
        self.refused("c_link_no_session", 401, "auth_required")
        self.refused("c_link_bad_session", 401, "session_ended")

    def test_switching_links_the_account_and_retires_the_passcode(self):
        before = self.r("c_moe_whoami_before")["body"]
        self.assertEqual((before["linked"], before["email_masked"], before["pin_enabled"]), (False, None, True))
        r = self.r("c_link_moe")
        self.assertEqual(r["http"], 200, r["text"][:200])
        self.assertEqual({k: r["body"][k] for k in ("ok", "linked", "name", "email_masked")}, {"ok": True, "linked": True, "name": "Moe", "email_masked": "m***@gmail.com"})
        after = self.r("c_moe_whoami_after")["body"]
        self.assertEqual((after["linked"], after["email_masked"], after["pin_enabled"]), (True, "m***@gmail.com", False))
        self.assertEqual(self.r("c_moe_db"), {"passcode_hash": "-", "must_change": 0})
        self.assertEqual(self.r("c_moe_identity_db"), [{"provider": "google", "subject": "g-moe", "member": "Moe", "email": "moe.test@gmail.com"}])

    def test_the_old_passcode_no_longer_works_and_says_why(self):
        self.refused("c_moe_pin_login", 401, "pin_retired")
        self.refused("c_moe_pin_change", 403, "pin_retired")
        self.refused("c_admin_reset_moe", 409, "member_signs_in_with_google")
        self.assertEqual(self.r("c_moe_db_after_refusals"), "-", "none of those refusals brought a passcode back")

    def test_signing_in_with_google_gives_a_real_session(self):
        s = self.r("c_moe_google_signin")
        self.assertEqual(s["http"], 200)
        b = s["body"]
        self.assertEqual((b["ok"], b["linked"], b["name"], b["must_change"], b["is_admin"], b["email_masked"]), (True, True, "Moe", False, False, "m***@gmail.com"))
        self.assertGreaterEqual(len(b["token"]), 40)
        who = self.r("c_moe_new_session_whoami")["body"]
        self.assertEqual((who["name"], who["linked"], who["pin_enabled"]), ("Moe", True, False))

    def test_a_google_sign_in_refreshes_the_address_google_reports_today_and_the_time(self):
        """The scenario planted a stale address and the time 2000-01-01 on Moe's identity just before this sign-in. An address can change at Google (the sub never
        does), and last_login_at is how an admin can tell a live account from a dead one: both must be refreshed by the sign-in itself."""
        row = self.r("c_moe_identity_after_signin")
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0]["email"], "moe.test@gmail.com", "the sign-in must store the address Google reports today")
        self.assertTrue(str(row[0]["last_login_at"]) > "2001", "last_login_at must move on at every sign-in (it was planted as 2000-01-01): %s" % row[0]["last_login_at"])

    def test_members_who_have_not_switched_are_untouched(self):
        self.assertEqual(self.r("c_buddy_pin_login")["http"], 200)
        w = self.r("c_buddy_whoami")["body"]
        self.assertEqual((w["linked"], w["pin_enabled"]), (False, True))
        self.assertTrue(self.r("c_config_still_pin")["body"]["pin_enabled"])

    def test_nobody_can_take_another_members_google_account(self):
        self.refused("c_buddy_takes_moes_account", 409, "google_account_in_use")
        self.assertTrue(self.r("c_buddy_db_after"), "the member who was refused keeps their passcode")

    def test_a_second_google_account_needs_the_admin_first(self):
        self.refused("c_moe_second_account", 409, "already_linked")
        self.assertEqual(self.r("c_moe_identity_after_second"), [{"subject": "g-moe"}])

    def test_linking_the_same_account_again_is_a_harmless_repeat(self):
        r = self.r("c_moe_relink_same")
        self.assertEqual((r["http"], r["body"]["linked"]), (200, True))
        self.assertEqual(self.r("c_identity_count_after_relink"), 1)

    def test_an_unverified_email_cannot_be_linked(self):
        self.refused("c_link_email_unverified", 403, "email_unverified")
        self.assertTrue(self.r("c_zed_still_pin"))

    def test_two_members_racing_for_one_google_account_cannot_both_win(self):
        res = self.r("c_race")
        self.assertEqual(sorted(v["http"] for v in res.values()), [200, 409], res)
        loser = [w for w, v in res.items() if v["http"] == 409][0]
        winner = [w for w, v in res.items() if v["http"] == 200][0]
        self.assertEqual(res[loser]["reason"], "google_account_in_use")
        self.assertEqual(self.r("c_race_db"), [{"member": winner}])
        self.assertEqual(self.r("c_race_pins_retired"), {winner: True, loser: False}, "only the winner's passcode is retired")

    # ── D. recovery ──
    def test_the_admin_can_undo_a_link(self):
        self.refused("d_non_admin_unlink", 403, "admin_only")
        self.refused("d_no_target", 400, "target_required")
        self.refused("d_unknown", 404, "member_not_found")
        self.refused("d_not_linked", 409, "not_linked")
        r = self.r("d_unlink_moe")
        self.assertEqual((r["http"], r["body"]), (200, {"ok": True, "target": "Moe", "reset_to": "1234", "must_change": True}))
        self.refused("d_moe_old_session", 401, "session_ended")
        self.refused("d_moe_google_session", 401, "session_ended")
        self.assertTrue(self.r("d_moe_identity_gone"))
        pin = self.r("d_moe_pin_login")
        self.assertEqual((pin["http"], pin["body"]["must_change"]), (200, True), "the temporary passcode works and must be changed")
        g = self.r("d_moe_google_now_unlinked")
        self.assertEqual((g["http"], g["body"]["linked"]), (200, False), "the Google account is nobody's again")
        self.refused("d_unlink_again", 409, "not_linked")

    # ── E. privacy ──
    def test_an_address_never_appears_except_masked(self):
        m = {x["username"]: x for x in self.r("e_admin_members")["body"]["members"]}
        self.assertEqual((m["Moe"]["linked"], m["Moe"]["email_masked"]), (True, "m***@gmail.com"))
        self.assertEqual((m["Sai"]["linked"], m["Sai"]["email_masked"]), (False, None))
        self.assertNotIn("google_email", m["Moe"])
        for key in ("e_admin_members", "e_state", "e_whoami_sai"):
            for email in self.r("e_emails"):
                self.assertNotIn(email, self.r(key)["text"], "%s shows %s" % (key, email))
        self.assertEqual(self.r("e_link_moe_again")["http"], 200, "after the recovery the member can switch again")

    # ── F. signing out ──
    def test_signing_out_ends_the_session_for_real(self):
        self.assertEqual(self.r("f_logout")["body"], {"ok": True})
        self.refused("f_after_logout", 401, "session_ended")
        for key in ("f_logout_no_token", "f_logout_junk", "f_logout_twice"):
            self.assertEqual((self.r(key)["http"], self.r(key)["body"]), (200, {"ok": True}), key)

    # ── G. everybody switched ──
    def test_once_everybody_has_switched_nobody_has_a_passcode(self):
        self.assertEqual(self.r("g_pins_left"), [])
        self.assertFalse(self.r("g_config")["body"]["pin_enabled"])

    # ── H. the budget ──
    def test_sign_ins_have_their_own_rate_limit(self):
        self.assertEqual(self.r("h_codes"), [200] * 5 + [429] * 3)
        self.assertEqual(self.r("h_limited_body")["reason"], "rate_limited")


if __name__ == "__main__":
    unittest.main()
