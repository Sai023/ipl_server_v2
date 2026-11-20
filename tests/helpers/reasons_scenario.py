"""Run in a SUBPROCESS by tests/test_error_reasons.py (so the app is imported fresh against a scratch schema, whatever else the test process loaded).
Drives the REAL routes through Flask's test client for every kind of refusal that carries a machine `reason`, and prints one JSON document.
Environment: DATABASE_URL (a scratch schema)."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
os.chdir(REPO)
os.environ.pop("HOSTED", None)

import server  # noqa: E402,F401   (registers the blueprint on base.app; nothing is started: that is under __main__)
from base import app, db, hash_passcode  # noqa: E402

NOW = datetime.now(timezone.utc)
PAST = (NOW - timedelta(days=200)).isoformat()
FUTURE = (NOW + timedelta(days=60)).isoformat()


def make(slug, status, anchor, max_weeks=6):
    if not db.get_competition(slug):
        db.create_competition(slug, slug.upper(), status=status, max_weeks=max_weeks, week1_anchor_utc=anchor)


def call(method, url, token=None, body=None):
    headers = {"Authorization": "Bearer " + token} if token else {}
    r = app.test_client().open(url, method=method, json=body, headers=headers)
    return {"http": r.status_code, "body": r.get_json()}


make("ipl_2026", "completed", PAST, max_weeks=10)       # the default competition
make("sa_done", "completed", PAST)                       # saving here is locked
make("sa_open", "upcoming", FUTURE)                      # saving here is open (pre-season)
db.upsert_member_auth("Sai", hash_passcode("1234", "Sai"), 0, 1)
db.upsert_member_auth("Newbie", hash_passcode("4321", "Newbie"), 0, 0)
db.create_session("tok-sai", "Sai")
db.create_session("tok-newbie", "Newbie")

out = {}
out["no_token"] = call("POST", "/api/save-next-week/Newbie?comp=sa_open", None, {"team": []})
out["bad_token"] = call("POST", "/api/save-next-week/Newbie?comp=sa_open", "not-a-real-token", {"team": []})
out["not_admin"] = call("GET", "/api/admin/members", "tok-newbie")
out["other_team"] = call("POST", "/api/save-next-week/Sai?comp=sa_open", "tok-newbie", {"team": []})
out["locked"] = call("POST", "/api/save-next-week/Newbie?comp=sa_done", "tok-newbie", {"team": []})
out["invalid_team"] = call("POST", "/api/save-next-week/Newbie?comp=sa_open", "tok-newbie", {"team": ["p1", "p2", "p3"], "cap": "p1", "vc": "p2"})
out["register_duplicate"] = call("POST", "/api/register", None, {"name": "Newbie", "passcode": "1111"})
out["register_bad_name"] = call("POST", "/api/register", None, {"name": "", "passcode": "1111"})
out["register_bad_pin"] = call("POST", "/api/register", None, {"name": "Zed", "passcode": "12"})
out["login_wrong"] = call("POST", "/api/login", None, {"name": "Newbie", "passcode": "0000"})
out["login_missing"] = call("POST", "/api/login", None, {"name": "", "passcode": ""})
out["pin_change_bad"] = call("POST", "/api/passcode/change", "tok-newbie", {"new": "12"})
out["reset_no_target"] = call("POST", "/api/admin/passcode/reset", "tok-sai", {})
out["reset_unknown"] = call("POST", "/api/admin/passcode/reset", "tok-sai", {"target_username": "Nobody"})

# the rate limiter last: the same address keeps calling a write route until it is refused
out["rate_limited"] = None
for _ in range(80):
    r = call("POST", "/api/login", None, {"name": "Newbie", "passcode": "0000"})
    if r["http"] == 429:
        out["rate_limited"] = r
        break

sys.stdout.write("\n@@RESULT@@" + json.dumps(out, default=str) + "\n")
