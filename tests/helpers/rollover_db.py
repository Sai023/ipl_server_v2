"""Shared fixture for the rollover database tests (tests/test_rollover_core.py, tests/test_rollover_safety.py), 2026-11-20.

One scratch schema per test MODULE (created in setUpModule, dropped in tearDownModule); every test gets its own competition, cloned from a template in ONE
statement, so a test costs a handful of round trips instead of dozens (the spare database is about 0.2 s away).

The season is the real SA20 2027 calendar (tests/fixtures/sa20_2027_calendar.json) turned into locks by logic.rollover_plan.build_schedule, so the dates
in these tests are the dates the league will actually live by. Needs TEST_DATABASE_URL (a DIRECT Postgres URL: the fixture uses and drops its own schema).
The imports of psycopg and db_manager are lazy, so this module can be imported (for its pure helpers) where they are not installed."""
import json
import os
import sys
import uuid
from datetime import timedelta
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from logic import rollover_plan as rp  # noqa: E402

MEMBERS = ("Sai", "Moe", "Buddy")
TEAM_A = [f"p{i}" for i in range(1, 12)]       # p1..p11
TEAM_B = [f"p{i}" for i in range(5, 16)]       # p5..p15
TEAM_C = [f"p{i}" for i in range(12, 23)]      # p12..p22


def season_rows():
    with open(os.path.join(ROOT, "tests", "fixtures", "sa20_2027_calendar.json"), encoding="utf-8") as f:
        fx = json.load(f)
    firsts = {}
    for m in fx["matches"]:
        t = rp.parse_utc(m["start_utc"])
        firsts[m["week_no"]] = min(t, firsts.get(m["week_no"], t))
    built = rp.build_schedule(firsts)
    assert not built["errors"], built["errors"]
    return built["rows"]


SEASON = season_rows()
LOCKS = {r["week_no"]: rp.parse_utc(r["lock_at_utc"]) for r in SEASON}


def at(week, **delta):
    """The lock instant of `week`, moved by a timedelta (at(2, hours=1) = an hour after week 2 locks)."""
    return LOCKS[week] + timedelta(**delta)


_SNAP_SQL = """
SELECT jsonb_build_object(
  'status', (SELECT status FROM competitions WHERE slug = %(slug)s),
  'rows',   (SELECT COALESCE(jsonb_agg(to_jsonb(u) ORDER BY display_name, week_no), '[]'::jsonb) FROM user_selections u WHERE competition_id = %(slug)s),
  'meta',   (SELECT COALESCE(jsonb_object_agg(key, value), '{}'::jsonb) FROM meta WHERE key LIKE %(pat)s),
  'log',    (SELECT COALESCE(jsonb_agg(to_jsonb(l) - 'id' ORDER BY to_week), '[]'::jsonb) FROM rollover_log l WHERE competition_id = %(slug)s),
  'weeks',  (SELECT COALESCE(jsonb_agg(to_jsonb(w) ORDER BY week_no), '[]'::jsonb) FROM competition_weeks w WHERE competition_id = %(slug)s)
) AS snap
"""


def row(snap, name, week):
    """One member's user_selections row from a snapshot (None if there is none)."""
    return next((r for r in snap["rows"] if r["display_name"] == name and r["week_no"] == week), None)


def team(r, which="tw"):
    return json.loads(r[f"{which}_team_json"])


def weeks_of(snap, name):
    return [r["week_no"] for r in snap["rows"] if r["display_name"] == name]


class ScratchDb:
    def __init__(self):
        import psycopg
        from db_manager import DatabaseManager
        raw = os.environ["TEST_DATABASE_URL"]
        self.schema = "t_roll_" + uuid.uuid4().hex[:8]
        self.admin = psycopg.connect(raw, autocommit=True)
        self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        self.db = DatabaseManager(raw + ("&" if "?" in raw else "?") + "options=" + quote(f"-c search_path={self.schema}"))
        self._n = 0
        for name in MEMBERS:
            self.db.upsert_member_auth(name, "hash", 0, 1 if name == "Sai" else 0)
        self._make_template()

    def _make_template(self):
        db = self.db
        db.create_competition("tpl", "Template", status="active", max_weeks=6, week1_anchor_utc=rp.iso(LOCKS[1] + timedelta(minutes=60)))
        self.sql("INSERT INTO players (competition_id,id,name,team,price,role) "
                 "SELECT 'tpl','p'||g,'Player '||g,'AAA',5.0,'BAT' FROM generate_series(1,30) g")
        db.set_schedule("tpl", SEASON)
        db.save_next_week("Sai", TEAM_A, "p1", "p2", competition_id="tpl")          # Sai and Moe have drafted; Buddy has not
        db.save_next_week("Moe", TEAM_B, "p5", "p6", competition_id="tpl")

    def close(self):
        try:
            self.db._pool.close()
        finally:
            try:
                self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
            finally:
                self.admin.close()

    # ── helpers for the tests ──────────────────────────────────────────────────────────────────────────────────────
    def sql(self, text):
        """Run one statement (or several, separated by ';', when it has no parameters) in its own transaction."""
        with self.db._pool.connection() as con:
            con.execute(text)

    def clone(self, *, status="active", schedule=True, drafts=True, auto=None):
        """A fresh competition cloned from the template in ONE round trip: the 30-player roster, the SA20 lock schedule, and (drafts=True) the two drafts
        Sai and Moe made before the season (Buddy never drafted). Returns its slug."""
        self._n += 1
        s = f"c{self._n}"
        parts = [
            "INSERT INTO competitions (slug,name,format,status,budget_total,xi_size,max_weeks,week1_anchor_utc,deadline_hour,deadline_min,valid_teams_json,created_at) "
            f"SELECT '{s}','{s}',format,'{status}',budget_total,xi_size,max_weeks,week1_anchor_utc,deadline_hour,deadline_min,valid_teams_json,created_at "
            "FROM competitions WHERE slug='tpl'",
            f"INSERT INTO players (competition_id,id,name,team,price,role) SELECT '{s}',id,name,team,price,role FROM players WHERE competition_id='tpl'",
        ]
        if schedule:
            parts.append("INSERT INTO competition_weeks (competition_id,week_no,lock_at_utc,first_match_utc) "
                         f"SELECT '{s}',week_no,lock_at_utc,first_match_utc FROM competition_weeks WHERE competition_id='tpl'")
        if drafts:
            parts.append("INSERT INTO user_selections (competition_id,display_name,week_no,tw_team_json,tw_cap_id,tw_vc_id,nw_team_json,nw_cap_id,nw_vc_id) "
                         f"SELECT '{s}',display_name,week_no,tw_team_json,tw_cap_id,tw_vc_id,nw_team_json,nw_cap_id,nw_vc_id "
                         "FROM user_selections WHERE competition_id='tpl'")
        if auto is not None:
            parts.append(f"INSERT INTO meta (key,value) VALUES ('auto_activate:{s}','{1 if auto else 0}')")
        self.sql(";\n".join(parts))
        return s

    def snap(self, slug):
        """Everything a transition can touch, as one JSON-shaped dict, fetched in ONE statement: competition status, every member row, the competition's
        meta keys, the ledger (without its serial id) and the schedule. Two snapshots being equal means nothing changed."""
        with self.db._read() as con:
            return con.execute(_SNAP_SQL, {"slug": slug, "pat": f"%:{slug}"}).fetchone()["snap"]
