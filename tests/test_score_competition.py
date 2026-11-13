"""Tests for the pure helpers of score_competition.py (no network, no database)."""
import os, sys, unittest
from datetime import datetime, timezone
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


if __name__ == "__main__":
    unittest.main()
