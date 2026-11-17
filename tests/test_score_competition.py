"""Tests for the pure helpers of score_competition.py (no network, no database)."""
import contextlib, os, sys, types, unittest
from datetime import datetime, timezone
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import score_competition as sc

SA20 = {"DSG", "JSK", "MICT", "PC", "PR", "SEC"}
ANCHOR = datetime(2027, 1, 18, 14, tzinfo=timezone.utc)


class CbId(unittest.TestCase):
    def test_links(self):
        self.assertEqual(sc.cb_id_from_url("https://www.cricbuzz.com/live-cricket-scorecard/149618"), "149618")
        self.assertEqual(sc.cb_id_from_url("https://www.cricbuzz.com/live-cricket-scores/149618/srh-vs-rcb"), "149618")
        self.assertIsNone(sc.cb_id_from_url(sc.PLACEHOLDER))
        self.assertIsNone(sc.cb_id_from_url(""))
        self.assertIsNone(sc.cb_id_from_url(None))


class MatchDay(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(sc.match_day("Jan 17", ANCHOR), datetime(2027, 1, 17, tzinfo=timezone.utc))
        self.assertEqual(sc.match_day("Feb 21", ANCHOR), datetime(2027, 2, 21, tzinfo=timezone.utc))
        self.assertIsNone(sc.match_day("", ANCHOR))
        self.assertIsNone(sc.match_day("soon", ANCHOR))

    def test_season_spanning_new_year(self):
        a = datetime(2026, 12, 21, 14, tzinfo=timezone.utc)
        self.assertEqual(sc.match_day("Jan 3", a), datetime(2027, 1, 3, tzinfo=timezone.utc))
        self.assertEqual(sc.match_day("Dec 26", a), datetime(2026, 12, 26, tzinfo=timezone.utc))


class Pairing(unittest.TestCase):
    D = [{"cb_match_id": "300", "title": "Sec Vs Pc 1St Match Sa20 2027"},
         {"cb_match_id": "301", "title": "Pr Vs Jsk 2Nd Match Sa20 2027"},
         {"cb_match_id": "999", "title": "Ara Vs Ubl 15Th Match Abu Dhabi T10 League 2026"},   # other series
         {"cb_match_id": "998", "title": "Pr Vs Pc 3Rd Match Some Other League 2027"},          # right teams, wrong series
         {"cb_match_id": "310", "title": "Sec Vs Pc 31St Match Sa20 2027"}]

    def test_fills_by_pair_and_series(self):
        db = [{"id": "m01", "teams": ["SEC", "PC"], "cb": None}, {"id": "m02", "teams": ["PR", "JSK"], "cb": None},
              {"id": "m09", "teams": ["SEC", "PC"], "cb": None}, {"id": "m31", "teams": [], "cb": None}]
        self.assertEqual(sc.pair_discoveries(db, self.D, SA20, "sa20-2027"),
                         [("m01", "300"), ("m02", "301"), ("m09", "310")])

    def test_keeps_existing_and_never_reuses(self):
        db = [{"id": "m01", "teams": ["SEC", "PC"], "cb": "300"}, {"id": "m09", "teams": ["SEC", "PC"], "cb": None}]
        self.assertEqual(sc.pair_discoveries(db, self.D, SA20, "sa20-2027"), [("m09", "310")])

    def test_needs_two_competition_teams(self):
        self.assertEqual(sc.title_teams("Nz Vs Ind 4Th Odi", SA20), [])
        self.assertEqual(sc.title_teams("Mict Vs Dsg 17Th Match", SA20), ["MICT", "DSG"])


# -- matching by match number (Cricbuzz lists SA20 as "1St Match Sa20 2027": no team codes) ---------------------
# Our first five fixtures exactly as sa20_2027/schedule.json has them, in the shape discover() builds
SA20_DB = [{"id": "sa20_2027_m01", "teams": ["SEC", "PC"], "cb": None, "no": 1},
           {"id": "sa20_2027_m02", "teams": ["PR", "JSK"], "cb": None, "no": 2},
           {"id": "sa20_2027_m03", "teams": ["PC", "DSG"], "cb": None, "no": 3},
           {"id": "sa20_2027_m04", "teams": ["MICT", "JSK"], "cb": None, "no": 4},
           {"id": "sa20_2027_m05", "teams": ["DSG", "SEC"], "cb": None, "no": 5},
           {"id": "sa20_2027_m31", "teams": [], "cb": None, "no": 31}]                      # Qualifier 1: no teams yet
# What Cricbuzz's series page listed for SA20 on 2026-11-17 (ids as seen), plus other series with the same numbers
NUMBERED = [{"cb_match_id": c, "title": f"{o} Match Sa20 2027"}
            for c, o in (("168411", "1St"), ("168417", "2Nd"), ("168427", "3Rd"), ("168437", "4Th"), ("168451", "5Th"))]
OTHER = [{"cb_match_id": "777", "title": "1St Match Abu Dhabi T10 League 2026"}, {"cb_match_id": "778", "title": "2Nd Odi Eng Vs Sl 2026"}]


def hdr(a, b):
    return {"matchHeader": {"team1": {"shortName": a}, "team2": {"shortName": b}}}


class Ordinals(unittest.TestCase):
    def test_ordinal_in_title(self):
        self.assertEqual(sc.ordinal_in_title("1St Match Sa20 2027"), 1)
        self.assertEqual(sc.ordinal_in_title("22Nd Match Sa20 2027"), 22)
        self.assertEqual(sc.ordinal_in_title("Sec Vs Pc 3Rd Match Sa20 2027"), 3)
        self.assertEqual(sc.ordinal_in_title("SEC vs PC, 1st Match"), 1)
        for t in ("Qualifier 1 Sa20 2027", "Final Sa20 2027", "Match 5", "Sa20 2027", "", None):
            self.assertIsNone(sc.ordinal_in_title(t), t)

    def test_match_number(self):
        self.assertEqual(sc.match_number("sa20_2027_m05", "PR vs JSK, 5th Match"), 5)
        self.assertEqual(sc.match_number("sa20_2027_m05"), 5)
        self.assertEqual(sc.match_number("sa20_2027_m31", "Qualifier 1"), 31)
        self.assertIsNone(sc.match_number("sa20_2027_m05", "PR vs JSK, 6th Match"))     # id and title disagree: don't guess
        self.assertIsNone(sc.match_number("sa20_2027_final"))
        self.assertIsNone(sc.match_number(None))


class PairByNumber(unittest.TestCase):
    def test_numbered_titles_fill_our_matches(self):
        self.assertEqual(sc.pair_discoveries(SA20_DB, NUMBERED + OTHER, SA20, "sa20-2027"),
                         [("sa20_2027_m01", "168411"), ("sa20_2027_m02", "168417"), ("sa20_2027_m03", "168427"),
                          ("sa20_2027_m04", "168437"), ("sa20_2027_m05", "168451")])
        self.assertEqual({h for _, _, h in sc._pair_all(SA20_DB, NUMBERED, SA20, "sa20-2027")}, {"number"})

    def test_other_series_with_the_same_numbers_are_ignored(self):
        self.assertEqual(sc.pair_discoveries(SA20_DB, OTHER, SA20, "sa20-2027"), [])

    def test_a_bare_number_needs_the_series_name(self):
        self.assertEqual(sc.pair_discoveries(SA20_DB, NUMBERED, SA20, ""), [])
        self.assertEqual(sc.pair_discoveries(SA20_DB, NUMBERED, SA20, None), [])

    def test_matches_without_two_teams_or_a_number_are_left_alone(self):
        db = [{"id": "x_m31", "teams": [], "cb": None, "no": 31}, {"id": "x_m02", "teams": ["PR", "JSK"], "cb": None, "no": None}]
        d = [{"cb_match_id": "1", "title": "31St Match Sa20 2027"}, {"cb_match_id": "2", "title": "2Nd Match Sa20 2027"}]
        self.assertEqual(sc.pair_discoveries(db, d, SA20, "sa20-2027"), [])

    def test_two_fixtures_claiming_one_number_are_not_guessed(self):
        got = dict(sc.pair_discoveries(SA20_DB, NUMBERED + [{"cb_match_id": "168999", "title": "5Th Match Sa20 2027"}], SA20, "sa20-2027"))
        self.assertNotIn("sa20_2027_m05", got)
        self.assertEqual(len(got), 4)

    def test_team_titles_win_over_numbers(self):
        d = [{"cb_match_id": "300", "title": "Sec Vs Pc 1St Match Sa20 2027"}, {"cb_match_id": "168417", "title": "2Nd Match Sa20 2027"}]
        self.assertEqual(sc._pair_all(SA20_DB, d, SA20, "sa20-2027"),
                         [("sa20_2027_m01", "300", "teams"), ("sa20_2027_m02", "168417", "number")])

    def test_existing_links_are_kept_and_never_reused(self):
        db = [dict(m) for m in SA20_DB]
        db[0]["cb"] = "168411"
        got = sc.pair_discoveries(db, NUMBERED, SA20, "sa20-2027")
        self.assertNotIn("sa20_2027_m01", dict(got))
        self.assertNotIn("168411", {c for _, c in got})
        self.assertEqual(len(got), 4)


class HeaderTeams(unittest.TestCase):
    def test_header_teams(self):
        self.assertEqual(sc.header_teams(hdr(" sec", "Pc ")), ["SEC", "PC"])
        for bad in (None, {}, {"matchHeader": None}, hdr("SEC", ""), {"matchHeader": {"team1": {"shortName": "SEC"}}}):
            self.assertEqual(sc.header_teams(bad), [], bad)


class ConfirmByNumber(unittest.TestCase):
    TEAMS = {"sa20_2027_m01": ["SEC", "PC"], "sa20_2027_m02": ["PR", "JSK"]}

    def run_it(self, fills, scorecards):
        logs, calls = [], []

        def fetch(cid):
            calls.append(cid)
            v = scorecards[cid]
            if isinstance(v, Exception):
                raise v
            return v
        out = sc.confirm_by_number(fills, self.TEAMS, fetch, logs.append, sleep=lambda s: None)
        return out, logs, calls

    def test_confirmed_when_cricbuzz_names_the_same_two_teams_in_any_order(self):
        out, logs, _ = self.run_it([("sa20_2027_m01", "1", "number")], {"1": hdr("pc", "sec")})
        self.assertEqual(out, [("sa20_2027_m01", "1", "number+teams")])
        self.assertEqual(logs, [])

    def test_refused_when_the_teams_differ(self):
        out, logs, _ = self.run_it([("sa20_2027_m01", "1", "number")], {"1": hdr("PR", "JSK")})
        self.assertEqual(out, [])
        self.assertEqual(len(logs), 1)
        self.assertIn("NOT linked", logs[0])

    def test_kept_unconfirmed_when_the_header_names_no_teams(self):
        out, logs, _ = self.run_it([("sa20_2027_m01", "1", "number")], {"1": {"matchHeader": {}}})
        self.assertEqual(out, [("sa20_2027_m01", "1", "number")])

    def test_left_for_the_next_run_when_the_scorecard_cannot_be_read(self):
        out, logs, _ = self.run_it([("sa20_2027_m01", "1", "number"), ("sa20_2027_m02", "2", "number")],
                                   {"1": None, "2": RuntimeError("boom")})
        self.assertEqual(out, [])
        self.assertEqual(len(logs), 2)
        self.assertIn("RuntimeError", logs[1])

    def test_team_title_fills_are_not_fetched(self):
        out, _, calls = self.run_it([("sa20_2027_m01", "1", "teams")], {})
        self.assertEqual(out, [("sa20_2027_m01", "1", "teams")])
        self.assertEqual(calls, [])


class WrongScorecardGuard(unittest.TestCase):
    PAIR = {"SEC", "PC"}

    def test_right_scorecard_passes_even_with_one_side_only(self):
        self.assertFalse(sc.scorecard_mismatch({"SEC", "PC"}, self.PAIR, SA20))
        self.assertFalse(sc.scorecard_mismatch({"PC"}, self.PAIR, SA20))                 # match in progress, first innings

    def test_a_neighbouring_match_sharing_one_team_is_caught(self):
        # m05 DSG v SEC scorecard under m01 SEC v PC: one team in common, which the old rule let through
        self.assertTrue(sc.scorecard_mismatch({"DSG", "SEC"}, self.PAIR, SA20))

    def test_a_team_outside_the_competition_is_caught(self):
        self.assertTrue(sc.scorecard_mismatch({"SRH", "RCB"}, self.PAIR, SA20))
        self.assertTrue(sc.scorecard_mismatch({"SEC", "RCB"}, self.PAIR, SA20))

    def test_nothing_on_the_scorecard_cannot_be_confirmed(self):
        self.assertTrue(sc.scorecard_mismatch(set(), self.PAIR, SA20))

    def test_a_fixture_without_known_teams_only_needs_competition_teams(self):         # a playoff linked by hand
        self.assertFalse(sc.scorecard_mismatch({"DSG", "SEC"}, set(), SA20))
        self.assertTrue(sc.scorecard_mismatch({"SRH", "SEC"}, set(), SA20))

    def test_without_a_competition_team_list_the_fixture_still_decides(self):
        self.assertFalse(sc.scorecard_mismatch({"SEC", "PC"}, self.PAIR, set()))
        self.assertTrue(sc.scorecard_mismatch({"SEC", "DSG"}, self.PAIR, set()))


class DiscoverGlue(unittest.TestCase):
    """discover() end to end with a fake database and a fake Cricbuzz: a number-titled fixture is linked only after
    Cricbuzz's own header names the same two teams; the link is written with the same guarded UPDATE as before."""
    PH = "https://www.cricbuzz.com/live-cricket-scorecard/00000"
    ROWS = [{"id": "sa20_2027_m01", "title": "SEC vs PC, 1st Match", "teams_json": '["SEC", "PC"]', "scorecard_url": PH},
            {"id": "sa20_2027_m02", "title": "PR vs JSK, 2nd Match", "teams_json": '["PR", "JSK"]', "scorecard_url": PH},
            {"id": "sa20_2027_m03", "title": "PC vs DSG, 3rd Match", "teams_json": '["PC", "DSG"]', "scorecard_url": PH},
            {"id": "sa20_2027_m31", "title": "Qualifier 1", "teams_json": "[]", "scorecard_url": PH}]
    CARDS = {"168411": hdr("SEC", "PC"), "168417": hdr("JSK", "PR"), "168427": hdr("MICT", "PR")}     # 3rd shows the WRONG teams

    class Con:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, params=()):
            if sql.lstrip().upper().startswith("SELECT"):
                return types.SimpleNamespace(fetchall=lambda: self.db.rows)
            self.db.writes.append((sql, params))

    class DB:
        def __init__(self, rows):
            self.rows, self.writes = rows, []

        @contextlib.contextmanager
        def _read(self):
            yield DiscoverGlue.Con(self)

        @contextlib.contextmanager
        def _write(self):
            yield DiscoverGlue.Con(self)

    def run_discover(self, dry):
        db, logs = self.DB(self.ROWS), []
        fake_cd = types.ModuleType("logic.cricbuzz_discovery")
        fake_cd.fetch_series_matches = lambda sid, slug, debug=False: NUMBERED[:3] + OTHER
        fake_sc = types.ModuleType("scraper")
        fake_sc.fetch_scorecard_json = lambda cid: self.CARDS.get(cid)
        with mock.patch.dict(sys.modules, {"logic.cricbuzz_discovery": fake_cd, "scraper": fake_sc}), \
                mock.patch.object(sc.time, "sleep", lambda s: None):
            n = sc.discover(db, "sa20_2027", {}, "12791", "sa20-2027", SA20, dry, logs.append)
        return n, db.writes, "\n".join(logs)

    def test_links_confirmed_fixtures_and_refuses_the_wrong_one(self):
        n, writes, log = self.run_discover(dry=False)
        self.assertEqual(n, 2)
        self.assertEqual([(w[1][1], w[1][0].rsplit("/", 1)[1]) for w in writes],
                         [("sa20_2027_m01", "168411"), ("sa20_2027_m02", "168417")])
        self.assertTrue(all(w[0].startswith("UPDATE matches SET scorecard_url") and "LIKE" in w[0] for w in writes))
        self.assertIn("3 matched to our unfilled matches", log)
        self.assertIn("both teams confirmed", log)
        self.assertIn("sa20_2027_m03: Cricbuzz #168427 matches by number but its scorecard shows", log)

    def test_dry_run_writes_nothing(self):
        n, writes, _ = self.run_discover(dry=True)
        self.assertEqual((n, writes), (2, []))


if __name__ == "__main__":
    unittest.main()
