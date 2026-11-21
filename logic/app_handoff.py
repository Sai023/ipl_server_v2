"""
Sign-in hand-off to the installed app                              app_handoff v1.0.0
===========================================================================
Phase 3 of the SA20 readiness plan (2026-11-21). The Android app is a WebView, and Google refuses to sign anybody in inside a WebView. So the app does the sign-in in the
phone's own browser and the browser hands the member back through a link of the app's own: the pattern native apps use (RFC 8252, with the PKCE proof of RFC 7636).
This module is the server's memory of those hand-offs and does NOTHING else: no Flask, no database. What a hand-off MEANS (a signed-in member, a new session for the app)
is the routes' business (routes.api_auth_handoff and api_auth_handoff_redeem).

  1. The APP makes a random secret, the VERIFIER, and keeps it in its own storage. It sends only a hash of it, the CHALLENGE = base64url(SHA-256(verifier)), to the browser.
  2. The BROWSER signs the member in (Google, a passcode: anything) and asks for a CODE for that challenge:  issue(username, challenge).
  3. The browser gives the code to the app through the link bclfantasy://auth?code=...; the app proves it holds the verifier:  redeem(code, verifier)  answers the member's name ONCE.

A code that falls into the wrong hands (another app can register the same link) is useless without the verifier, which never left the app. A code is single-use even when
the verifier is wrong (a guess burns it), lives two minutes, and is bound to the member who asked for it; the store is capped. Kept in memory like the nonces and claim
tickets: a restart forgets them and the member simply starts the browser sign-in again.
"""
import base64
import collections
import hashlib
import hmac
import re
import secrets
import threading
import time

from config import HANDOFF_VER  # noqa: F401  (the version pin lives in config.py with the others)

CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")          # base64url of a SHA-256 digest (32 bytes), no padding
VERIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")       # RFC 7636: 43 to 128 unreserved characters
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def challenge_of(verifier):
    """base64url(SHA-256(verifier)) without padding: what the app sends instead of the verifier (RFC 7636, method S256)."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


class HandoffStore:
    """issue(username, challenge) -> a one-time code (or None when the store is full of live ones); redeem(code, verifier) -> the username, once, or None.
    - a code is bound to the member who asked for it and to the challenge it was asked for; nothing the app sends at redeem time can change either;
    - single-use: redeem() takes the code out of the store BEFORE it checks the verifier, so a wrong guess burns it;
    - valid for `ttl` seconds; one live code per (member, challenge): asking again for the same challenge replaces the older code;
    - capped: when full of live codes issue() answers None (the route says 'busy') instead of growing without limit."""

    def __init__(self, ttl=120, cap=2000, clock=time.monotonic):
        self._ttl, self._cap, self._clock = ttl, cap, clock
        self._items = collections.OrderedDict()           # code -> {"username", "challenge", "expires"}, oldest first (every code lives the same time)
        self._by_key = {}                                 # (username, challenge) -> its live code
        self._lock = threading.Lock()

    @property
    def ttl(self):
        return self._ttl

    def _purge(self, now):
        while self._items:
            code, t = next(iter(self._items.items()))
            if t["expires"] > now:
                break
            del self._items[code]
            if self._by_key.get((t["username"], t["challenge"])) == code:
                del self._by_key[(t["username"], t["challenge"])]

    def issue(self, username, challenge):
        """A code for this member and this challenge, or None when the store is full of live ones. The caller checks the challenge's shape (CHALLENGE_RE)."""
        now = self._clock()
        with self._lock:
            self._purge(now)
            old = self._by_key.pop((username, challenge), None)
            if old:
                self._items.pop(old, None)
            if len(self._items) >= self._cap:
                return None
            code = secrets.token_urlsafe(32)
            self._items[code] = {"username": username, "challenge": challenge, "expires": now + self._ttl}
            self._by_key[(username, challenge)] = code
            return code

    def redeem(self, code, verifier):
        """The member's name when `code` is live and `verifier` hashes to the challenge it was issued for; otherwise None. Either way the code is used up."""
        if not (isinstance(code, str) and CODE_RE.match(code) and isinstance(verifier, str) and VERIFIER_RE.match(verifier)):
            return None
        with self._lock:
            self._purge(self._clock())
            t = self._items.pop(code, None)
            if not t:
                return None
            if self._by_key.get((t["username"], t["challenge"])) == code:
                del self._by_key[(t["username"], t["challenge"])]
        # the proof is compared outside the lock, in constant time; the code is already gone, so a wrong verifier cannot be tried again
        return t["username"] if hmac.compare_digest(t["challenge"], challenge_of(verifier)) else None
