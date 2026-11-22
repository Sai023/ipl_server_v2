"""
BCL Fantasy - Origin                                      origin v1.0.0
===========================================================================
Where the app lives: the ONE place that knows the app's public address, and the switch that turns an OLD address into a "We've moved" page.

Pure functions only: stdlib only, zero project imports.

Why (plan item 1.10, 2026-11-22): the league stays on the free Render address (no domain is bought). An address that may one day change must not be written
into the code: an installed app, a Google sign-in setting and a reminder's sender identity all hang on it. So nothing in the code names the address; code that
needs it asks this module, and the module reads ONE setting. A test (tests/test_origin.py) fails if the live address is written into a source file.

The settings (environment variables, all optional)
--------------------------------------------------
PUBLIC_BASE_URL      the app's own address, e.g. https://league.example.org: an ORIGIN (https + host [+ port]), no path. Wins over everything.
RENDER_EXTERNAL_URL  set by Render itself on a web service (the address Render gave the service): used when PUBLIC_BASE_URL is not set. It does NOT follow
                     a custom domain, which is why PUBLIC_BASE_URL wins.
MOVED_TO_URL         set ONLY on an OLD address after a move: every page there becomes the "We've moved" page that points to this new address, and every API
                     call except /api/ping, /api/health and /api/version answers 410 with the reason "moved". A request that came to the address it names is
                     never turned away: a new service that inherited the variable must not tell everyone it has moved to itself, and one service that answers
                     both an old and a new hostname shows the page on the old one only. (The test is the Host header of the request, never PUBLIC_BASE_URL.)

Public API
----------
normalize_base_url(raw) -> str | None
    A clean origin ("https://host" or "https://host:port", lower case, no trailing slash, default port dropped), or None when `raw` is not one: empty, no
    scheme, a path, a query, a fragment, user info, a space or a backslash, a non-ASCII or malformed host, a bad port, or plain http for anything but the
    developer's own machine (localhost, 127.0.0.1, [::1]).

origin_of_host(host_header) -> str | None
    The origin a request's Host header implies: https for a real name, http for the developer's own machine; None when the header is not a clean host.

resolve_public_base(env, request_host=None) -> (url | None, source)
    The app's own address and where it came from: "env" (PUBLIC_BASE_URL), "platform" (RENDER_EXTERNAL_URL), "request" (derived from the Host header of the
    request being answered, see origin_of_host) or "none". Code with no request (an alert sent from a background job)
    passes no `request_host` and must cope with None.

moved_target(env) -> str | None
    The clean address in MOVED_TO_URL, or None.

config_problems(env) -> [str]
    Plain sentences about settings that are set but unusable (they are ignored, never fatal). Logged at boot and shown by /api/version.

moved_decision(method, path, moved_to, own_origins=()) -> "pass" | "page" | "gone"
    What an OLD address does with a request: "pass" (answer it as usual: static files, the manifest, /offline, /api/ping, /api/health, /api/version, and anything
    that is not a person opening a link), "page" (answer with the "We've moved" page: a GET or HEAD for a path that opens the app) or "gone" (any other /api/
    call: 410). Always "pass" when `moved_to` is empty or is one of `own_origins` (the origins this request came to).

moved_page_html(moved_to) -> str, moved_gone_body(moved_to) -> dict
    The page (self-contained: no script, one inline style, the logo from /static) and the JSON body of the 410.
"""

import html as _html
import re
from urllib.parse import urlsplit

PUBLIC_BASE_ENV = "PUBLIC_BASE_URL"
PLATFORM_ENV = "RENDER_EXTERNAL_URL"
MOVED_TO_ENV = "MOVED_TO_URL"

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST_RE = re.compile(r"^" + _LABEL + r"(?:\." + _LABEL + r")*$")
_DEFAULT_PORT = {"https": 443, "http": 80}

# an OLD address keeps answering these, so Render's health check, the page's banner probe and an operator asking "which version is this?" still work
_OPEN_API = ("/api/ping", "/api/health", "/api/version")
_OPEN_PATHS = ("/manifest.json", "/offline")


def _is_local(host):
    return host in _LOCAL_HOSTS or host.endswith(".localhost")


def normalize_base_url(raw):
    """A clean origin, or None (see the module docstring for what is refused)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or len(s) > 300:
        return None
    if any(ord(c) <= 32 or ord(c) >= 127 for c in s) or any(c in s for c in "\\@?#"):
        return None                              # spaces, non-ASCII, user-info tricks, a query or a fragment
    try:
        p = urlsplit(s)
        port = p.port
    except ValueError:                           # a malformed IPv6 literal, a port that is not a number or out of range
        return None
    scheme = p.scheme.lower()
    if scheme not in _DEFAULT_PORT or p.path not in ("", "/"):
        return None
    host = (p.hostname or "").lower()
    if not host:
        return None
    if ":" in host:                              # an IPv6 literal: only the loopback address is accepted
        if host != "::1":
            return None
        shown = "[::1]"
    else:
        if len(host) > 253 or not _HOST_RE.match(host):
            return None
        shown = host
    if scheme == "http" and not _is_local(host):
        return None                              # plain http is for the developer's own machine only
    if port is not None and not 1 <= port <= 65535:
        return None
    out = scheme + "://" + shown
    if port is not None and port != _DEFAULT_PORT[scheme]:
        out += ":" + str(port)
    return out


def origin_of_host(host_header):
    """The origin a request's Host header implies (https for a real name, http for the developer's own machine), or None when the header is not a clean host."""
    h = (host_header or "").strip().lower()
    if not h or any(c in h for c in "/\\@?# "):
        return None
    try:
        host = urlsplit("//" + h).hostname or ""
    except ValueError:
        return None
    return normalize_base_url(("http" if _is_local(host) else "https") + "://" + h)


def resolve_public_base(env, request_host=None):
    """(url, source): PUBLIC_BASE_URL, else the platform's own address, else the one the request came to, else (None, "none")."""
    env = env or {}
    for key, source in ((PUBLIC_BASE_ENV, "env"), (PLATFORM_ENV, "platform")):
        url = normalize_base_url(env.get(key))
        if url:
            return url, source
    url = origin_of_host(request_host)
    if url:
        return url, "request"
    return None, "none"


def moved_target(env):
    """The clean address in MOVED_TO_URL, or None (unset, empty or not a clean address)."""
    return normalize_base_url((env or {}).get(MOVED_TO_ENV))


def config_problems(env):
    """Sentences about settings that are set but unusable. Nothing here is fatal: an unusable setting is ignored."""
    env = env or {}
    out = []
    for key, effect in ((PUBLIC_BASE_ENV, "the address is taken from the platform or from each request instead"),
                        (MOVED_TO_ENV, "this address is NOT showing the moved page")):
        raw = (env.get(key) or "").strip()
        if raw and normalize_base_url(raw) is None:
            out.append("%s is set but is not a clean address (https://host, no path, no query, no user name): it is ignored, so %s" % (key, effect))
    return out


def moved_decision(method, path, moved_to, own_origins=()):
    """What an old address does with this request: "pass", "page" or "gone" (see the module docstring)."""
    if not moved_to or moved_to in tuple(own_origins or ()):
        return "pass"
    p = path or "/"
    if p == "/api" or p.startswith("/api/"):
        return "pass" if p in _OPEN_API else "gone"
    if p.startswith("/static/") or p in _OPEN_PATHS:
        return "pass"
    if (method or "").upper() in ("GET", "HEAD"):
        leaf = p.rsplit("/", 1)[-1]
        if leaf in ("", "index.html") or "." not in leaf:        # a person opening a link: the same rule the app uses to decide who gets the app page
            return "page"
    return "pass"


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#F3EBD8">
<meta name="robots" content="noindex">
<title>BCL Fantasy has moved</title>
<style>
html{-webkit-text-size-adjust:100%}
body{margin:0;background:#F3EBD8;color:#3A352C;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;line-height:1.5}
main{max-width:440px;margin:0 auto;padding:32px 20px 48px;text-align:center}
h1{color:#231F18;margin:16px 0 8px;font-size:30px}
h2{color:#231F18;margin:32px 0 8px;font-size:18px}
p{margin:12px 0}
.go{display:inline-block;min-height:48px;box-sizing:border-box;padding:12px 24px;border-radius:12px;background:#231F18;color:#F3EBD8;font-weight:700;text-decoration:none}
.go:focus-visible{outline:3px solid #8A4B12;outline-offset:3px}
.addr{font-family:ui-monospace,Consolas,monospace;font-size:15px;word-break:break-all;color:#231F18}
ol{text-align:left;margin:8px auto;padding-left:24px}
li{margin:8px 0}
.note{color:#6A6255;font-size:15px}
</style>
</head>
<body>
<main>
<img src="/static/brand/mark-paper.svg" width="96" height="96" alt="">
<h1>We've moved</h1>
<p>BCL Fantasy now lives at a new address. This old address no longer opens the game.</p>
<p><a class="go" href="@NEW@/">Open the new address</a></p>
<p class="addr">@NEW@</p>
<h2>It takes about five minutes</h2>
<ol>
<li>Open the new address above.</li>
<li>Tap <b>Continue with Google</b>, with the same Google account as before.</li>
<li>If you use the Android app, install the new version that the league admin sends you. It replaces the old one.</li>
<li>If you had turned reminders on, turn them on again.</li>
</ol>
<p class="note">Your team, your points and the league are all still there. Only the address changed.</p>
</main>
</body>
</html>
"""


def moved_page_html(moved_to):
    """The "We've moved" page. `moved_to` is escaped here, whatever the caller did with it."""
    return _PAGE.replace("@NEW@", _html.escape(str(moved_to), quote=True))


def moved_gone_body(moved_to):
    """The JSON body of the 410 an old address gives an API call."""
    return {"error": "This app has moved to a new address.", "ok": False, "code": 410, "reason": "moved", "moved_to": moved_to}
