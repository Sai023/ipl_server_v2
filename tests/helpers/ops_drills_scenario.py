"""Run in a SUBPROCESS by tests/test_ops_core.py. The "kill the clock" drills of Phase 2 (the exit test of the plan: each trigger disabled in turn, the handover must still
land) on the REAL routes and a scratch schema. Prints one JSON document after the marker @@RESULT@@:
  1..4  each trigger ALONE lands the handover (an external clock, the server's own clock loop, an admin, the older scheduler route)
  5     every trigger at the same moment lands it exactly once
  6     the server's own clock loop: how long it sleeps (never more than five minutes, never less than five seconds), that a failing cycle does not kill it, max_cycles
Environment: DATABASE_URL (a scratch schema)."""
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

H, D, M = timedelta(hours=1), timedelta(days=1), timedelta(minutes=1)
TEAM = [f"p{i}" for i in range(1, 12)]
ADMIN = "tok-drills-admin"
OPS_TOKEN, SCHED_TOKEN = "drill-clock-token-0123456789abcdef", "drill-scheduler-token-abcdef012345"
os.environ["OPS_TOKEN"], os.environ["ROLLOVER_TOKEN"] = OPS_TOKEN, SCHED_TOKEN


def make_due(slug):
    """An active competition whose week-1 lock passed three minutes ago (by the REAL clock, which the routes and the loop use)."""
    at = datetime.now(timezone.utc)
    rows = [{"week_no": i, "lock_at_utc": (at + o).isoformat(), "first_match_utc": (at + o + H).isoformat()} for i, o in enumerate((-3 * M, 7 * D, 14 * D), start=1)]
    db.create_competition(slug, "Drill " + slug, status="active", max_weeks=3, week1_anchor_utc=(at - 60 * D).isoformat())
    with db._pool.connection() as con:
        con.execute("INSERT INTO players (competition_id,id,name,team,price,role) SELECT %s,'p'||g,'Player '||g,'AAA',5.0,"
                    "CASE WHEN g%%4=0 THEN 'WK' WHEN g%%4=1 THEN 'BAT' WHEN g%%4=2 THEN 'AR' ELSE 'BOWL' END FROM generate_series(1,30) g", (slug,))
    for who in ("Sai", "Moe"):
        db.save_next_week(who, TEAM, "p1", "p2", competition_id=slug)
    db.set_schedule(slug, rows)


def ledger(slug):
    with db._pool.connection() as con:
        return [{"to_week": r["to_week"], "triggered_by": r["triggered_by"]} for r in con.execute(
            "SELECT to_week, triggered_by FROM rollover_log WHERE competition_id=%s ORDER BY to_week", (slug,)).fetchall()]


def call(method, url, token=None):
    headers = {"Authorization": "Bearer " + token} if token is not None else {}
    r = app.test_client().open(url, method=method, headers=headers)
    return {"http": r.status_code, "body": r.get_json(silent=True)}


def own_clock_once():
    """The server's own clock, run for exactly one cycle with no delay (the real _ops_run)."""
    routes.ops_clock_loop(threading.Event(), first_delay_s=0, max_cycles=1)


db.upsert_member_auth("Sai", hash_passcode("1234", "Sai"), 0, 1)
db.upsert_member_auth("Moe", hash_passcode("1234", "Moe"), 0, 0)
db.create_session(ADMIN, "Sai", ttl_days=1)
base._write_limiter._max = 10 ** 6
base._auth_limiter._max = 10 ** 6
out = {}

# ── 1..4  each trigger alone ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
make_due("d1")
out["d1_http"] = call("POST", "/api/ops/tick?source=supabase", OPS_TOKEN)["http"]
out["d1_ledger"] = ledger("d1")
make_due("d2")
own_clock_once()
out["d2_ledger"] = ledger("d2")
out["d2_heartbeat"] = {h["source"]: h["detail"] for h in db.get_heartbeats()}.get("tick:app")
make_due("d3")
out["d3_http"] = call("POST", "/api/ops/tick", ADMIN)["http"]
out["d3_ledger"] = ledger("d3")
make_due("d4")
out["d4_http"] = call("POST", "/api/rollover?comp=d4", SCHED_TOKEN)["http"]
out["d4_ledger"] = ledger("d4")

# ── 5  every trigger at the same moment ──────────────────────────────────────────────────────────────────────────────────────────────────
make_due("d5")
gate = threading.Barrier(5)
codes = []


def racer(fn):
    gate.wait()
    codes.append(fn())


fns = [lambda: (own_clock_once(), 200)[1],
       lambda: call("POST", "/api/ops/tick?source=supabase", OPS_TOKEN)["http"],
       lambda: call("POST", "/api/ops/tick?source=pc", SCHED_TOKEN)["http"],
       lambda: call("POST", "/api/ops/tick", ADMIN)["http"],
       lambda: call("POST", "/api/rollover?comp=d5", SCHED_TOKEN)["http"]]
ts = [threading.Thread(target=racer, args=(f,)) for f in fns]
[t.start() for t in ts]
[t.join() for t in ts]
out["d5_codes"] = sorted(codes)
out["d5_ledger"] = ledger("d5")


# ── 6  the server's own clock loop ───────────────────────────────────────────────────────────────────────────────────────────────────────
class FakeStop:
    """A stop event that records how long the loop asked to sleep and says 'stop' after `limit` sleeps."""

    def __init__(self, limit):
        self.limit, self.waits = limit, []

    def wait(self, seconds):
        self.waits.append(seconds)
        return len(self.waits) > self.limit


def loop_with(answers, limit):
    """Run the loop with a stand-in for the tick that returns (or raises) each of `answers` in turn."""
    stop, calls = FakeStop(limit), []

    def run():
        a = answers[min(len(calls), len(answers) - 1)]
        calls.append(a)
        if isinstance(a, Exception):
            raise a
        return a

    routes.ops_clock_loop(stop, run=run, first_delay_s=3)
    return {"waits": stop.waits, "runs": len(calls)}


out["l_idle_is_capped"] = loop_with([{"next_tick_in_s": 1800}], 2)
out["l_busy_is_kept"] = loop_with([{"next_tick_in_s": 60}], 2)
out["l_never_busier_than_five_seconds"] = loop_with([{"next_tick_in_s": 1}], 2)
out["l_no_answer_means_a_minute"] = loop_with([{}, {"next_tick_in_s": 0}], 3)
out["l_a_failing_cycle_does_not_kill_it"] = loop_with([RuntimeError("the database blinked"), {"next_tick_in_s": 60}], 3)
stop3, calls3 = FakeStop(10 ** 6), []
routes.ops_clock_loop(stop3, run=lambda: calls3.append(1) or {"next_tick_in_s": 60}, first_delay_s=0, max_cycles=3)
out["l_max_cycles"] = {"runs": len(calls3), "waits": len(stop3.waits)}
ev = threading.Event()
ev.set()
calls4 = []
routes.ops_clock_loop(ev, run=lambda: calls4.append(1) or {"next_tick_in_s": 60}, first_delay_s=0)
out["l_a_stopped_loop_never_runs"] = len(calls4)

print("@@RESULT@@" + json.dumps(out, default=str) + "@@RESULT@@")
