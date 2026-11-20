"""Run in a SUBPROCESS by tests/test_server_clock.py (so the app is imported fresh against a scratch schema, whatever else the test process loaded).
Calls the REAL /api/poll and /api/state through Flask's test client and prints one JSON document: what came back, and the clock around the calls.
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

NOW = datetime.now(timezone.utc)
for slug, status, anchor in (("ipl_2026", "completed", NOW - timedelta(days=200)), ("sa_clock", "upcoming", NOW + timedelta(days=60))):
    if not db.get_competition(slug):
        db.create_competition(slug, slug.upper(), status=status, max_weeks=6, week1_anchor_utc=anchor.isoformat())
db.save_next_week("Sai", [f"p{i}" for i in range(1, 12)], "p1", "p2", competition_id="sa_clock")

client = app.test_client()
out = {"before": datetime.now(timezone.utc).isoformat()}
r = client.get("/api/poll?comp=sa_clock")
out["poll"] = {"http": r.status_code, "body": r.get_json()}
r = client.get("/api/state?comp=sa_clock")
out["state"] = {"http": r.status_code, "etag": r.headers.get("ETag"), "has_deadline": "deadline" in (r.get_json() or {}), "server_now_utc": (r.get_json() or {}).get("server_now_utc")}
r2 = client.get("/api/state?comp=sa_clock", headers={"If-None-Match": out["state"]["etag"]})
out["state_unchanged"] = {"http": r2.status_code, "body_length": len(r2.get_data())}
out["after"] = datetime.now(timezone.utc).isoformat()
sys.stdout.write("\n@@RESULT@@" + json.dumps(out, default=str) + "\n")
