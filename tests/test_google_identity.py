"""Google sign-in, the token check (logic/google_identity.py, 2026-11-21): a token is accepted only when it is signed by one of Google's keys with the algorithm
pinned to RS256, was issued to OUR client id, by Google, and has not expired; it carries a verified email and the exact nonce we handed out. Pure: no Flask, no
database. The tokens here are REALLY signed (a throwaway RSA key made for each run) and the "Google" that serves the keys is a tiny local server."""
import base64
import hashlib
import hmac
import http.server
import json
import os
import sys
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    HAVE_CRYPTO = True
except Exception:                      # pragma: no cover
    HAVE_CRYPTO = False

from logic import google_identity as gi  # noqa: E402

CLIENT = "1234567890-abcdefghijklmnop.apps.googleusercontent.com"


def new_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FixedKeys:
    """A key source with one key, so no network is touched."""

    def __init__(self, key):
        self.key = key.public_key()

    def key_for(self, token):
        return self.key


def make_token(private_key, kid="k1", **over):
    now = int(time.time())
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT, "sub": "110169484474386276334", "email": "sam.smith@gmail.com", "email_verified": True,
              "nonce": "N-1", "iat": now, "exp": now + 3600, "name": "Sam Smith"}
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


@unittest.skipUnless(HAVE_CRYPTO, "needs PyJWT and cryptography")
class TheTokenCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = new_key()
        cls.other = new_key()
        cls.keys = FixedKeys(cls.key)

    def check(self, token, nonce="N-1", audience=CLIENT):
        return gi.verify_id_token(token, audience=audience, expected_nonce=nonce, source=self.keys)

    def refused(self, token, reason, nonce="N-1", audience=CLIENT):
        with self.assertRaises(gi.GoogleAuthError) as cm:
            self.check(token, nonce, audience)
        self.assertEqual(cm.exception.reason, reason, cm.exception.detail)

    def test_a_good_token_gives_the_sub_the_email_and_the_name(self):
        c = self.check(make_token(self.key))
        self.assertEqual(c, {"sub": "110169484474386276334", "email": "sam.smith@gmail.com", "name": "Sam Smith"})

    def test_both_spellings_of_the_issuer_are_accepted_and_nothing_else(self):
        self.check(make_token(self.key, iss="accounts.google.com"))
        self.check(make_token(self.key, iss="https://accounts.google.com"))
        self.refused(make_token(self.key, iss="https://evil.example.com"), "token_invalid")
        self.refused(make_token(self.key, iss="https://accounts.google.com.evil.example"), "token_invalid")

    def test_a_token_issued_to_another_app_is_refused(self):
        self.refused(make_token(self.key, aud="9999-other.apps.googleusercontent.com"), "token_invalid")
        self.refused(make_token(self.key, aud=None), "token_invalid")

    def test_expiry_with_a_minute_of_leeway(self):
        now = int(time.time())
        self.refused(make_token(self.key, iat=now - 7200, exp=now - 3600), "token_expired")
        self.check(make_token(self.key, iat=now - 3600, exp=now - 30))               # 30 s past: inside the leeway
        self.refused(make_token(self.key, iat=now - 3600, exp=now - 120), "token_expired")
        self.refused(make_token(self.key, iat=now + 3600, exp=now + 7200), "token_invalid")   # issued in the future
        self.refused(make_token(self.key, exp=None), "token_invalid")
        self.refused(make_token(self.key, iat=None), "token_invalid")

    def test_a_token_signed_by_somebody_else_is_refused(self):
        self.refused(make_token(self.other), "token_invalid")

    def test_the_algorithm_is_pinned_so_the_old_tricks_fail(self):
        def b64(d):
            return base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).rstrip(b"=").decode()
        now = int(time.time())
        payload = {"iss": "https://accounts.google.com", "aud": CLIENT, "sub": "1", "email": "a@gmail.com", "email_verified": True, "nonce": "N-1", "iat": now, "exp": now + 3600}
        # "alg": "none" - an unsigned token
        self.refused(b64({"alg": "none", "typ": "JWT", "kid": "k1"}) + "." + b64(payload) + ".", "token_invalid")
        # "alg": "HS256" signed with Google's PUBLIC key as the shared secret (the classic algorithm-confusion attack)
        pem = self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        signing_input = (b64({"alg": "HS256", "typ": "JWT", "kid": "k1"}) + "." + b64(payload)).encode()
        sig = base64.urlsafe_b64encode(hmac.new(pem, signing_input, hashlib.sha256).digest()).rstrip(b"=").decode()
        self.refused(signing_input.decode() + "." + sig, "token_invalid")
        # RS384 and ES256 are not what Google uses either
        self.refused(jwt.encode(payload, self.key, algorithm="RS384", headers={"kid": "k1"}), "token_invalid")

    def test_the_email_must_be_there_and_verified(self):
        self.refused(make_token(self.key, email_verified=False), "email_unverified")
        self.refused(make_token(self.key, email_verified=None), "email_unverified")
        self.check(make_token(self.key, email_verified="true"))                      # some libraries send the string
        self.refused(make_token(self.key, email_verified="false"), "email_unverified")
        self.refused(make_token(self.key, email=None), "token_invalid")
        self.refused(make_token(self.key, email="not-an-address"), "token_invalid")

    def test_the_sub_must_be_a_plain_non_empty_string(self):
        self.refused(make_token(self.key, sub=None), "token_invalid")
        self.refused(make_token(self.key, sub=""), "token_invalid")
        self.refused(make_token(self.key, sub=12345), "token_invalid")
        self.refused(make_token(self.key, sub="x" * 300), "token_invalid")

    def test_the_nonce_must_be_exactly_the_one_asked_for(self):
        self.refused(make_token(self.key, nonce="N-1"), "nonce_invalid", nonce="N-2")
        self.refused(make_token(self.key, nonce=None), "nonce_invalid")
        self.refused(make_token(self.key, nonce=""), "nonce_invalid")
        self.refused(make_token(self.key, nonce=123), "nonce_invalid")
        self.refused(make_token(self.key), "nonce_invalid", nonce="")
        self.refused(make_token(self.key), "nonce_invalid", nonce=None)
        self.refused(make_token(self.key, nonce="N-1 "), "nonce_invalid")            # no sloppy comparison

    def test_rubbish_is_refused_without_a_crash(self):
        for bad in (None, "", "abc", "a.b", "a.b.c.d", 12345, b"a.b.c", "x" * 5000, "...", "eyJhbGciOiJSUzI1NiJ9.e30.", {"a": 1}, ["a.b.c"]):
            self.refused(bad, "token_invalid")

    def test_no_client_id_means_not_configured(self):
        self.refused(make_token(self.key), "google_not_configured", audience="")
        self.refused(make_token(self.key), "google_not_configured", audience=None)

    def test_the_client_id_comes_from_the_environment_and_is_trimmed(self):
        old = os.environ.get("GOOGLE_CLIENT_ID")
        try:
            os.environ.pop("GOOGLE_CLIENT_ID", None)
            self.assertEqual((gi.client_id(), gi.enabled()), ("", False))
            os.environ["GOOGLE_CLIENT_ID"] = "  " + CLIENT + "\n"
            self.assertEqual((gi.client_id(), gi.enabled()), (CLIENT, True))
            os.environ["GOOGLE_CLIENT_ID"] = "   "
            self.assertFalse(gi.enabled())
        finally:
            if old is None:
                os.environ.pop("GOOGLE_CLIENT_ID", None)
            else:
                os.environ["GOOGLE_CLIENT_ID"] = old

    def test_the_override_is_used_and_can_be_removed(self):
        gi.set_key_source(self.keys)
        try:
            self.assertIs(gi.key_source(), self.keys)
            self.assertEqual(gi.verify_id_token(make_token(self.key), audience=CLIENT, expected_nonce="N-1")["sub"], "110169484474386276334")
        finally:
            gi.set_key_source(None)
        self.assertIsNot(gi.key_source(), self.keys)


class TheEmailMask(unittest.TestCase):
    def test_masks(self):
        self.assertEqual(gi.mask_email("sam.smith@gmail.com"), "s***@gmail.com")
        self.assertEqual(gi.mask_email("a@b.co"), "a***@b.co")
        for bad in (None, "", "nobody", "@gmail.com", "x@", 5):
            self.assertEqual(gi.mask_email(bad), "")


class TheNonceStore(unittest.TestCase):
    def test_a_nonce_works_once(self):
        s = gi.NonceStore()
        n = s.issue()
        self.assertTrue(n and len(n) >= 24)
        self.assertTrue(s.consume(n))
        self.assertFalse(s.consume(n), "a second use is a replay")

    def test_a_nonce_we_never_issued_is_refused(self):
        s = gi.NonceStore()
        for bad in ("made-up", "", None, 5, ["x"]):
            self.assertFalse(s.consume(bad))

    def test_a_nonce_expires(self):
        now = [1000.0]
        s = gi.NonceStore(ttl=600, clock=lambda: now[0])
        a, b = s.issue(), s.issue()
        self.assertNotEqual(a, b)
        now[0] += 599
        self.assertTrue(s.consume(a))
        now[0] += 2
        self.assertFalse(s.consume(b), "ten minutes is the limit")

    def test_a_flood_cannot_grow_the_store_without_limit_and_room_comes_back(self):
        now = [0.0]
        s = gi.NonceStore(ttl=10, cap=3, clock=lambda: now[0])
        got = [s.issue() for _ in range(5)]
        self.assertEqual([x is not None for x in got], [True, True, True, False, False])
        now[0] += 11
        self.assertIsNotNone(s.issue(), "expired nonces are purged, so there is room again")

    def test_two_threads_cannot_both_use_one_nonce(self):
        s = gi.NonceStore()
        n = s.issue()
        wins = []
        gate = threading.Barrier(8)

        def go():
            gate.wait()
            wins.append(s.consume(n))
        ts = [threading.Thread(target=go) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sum(wins), 1)


class TheClaimStore(unittest.TestCase):
    """A good Google account that belongs to nobody yet is remembered under a ticket: bound to that account, single-use, short-lived, and its wrong passcodes are
    counted per Google account (a four-digit passcode must not be guessable by signing in with Google again and again)."""

    def test_a_ticket_stands_for_the_google_account_it_was_issued_for(self):
        s = gi.ClaimStore()
        t = s.issue("sub-a", "a@gmail.com")
        self.assertTrue(t and len(t) >= 24)
        self.assertEqual(s.peek(t), {"sub": "sub-a", "email": "a@gmail.com"})
        self.assertEqual(s.peek(t), {"sub": "sub-a", "email": "a@gmail.com"}, "looking is not using")
        self.assertNotEqual(s.issue("sub-b", "b@gmail.com"), t)

    def test_a_ticket_works_once(self):
        s = gi.ClaimStore()
        t = s.issue("sub-a", "a@gmail.com")
        self.assertEqual(s.spend(t), {"sub": "sub-a", "email": "a@gmail.com"})
        self.assertIsNone(s.spend(t), "a second use is a replay")
        self.assertIsNone(s.peek(t))

    def test_a_ticket_we_never_issued_is_refused_everywhere(self):
        s = gi.ClaimStore()
        for bad in ("made-up", "", None, 5, ["x"]):
            self.assertIsNone(s.peek(bad))
            self.assertIsNone(s.spend(bad))
            self.assertIsNone(s.wrong_passcode(bad))

    def test_a_ticket_expires_after_ten_minutes(self):
        now = [1000.0]
        s = gi.ClaimStore(clock=lambda: now[0])
        self.assertEqual(s.ttl, 600)
        a, b = s.issue("sub-a", "a@gmail.com"), s.issue("sub-b", "b@gmail.com")
        now[0] += 599
        self.assertIsNotNone(s.spend(a))
        now[0] += 2
        self.assertIsNone(s.spend(b))
        self.assertIsNone(s.peek(b))

    def test_a_google_account_has_one_live_ticket_and_the_newest_wins(self):
        s = gi.ClaimStore()
        old = s.issue("sub-a", "a@gmail.com")
        new = s.issue("sub-a", "a@gmail.com")
        self.assertNotEqual(old, new)
        self.assertIsNone(s.peek(old), "the older ticket of the same account is gone")
        self.assertIsNotNone(s.peek(new))

    def test_wrong_passcodes_are_counted_and_the_fifth_locks_the_google_account_out(self):
        s = gi.ClaimStore()
        t = s.issue("sub-a", "a@gmail.com")
        self.assertEqual(s.max_tries, 5)
        self.assertEqual([s.wrong_passcode(t) for _ in range(4)], [4, 3, 2, 1])
        self.assertEqual(s.tries_left("sub-a"), 1)
        self.assertFalse(s.locked("sub-a"))
        self.assertEqual(s.wrong_passcode(t), 0)
        self.assertTrue(s.locked("sub-a"))
        self.assertIsNone(s.peek(t), "the ticket dies with the last try")

    def test_signing_in_with_google_again_does_not_reset_the_count(self):
        s = gi.ClaimStore()
        for _ in range(5):
            t = s.issue("sub-a", "a@gmail.com")
            s.wrong_passcode(t)
        self.assertTrue(s.locked("sub-a"), "five wrong passcodes across five tickets is still five: a new ticket is no fresh start")
        self.assertFalse(s.locked("sub-b"), "another Google account is unaffected")

    def test_the_lockout_ends_with_the_window_and_a_success_forgets_the_count(self):
        now = [0.0]
        s = gi.ClaimStore(clock=lambda: now[0])
        t = s.issue("sub-a", "a@gmail.com")
        for _ in range(5):
            s.wrong_passcode(t)
        self.assertTrue(s.locked("sub-a"))
        now[0] += 601
        self.assertFalse(s.locked("sub-a"), "ten minutes later it may try again")
        t = s.issue("sub-a", "a@gmail.com")
        s.wrong_passcode(t)
        s.wrong_passcode(t)
        self.assertEqual(s.tries_left("sub-a"), 3)
        self.assertIsNotNone(s.spend(t))
        self.assertEqual(s.tries_left("sub-a"), 5, "a claim that worked clears the count")

    def test_a_flood_cannot_grow_the_store_without_limit_and_room_comes_back(self):
        now = [0.0]
        s = gi.ClaimStore(ttl=10, cap=3, clock=lambda: now[0])
        got = [s.issue("sub-%d" % i, "x%d@gmail.com" % i) for i in range(5)]
        self.assertEqual([x is not None for x in got], [True, True, True, False, False])
        now[0] += 11
        self.assertIsNotNone(s.issue("sub-9", "x9@gmail.com"), "expired tickets are purged, so there is room again")

    def test_a_replaced_ticket_makes_room_for_the_same_account_even_when_full(self):
        s = gi.ClaimStore(cap=2)
        s.issue("sub-a", "a@gmail.com")
        s.issue("sub-b", "b@gmail.com")
        self.assertIsNotNone(s.issue("sub-a", "a@gmail.com"), "the account's own older ticket is replaced, not added")
        self.assertIsNone(s.issue("sub-c", "c@gmail.com"))

    def test_two_threads_cannot_both_spend_one_ticket(self):
        s = gi.ClaimStore()
        t = s.issue("sub-a", "a@gmail.com")
        wins = []
        gate = threading.Barrier(8)

        def go():
            gate.wait()
            wins.append(s.spend(t) is not None)
        ts = [threading.Thread(target=go) for _ in range(8)]
        [x.start() for x in ts]
        [x.join() for x in ts]
        self.assertEqual(sum(wins), 1)


class CertsServer:
    """Plays Google's certificate endpoint: serves a JWKS made from the keys it is given, and counts the requests."""

    def __init__(self, keys):
        self.keys = keys                      # {kid: private key}
        self.hits = 0
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits += 1
                body = json.dumps({"keys": [dict(jwt.algorithms.RSAAlgorithm.to_jwk(k.public_key(), as_dict=True), kid=kid, use="sig", alg="RS256") for kid, k in outer.keys.items()]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d/certs" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@unittest.skipUnless(HAVE_CRYPTO, "needs PyJWT and cryptography")
class TheRealKeySourceAgainstALocalCertsServer(unittest.TestCase):
    def test_the_whole_path_with_real_key_fetching(self):
        k1 = new_key()
        srv = CertsServer({"k1": k1})
        try:
            src = gi.KeySource(url=srv.url, lifespan=3600, timeout=3)
            c = gi.verify_id_token(make_token(k1, kid="k1"), audience=CLIENT, expected_nonce="N-1", source=src)
            self.assertEqual(c["email"], "sam.smith@gmail.com")
            first = srv.hits
            gi.verify_id_token(make_token(k1, kid="k1"), audience=CLIENT, expected_nonce="N-1", source=src)
            self.assertEqual(srv.hits, first, "the keys are cached: a second sign-in does not ask Google again")
        finally:
            srv.stop()

    def test_a_key_rotation_is_picked_up(self):
        k1, k2 = new_key(), new_key()
        srv = CertsServer({"k1": k1})
        try:
            src = gi.KeySource(url=srv.url, lifespan=3600, timeout=3)
            gi.verify_id_token(make_token(k1, kid="k1"), audience=CLIENT, expected_nonce="N-1", source=src)
            srv.keys = {"k2": k2}                                             # Google rotates: a new key id
            gi.verify_id_token(make_token(k2, kid="k2"), audience=CLIENT, expected_nonce="N-1", source=src)
        finally:
            srv.stop()

    def test_a_token_naming_a_key_google_does_not_have_is_refused_as_invalid(self):
        k1, rogue = new_key(), new_key()
        srv = CertsServer({"k1": k1})
        try:
            src = gi.KeySource(url=srv.url, lifespan=3600, timeout=3)
            with self.assertRaises(gi.GoogleAuthError) as cm:
                gi.verify_id_token(make_token(rogue, kid="rogue-key"), audience=CLIENT, expected_nonce="N-1", source=src)
            self.assertEqual(cm.exception.reason, "token_invalid")
        finally:
            srv.stop()

    def test_google_being_unreachable_is_its_own_reason_not_a_bad_token(self):
        k1 = new_key()
        srv = CertsServer({"k1": k1})
        url = srv.url
        srv.stop()                                                            # nothing listens any more
        src = gi.KeySource(url=url, lifespan=3600, timeout=2)
        with self.assertRaises(gi.GoogleAuthError) as cm:
            gi.verify_id_token(make_token(k1, kid="k1"), audience=CLIENT, expected_nonce="N-1", source=src)
        self.assertEqual(cm.exception.reason, "google_unreachable")


if __name__ == "__main__":
    unittest.main()
