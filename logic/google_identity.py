"""
Google sign-in, the server's half                              google_identity v1.0.0
===========================================================================
Phase 3 of the SA20 readiness plan (2026-11-21). The page shows Google's own "Continue with Google" button; Google hands the page a signed ID token (a JWT);
the page sends it here. This module checks that token and does NOTHING else: no Flask, no database. What a verified token MEANS (sign in, link, join) is the
routes' business.

A token is accepted only when ALL of these hold (Google's documented checklist, https://developers.google.com/identity/gsi/web/guides/verify-google-id-token):
  - it is signed by one of Google's published keys, with the algorithm pinned to RS256 (a token that names any other algorithm, "none" included, is refused
    before a key is even looked up, so the old "sign it with the public key as an HMAC secret" trick cannot work);
  - `aud` is OUR client id (otherwise a token Google issued to some other app could be replayed here);
  - `iss` is accounts.google.com or https://accounts.google.com;
  - it has not expired (a minute of clock leeway), and was not issued in the future;
  - `sub`, `exp`, `iat`, `iss`, `aud` are all present;
  - `email` is present and `email_verified` is true;
  - its `nonce` is the one we issued for this sign-in attempt (see NonceStore): a captured token cannot be replayed and a stranger's token cannot be fed to
    somebody else's browser.
The identity is Google's `sub` (a stable account id that is never reused), NEVER the email address, which can change.

The keys come from Google's certificate endpoint through PyJWT's PyJWKClient (cached; it refetches when a token names a key it does not hold, which is how a key
rotation is picked up). PyJWT is imported lazily: if it were ever missing, only Google sign-in would be unavailable, never the whole app.
"""
import collections
import hmac
import os
import secrets
import threading
import time

from config import GOOGLE_IDENTITY_VER  # noqa: F401  (the version pin lives in config.py with the others)
CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
MAX_TOKEN_CHARS = 4096          # a real ID token is about 1,000 to 1,300 characters
LEEWAY_SECONDS = 60             # clock difference we forgive, either way


class GoogleAuthError(Exception):
    """A refusal. `reason` is a machine word the route turns into the reply; `detail` is for the server log only and never reaches a caller."""

    def __init__(self, reason, detail=""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def client_id():
    """Our OAuth client id (public by design, not a secret), from the environment; '' when Google sign-in is not set up."""
    return (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()


def enabled():
    return bool(client_id())


def mask_email(email):
    """'sam.smith@gmail.com' -> 's***@gmail.com': enough for a member to recognise their own account, never the address itself."""
    local, _, domain = str(email or "").partition("@")
    return (local[:1] + "***@" + domain) if local and domain else ""


# ── Google's keys ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

class KeySource:
    """Google's public signing keys, fetched from `url` and cached for `lifespan` seconds."""

    def __init__(self, url=CERTS_URL, lifespan=3600, timeout=5):
        self._url, self._lifespan, self._timeout = url, lifespan, timeout
        self._client = None
        self._lock = threading.Lock()

    def key_for(self, token):
        """The public key that signed `token` (named by the `kid` in its header)."""
        import jwt
        with self._lock:
            if self._client is None:
                self._client = jwt.PyJWKClient(self._url, cache_keys=True, lifespan=self._lifespan, timeout=self._timeout)
            client = self._client
        try:
            return client.get_signing_key_from_jwt(token).key
        except jwt.PyJWKClientConnectionError as e:
            raise GoogleAuthError("google_unreachable", "could not fetch Google's keys: %s" % e)
        except jwt.PyJWTError as e:                      # an unknown kid, a header that is not a JWT header ...
            raise GoogleAuthError("token_invalid", "key lookup: %s" % type(e).__name__)


_default_source = KeySource()
_override_source = None


def set_key_source(source):
    """Tests give the verifier their own keys (so no network is touched). None puts Google's back."""
    global _override_source
    _override_source = source


def key_source():
    return _override_source or _default_source


# ── the check ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def verify_id_token(token, *, audience, expected_nonce, source=None, leeway=LEEWAY_SECONDS):
    """The claims we use ({"sub", "email", "name"}) of a valid token, or GoogleAuthError. `expected_nonce` is mandatory: a caller that has no nonce has no business here."""
    import jwt
    if not audience:
        raise GoogleAuthError("google_not_configured", "no client id")
    if not expected_nonce or not isinstance(expected_nonce, str):
        raise GoogleAuthError("nonce_invalid", "no nonce was sent")
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_CHARS or token.count(".") != 2:
        raise GoogleAuthError("token_invalid", "not the shape of a JWT")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise GoogleAuthError("token_invalid", "header: %s" % type(e).__name__)
    if header.get("alg") != "RS256":                    # pinned BEFORE any key is chosen: "none", "HS256" and every other algorithm are refused here
        raise GoogleAuthError("token_invalid", "algorithm %r" % (header.get("alg"),))
    key = (source or key_source()).key_for(token)
    try:
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=audience, issuer=list(ISSUERS), leeway=leeway,
                            options={"require": ["exp", "iat", "iss", "aud", "sub"]})
    except jwt.ExpiredSignatureError:
        raise GoogleAuthError("token_expired", "expired")
    except jwt.PyJWTError as e:
        raise GoogleAuthError("token_invalid", "decode: %s" % type(e).__name__)
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub or len(sub) > 255:
        raise GoogleAuthError("token_invalid", "no usable sub")
    email = claims.get("email")
    if not isinstance(email, str) or "@" not in email or len(email) > 320:
        raise GoogleAuthError("token_invalid", "no usable email")
    if claims.get("email_verified") not in (True, "true"):
        raise GoogleAuthError("email_unverified", "email_verified is not true")
    sent = claims.get("nonce")
    if not isinstance(sent, str) or not hmac.compare_digest(sent.encode("utf-8"), expected_nonce.encode("utf-8")):
        raise GoogleAuthError("nonce_invalid", "the token's nonce is not the one asked for")
    return {"sub": sub, "email": email.strip(), "name": str(claims.get("name") or "")[:60]}


# ── nonces ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

class NonceStore:
    """Single-use, short-lived random strings we hand the page BEFORE it asks Google, which Google puts inside the token it signs. Kept in memory: a restart
    forgets them, and the only cost is that a sign-in begun just before a restart has to be started again."""

    def __init__(self, ttl=600, cap=5000, clock=time.monotonic):
        self._ttl, self._cap, self._clock = ttl, cap, clock
        self._items = collections.OrderedDict()           # nonce -> expiry, oldest first
        self._lock = threading.Lock()

    def _purge(self, now):
        while self._items:
            nonce, expires = next(iter(self._items.items()))
            if expires > now:
                break
            del self._items[nonce]

    def issue(self):
        """A fresh nonce, or None when the store is full of live ones (a flood: the caller answers 'busy' instead of growing without limit)."""
        now = self._clock()
        with self._lock:
            self._purge(now)
            if len(self._items) >= self._cap:
                return None
            nonce = secrets.token_urlsafe(24)
            self._items[nonce] = now + self._ttl
            return nonce

    def consume(self, nonce):
        """True exactly once for a nonce we issued that has not expired."""
        if not isinstance(nonce, str) or not nonce:
            return False
        now = self._clock()
        with self._lock:
            self._purge(now)
            return self._items.pop(nonce, None) is not None
