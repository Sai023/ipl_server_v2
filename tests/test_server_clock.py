"""The server sends its clock (2026-11-20, Phase 1.4 of the SA20 readiness plan): /api/poll and /api/state carry `server_now_utc`, so the page counts
down to a lock on the SERVER's time and not on a phone clock that may be wrong. The page side is tests/js/server_clock.test.js.

1. file guards: the two routes add the field
2. the real routes on scratch data (needs Flask and TEST_DATABASE_URL; skipped otherwise), run in a subprocess"""
import json
import os
import re
import subprocess
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


class TheRoutesAddTheServerClock(unittest.TestCase):
    def route(self, path):
        src = read("routes.py")
        i = src.index('@bp.route("' + path + '"')
        return src[i:src.index("\n@bp.route(", i + 10)]

    def test_poll_carries_both_server_stamps(self):
        body = self.route("/api/poll")
        self.assertIsNotNone(re.search(r'"server_now_utc"\s*:\s*datetime\.now\(timezone\.utc\)\.isoformat\(\)', body))
        self.assertIsNotNone(re.search(r'"server_recv_utc"\s*:\s*recv\.isoformat\(\)', body))
        self.assertLess(body.index("recv = datetime.now(timezone.utc)"), body.index("db.get_etags"), "the arrival stamp is taken before any work is done")

    def test_state_carries_both_server_stamps_after_the_304_check(self):
        body = self.route("/api/state")
        self.assertIn('state["server_now_utc"]', body)
        self.assertIn('state["server_recv_utc"]', body)
        self.assertLess(body.index("recv = datetime.now(timezone.utc)"), body.index("db.get_state"), "the arrival stamp is taken before any work is done")
        self.assertLess(body.index('return "", 304'), body.index('state["server_now_utc"]'), "a 304 has no body, so the time is added on the 200 path only")


def _flask_available():
    try:
        import click  # noqa: F401
        import flask  # noqa: F401
        return True
    except Exception:
        return False


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL") and _flask_available(), "needs Flask and TEST_DATABASE_URL (run with `py -3.14`)")
class TheRealRoutesOnScratchData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        raw = os.environ["TEST_DATABASE_URL"]
        cls.schema = "t_clock_" + uuid.uuid4().hex[:8]
        cls.admin = psycopg.connect(raw, autocommit=True)
        cls.admin.execute(f'CREATE SCHEMA "{cls.schema}"')
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        env["DATABASE_URL"] = raw + ("&" if "?" in raw else "?") + "options=" + quote(f"-c search_path={cls.schema}")
        env.pop("HOSTED", None)
        p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tests", "helpers", "clock_scenario.py")], env=env, capture_output=True, text=True, timeout=300)
        marker = "@@RESULT@@"
        cls.err = (p.stderr or "")[-1500:]
        cls.out = json.loads(p.stdout.split(marker)[1]) if marker in p.stdout else None

    @classmethod
    def tearDownClass(cls):
        try:
            cls.admin.execute(f'DROP SCHEMA "{cls.schema}" CASCADE')
        finally:
            cls.admin.close()

    def setUp(self):
        self.assertIsNotNone(self.out, "the scenario script did not finish:\n" + self.err)

    def assertIsServerNow(self, iso, label):
        t = datetime.fromisoformat(iso)
        self.assertIsNotNone(t.tzinfo, label + ": the time must carry its zone")
        lo = datetime.fromisoformat(self.out["before"]) - timedelta(seconds=1)
        hi = datetime.fromisoformat(self.out["after"]) + timedelta(seconds=1)
        self.assertTrue(lo <= t <= hi, f"{label}: {iso} is not the time of the call ({self.out['before']} .. {self.out['after']})")
        self.assertEqual(t.utcoffset(), timedelta(0), label + ": the time is UTC")

    def assertStampsAreInOrder(self, recv, now, label):
        self.assertLessEqual(datetime.fromisoformat(recv), datetime.fromisoformat(now), label + ": the request cannot arrive after the reply leaves")

    def test_poll_says_what_time_it_is(self):
        r = self.out["poll"]
        self.assertEqual(r["http"], 200); self.assertTrue(r["body"]["ok"]); self.assertTrue(r["body"]["state_etag"])
        self.assertIsServerNow(r["body"]["server_now_utc"], "/api/poll")
        self.assertIsServerNow(r["body"]["server_recv_utc"], "/api/poll arrival")
        self.assertStampsAreInOrder(r["body"]["server_recv_utc"], r["body"]["server_now_utc"], "/api/poll")

    def test_state_says_what_time_it_is_and_still_has_its_deadline(self):
        r = self.out["state"]
        self.assertEqual(r["http"], 200); self.assertTrue(r["etag"]); self.assertTrue(r["has_deadline"])
        self.assertIsServerNow(r["server_now_utc"], "/api/state")
        self.assertIsServerNow(r["server_recv_utc"], "/api/state arrival")
        self.assertStampsAreInOrder(r["server_recv_utc"], r["server_now_utc"], "/api/state")

    def test_an_unchanged_state_is_still_a_304_with_no_body(self):
        r = self.out["state_unchanged"]
        self.assertEqual(r["http"], 304); self.assertEqual(r["body_length"], 0)


if __name__ == "__main__":
    unittest.main()
