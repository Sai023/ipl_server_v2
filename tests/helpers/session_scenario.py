"""Run in a SUBPROCESS by tests/test_session_policy.py (so the app is imported fresh against a scratch schema, whatever else the test process loaded).
Drives the REAL auth routes through Flask's test client (register, login, whoami, passcode change) and prints one JSON document with what happened.
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
from base import app, db  # noqa: E402


def now():
    return datetime.now(timezone.utc)


def iso(**kw):
    return (now() + timedelta(**kw)).isoformat()


def expiry(token):
    with db._read() as con:
        r = con.execute("SELECT expires_at FROM sessions WHERE token=%s", (token,)).fetchone()
    return r["expires_at"] if r else None


def set_expiry(token, value):
    with db._write() as con:
        con.execute("UPDATE sessions SET expires_at=%s WHERE token=%s", (value, token))


def days_left(value):
    return None if value is None else (datetime.fromisoformat(value) - now()).total_seconds() / 86400.0


def whoami(token):
    return app.test_client().get("/api/whoami", headers={"Authorization": "Bearer " + token}).status_code


def used(token, label_value):
    """Give the session an expiry, use it once through the real route, and report what the row looked like before and after."""
    set_expiry(token, label_value)
    before = expiry(token)
    http = whoami(token)
    after = expiry(token)
    return {"http": http, "before": before, "after": after, "days_left_before": days_left(before), "days_left_after": days_left(after)}


if not db.get_competition("ipl_2026"):
    db.create_competition("ipl_2026", "IPL 2026", status="completed", max_weeks=10, week1_anchor_utc=iso(days=-200))

out = {}

# 1. the routes that start a session
r = app.test_client().post("/api/register", json={"name": "Newbie", "passcode": "4321"})
j = r.get_json() or {}
tok_reg = j.get("token", "")
out["register"] = {"http": r.status_code, "days_left": days_left(expiry(tok_reg))}
r = app.test_client().post("/api/login", json={"name": "Newbie", "passcode": "4321"})
j = r.get_json() or {}
tok_login = j.get("token", "")
out["login"] = {"http": r.status_code, "days_left": days_left(expiry(tok_login))}

# 2. using a session through a real route
before_fresh = expiry(tok_reg)
http_fresh = whoami(tok_reg)
out["fresh"] = {"http": http_fresh, "before": before_fresh, "after": expiry(tok_reg)}
out["just_over_30"] = used(tok_reg, iso(days=31))
out["just_under_30"] = used(tok_reg, iso(days=29))
out["ten_days_left"] = used(tok_reg, iso(days=10))

# 3. a session issued under the old 30-day rule (30 days from sign-in, so a little under 30 days remain when it is first used)
db.create_session("tok-old30", "Newbie", ttl_days=30)
out["old_30_day_session"] = {"http": whoami("tok-old30"), "days_left_after": days_left(expiry("tok-old30"))}

# 4. a finished session is rejected and removed, never revived; another person's finished session is cleaned up along the way
db.create_session("tok-finished", "Newbie")
out["expired"] = used("tok-finished", iso(days=-1))
db.create_session("tok-bystander", "Newbie")
set_expiry("tok-bystander", iso(hours=-5))
whoami(tok_reg)                                          # a different, valid session is used: the finished one should be swept away
out["cleanup"] = {"bystander_gone": expiry("tok-bystander") is None}
out["unknown"] = {"http": whoami("no-such-token")}

# 5. changing the passcode retires every old session and issues a fresh one
r = app.test_client().post("/api/passcode/change", json={"new": "9876"}, headers={"Authorization": "Bearer " + tok_login})
j = r.get_json() or {}
new_tok = j.get("token", "")
out["passcode_change"] = {"http": r.status_code, "days_left_new": days_left(expiry(new_tok)), "old_token_http": whoami(tok_login)}

sys.stdout.write("\n@@RESULT@@" + json.dumps(out, default=str) + "\n")
