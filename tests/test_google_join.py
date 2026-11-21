"""Joining the league with a Google account, on the real routes (Google sign-in step 3, 2026-11-21; needs Flask, PyJWT, cryptography and TEST_DATABASE_URL; skipped otherwise),
run in a subprocess on scratch data. Google is replaced by a throwaway RSA key pair: the tokens are really signed and verified.

What must hold: a new person with a Google account joins with a display name and NOTHING else (no passcode is chosen, none exists); the member and their Google identity are created
together or not at all; a name is 1-30 printable characters and unique case-insensitively (a refused name leaves the ticket alone so they can try another); the page cannot choose
the Google account, the admin flag or a passcode; an admin can close joining and there is a member cap (also when two people race for the last place); the old passcode
registration is closed while Google sign-in is on and works again when it is off; nothing leaks."""
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
class JoiningOnTheRealRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        raw = os.environ["TEST_DATABASE_URL"]
        cls.schema = "t_join_" + uuid.uuid4().hex[:8]
        cls.admin = psycopg.connect(raw, autocommit=True)
        cls.admin.execute(f'CREATE SCHEMA "{cls.schema}"')
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        env["DATABASE_URL"] = raw + ("&" if "?" in raw else "?") + "options=" + quote(f"-c search_path={cls.schema}")
        env.pop("HOSTED", None)
        env.pop("GOOGLE_CLIENT_ID", None)
        p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tests", "helpers", "google_join_scenario.py")], env=env, capture_output=True, text=True, timeout=1800)
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

    # ── A. the offer ──
    def test_a_new_google_account_is_told_it_may_join_alongside_the_members_it_could_claim(self):
        r = self.r("a_offer")
        self.assertEqual(r["http"], 200)
        c = r["body"]["claim"]
        self.assertEqual(c["join"], {"allowed": True, "why": ""})
        self.assertEqual(self.r("a_statuses"), {"Moe": "free", "Sai": "free"})

    # ── B. joining ──
    def test_joining_needs_a_display_name_and_nothing_else_and_signs_the_new_member_in(self):
        r = self.r("b_join")
        b = r["body"]
        self.assertEqual(r["http"], 200)
        self.assertEqual((b["ok"], b["linked"], b["joined"], b["name"], b["is_admin"], b["must_change"], b["email_masked"]), (True, True, True, "Nina", False, False, "p***@gmail.com"))
        self.assertTrue(isinstance(b["token"], str) and len(b["token"]) >= 20)
        who = self.r("b_whoami")["body"]
        self.assertEqual((who["name"], who["linked"], who["pin_enabled"], who["is_admin"], who["email_masked"]), ("Nina", True, False, False, "p***@gmail.com"), "a real session")

    def test_the_new_member_has_no_passcode_and_one_google_identity(self):
        self.assertEqual(self.r("b_member_db"), [{"username": "Nina", "passcode_hash": "-", "must_change": 0, "is_admin": 0}])
        self.assertEqual(self.r("b_identity_db"), [{"subject": "g-join-one", "member": "Nina", "email": "person.one@gmail.com"}])
        self.refused("b_pin_login", 401, "pin_retired")

    def test_the_new_member_is_part_of_the_league(self):
        self.assertGreaterEqual(self.r("b_selection_rows"), 1, "an (empty) team row exists, like for every member")
        self.assertTrue(self.r("b_state_has_member"))
        listed = {m["username"]: m for m in self.r("b_admin_list")["body"]["members"]}
        self.assertEqual((listed["Nina"]["linked"], listed["Nina"]["email_masked"], listed["Nina"]["is_admin"]), (True, "p***@gmail.com", False))
        self.assertEqual(self.r("b_next_offer_statuses"), {"Moe": "free", "Nina": "linked", "Sai": "free"}, "and a name that joined with Google can never be claimed")

    def test_the_page_cannot_choose_the_google_account_the_admin_flag_or_a_passcode(self):
        self.assertTrue(self.r("b_evil_absent"))
        self.assertEqual((self.r("b_join")["body"]["is_admin"], self.r("b_member_db")[0]["is_admin"], self.r("b_member_db")[0]["passcode_hash"]), (False, 0, "-"))

    def test_the_same_google_account_signs_straight_in_afterwards_and_the_ticket_is_used_up(self):
        g = self.r("b_google_again")["body"]
        self.assertEqual((g["ok"], g["linked"], g["name"]), (True, True, "Nina"))
        self.assertNotIn("claim", g)
        self.refused("b_ticket_reuse", 401, "claim_expired")

    # ── C. names ──
    def test_a_name_must_be_one_to_thirty_printable_characters(self):
        for key in ("c_blank", "c_too_long", "c_control_chars", "c_line_breaks_only", "c_not_a_string"):
            self.refused(key, 400, "name_invalid")
        self.refused("c_none", 400, "name_invalid")
        self.assertEqual(self.r("c_exactly_30")["http"], 200, "thirty is the limit and it is allowed")
        self.assertEqual(self.r("c_exactly_30")["body"]["name"], "y" * 30)

    def test_a_name_somebody_has_is_refused_whatever_its_case_or_padding(self):
        for key in ("c_exact_duplicate", "c_case_duplicate", "c_padded_duplicate", "c_duplicate_of_a_joiner"):
            self.refused(key, 409, "name_taken")

    def test_white_space_in_a_name_is_trimmed_and_collapsed(self):
        r = self.r("c_spaces_collapsed")
        self.assertEqual((r["http"], r["body"]["name"]), (200, "Ravi Kumar"))
        self.assertEqual(self.r("c_ravi_db"), [{"username": "Ravi Kumar"}])

    def test_a_refused_name_leaves_the_ticket_alone_so_another_can_be_tried(self):
        self.assertTrue(self.r("c_ticket_survived_every_refusal"))
        self.assertEqual(self.r("c_spaces_collapsed")["http"], 200, "the same ticket joined with a free name after ten refusals")

    def test_the_other_refusals_say_what_is_wrong(self):
        self.refused("c_no_ticket", 400, "credentials_required")
        self.refused("c_made_up_ticket", 401, "claim_expired")
        self.assertEqual(self.r("c_not_json"), 400)

    # ── D. closing joining, the cap ──
    def test_only_an_admin_can_open_or_close_joining(self):
        self.refused("d_non_admin", 403, "admin_only")
        self.refused("d_no_session", 401, "auth_required")
        self.refused("d_bad_body", 400, "bad_request")

    def test_a_closed_league_takes_nobody_new_and_the_ticket_survives_until_it_reopens(self):
        c = self.r("d_close")["body"]
        self.assertEqual((c["ok"], c["joining_open"], c["member_cap"]), (True, False, 30))
        self.assertEqual(self.r("d_offer_closed")["body"]["claim"]["join"], {"allowed": False, "why": "closed"})
        self.refused("d_join_closed", 403, "joining_closed")
        self.assertTrue(self.r("d_ticket_alive_after_closed"))
        self.assertTrue(self.r("d_reopen")["body"]["joining_open"])
        self.assertEqual(self.r("d_join_after_reopen")["http"], 200, "reopened: the same ticket joins")

    def test_the_admin_list_shows_whether_joining_is_open_and_how_full_the_league_is(self):
        v = self.r("d_members_view")["body"]
        self.assertEqual((v["joining_open"], v["member_cap"]), (False, 30))
        self.assertGreaterEqual(v["member_count"], 3)
        self.assertEqual(self.r("d_cap_in_view"), 30)

    def test_a_full_league_takes_nobody_new(self):
        self.assertEqual(self.r("d_offer_full")["body"]["claim"]["join"], {"allowed": False, "why": "full"})
        self.refused("d_join_full", 409, "members_full")
        self.assertEqual(self.r("d_dao_full"), "full", "the data layer refuses too, so the cap cannot be raced")
        self.assertTrue(self.r("d_direct_absent"), "and nothing was half-created")

    # ── E. the passcode registration ──
    def test_passcode_registration_is_closed_while_google_sign_in_is_on_and_works_again_when_it_is_off(self):
        self.refused("e_register_with_google_on", 403, "register_closed")
        self.assertTrue(self.r("e_oldway_absent"), "nobody was created")
        off = self.r("e_register_with_google_off")
        self.assertEqual((off["http"], off["body"]["ok"], off["body"]["name"]), (200, True, "OldWay"), "the fallback for when Google is switched off")
        self.refused("e_join_with_google_off", 503, "google_not_configured")

    # ── F. races ──
    def test_two_people_racing_for_one_name_cannot_both_get_it(self):
        res = self.r("f_same_name")
        self.assertEqual(sorted(v["http"] for v in res.values()), [200, 409])
        self.assertEqual([v["reason"] for v in res.values() if v["http"] == 409], ["name_taken"])
        self.assertEqual(len(self.r("f_same_name_db")), 1)

    def test_one_google_account_cannot_become_two_members(self):
        res = self.r("f_one_ticket")
        self.assertEqual(sorted(v["http"] for v in res.values())[0], 200)
        self.assertEqual(self.r("f_one_ticket_identities"), 1)
        self.assertEqual(self.r("f_one_ticket_members"), 1, "exactly one of the two names exists")
        for v in res.values():
            if v["http"] != 200:
                self.assertIn(v["reason"], ("google_account_in_use", "claim_expired"))

    def test_two_people_racing_for_the_last_place_cannot_both_get_it(self):
        res = self.r("f_last_slot")
        self.assertEqual(sorted(v["http"] for v in res.values()), [200, 409])
        self.assertEqual([v["reason"] for v in res.values() if v["http"] == 409], ["members_full"])
        self.assertEqual(self.r("f_last_slot_members"), 1)

    def test_a_newer_sign_in_replaces_the_older_ticket(self):
        self.refused("f_old_ticket", 401, "claim_expired")
        self.assertEqual(self.r("f_new_ticket")["http"], 200)

    # ── G. leaks ──
    def test_nothing_leaks_that_should_not(self):
        self.assertEqual(self.r("g_full_addresses_seen"), [], "no full address in any reply")
        self.assertFalse(self.r("g_join_has_ticket"))
        self.assertEqual(self.r("g_join_keys"), ["email_masked", "is_admin", "joined", "linked", "must_change", "name", "ok", "token"])
        self.assertEqual(self.r("g_offer_join_keys"), ["allowed", "why"])
        self.assertFalse(self.r("g_hash_leak"))

    # ── H. rate limit ──
    def test_joining_shares_the_sign_in_rate_limit(self):
        self.assertEqual(self.r("h_limited"), [401, 401, 401, 429, 429])


if __name__ == "__main__":
    unittest.main()
