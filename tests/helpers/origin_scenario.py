"""Run in a SUBPROCESS by tests/test_origin.py (so the app is imported fresh against a scratch schema, whatever else the test process loaded).
Drives the REAL routes through Flask's test client for plan item 1.10 (2026-11-22, release 2.27.0) and prints one JSON document after the marker @@RESULT@@:
  A  /api/version says where the server believes its address is (nothing set, the platform's address, PUBLIC_BASE_URL, an unusable PUBLIC_BASE_URL)
  B  an OLD address (MOVED_TO_URL set): links open the moved page, the API is 410, what keeps working, and the page rule against the app's own
  C  a request that came to the address MOVED_TO_URL names stays open (an inherited variable; one service with an old and a new hostname shows the page on the
     old one only); an unusable MOVED_TO_URL is ignored
Environment: DATABASE_URL (a scratch schema)."""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
os.chdir(REPO)
os.environ.pop("HOSTED", None)

import server  # noqa: E402,F401   (registers the blueprint on base.app; nothing is started: that is under __main__)
import base  # noqa: E402
import routes  # noqa: E402,F401
from base import app  # noqa: E402

LIVE = "https://ipl-fantasy-2026-h0m9.onrender.com"
NEW = "https://new.example.org"
SETTINGS = ("PUBLIC_BASE_URL", "RENDER_EXTERNAL_URL", "MOVED_TO_URL")


def env(**kw):
    """Set exactly these settings (and none of the others)."""
    for k in SETTINGS:
        os.environ.pop(k, None)
    for k, v in kw.items():
        os.environ[k] = v


def call(method, url, body=None, headers=None, host=None):
    """`host` (an origin such as https://old.example.net) makes the request come to that hostname instead of the test client's own `localhost`."""
    kw = {"base_url": host} if host else {}
    r = app.test_client().open(url, method=method, json=body, headers=dict(headers or {}), **kw)
    ct = r.headers.get("Content-Type", "")
    data = r.get_data()
    txt = data.decode("utf-8", "replace")
    return {"http": r.status_code, "ct": ct, "body": r.get_json(silent=True) if "json" in ct else None, "text": txt[:300], "text_all": txt[:20000], "body_len": len(data),
            "is_html": "<html" in txt.lower() or "<!doctype" in txt.lower(),
            "app_page": 'rel="manifest"' in txt,                                  # the app page links the manifest; the moved page and /offline do not
            "moved_page": "BCL Fantasy has moved" in txt and "We've moved" in txt,
            "headers": {k: v for k, v in r.headers.items()}}


out = {}

# ── A  nothing set, then each way the address can be known ─────────────────────────────────────────────────────────────────────────────────
env()
out["a_unset"] = call("GET", "/api/version")
out["a_page_unset"] = call("GET", "/")
out["a_state_unset"] = call("GET", "/api/auth/config")                  # an API route that is NOT on the old address's open list, answering normally
out["a_post_root"] = call("POST", "/")
env(RENDER_EXTERNAL_URL=LIVE)
out["a_platform"] = call("GET", "/api/version")
env(PUBLIC_BASE_URL=NEW, RENDER_EXTERNAL_URL=LIVE)
out["a_env"] = call("GET", "/api/version")
env(PUBLIC_BASE_URL="https://example.com/app", RENDER_EXTERNAL_URL=LIVE)
out["a_bad_env"] = call("GET", "/api/version")

# the page rule: what the app itself does with each of these when nothing is set (the app page, or not)
PARITY = [("GET", "/"), ("GET", "/index.html"), ("GET", "/some/link"), ("GET", "/a/b/c"), ("GET", "/league/week-3/"), ("HEAD", "/"), ("GET", "/robots.txt"), ("GET", "/favicon.ico"),
          ("GET", "/wp-login.php"), ("GET", "/some/file.json"), ("GET", "/.env"), ("POST", "/"), ("POST", "/some/link"), ("PUT", "/x"), ("GET", "/static/ui.js"),
          ("GET", "/manifest.json"), ("GET", "/offline"), ("GET", "/api/nothing")]
env()
unset_page = {(m, p): call(m, p)["app_page"] for m, p in PARITY}

# ── B  an OLD address ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
env(MOVED_TO_URL=NEW)
out["b_root"] = call("GET", "/")
out["b_link"] = call("GET", "/some/link")
out["b_index"] = call("GET", "/index.html")
out["b_head"] = call("HEAD", "/")
out["b_state"] = call("GET", "/api/state")
out["b_login"] = call("POST", "/api/login", {"name": "Sai", "passcode": "1234"})
out["b_auth_config"] = call("GET", "/api/auth/config")
out["b_whoami"] = call("GET", "/api/whoami", headers={"Authorization": "Bearer nothing"})
out["b_ping"] = call("GET", "/api/ping")
out["b_health"] = call("GET", "/api/health")
out["b_version"] = call("GET", "/api/version")
out["b_static"] = call("GET", "/static/ui.js")
out["b_logo"] = call("GET", "/static/brand/mark-paper.svg")
out["b_manifest"] = call("GET", "/manifest.json")
out["b_offline"] = call("GET", "/offline")
out["b_robots"] = call("GET", "/robots.txt")
out["b_post_root"] = call("POST", "/")
out["b_parity"] = []
for m, p in PARITY:
    r = call(m, p)
    out["b_parity"].append({"method": m, "path": p, "moved_page": r["moved_page"], "app_page_when_unset": unset_page[(m, p)], "http": r["http"]})

# ── C  a request that came to the NEW address, one service with two hostnames, and an unusable setting ────────────────────────────────────
env(PUBLIC_BASE_URL="http://localhost", MOVED_TO_URL="http://localhost")        # the test client's own host is localhost: this request came to the new address
out["c_same_host"] = call("GET", "/")
out["c_same_host_api"] = call("GET", "/api/auth/config")
out["c_same_host_version"] = call("GET", "/api/version")
env(MOVED_TO_URL="http://localhost")                                           # nothing else configured: the request's own host is what gives it away
out["c_request_only"] = call("GET", "/")
env(PUBLIC_BASE_URL=NEW, MOVED_TO_URL=NEW)                                     # ONE service that answers an old hostname and the new one
out["c_two_hosts_old"] = call("GET", "/", host="https://old.example.net")
out["c_two_hosts_old_api"] = call("GET", "/api/auth/config", host="https://old.example.net")
out["c_two_hosts_new"] = call("GET", "/", host=NEW)
out["c_two_hosts_new_api"] = call("GET", "/api/auth/config", host=NEW)
env(MOVED_TO_URL="new.example.org")
out["c_bad"] = call("GET", "/api/version")
out["c_bad_page"] = call("GET", "/")

print("@@RESULT@@" + json.dumps(out) + "@@RESULT@@")
