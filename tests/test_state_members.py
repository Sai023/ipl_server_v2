"""Regression: a brand-new competition (no saved picks yet) must still list every registered member,
otherwise the login screen is empty and signed-in members are logged out when it is activated.
Needs a scratch Postgres: set TEST_DATABASE_URL (a DIRECT Neon/Postgres URL); the test uses and drops its own schema."""
import os
import unittest
import uuid
from urllib.parse import quote


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "set TEST_DATABASE_URL to run the database tests")
class StateListsEveryMember(unittest.TestCase):
    def test_new_competition_lists_registered_members(self):
        import psycopg
        raw = os.environ["TEST_DATABASE_URL"]
        schema = "t_state_" + uuid.uuid4().hex[:8]
        admin = psycopg.connect(raw, autocommit=True)
        admin.execute(f'CREATE SCHEMA "{schema}"')
        db = None
        try:
            import sys
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from db_manager import DatabaseManager
            db = DatabaseManager(raw + ("&" if "?" in raw else "?") + "options=" + quote(f"-c search_path={schema}"))
            db.create_competition("ipl_x", "IPL X", status="active")
            db.create_competition("sa_x", "SA X", status="upcoming")
            for name in ("Sai", "Moe"):
                db.upsert_member_auth(name, "hash", 0, 1 if name == "Sai" else 0)
            db.upsert_member("Sai", {"this_week": {"team": ["a1"], "cap": "a1", "vc": None}, "next_week": {"team": [], "cap": None, "vc": None}}, competition_id="ipl_x")
            self.assertEqual(sorted(db.get_state("sa_x")["members"]), ["Moe", "Sai"])          # no picks in sa_x at all
            ipl = db.get_state("ipl_x")["members"]
            self.assertEqual(sorted(ipl), ["Moe", "Sai"])
            self.assertEqual(ipl["Sai"]["this_week"]["team"], ["a1"])                          # real picks untouched
            self.assertEqual(ipl["Moe"]["this_week"]["team"], [])
        finally:
            try:
                db._pool.close()
            except Exception:
                pass
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
            admin.close()


if __name__ == "__main__":
    unittest.main()
