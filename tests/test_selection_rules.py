"""A saved XI must name a captain AND a vice-captain (asked for 2026-11-19: the captain scores double, so a team without one is never saved).

The rule is decided before the database is touched, so most cases here need no database. The last case (skipped unless TEST_DATABASE_URL is set, like
the other database tests) runs the whole validation against a scratch schema, to show a valid XI with both roles still passes."""
import os
import sys
import unittest
import uuid
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db_manager import DatabaseManager  # noqa: E402

TEAM = [f"p{i}" for i in range(1, 12)]


class _NoDatabase(DatabaseManager):
    """A DAO that fails loudly if a rule that must be decided up front reaches for the database."""

    def __init__(self):  # no pool, no connection
        pass

    def _cid(self, competition_id=None):
        return competition_id or "c"

    def _read(self):
        raise AssertionError("the database must not be read for this rule")


class CaptainAndViceAreMandatory(unittest.TestCase):
    def setUp(self):
        self.db = _NoDatabase()

    def check(self, team, cap, vc):
        return self.db.validate_selection(team, cap, vc, 100.0, 11)

    def test_no_captain_and_no_vice_captain(self):
        ok, msg, _ = self.check(TEAM, None, None)
        self.assertFalse(ok)
        self.assertEqual(msg, "Choose a captain and a vice-captain before saving")

    def test_empty_strings_count_as_missing(self):
        ok, msg, _ = self.check(TEAM, "", "")
        self.assertFalse(ok)
        self.assertIn("captain and a vice-captain", msg)

    def test_vice_captain_but_no_captain(self):
        ok, msg, _ = self.check(TEAM, None, "p2")
        self.assertFalse(ok)
        self.assertEqual(msg, "Choose a captain before saving")

    def test_captain_but_no_vice_captain(self):
        ok, msg, _ = self.check(TEAM, "p1", None)
        self.assertFalse(ok)
        self.assertEqual(msg, "Choose a vice-captain before saving")

    def test_size_and_duplicates_are_still_reported_first(self):
        ok, msg, _ = self.check(TEAM[:10], None, None)
        self.assertFalse(ok)
        self.assertTrue(msg.startswith("Need exactly 11 players"), msg)
        ok, msg, _ = self.check(TEAM[:10] + ["p1"], None, None)
        self.assertFalse(ok)
        self.assertEqual(msg, "Duplicate players are not allowed")

    def test_clearing_a_draft_is_still_allowed(self):
        self.assertEqual(self.check([], None, None), (True, None, 0.0))


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "set TEST_DATABASE_URL to run the database tests")
class FullValidationStillWorks(unittest.TestCase):
    def test_valid_xi_with_both_roles_passes_and_the_other_rules_still_hold(self):
        import psycopg
        raw = os.environ["TEST_DATABASE_URL"]
        schema = "t_sel_" + uuid.uuid4().hex[:8]
        admin = psycopg.connect(raw, autocommit=True)
        admin.execute(f'CREATE SCHEMA "{schema}"')
        db = None
        try:
            db = DatabaseManager(raw + ("&" if "?" in raw else "?") + "options=" + quote(f"-c search_path={schema}"))
            db.create_competition("sel_x", "SEL X", status="active")
            with db._write() as con:
                for i, pid in enumerate(TEAM + ["p12"]):
                    con.execute("INSERT INTO players (id,name,team,role,price,competition_id) VALUES (%s,%s,%s,%s,%s,%s)",
                                (pid, f"Player {i}", "AAA", "BAT", 8.0, "sel_x"))
            v = lambda team, cap, vc: db.validate_selection(team, cap, vc, 100.0, 11, competition_id="sel_x")  # noqa: E731
            ok, msg, total = v(TEAM, "p1", "p2")
            self.assertTrue(ok, msg)
            self.assertEqual(total, 88.0)
            self.assertFalse(v(TEAM, "p12", "p2")[0])            # captain not in the XI
            self.assertFalse(v(TEAM, "p1", "p12")[0])            # vice-captain not in the XI
            self.assertFalse(v(TEAM, "p1", "p1")[0])             # the same player in both roles
            self.assertFalse(db.validate_selection(TEAM, "p1", "p2", 80.0, 11, competition_id="sel_x")[0])   # over budget
            self.assertFalse(v(TEAM[:10] + ["nobody"], "p1", "p2")[0])   # unknown player
        finally:
            try:
                db._pool.close()
            except Exception:
                pass
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
            admin.close()


if __name__ == "__main__":
    unittest.main()
