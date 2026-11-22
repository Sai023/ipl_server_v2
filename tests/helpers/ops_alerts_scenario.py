"""Run in a SUBPROCESS by tests/test_ops_core.py. The admin alerts of Phase 2 through the REAL tick routine and a fake Telegram (a small server on this machine), on a scratch
schema. Prints one JSON document after the marker @@RESULT@@:
  1 a competition that is locked and cannot start: ONE message, nothing a minute later, "STILL FAILING" after half an hour, then RESOLVED + "is live" when it starts
  2 a send that fails is raised again by the next tick (it is not lost)
  3 the test alert: ok, a failing Telegram says so without the token, an unconfigured one says so
  4 without Telegram nothing is recorded, so a problem that is already there is raised the moment Telegram is set up
  5 a clock that goes quiet in a busy window, and is heard again
Environment: DATABASE_URL (a scratch schema)."""
import http.server
import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
os.chdir(REPO)
os.environ.pop("HOSTED", None)
for k in ("OPS_TOKEN", "ROLLOVER_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_API_BASE", "PUBLIC_BASE_URL", "RENDER_EXTERNAL_URL", "MOVED_TO_URL", "OPS_APP_CLOCK"):
    os.environ.pop(k, None)

import server  # noqa: E402,F401
import base  # noqa: E402
import routes  # noqa: E402
from base import app, db, hash_passcode  # noqa: E402

NOW = datetime.now(timezone.utc)
H, D, M = timedelta(hours=1), timedelta(days=1), timedelta(minutes=1)
TEAM = [f"p{i}" for i in range(1, 12)]
ADMIN = "tok-alerts-admin"
TG_TOKEN, TG_CHAT = "987654321:AAH-alerts-test-token-0123456789", "-100555"
BASE_URL = "https://league.example.org"


class FakeTelegram:
    def __init__(self):
        self.mode = "ok"
        self.seen = []                                    # every request: {"text", "chat_id", "status"}
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8") or "{}")
                if outer.mode == "500":
                    status, data = 500, b'{"ok": false, "description": "Internal Server Error"}'
                elif outer.mode == "401":
                    status, data = 401, b'{"ok": false, "error_code": 401, "description": "Unauthorized"}'
                else:
                    status, data = 200, b'{"ok": true, "result": {"message_id": 1}}'
                outer.seen.append({"text": body.get("text"), "chat_id": body.get("chat_id"), "status": status, "path": self.path})
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


fake = FakeTelegram()


def configure(on=True):
    for k, v in (("TELEGRAM_BOT_TOKEN", TG_TOKEN), ("TELEGRAM_CHAT_ID", TG_CHAT), ("TELEGRAM_API_BASE", "http://127.0.0.1:%d" % fake.port)):
        if on:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


def rows(*offsets, at=NOW):
    return [{"week_no": i, "lock_at_utc": (at + o).isoformat(), "first_match_utc": (at + o + H).isoformat()} for i, o in enumerate(offsets, start=1)]


def make(slug, name, status, schedule, max_weeks=3, auto=None):
    db.create_competition(slug, name, status=status, max_weeks=max_weeks, week1_anchor_utc=(NOW - 60 * D).isoformat())
    with db._pool.connection() as con:
        con.execute("INSERT INTO players (competition_id,id,name,team,price,role) SELECT %s,'p'||g,'Player '||g,'AAA',5.0,"
                    "CASE WHEN g%%4=0 THEN 'WK' WHEN g%%4=1 THEN 'BAT' WHEN g%%4=2 THEN 'AR' ELSE 'BOWL' END FROM generate_series(1,30) g", (slug,))
    for who in ("Sai", "Moe"):
        db.save_next_week(who, TEAM, "p1", "p2", competition_id=slug)
    db.set_schedule(slug, schedule, auto_activate=auto)


marks = {"n": 0}


def tick(after, source="supabase", beat=True):
    """One tick at NOW + `after`; returns what the tick said and every message Telegram saw since the last call."""
    rep = routes._ops_run(source, now=NOW + after, beat=beat)
    new = fake.seen[marks["n"]:]
    marks["n"] = len(fake.seen)
    return {"rep": {k: rep[k] for k in ("ok", "alerts_sent", "health", "mode")}, "applied": [(a["competition"], a["to_week"], a["activated"]) for a in rep["applied"]],
            "sent": [m["text"] for m in new if m["status"] == 200], "attempts": [m["status"] for m in new]}


def call(method, url, token=None):
    headers = {"Authorization": "Bearer " + token} if token is not None else {}
    r = app.test_client().open(url, method=method, headers=headers)
    return {"http": r.status_code, "body": r.get_json(silent=True)}


db.upsert_member_auth("Sai", hash_passcode("1234", "Sai"), 0, 1)
db.upsert_member_auth("Moe", hash_passcode("1234", "Moe"), 0, 0)
db.create_session(ADMIN, "Sai", ttl_days=1)
base._write_limiter._max = 10 ** 6
base._auth_limiter._max = 10 ** 6
configure(True)
os.environ["PUBLIC_BASE_URL"] = BASE_URL
out = {}

# ── 1  an incident: one message, a reminder, resolved + the event ──────────────────────────────────────────────────────────────────────────
make("a_comp", "Alpha League", "upcoming", rows(-5 * M, 7 * D, 14 * D))              # locked 5 minutes ago, upcoming, does not start itself: blocked
out["s1_first"] = tick(0 * M)
out["s1_a_minute_later"] = tick(1 * M)
out["s1_twenty_nine_minutes"] = tick(29 * M)
out["s1_half_an_hour"] = tick(30 * M)
db.set_meta("auto_activate:a_comp", "1")                                          # an admin turns auto-activation on: the next tick starts it
out["s1_fixed"] = tick(32 * M)
out["s1_quiet_again"] = tick(33 * M)
out["s1_state"] = db.ops_alert_state()

# ── 2  a send that fails is raised again ─────────────────────────────────────────────────────────────────────────────────────────────────
make("b_comp", "Beta League", "upcoming", rows(-5 * M, 7 * D, 14 * D))
fake.mode = "500"
out["s2_failed_send"] = tick(40 * M)
out["s2_state_after_failure"] = sorted((db.ops_alert_state().get("incidents") or {}))
fake.mode = "ok"
out["s2_retry"] = tick(41 * M)
out["s2_state_after_retry"] = sorted((db.ops_alert_state().get("incidents") or {}))

# ── 3  the test alert ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
n0 = len(fake.seen)
out["s3_ok"] = call("POST", "/api/ops/test-alert", ADMIN)
out["s3_what_telegram_got"] = [m["text"] for m in fake.seen[n0:]]
fake.mode = "401"
out["s3_telegram_refuses"] = call("POST", "/api/ops/test-alert", ADMIN)
fake.mode = "ok"
configure(False)
out["s3_not_configured"] = call("POST", "/api/ops/test-alert", ADMIN)
marks["n"] = len(fake.seen)

# ── 4  without Telegram nothing is recorded ──────────────────────────────────────────────────────────────────────────────────────────────
make("c_comp", "Gamma League", "upcoming", rows(-5 * M, 7 * D, 14 * D))
before = json.dumps(db.ops_alert_state(), sort_keys=True)
out["s4_not_configured"] = tick(50 * M)
out["s4_state_unchanged"] = json.dumps(db.ops_alert_state(), sort_keys=True) == before
configure(True)
out["s4_configured_now"] = tick(51 * M)
out["s4_state"] = sorted((db.ops_alert_state().get("incidents") or {}))

# ── 5  a clock that goes quiet in a busy window ───────────────────────────────────────────────────────────────────────────────────────────
make("d_comp", "Delta League", "active", rows(10 * H, 7 * D, 14 * D))                  # a lock within 48 hours: the clocks should tick every minute
db.touch_heartbeat("tick:pc", "ok", now=NOW + 55 * M)                                 # the PC clock last ticked 5 minutes before the tick below
out["s5_quiet_clock"] = tick(60 * M)
db.touch_heartbeat("tick:pc", "ok", now=NOW + 60 * M + timedelta(seconds=30))
out["s5_heard_again"] = tick(61 * M)

out["tg_token_in_any_text"] = any(TG_TOKEN in (m["text"] or "") or "AAH-alerts" in (m["text"] or "") for m in fake.seen)
out["chat_ids"] = sorted({m["chat_id"] for m in fake.seen})
out["paths"] = sorted({m["path"] for m in fake.seen})

print("@@RESULT@@" + json.dumps(out, default=str) + "@@RESULT@@")
