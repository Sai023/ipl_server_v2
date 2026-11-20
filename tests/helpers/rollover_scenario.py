"""Run in a SUBPROCESS by tests/test_rollover_guards.py (so the app is imported fresh against a scratch schema, whatever else the test process loaded).
Drives the REAL /api/rollover, /api/save-next-week and /api/state routes through Flask's test client and prints one JSON document with what happened.
Rewritten 2026-11-20 for the lock-schedule rollover (Phase 1.5). Times are relative to the real clock, because the routes use it.
Environment: DATABASE_URL (a scratch schema), ROLLOVER_TOKEN."""
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

TOKEN = os.environ["ROLLOVER_TOKEN"]
TEAM = [f"p{i}" for i in range(1, 12)]
NOW = datetime.now(timezone.utc)
H, D = timedelta(hours=1), timedelta(days=1)


def rows(*offsets):
    """A schedule whose week k locks at NOW + offsets[k-1] (the first match an hour later)."""
    return [{"week_no": i, "lock_at_utc": (NOW + off).isoformat(), "first_match_utc": (NOW + off + H).isoformat()} for i, off in enumerate(offsets, start=1)]


def make(slug, status, schedule=None, auto=None, anchor=None, completed=False):
    db.create_competition(slug, slug.upper(), status="active" if completed else status, max_weeks=6,
                          week1_anchor_utc=(anchor or NOW - 60 * D).isoformat())
    with db._pool.connection() as con:
        con.execute("INSERT INTO players (competition_id,id,name,team,price,role) SELECT %s,'p'||g,'Player '||g,'AAA',5.0,'BAT' FROM generate_series(1,30) g", (slug,))
    for who in ("Sai", "Moe", "Buddy"):
        db.save_next_week(who, TEAM, "p1", "p2", competition_id=slug)
    if schedule:
        db.set_schedule(slug, schedule, auto_activate=auto)
    if completed:
        db.set_competition_status(slug, "completed")


def weeks(slug):
    return {r["display_name"]: r["cur_wk"] for r in db.get_users_and_max_weeks(competition_id=slug)}


def ledger(slug):
    return [e["to_week"] for e in db.get_rollover_log(slug)]


def call(url, bearer=TOKEN, method="post", body=None):
    c = app.test_client()
    kw = {"headers": {"Authorization": "Bearer " + bearer}}
    if body is not None:
        kw["json"] = body
    r = getattr(c, method)(url, **kw)
    return {"http": r.status_code, "body": r.get_json(silent=True)}


def state_deadline(slug):
    r = app.test_client().get(f"/api/state?comp={slug}")
    return r.get_json()["deadline"]


make("sa_unsched", "active", anchor=NOW + 60 * D)                                     # no schedule at all (anchor in the future: the old rules leave drafting open)
make("sa_upcoming", "upcoming", rows(-10 * D, 10 * D, 17 * D))                        # week 1 is overdue but the competition does not start itself
make("sa_auto", "upcoming", rows(-1 * H, 7 * D, 14 * D), auto=True)                   # starts and activates itself at the week-1 lock
make("sa_future", "active", rows(30 * D, 37 * D, 44 * D))                             # nothing due for a month
make("ipl_done", "active", rows(-30 * D, -20 * D, -10 * D), completed=True)           # a finished competition
make("sa_catchup", "active", rows(-30 * D, -20 * D, 10 * D, 17 * D))                  # weeks 1 and 2 overdue, 3 and 4 not yet
make("sa_dry", "active", rows(-5 * D, -3 * D, 20 * D))
make("sa_lock", "active", rows(-1 * H, 7 * D, 14 * D))                                # week 1 locked an hour ago and its handover has not run
db.upsert_member_auth("Sai", hash_passcode("1234", "Sai"), 0, 1)
db.create_session("tok-admin", "Sai", ttl_days=1)

out = {}
snap = lambda slug: {"weeks": weeks(slug), "ledger": ledger(slug), "status": db.get_competition(slug)["status"]}   # noqa: E731

out["unscheduled"] = {"resp": call("/api/rollover?comp=sa_unsched"), **snap("sa_unsched")}
out["upcoming"] = {"resp": call("/api/rollover?comp=sa_upcoming"), **snap("sa_upcoming")}
out["upcoming_forced"] = {"resp": call("/api/rollover?comp=sa_upcoming&force=1&confirm=sa_upcoming:1", bearer="tok-admin"), **snap("sa_upcoming")}
out["future"] = {"resp": call("/api/rollover?comp=sa_future"), **snap("sa_future")}
out["completed"] = {"resp": call("/api/rollover?comp=ipl_done"), **snap("ipl_done")}

out["auto_first"] = {"resp": call("/api/rollover?comp=sa_auto"), **snap("sa_auto")}
out["auto_again"] = {"resp": call("/api/rollover?comp=sa_auto"), **snap("sa_auto")}

out["catchup_first"] = {"resp": call("/api/rollover?comp=sa_catchup"), **snap("sa_catchup")}
out["catchup_again"] = {"resp": call("/api/rollover?comp=sa_catchup"), **snap("sa_catchup")}
out["forced_by_token"] = {"resp": call("/api/rollover?comp=sa_catchup&force=1"), **snap("sa_catchup")}
out["forced_no_confirm"] = {"resp": call("/api/rollover?comp=sa_catchup&force=1", bearer="tok-admin"), **snap("sa_catchup")}
out["forced_wrong_confirm"] = {"resp": call("/api/rollover?comp=sa_catchup&force=1&confirm=sa_catchup:5", bearer="tok-admin"), **snap("sa_catchup")}
out["forced_ok"] = {"resp": call("/api/rollover?comp=sa_catchup&force=1&confirm=sa_catchup:3", bearer="tok-admin"), **snap("sa_catchup")}
out["anonymous"] = {"resp": call("/api/rollover?comp=sa_catchup", bearer="nope"), **snap("sa_catchup")}

out["dry_preview"] = {"resp": call("/api/rollover?comp=sa_dry&dry_run=1"), **snap("sa_dry")}
out["dry_real"] = {"resp": call("/api/rollover?comp=sa_dry"), **snap("sa_dry")}

out["lock_state_before"] = state_deadline("sa_lock")
out["lock_save_awaiting"] = call("/api/save-next-week/Sai?comp=sa_lock", bearer="tok-admin", body={"team": TEAM, "cap": "p1", "vc": "p2"})
out["lock_roll"] = {"resp": call("/api/rollover?comp=sa_lock"), **snap("sa_lock")}
out["lock_state_after"] = state_deadline("sa_lock")
out["lock_save_open"] = call("/api/save-next-week/Sai?comp=sa_lock", bearer="tok-admin", body={"team": TEAM[::-1], "cap": "p3", "vc": "p4"})
out["unsched_save_open"] = call("/api/save-next-week/Sai?comp=sa_unsched", bearer="tok-admin", body={"team": TEAM, "cap": "p1", "vc": "p2"})
out["unsched_state"] = state_deadline("sa_unsched")
out["saved_team"] = json.loads(db.get_selection_row("Sai", 1, competition_id="sa_lock")["nw_team_json"])

out["heartbeats"] = {h["source"]: h["detail"] for h in db.get_heartbeats()}

# ── the admin "schedule" action: the real SA20 fixtures (week 1 = Sun 17 Jan, anchor Mon 18 Jan 2027 14:00 UTC) ──────────────────────────────────────
with open(os.path.join(REPO, "tests", "fixtures", "sa20_2027_schedule.json"), encoding="utf-8") as f:
    SCHED = json.load(f)
db.upsert_member_auth("Moe", hash_passcode("1234", "Moe"), 0, 0)
db.create_session("tok-moe", "Moe", ttl_days=1)
db.create_competition("sa20_sched", "SA20 schedule test", status="upcoming", max_weeks=10, week1_anchor_utc="2027-01-18T14:00:00+00:00")
admin = lambda body: call("/api/admin/competition", bearer="tok-admin", body=dict(body, slug="sa20_sched"))   # noqa: E731
stored = lambda: {"weeks": [(w["week_no"], w["lock_at_utc"]) for w in db.get_schedule("sa20_sched")], "auto": db.get_auto_activate("sa20_sched")}   # noqa: E731
out["sched_before_import"] = admin({"action": "schedule", "schedule": SCHED})                  # no fixtures stored yet: allowed, with a warning
out["sched_import"] = admin({"action": "import", "schedule": SCHED, "dry_run": False})
out["sched_not_admin"] = call("/api/admin/competition", bearer="tok-moe", body={"action": "schedule", "slug": "sa20_sched", "schedule": SCHED})
out["sched_dry"] = admin({"action": "schedule", "schedule": SCHED, "auto_activate": True})     # dry_run is true unless it says false
out["sched_after_dry"] = stored()
out["sched_missing_file"] = admin({"action": "schedule"})
out["sched_bad_flag"] = admin({"action": "schedule", "schedule": SCHED, "auto_activate": "yes"})
out["sched_real"] = admin({"action": "schedule", "schedule": SCHED, "auto_activate": True, "dry_run": False})
out["sched_after_real"] = stored()
out["sched_again"] = admin({"action": "schedule", "schedule": SCHED, "dry_run": False})        # no auto_activate key: the setting is kept
out["sched_after_again"] = stored()
with db._pool.connection() as con:
    con.execute("UPDATE matches SET week_no=3 WHERE id='sa20_sched_m05'")                      # the stored fixtures now disagree with the file
out["sched_mismatch"] = admin({"action": "schedule", "schedule": SCHED, "dry_run": False})
out["sched_after_mismatch"] = stored()

db.set_meta("_last_rollover", "2026-05-18T16:49:51+00:00")   # the pre-multi-competition key
out["meta"] = {"default_comp": db.get_meta_comp("_last_rollover", "ipl_2026", ""),
               "other_comp": db.get_meta_comp("_last_rollover", "sa_upcoming", ""),
               "other_comp_default": db.get_meta_comp("_last_rollover", "sa_upcoming", "never")}
sys.stdout.write("\n@@RESULT@@" + json.dumps(out, default=str) + "\n")
