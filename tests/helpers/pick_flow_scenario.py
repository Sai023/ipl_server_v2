"""Run in a SUBPROCESS by tests/test_pick_flow.py (so the app is imported fresh against a scratch schema, whatever else the test process loaded).
Drives the REAL /api/save-next-week, /api/keep-xi and /api/history routes through Flask's test client (Phase 1.6, 2026-11-20) and prints one JSON document.
Times are relative to the real clock, because the routes use it.   Environment: DATABASE_URL (a scratch schema)."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
os.chdir(REPO)
os.environ.pop("HOSTED", None)

import server  # noqa: E402,F401   (registers the blueprint on base.app; nothing is started: that is under __main__)
import base  # noqa: E402
from base import app, db, hash_passcode  # noqa: E402

base._write_limiter._max = 10 ** 6            # these scenarios make many writes from one "IP"
NOW = datetime.now(timezone.utc)
H, D = timedelta(hours=1), timedelta(days=1)
TEAM = [f"p{i}" for i in range(1, 12)]
OTHER = [f"p{i}" for i in range(12, 23)]


def rows(*offsets):
    return [{"week_no": i, "lock_at_utc": (NOW + o).isoformat(), "first_match_utc": (NOW + o + H).isoformat()} for i, o in enumerate(offsets, start=1)]


def make(slug, schedule):
    db.create_competition(slug, slug.upper(), status="active", max_weeks=6, week1_anchor_utc=(NOW - 60 * D).isoformat())
    with db._pool.connection() as con:
        con.execute("INSERT INTO players (competition_id,id,name,team,price,role) SELECT %s,'p'||g,'Player '||g,'AAA',5.0,'BAT' FROM generate_series(1,30) g", (slug,))
    db.set_schedule(slug, schedule)


for who in ("Sai", "Moe", "Buddy"):
    db.upsert_member_auth(who, hash_passcode("1234", who), 0, 1 if who == "Sai" else 0)
    db.create_session("tok-" + who.lower(), who, ttl_days=1)


def call(url, token, body=None, method="post"):
    c = app.test_client()
    kw = {"headers": {"Authorization": "Bearer " + token}}
    if body is not None:
        kw["json"] = body
    r = getattr(c, method)(url, **kw)
    return {"http": r.status_code, "body": r.get_json(silent=True)}


def current_row(who, slug):
    h = app.test_client().get(f"/api/history/{who}?comp={slug}").get_json()
    row = next(w for w in h["weeks"] if w["is_current"])
    return {"week": h["current_week"], "week_no": row["week_no"], "touched": row["next_week_touched_at"], "draft_size": len(row["next_week"]["team"]), "xi_size": len(row["this_week"]["team"])}


out = {}

# ── an active competition: week 1 has started, week 2 locks in 5 days ────────────────────────────────────────────────────────────────────
make("pf", rows(-10 * D, 5 * D, 12 * D))
db.save_next_week("Sai", TEAM, "p1", "p2", competition_id="pf")                     # Sai's pre-season pick (saved, so touched)
out["pre_season_touched"] = db.get_history("Sai", competition_id="pf")["weeks"][0]["next_week_touched_at"] is not None
rep = db.reconcile_competition("pf", NOW, triggered_by="scenario")
out["start_applied"] = [a["to_week"] for a in rep["applied"]]
out["after_start"] = {"Sai": current_row("Sai", "pf"), "Moe": current_row("Moe", "pf")}   # the start resets "touched": the draft is next week's now, and nobody has confirmed it

out["keep_nothing"] = call("/api/keep-xi/Moe?comp=pf", "tok-moe", {"week": 2})            # Moe never picked anything: nothing to keep (after the start a draft is for week 2)
out["keep_wrong_week"] = call("/api/keep-xi/Sai?comp=pf", "tok-sai", {"week": 1})        # a page that loaded BEFORE the start still thinks its draft is for week 1 (the row week did not change)
out["keep_far_week"] = call("/api/keep-xi/Sai?comp=pf", "tok-sai", {"week": 3})
out["keep_not_your_team"] = call("/api/keep-xi/Moe?comp=pf", "tok-sai", {"week": 2})
out["keep_no_token"] = call("/api/keep-xi/Sai?comp=pf", "nope", {"week": 2})
out["keep_bad_week"] = call("/api/keep-xi/Sai?comp=pf", "tok-sai", {"week": "next"})
out["before_keep"] = current_row("Sai", "pf")
out["keep_ok"] = call("/api/keep-xi/Sai?comp=pf", "tok-sai", {"week": 2})
out["after_keep"] = current_row("Sai", "pf")
out["team_after_keep"] = db.get_history("Sai", competition_id="pf")["weeks"][0]["next_week"]["team"]      # keeping changes nothing about the team

out["save_ok"] = call("/api/save-next-week/Moe?comp=pf", "tok-moe", {"team": OTHER, "cap": "p12", "vc": "p13", "week": 2})
out["after_save"] = current_row("Moe", "pf")
out["save_wrong_week"] = call("/api/save-next-week/Moe?comp=pf", "tok-moe", {"team": TEAM, "cap": "p1", "vc": "p2", "week": 1})
out["save_bad_week"] = call("/api/save-next-week/Moe?comp=pf", "tok-moe", {"team": TEAM, "cap": "p1", "vc": "p2", "week": 1.5})
out["moe_team_unchanged"] = db.get_history("Moe", competition_id="pf")["weeks"][0]["next_week"]["team"] == OTHER
out["save_without_week"] = call("/api/save-next-week/Moe?comp=pf", "tok-moe", {"team": TEAM, "cap": "p1", "vc": "p2"})   # an old page that does not state its week still works

# ── a handover runs while a page is open: its week is stale ────────────────────────────────────────────────────────────────────────────
rep = db.reconcile_competition("pf", NOW + 6 * D, triggered_by="scenario")               # week 2 locked 5 days from now: apply it as if that time had come
out["week2_applied"] = [a["to_week"] for a in rep["applied"]]
out["stale_save"] = call("/api/save-next-week/Sai?comp=pf", "tok-sai", {"team": OTHER, "cap": "p12", "vc": "p13", "week": 2})      # the page loaded when drafts were for week 2
out["stale_keep"] = call("/api/keep-xi/Sai?comp=pf", "tok-sai", {"week": 2})
out["sai_team_after_stale"] = db.get_history("Sai", competition_id="pf")["weeks"][1]["next_week"]["team"]
out["week2_rows"] = {"Sai": current_row("Sai", "pf"), "Moe": current_row("Moe", "pf")}      # new rows: nobody has confirmed anything yet
h = app.test_client().get("/api/history/Sai?comp=pf").get_json()
out["week1_row_still_touched"] = h["weeks"][0]["next_week_touched_at"] is not None
# (the scenario clock is 6 days ahead of the route's clock, so picking is open: the next lock is week 3, 12 days out; the route uses the REAL now, where week 2's lock is still ahead)

# ── picking locked: the lock has passed and the handover has not run ─────────────────────────────────────────────────────────────────────
make("pf_locked", rows(-1 * H, 7 * D))
db.save_next_week("Sai", TEAM, "p1", "p2", competition_id="pf_locked")
out["locked_save"] = call("/api/save-next-week/Sai?comp=pf_locked", "tok-sai", {"team": TEAM, "cap": "p1", "vc": "p2", "week": 1})
out["locked_keep"] = call("/api/keep-xi/Sai?comp=pf_locked", "tok-sai", {"week": 1})

sys.stdout.write("\n@@RESULT@@" + json.dumps(out, default=str) + "\n")
