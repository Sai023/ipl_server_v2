"""Run in a SUBPROCESS by tests/test_ops_core.py (so the app is imported fresh against a scratch schema). Drives the REAL routes and the REAL tick routine for Phase 2
(2026-11-22, release 2.28.0) and prints one JSON document after the marker @@RESULT@@:
  A  who may call the ops routes (no token, a wrong token, a member, the clock token, the scheduler token, an admin; the rate limit on guesses)
  B  the tick applies what is due, once (five competitions: due, in the future, upcoming that starts itself, upcoming that does not, no schedule), a repeat changes nothing
  C  four ticks at the same moment apply a handover exactly once
  D  the tick over HTTP with the real clock: the sources, the status card's data, a competition that fails does not stop the others
  E  the alert state is one atomic row: eight writers lose nothing
  F  /api/version lists the new modules
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

import server  # noqa: E402,F401   (registers the blueprint on base.app; nothing is started: that is under __main__)
import base  # noqa: E402
import routes  # noqa: E402
from base import app, db, hash_passcode  # noqa: E402

NOW = datetime.now(timezone.utc)
H, D, M = timedelta(hours=1), timedelta(days=1), timedelta(minutes=1)
TEAM = [f"p{i}" for i in range(1, 12)]
ADMIN, MOE = "tok-ops-admin", "tok-ops-moe"
OPS_TOKEN, SCHED_TOKEN = "ops-clock-token-0123456789abcdef", "scheduler-token-abcdef0123456789"


def rows(*offsets, at=NOW):
    return [{"week_no": i, "lock_at_utc": (at + o).isoformat(), "first_match_utc": (at + o + H).isoformat()} for i, o in enumerate(offsets, start=1)]


def make(slug, name, status, schedule, max_weeks=3, auto=None):
    db.create_competition(slug, name, status=status, max_weeks=max_weeks, week1_anchor_utc=(NOW - 60 * D).isoformat())
    with db._pool.connection() as con:
        con.execute("INSERT INTO players (competition_id,id,name,team,price,role) SELECT %s,'p'||g,'Player '||g,'AAA',5.0,"
                    "CASE WHEN g%%4=0 THEN 'WK' WHEN g%%4=1 THEN 'BAT' WHEN g%%4=2 THEN 'AR' ELSE 'BOWL' END FROM generate_series(1,30) g", (slug,))
    for who in ("Sai", "Moe"):
        db.save_next_week(who, TEAM, "p1", "p2", competition_id=slug)
    if schedule is not None:
        db.set_schedule(slug, schedule, auto_activate=auto)


def call(method, url, token=None, body=None):
    headers = {"Authorization": "Bearer " + token} if token is not None else {}          # token "" sends an EMPTY bearer
    r = app.test_client().open(url, method=method, json=body, headers=headers)
    return {"http": r.status_code, "body": r.get_json(silent=True)}


def ledger(slug):
    with db._pool.connection() as con:
        return [dict(r) for r in con.execute("SELECT to_week, kind, triggered_by FROM rollover_log WHERE competition_id=%s ORDER BY to_week", (slug,)).fetchall()]


def status_of(slug):
    with db._pool.connection() as con:
        return con.execute("SELECT status FROM competitions WHERE slug=%s", (slug,)).fetchone()["status"]


def heartbeats():
    return {h["source"]: h["detail"] for h in db.get_heartbeats()}


db.upsert_member_auth("Sai", hash_passcode("1234", "Sai"), 0, 1)
db.upsert_member_auth("Moe", hash_passcode("1234", "Moe"), 0, 0)
db.create_session(ADMIN, "Sai", ttl_days=1)
db.create_session(MOE, "Moe", ttl_days=1)
base._write_limiter._max = 10 ** 6
base._auth_limiter._max = 10 ** 6
out = {}

# ── A  who may call ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
os.environ["OPS_TOKEN"], os.environ["ROLLOVER_TOKEN"] = OPS_TOKEN, SCHED_TOKEN
out["a_no_credentials"] = call("POST", "/api/ops/tick")
out["a_wrong_token"] = call("POST", "/api/ops/tick", "not-the-token")
out["a_member"] = call("POST", "/api/ops/tick", MOE)
out["a_status_no_credentials"] = call("GET", "/api/ops/status")
out["a_status_member"] = call("GET", "/api/ops/status", MOE)
out["a_test_alert_clock_token"] = call("POST", "/api/ops/test-alert", OPS_TOKEN)
out["a_test_alert_member"] = call("POST", "/api/ops/test-alert", MOE)
out["a_ops_token"] = call("POST", "/api/ops/tick?source=supabase", OPS_TOKEN)
out["a_scheduler_token"] = call("POST", "/api/ops/tick?source=github", SCHED_TOKEN)
out["a_admin"] = call("POST", "/api/ops/tick", ADMIN)
out["a_status_ops_token"] = call("GET", "/api/ops/status", OPS_TOKEN)
out["a_status_admin"] = call("GET", "/api/ops/status", ADMIN)
out["a_tick_is_post_only"] = call("GET", "/api/ops/tick", OPS_TOKEN)
os.environ.pop("OPS_TOKEN")                                                       # with the clock token unset the scheduler token still works ...
out["a_scheduler_still_works"] = call("POST", "/api/ops/tick", SCHED_TOKEN)
os.environ.pop("ROLLOVER_TOKEN")                                                  # ... and with BOTH unset, an empty bearer must not match an empty setting
out["a_empty_vs_unset"] = call("POST", "/api/ops/tick", "")
out["a_anything_vs_unset"] = call("POST", "/api/ops/tick", "anything")
os.environ["OPS_TOKEN"], os.environ["ROLLOVER_TOKEN"] = OPS_TOKEN, SCHED_TOKEN
base._auth_limiter._max = 4                                                       # guesses are counted: the fifth wrong token in a minute is refused outright
base._auth_limiter._calls.clear()
out["a_guesses"] = [call("POST", "/api/ops/tick", "guess-%d" % i)["http"] for i in range(7)]
out["a_a_good_token_is_not_counted"] = call("POST", "/api/ops/tick", OPS_TOKEN)["http"]
base._auth_limiter._max = 10 ** 6
base._auth_limiter._calls.clear()

# ── B  the tick applies what is due, once ─────────────────────────────────────────────────────────────────────────────────────────────────
make("c_due", "Due League", "active", rows(-5 * M, 7 * D, 14 * D))
make("c_future", "Future League", "active", rows(3 * D, 10 * D, 17 * D))
make("c_auto", "Starts Itself", "upcoming", rows(-5 * M, 7 * D, 14 * D), auto=True)
make("c_manual", "Needs An Admin", "upcoming", rows(-10 * M, 7 * D, 14 * D))
make("c_none", "No Schedule", "active", None)
out["b_tick1"] = routes._ops_run("supabase", now=NOW)
out["b_ledger_after_1"] = {s: ledger(s) for s in ("c_due", "c_future", "c_auto", "c_manual", "c_none")}
out["b_status_after_1"] = {s: status_of(s) for s in ("c_due", "c_future", "c_auto", "c_manual", "c_none")}
out["b_heartbeats_after_1"] = heartbeats()
out["b_tick2"] = routes._ops_run("supabase", now=NOW + M)
out["b_ledger_after_2"] = {s: ledger(s) for s in ("c_due", "c_future", "c_auto", "c_manual", "c_none")}
out["b_tick3_pc"] = routes._ops_run("pc", now=NOW + 2 * M)
out["b_heartbeat_sources"] = sorted(heartbeats())
out["b_comps"] = [{k: (sorted(v) if isinstance(v, set) else v) for k, v in c.items() if k != "rows"} | {"n_rows": len(c["rows"])} for c in db.ops_competitions()]
out["b_recent"] = [{k: r[k] for k in ("slug", "name", "kind", "to_week", "triggered_by", "members", "summary", "activated")} for r in db.recent_applied(10)]
out["b_no_beat"] = {"before": heartbeats().get("tick:quiet"), "rep": routes._ops_run("quiet", now=NOW + 3 * M, beat=False)["ok"], "after": heartbeats().get("tick:quiet")}

# an admin undoes week 1 of c_due: the ledger row stays with undone_at set, so the week is HELD: no trigger applies it again, it leaves the "in force" list, the card says why
with db._pool.connection() as con:
    con.execute("UPDATE rollover_log SET undone_at=%s WHERE competition_id=%s AND to_week=1", ((NOW + 5 * M).isoformat(), "c_due"))
out["b_undo_tick"] = routes._ops_run("supabase", now=NOW + 6 * M)
out["b_undo_ledger"] = ledger("c_due")
out["b_undo_recent"] = sorted(r["slug"] for r in db.recent_applied(10))
_cd = {c["slug"]: c for c in db.ops_competitions()}["c_due"]
out["b_undo_comp"] = {"handled": sorted(_cd["handled"]), "held": sorted(_cd["held"])}
_st = {s["slug"]: s for s in routes._ops.build_snapshot(db.ops_competitions(), [], NOW + 6 * M)["states"]}["c_due"]
out["b_undo_state"] = {"due": _st["due"], "blocked": _st["blocked"]}

# ── C  four ticks at the same moment ──────────────────────────────────────────────────────────────────────────────────────────────────────
make("c_race", "Race League", "active", rows(-5 * M, 7 * D, 14 * D))
reps = []
gate = threading.Barrier(4)


def racer(i):
    gate.wait()
    reps.append(routes._ops_run("race%d" % i, now=NOW + 4 * M))


ts = [threading.Thread(target=racer, args=(i,)) for i in range(4)]
[t.start() for t in ts]
[t.join() for t in ts]
out["c_applied_total"] = sum(1 for r in reps for a in r["applied"] if a["competition"] == "c_race")
out["c_all_ok"] = all(r["ok"] for r in reps)
out["c_ledger"] = ledger("c_race")

# ── D  over HTTP with the real clock ─────────────────────────────────────────────────────────────────────────────────────────────────────
make("h_late", "Late League", "active", rows(-10 * M, 7 * D, 14 * D, at=datetime.now(timezone.utc)))
out["d_pc"] = call("POST", "/api/ops/tick?source=pc", OPS_TOKEN)
out["d_app_is_not_claimable"] = call("POST", "/api/ops/tick?source=app", OPS_TOKEN)
out["d_odd_source"] = call("POST", "/api/ops/tick?source=Bad%20One!", OPS_TOKEN)
out["d_admin_source"] = call("POST", "/api/ops/tick", ADMIN)
out["d_ledger_h_late"] = ledger("h_late")
out["d_heartbeats"] = heartbeats()
out["d_status"] = call("GET", "/api/ops/status", ADMIN)
out["d_status_token"] = call("GET", "/api/ops/status", OPS_TOKEN)
make("h_1_bad", "Broken League", "active", rows(-15 * M, 7 * D, 14 * D, at=datetime.now(timezone.utc)))             # created FIRST, so the tick meets it first
make("h_2_ok", "Second Late League", "active", rows(-12 * M, 7 * D, 14 * D, at=datetime.now(timezone.utc)))
with db._pool.connection() as con:                                                # a start for a competition that already has week-2 rows cannot run
    con.execute("INSERT INTO user_selections (competition_id, display_name, week_no) VALUES (%s,%s,%s)", ("h_1_bad", "Sai", 2))
out["d_failing_tick"] = call("POST", "/api/ops/tick?source=supabase", OPS_TOKEN)
out["d_ledger_h_ok"] = ledger("h_2_ok")
out["d_ledger_h_bad"] = ledger("h_1_bad")
out["d_heartbeat_after_failure"] = heartbeats().get("tick:supabase")
out["d_status_after_failure"] = call("GET", "/api/ops/status", ADMIN)

# ── E  the alert state is one atomic row ──────────────────────────────────────────────────────────────────────────────────────────────────
gate2 = threading.Barrier(8)


def writer(i):
    gate2.wait()
    db.ops_alert_step(lambda s: ([], {"incidents": s.get("incidents", {}), "announced": (s.get("announced") or []) + [i]}))


ws = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
[t.start() for t in ws]
[t.join() for t in ws]
out["e_announced"] = sorted(db.ops_alert_state().get("announced") or [])
out["e_returns_the_messages"] = db.ops_alert_step(lambda s: (["x", "y"], s))

# ── F  the version ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
v = call("GET", "/api/version")["body"]
out["f_modules"] = {k: v["modules"].get(k) for k in ("ops_plan", "ops_notify", "origin")}

print("@@RESULT@@" + json.dumps(out, default=str) + "@@RESULT@@")
