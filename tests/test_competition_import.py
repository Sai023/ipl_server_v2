"""
Tests for logic/competition_import.py (admin roster + schedule import validation).

Pure logic: no database, no network. Run from the repository root:

    python -m unittest discover -s tests -v

The IPL 2026 regression pins the week-numbering rule to what the legacy seeder
stored in production for all 74 matches (week distribution below).
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from logic import competition_import as ci  # noqa: E402

IPL_ANCHOR = ci.parse_anchor("2026-03-30T14:00:00+00:00")
IPL_TEAMS = ["CSK", "DC", "GT", "KKR", "LSG", "MI", "PBKS", "RCB", "RR", "SRH"]
SA_TEAMS = ["DSG", "JSK", "MICT", "PC", "PR", "SEC"]
SA_ANCHOR = ci.parse_anchor("2027-01-18T14:00:00+00:00")


def player(**kw):
    base = {"id": "mct01", "name": "Test Player", "team": "MICT", "price": 7.0, "role": "BAT"}
    base.update(kw)
    return base


def roster(n=12, price=None, team="MICT"):
    """n players. Default prices are graded 4, 5, 6 ... so a 12-player roster has a cheapest XI
    (99) inside the 100 budget and a priciest XI (110) above it; pass `price` for a flat price."""
    return [player(id="mct%02d" % i, name="Player %d" % i, team=team,
                   price=float(price if price is not None else i + 3)) for i in range(1, n + 1)]


def match(no, title="DSG vs JSK, Match", date="2027-01-17", time="21:00", **kw):
    m = {"match_no": no, "title": title, "date": date, "time_ist": time}
    m.update(kw)
    return m


class ParsePlayers(unittest.TestCase):
    def test_real_ipl_roster_is_valid(self):
        raw = json.loads((ROOT / "data" / "ipl_2026" / "players.json").read_text(encoding="utf-8"))
        r = ci.parse_players(raw, valid_teams=IPL_TEAMS)
        self.assertEqual(r["errors"], [])
        self.assertEqual(len(r["rows"]), 195)
        self.assertEqual(r["stats"]["by_role"], {"AR": 47, "BAT": 48, "BOWL": 70, "WK": 30})
        # the known data quirk (Azmatullah Omarzai listed twice) is only a warning
        self.assertTrue(any("Omarzai" in w for w in r["warnings"]))

    def test_minimal_valid_roster(self):
        r = ci.parse_players(roster())
        self.assertEqual(r["errors"], [])
        self.assertEqual(len(r["rows"]), 12)
        self.assertEqual(r["rows"][0], ("mct01", "Player 1", "MICT", 4.0, "BAT"))

    def test_legacy_tuples_and_wrapper_are_accepted(self):
        tuples = [("mct%02d" % i, "P%d" % i, "MICT", float(i + 3), "BAT") for i in range(1, 13)]
        self.assertEqual(ci.parse_players(tuples)["errors"], [])
        self.assertEqual(ci.parse_players({"players": roster()})["errors"], [])

    def test_id_must_follow_the_captain_id_rule(self):
        for bad in ("mict01", "MCT01", "m1x", "mct001", "01", "", None, 5):
            r = ci.parse_players(roster() + [player(id=bad, name="Bad")])
            self.assertTrue(any("id must be" in e for e in r["errors"]), bad)

    def test_role_is_normalised_but_must_be_known(self):
        r = ci.parse_players(roster() + [player(id="mct20", name="Lower", role="bat")])
        self.assertEqual(r["errors"], [])
        self.assertEqual(r["rows"][-1][4], "BAT")
        r = ci.parse_players(roster() + [player(id="mct21", name="X", role="Batsman")])
        self.assertTrue(any("role must be" in e for e in r["errors"]))

    def test_duplicate_id_is_an_error_duplicate_name_a_warning(self):
        r = ci.parse_players(roster() + [player(id="mct01", name="Other")])
        self.assertTrue(any("duplicate id" in e for e in r["errors"]))
        r = ci.parse_players(roster() + [player(id="mct30", name="player 1")])
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("also used" in w for w in r["warnings"]))

    def test_price_rules(self):
        for bad in (-1, 101, "7", None, True, float("nan")):
            r = ci.parse_players(roster() + [player(id="mct40", name="Priced", price=bad)])
            self.assertTrue(any("price" in e for e in r["errors"]), bad)
        r = ci.parse_players(roster() + [player(id="mct41", name="Free", price=0)])
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("free player" in w for w in r["warnings"]))

    def test_team_must_belong_to_the_competition(self):
        r = ci.parse_players(roster() + [player(id="xx01", name="Alien", team="ZZZ")], valid_teams=["MICT"])
        self.assertTrue(any("not one of this competition's teams" in e for e in r["errors"]))
        self.assertEqual(ci.parse_players(roster(), valid_teams=["MICT", "PR"])["errors"], [])
        # a team listed by the competition but absent from the roster is advisory only
        self.assertTrue(any("no players for team" in w
                            for w in ci.parse_players(roster(), valid_teams=["MICT", "PR"])["warnings"]))

    def test_roster_must_be_able_to_field_a_team(self):
        r = ci.parse_players(roster(n=5))
        self.assertTrue(any("fewer than the XI size" in e for e in r["errors"]))
        r = ci.parse_players(roster(n=12, price=9.5))          # cheapest XI = 104.5 > 100
        self.assertTrue(any("cheapest" in e for e in r["errors"]))
        r = ci.parse_players(roster(n=12, price=2.0))          # priciest XI = 22 < 100
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("never bind" in w for w in r["warnings"]))

    def test_shape_errors(self):
        for bad in ({}, {"a": 1}, "text", None, 5, []):
            r = ci.parse_players(bad)
            self.assertTrue(r["errors"], bad)
            self.assertEqual(r["rows"], [])
        r = ci.parse_players(roster() + ["nope"])
        self.assertTrue(any("expected an object" in e for e in r["errors"]))

    def test_names_are_tidied(self):
        r = ci.parse_players(roster() + [player(id="mct50", name="  Kyle   Verreynne ")])
        self.assertEqual(r["rows"][-1][1], "Kyle Verreynne")

    def test_error_list_is_capped(self):
        r = ci.parse_players([player(id="BAD", name="n%d" % i) for i in range(200)])
        self.assertLessEqual(len(r["errors"]), ci.MAX_MESSAGES + 1)
        self.assertTrue(r["errors"][-1].startswith("... and "))


class WeekNumbers(unittest.TestCase):
    def test_boundaries_around_the_first_rollover(self):
        # IPL anchor 2026-03-30 14:00 UTC == 19:30 IST
        self.assertEqual(ci.week_no_for_match("2026-03-30", "19:29", IPL_ANCHOR), 1)
        self.assertEqual(ci.week_no_for_match("2026-03-30", "19:30", IPL_ANCHOR), 2)
        self.assertEqual(ci.week_no_for_match("2026-04-06", "19:29", IPL_ANCHOR), 2)
        self.assertEqual(ci.week_no_for_match("2026-04-06", "19:30", IPL_ANCHOR), 3)
        self.assertEqual(ci.week_no_for_match("2026-03-01", "19:30", IPL_ANCHOR), 1)

    def test_anchor_parsing(self):
        self.assertIsNone(ci.parse_anchor(None))
        self.assertIsNone(ci.parse_anchor(""))
        self.assertIsNone(ci.parse_anchor("garbage"))
        self.assertIsNone(ci.parse_anchor("1970-01-01T00:00:00+00:00"))   # the "never set" default
        self.assertEqual(ci.parse_anchor("2027-01-18T14:00:00Z").utcoffset().total_seconds(), 0)
        self.assertIsNotNone(ci.parse_anchor("2027-01-18T14:00:00"))      # naive -> UTC


class ParseSchedule(unittest.TestCase):
    def test_real_ipl_schedule_reproduces_what_production_stored(self):
        raw = json.loads((ROOT / "data" / "ipl_2026" / "schedule.json").read_text(encoding="utf-8"))
        r = ci.parse_schedule(raw, slug="ipl_2026", anchor=IPL_ANCHOR, valid_teams=IPL_TEAMS)
        self.assertEqual(r["errors"], [])
        self.assertEqual(len(r["rows"]), 74)
        # verified against the 74 rows stored in production by the legacy seeder
        self.assertEqual(r["stats"]["weeks"],
                         {"1": 2, "2": 9, "3": 9, "4": 9, "5": 9, "6": 8, "7": 8, "8": 8, "9": 8, "10": 4})
        self.assertEqual(r["stats"]["max_week"], 10)
        self.assertEqual(r["stats"]["teams_tbd"], 4)             # the playoffs
        self.assertTrue(any("no teams yet" in w for w in r["warnings"]))
        self.assertEqual(r["rows"][0]["id"], "ipl_2026_m01")
        self.assertEqual(r["rows"][0]["teams"], ["SRH", "RCB"])
        self.assertEqual(r["rows"][0]["date_label"], "Mar 28")
        self.assertEqual(r["rows"][0]["week_no"], 1)
        self.assertEqual(r["meta"].get("series_id"), "9241")

    def test_a_missing_anchor_blocks_the_import(self):
        r = ci.parse_schedule([match(1)], slug="x", anchor=None)
        self.assertTrue(any("week-1 date" in e for e in r["errors"]))
        self.assertEqual(r["rows"], [])

    def test_basic_rows_and_ids(self):
        r = ci.parse_schedule({"matches": [match(1), match(2, "JSK vs PR, M2", "2027-01-18", "18:00"),
                                           match(100, "SEC vs PC, M100", "2027-02-20")]},
                              slug="sa20_2027", anchor=SA_ANCHOR, valid_teams=SA_TEAMS)
        self.assertEqual(r["errors"], [])
        self.assertEqual([x["id"] for x in r["rows"]], ["sa20_2027_m01", "sa20_2027_m02", "sa20_2027_m100"])
        self.assertEqual(r["rows"][0]["date_label"], "Jan 17")
        self.assertEqual(r["rows"][0]["teams_json"], '["DSG", "JSK"]')
        self.assertEqual(r["rows"][0]["scorecard_url"], ci.PLACEHOLDER_URL)

    def test_bare_list_is_accepted(self):
        r = ci.parse_schedule([match(i, date="2027-01-%02d" % (16 + i)) for i in range(1, 13)],
                              slug="s", anchor=SA_ANCHOR)
        self.assertEqual(r["errors"], [])

    def test_bad_dates_and_times(self):
        for bad in ({"date": "2027-02-30"}, {"date": "27-01-17"}, {"time_ist": "25:00"}, {"time_ist": "9"},
                    {"date": None}, {"time_ist": None}):
            r = ci.parse_schedule([match(1, **bad)], slug="s", anchor=SA_ANCHOR)
            self.assertTrue(any("date must be" in e for e in r["errors"]), bad)

    def test_match_numbers(self):
        r = ci.parse_schedule([match(1), match(1)], slug="s", anchor=SA_ANCHOR)
        self.assertTrue(any("duplicate match_no" in e for e in r["errors"]))
        for bad in (0, -1, "1", None, True):
            r = ci.parse_schedule([match(bad)], slug="s", anchor=SA_ANCHOR)
            self.assertTrue(any("match_no" in e for e in r["errors"]), bad)

    def test_teams_from_title_explicit_or_to_be_decided(self):
        r = ci.parse_schedule([match(1, "Qualifier 1"), match(2, "Final", teams=["DSG", "PC"])],
                              slug="s", anchor=SA_ANCHOR)
        self.assertEqual(r["errors"], [])
        self.assertEqual(r["rows"][0]["teams"], [])
        self.assertEqual(r["rows"][0]["teams_json"], "[]")
        self.assertEqual(r["rows"][1]["teams"], ["DSG", "PC"])
        for bad in (["DSG"], ["DSG", "DSG"], ["DSG", 5], "DSG vs PC"):
            r = ci.parse_schedule([match(1, "x", teams=bad)], slug="s", anchor=SA_ANCHOR)
            self.assertTrue(r["errors"], bad)

    def test_teams_must_belong_to_the_competition(self):
        r = ci.parse_schedule([match(1, "DSG vs ZZZ, M1")], slug="s", anchor=SA_ANCHOR, valid_teams=SA_TEAMS)
        self.assertTrue(any("not among this competition's teams" in e for e in r["errors"]))

    def test_cricbuzz_ids(self):
        r = ci.parse_schedule([match(1, cricbuzz_id="149618"), match(2, cricbuzz_id=149619), match(3, cricbuzz_id="")],
                              slug="s", anchor=SA_ANCHOR)
        self.assertEqual(r["errors"], [])
        self.assertEqual(r["rows"][0]["scorecard_url"], "https://www.cricbuzz.com/live-cricket-scorecard/149618")
        self.assertEqual(r["rows"][1]["cricbuzz_id"], "149619")
        self.assertEqual(r["rows"][2]["scorecard_url"], ci.PLACEHOLDER_URL)
        self.assertEqual(r["stats"]["with_cricbuzz_id"], 2)
        r = ci.parse_schedule([match(1, cricbuzz_id="abc")], slug="s", anchor=SA_ANCHOR)
        self.assertTrue(any("cricbuzz_id" in e for e in r["errors"]))

    def test_advisory_warnings(self):
        r = ci.parse_schedule([match(1), match(3, date="2027-01-19")], slug="s", anchor=SA_ANCHOR)
        self.assertTrue(any("continuous" in w for w in r["warnings"]))
        self.assertTrue(any("only 2 matches" in w for w in r["warnings"]))
        r = ci.parse_schedule([match(1, date="2026-06-01")], slug="s", anchor=SA_ANCHOR)
        self.assertTrue(any("before the week-1 date" in w for w in r["warnings"]))

    def test_shape_errors(self):
        for bad in ({}, {"matches": []}, {"matches": "x"}, "x", None, []):
            r = ci.parse_schedule(bad, slug="s", anchor=SA_ANCHOR)
            self.assertTrue(r["errors"], bad)
            self.assertEqual(r["rows"], [])

    def test_metadata_is_extracted(self):
        r = ci.parse_schedule({"series_id": "9999", "series_slug": "sa20-2027", "year": 2027,
                               "matches": [match(i, date="2027-01-%02d" % (16 + i)) for i in range(1, 13)]},
                              slug="s", anchor=SA_ANCHOR)
        self.assertEqual(r["meta"], {"series_id": "9999", "series_slug": "sa20-2027", "year": 2027})


class CrossCheck(unittest.TestCase):
    def test_teams_that_disagree_are_flagged(self):
        rows = [("a01", "A", "DSG", 5.0, "BAT"), ("a02", "B", "JSK", 5.0, "BAT"), ("a03", "C", "XXX", 5.0, "BAT")]
        fixtures = [{"teams": ["DSG", "JSK"]}, {"teams": ["DSG", "PC"]}, {"teams": []}]
        out = ci.cross_check(rows, fixtures)
        self.assertEqual(len(out), 2)
        self.assertIn("PC", out[0])
        self.assertIn("XXX", out[1])
        self.assertEqual(ci.cross_check(rows, []), [])
        self.assertEqual(ci.cross_check(None, fixtures), [])


if __name__ == "__main__":
    unittest.main()
