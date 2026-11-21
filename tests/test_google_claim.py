"""Claiming a name with a Google account that belongs to nobody yet, on the real routes (Google sign-in step 2, 2026-11-21; needs Flask, PyJWT, cryptography and
TEST_DATABASE_URL; skipped otherwise), run in a subprocess on scratch data. Google is replaced by a throwaway RSA key pair: the tokens are really signed and verified.

What must hold: a good Google account nobody has linked is offered a claim (a ticket bound to the account the SERVER verified) with the list of members who can be
picked; one visit is enough (pick the name, enter the passcode once: linked, passcode retired, signed in); a member who already has a Google account can never be picked
again and a temporary (known) passcode cannot take a name; the passcode is the only secret, so wrong ones are counted per Google account and five lock it out however
often it signs in again; a ticket works once, for ten minutes, for the account it was issued for; two accounts racing for one member cannot both win; an admin can remove
a link (also their own, without being signed out on the device they use); and nothing leaks: names and a word each, a masked address, never a hash."""
import json
import os
import subprocess
import sys
import unittest
import uuid
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
class TheClaimOnTheRealRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        raw = os.environ["TEST_DATABASE_URL"]
        cls.schema = "t_claim_" + uuid.uuid4().hex[:8]
        cls.admin = psycopg.connect(raw, autocommit=True)
        cls.admin.execute(f'CREATE SCHEMA "{cls.schema}"')
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        env["DATABASE_URL"] = raw + ("&" if "?" in raw else "?") + "options=" + quote(f"-c search_path={cls.schema}")
        env.pop("HOSTED", None)
        env.pop("GOOGLE_CLIENT_ID", None)
        p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tests", "helpers", "google_claim_scenario.py")], env=env, capture_output=True, text=True, timeout=1800)
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

    # ── A. an account nobody has linked is offered a claim ──
    def test_a_good_google_account_that_belongs_to_nobody_gets_a_claim_not_a_dead_end(self):
        r = self.r("a_unlinked")
        b = r["body"]
        self.assertEqual(r["http"], 200)
        self.assertEqual((b["ok"], b["linked"], b["email_masked"]), (True, False, "m***@gmail.com"))
        c = b["claim"]
        self.assertTrue(isinstance(c["ticket"], str) and len(c["ticket"]) >= 24)
        self.assertEqual((c["expires_in"], c["email_masked"]), (600, "m***@gmail.com"))
        self.assertNotIn("token", b, "no session until the member has said who they are")
        self.assertTrue(self.r("a_sessions_unchanged"), "no session is created by asking")
        self.assertEqual(self.r("a_identities_before"), 1, "and nothing is linked by asking (only the one that was linked before the test)")

    def test_the_member_list_says_who_can_be_picked_and_why_not(self):
        self.assertEqual(self.r("a_statuses"), {"Buddy": "free", "Kim": "free", "Lin": "linked", "Moe": "free", "Sai": "free", "Zed": "temp"})
        names = [m["name"] for m in self.r("a_unlinked")["body"]["claim"]["members"]]
        self.assertEqual(names, sorted(names, key=lambda n: (n.lower(), n)), "in name order")

    # ── B. one visit: pick the name, enter the passcode once ──
    def test_claiming_links_the_account_retires_the_passcode_and_signs_the_member_in(self):
        r = self.r("b_claim_moe")
        b = r["body"]
        self.assertEqual(r["http"], 200)
        self.assertEqual((b["ok"], b["linked"], b["claimed"], b["name"], b["is_admin"], b["must_change"], b["email_masked"]), (True, True, True, "Moe", False, False, "m***@gmail.com"))
        self.assertTrue(isinstance(b["token"], str) and len(b["token"]) >= 20)
        who = self.r("b_whoami")["body"]
        self.assertEqual((who["name"], who["linked"], who["pin_enabled"], who["email_masked"]), ("Moe", True, False, "m***@gmail.com"), "the new session is a real one")
        self.assertEqual(self.r("b_identity_db"), [{"subject": "g-moe-acct", "member": "Moe", "email": "moe.acct@gmail.com"}])
        self.assertEqual((self.r("b_member_db")["passcode_hash"], self.r("b_member_db")["must_change"]), ("-", 0), "the passcode is retired in the same step")

    def test_the_page_cannot_choose_which_google_account_or_which_member_gets_linked(self):
        # the claim body carried sub / email / member of somebody else: the server used the account IT verified and the member the passcode proved
        self.assertTrue(self.r("b_evil_identity_absent"))
        self.assertEqual([row["member"] for row in self.r("b_identity_db")], ["Moe"])

    def test_after_claiming_the_passcode_is_retired_and_the_google_account_recognises_the_member(self):
        self.refused("b_pin_login", 401, "pin_retired")
        g = self.r("b_google_again")["body"]
        self.assertEqual((g["ok"], g["linked"], g["name"], g["is_admin"]), (True, True, "Moe", False))
        self.assertNotIn("claim", g, "a linked account is signed in, not offered a claim")
        self.assertTrue(g["token"])

    def test_a_ticket_is_used_up_by_its_claim(self):
        self.refused("b_ticket_reuse", 401, "claim_expired")
        self.assertTrue(self.r("b_buddy_untouched"), "and the second attempt touched nobody")

    # ── C. refusals ──
    def test_a_name_that_is_linked_can_never_be_picked_again(self):
        self.assertEqual((self.r("c_statuses")["Moe"], self.r("c_statuses")["Lin"]), ("linked", "linked"))
        self.refused("c_pick_moe", 409, "member_taken")
        self.refused("c_pick_lin", 409, "member_taken")

    def test_a_temporary_passcode_is_a_known_value_and_cannot_take_a_name(self):
        self.assertEqual(self.r("c_statuses")["Zed"], "temp")
        self.refused("c_pick_zed", 409, "temp_passcode")

    def test_the_other_refusals_say_what_is_wrong(self):
        self.refused("c_unknown_name", 404, "member_not_found")
        self.refused("c_no_ticket", 400, "credentials_required")
        self.refused("c_short_passcode", 400, "credentials_required")
        self.refused("c_blank_name", 400, "credentials_required")
        self.assertEqual(self.r("c_not_json"), 400)
        self.refused("c_made_up_ticket", 401, "claim_expired")

    def test_refusals_about_the_name_do_not_burn_the_ticket_or_count_as_wrong_passcodes(self):
        self.assertEqual(self.r("c_tries_left_after_refusals"), 5)
        r = self.r("c_pick_buddy")
        self.assertEqual((r["http"], r["body"]["name"], r["body"]["claimed"]), (200, "Buddy", True), "the same ticket still worked after seven refusals")
        self.assertEqual(self.r("c_buddy_db"), [{"subject": "g-other", "passcode_hash": "-"}])

    # ── D. a four-digit passcode cannot be guessed ──
    def test_wrong_passcodes_count_down_and_the_fifth_locks_that_google_account_out(self):
        w = self.r("d_wrong")
        self.assertEqual([x["http"] for x in w], [401, 401, 401, 401, 429])
        self.assertEqual([x["body"].get("attempts_left") for x in w[:4]], [4, 3, 2, 1])
        for x in w[:4]:
            self.assertEqual((x["body"]["reason"], x["body"]["ok"], x["body"]["code"]), ("bad_credentials", False, 401))
            self.assertNotIn("token", x["body"])
        self.assertEqual(w[4]["body"]["reason"], "claim_locked")
        self.assertNotIn("attempts_left", w[4]["body"])

    def test_once_locked_the_right_passcode_is_no_use_and_signing_in_again_is_no_fresh_start(self):
        self.refused("d_right_after_lock", 401, "claim_expired")             # the ticket died with the last try
        self.refused("d_new_sign_in_while_locked", 429, "claim_locked")
        self.assertTrue(self.r("d_locked"))
        other = self.r("d_other_account_unaffected")
        self.assertEqual((other["http"], bool(other["body"].get("claim"))), (200, True), "another Google account is unaffected")
        self.assertTrue(self.r("d_kim_untouched"))
        self.assertTrue(self.r("d_kim_identity_absent"), "five guesses took nothing")

    def test_another_members_passcode_is_just_wrong_and_the_right_one_still_works_afterwards(self):
        w = self.r("d_cross_wrong")
        self.assertEqual((w["http"], w["body"]["reason"], w["body"]["attempts_left"]), (401, "bad_credentials", 4))
        r = self.r("d_cross_right")
        self.assertEqual((r["http"], r["body"]["name"], r["body"]["is_admin"], r["body"]["claimed"]), (200, "Sai", True, True))
        self.assertEqual(self.r("d_cross_tries_after"), 5, "a claim that worked clears the count")

    # ── E. races and tickets ──
    def test_two_google_accounts_racing_for_one_member_cannot_both_win(self):
        res = self.r("e_race_members")
        self.assertEqual(sorted(v["http"] for v in res.values()), [200, 409])
        self.assertEqual([v["reason"] for v in res.values() if v["http"] == 409], ["member_taken"])
        self.assertEqual(len(self.r("e_race_db")), 1, "exactly one account is linked to Kim")

    def test_one_ticket_cannot_be_used_by_eight_requests_at_once(self):
        self.assertEqual(self.r("e_one_ticket_http"), [200] + [401] * 7)
        self.assertEqual(self.r("e_one_ticket_identity_rows"), 1)

    def test_a_newer_sign_in_replaces_the_older_ticket_of_the_same_google_account(self):
        self.refused("e_old_ticket", 401, "claim_expired")
        self.assertEqual(self.r("e_new_ticket")["http"], 200)

    def test_a_ticket_is_good_for_ten_minutes_and_no_longer(self):
        self.assertTrue(self.r("f_just_in_time_peek"))
        self.refused("f_late", 401, "claim_expired")
        self.assertTrue(self.r("f_ben_untouched"))

    # ── G. the admin removes links ──
    def test_the_admin_can_remove_a_link_the_name_becomes_free_only_through_a_passcode_the_member_chose(self):
        self.assertTrue(self.r("g_sai_linked"))
        u = self.r("g_unlink_moe")
        self.assertEqual((u["http"], u["body"]["ok"], u["body"]["target"], u["body"]["must_change"], u["body"]["self"]), (200, True, "Moe", True, False))
        self.refused("g_moe_old_session", 401, "session_ended")
        self.assertEqual(self.r("g_statuses")["Moe"], "temp", "unlinked, the name has a temporary passcode: it is not offered as free")
        self.refused("g_pick_moe_temp", 409, "temp_passcode")

    def test_an_admin_who_unlinks_themselves_stays_signed_in_on_that_device_only(self):
        u = self.r("g_unlink_self")
        self.assertEqual((u["http"], u["body"]["ok"], u["body"]["self"]), (200, True, True))
        same = self.r("g_self_whoami_same_device")["body"]
        self.assertEqual((same["name"], same["linked"], same["must_change"], same["pin_enabled"]), ("Sai", False, True, True), "still signed in, and now asked for a new passcode")
        self.refused("g_self_whoami_other_device", 401, "session_ended")
        self.refused("g_self_unlink_not_linked_again", 409, "not_linked")

    def test_only_an_admin_can_remove_a_link(self):
        self.refused("g_non_admin_unlink", 403, "admin_only")

    # ── H. what leaks ──
    def test_nothing_leaks_that_should_not(self):
        self.assertEqual(self.r("h_full_addresses_seen"), [], "no full address in any reply")
        self.assertEqual(self.r("h_member_entry_keys"), ["name", "status"], "names and a word each")
        self.assertEqual(self.r("h_claim_keys"), ["email_masked", "expires_in", "join", "members", "ticket"])
        self.assertFalse(self.r("h_claim_success_has_ticket"), "the ticket is not echoed once it is spent")
        self.assertFalse(self.r("h_hash_leak"), "no passcode hash in any reply")

    # ── I. doors ──
    def test_claiming_shares_the_sign_in_rate_limit(self):
        self.assertEqual(self.r("i_limited"), [401, 401, 401, 429, 429])

    def test_nothing_works_until_a_client_id_is_set(self):
        self.refused("i_off", 503, "google_not_configured")


if __name__ == "__main__":
    unittest.main()
