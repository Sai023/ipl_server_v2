"""Run in a SUBPROCESS by tests/test_rollover_guards.py (so the app is imported fresh against a scratch schema, whatever else the test process loaded).
Drives the REAL /api/rollover route through Flask's test client and prints one JSON document with what happened.
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
PAST = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
FUTURE = (datetime.now(timezone.utc) + timedelta(days=60)).isoformat()


def make(slug, status, anchor, max_weeks=6):
    if not db.get_competition(slug):
        db.create_competition(slug, slug.upper(), status=status, max_weeks=max_weeks, week1_anchor_utc=anchor)
    for who in ("Sai", "Moe", "Buddy"):
        db.save_next_week(who, TEAM, "p1", "p2", competition_id=slug)


def weeks(slug):
    return {r["display_name"]: r["cur_wk"] for r in db.get_users_and_max_weeks(competition_id=slug)}


def call(url, bearer=TOKEN):
    r = app.test_client().post(url, headers={"Authorization": "Bearer " + bearer})
    return {"http": r.status_code, "body": r.get_json()}


make("ipl_2026", "completed", PAST, max_weeks=10)          # the default competition (legacy meta keys belong to it)
make("sa_upcoming", "upcoming", FUTURE)
make("sa_active_future", "active", FUTURE)
make("sa_active", "active", PAST)
db.upsert_member_auth("Sai", hash_passcode("1234", "Sai"), 0, 1)
db.create_session("tok-admin", "Sai", ttl_days=1)

out = {}
out["upcoming"] = {"resp": call("/api/rollover?comp=sa_upcoming"), "weeks": weeks("sa_upcoming")}
out["upcoming_forced_by_token"] = {"resp": call("/api/rollover?comp=sa_upcoming&force=1"), "weeks": weeks("sa_upcoming")}
out["upcoming_forced_by_admin"] = {"resp": call("/api/rollover?comp=sa_upcoming&force=1&confirm=sa_upcoming:2", bearer="tok-admin"), "weeks": weeks("sa_upcoming")}
out["active_future"] = {"resp": call("/api/rollover?comp=sa_active_future"), "weeks": weeks("sa_active_future")}
out["completed"] = {"resp": call("/api/rollover?comp=ipl_2026"), "weeks": weeks("ipl_2026")}

out["active_first"] = {"resp": call("/api/rollover?comp=sa_active"), "weeks": weeks("sa_active")}
out["active_again"] = {"resp": call("/api/rollover?comp=sa_active"), "weeks": weeks("sa_active")}
out["forced_by_token"] = {"resp": call("/api/rollover?comp=sa_active&force=1"), "weeks": weeks("sa_active")}
out["forced_no_confirm"] = {"resp": call("/api/rollover?comp=sa_active&force=1", bearer="tok-admin"), "weeks": weeks("sa_active")}
out["forced_wrong_confirm"] = {"resp": call("/api/rollover?comp=sa_active&force=1&confirm=sa_active:5", bearer="tok-admin"), "weeks": weeks("sa_active")}
out["forced_ok"] = {"resp": call("/api/rollover?comp=sa_active&force=1&confirm=sa_active:3", bearer="tok-admin"), "weeks": weeks("sa_active")}
out["anonymous"] = {"resp": call("/api/rollover?comp=sa_active", bearer="nope"), "weeks": weeks("sa_active")}

db.set_meta("_last_rollover", "2026-05-18T16:49:51+00:00")   # the pre-multi-competition key
out["meta"] = {"default_comp": db.get_meta_comp("_last_rollover", "ipl_2026", ""),
               "other_comp": db.get_meta_comp("_last_rollover", "sa_upcoming", ""),
               "other_comp_default": db.get_meta_comp("_last_rollover", "sa_upcoming", "never")}
sys.stdout.write("\n@@RESULT@@" + json.dumps(out, default=str) + "\n")
