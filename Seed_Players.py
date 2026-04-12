#!/usr/bin/env python3
"""
IPL Fantasy 2026 — Player Roster Seeder
========================================
Populates the `players` table with IPL 2026 squad players.

ID convention:  {team_prefix}{number:02d}
  c=CSK  d=DC  g=GT  k=KKR  l=LSG  m=MI  p=PBKS  r=RCB  rr=RR  s=SRH

Roles: BAT, BOWL, AR (all-rounder), WK (wicketkeeper)

Usage:
    python Seed_Players.py          # seed/update players
    python Seed_Players.py --reset  # wipe + re-seed
"""

import argparse
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH  = BASE_DIR / "data" / "fantasy.db"

# (id, name, team, price, role)
PLAYERS = [
    # ══ CSK ══
    ("c01", "Ruturaj Gaikwad",       "CSK",  14.0, "BAT"),
    ("c02", "Devon Conway",          "CSK",  10.0, "BAT"),
    ("c03", "Shivam Dube",           "CSK",  10.5, "AR"),
    ("c04", "Ravindra Jadeja",       "CSK",  14.0, "AR"),
    ("c05", "MS Dhoni",              "CSK",   4.0, "WK"),
    ("c06", "Matheesha Pathirana",   "CSK",  11.0, "BOWL"),
    ("c07", "Tushar Deshpande",      "CSK",   6.5, "BOWL"),
    ("c08", "Maheesh Theekshana",    "CSK",   8.0, "BOWL"),
    ("c09", "Deepak Chahar",         "CSK",   9.0, "BOWL"),
    ("c10", "Moeen Ali",             "CSK",   8.0, "AR"),
    ("c11", "Ajinkya Rahane",        "CSK",   5.0, "BAT"),
    ("c12", "Rachin Ravindra",       "CSK",  11.0, "AR"),
    ("c13", "Daryl Mitchell",        "CSK",   9.0, "AR"),
    ("c14", "Mukesh Choudhary",      "CSK",   4.0, "BOWL"),
    ("c15", "Shaik Rasheed",         "CSK",   4.0, "BAT"),
    ("c16", "Aravind Swaminathan",   "CSK",   3.0, "WK"),
    ("c17", "Nishant Sindhu",        "CSK",   3.0, "AR"),
    ("c18", "Sameer Rizvi",          "CSK",   5.0, "BAT"),
    ("c19", "Mustafizur Rahman",     "CSK",   8.0, "BOWL"),
    ("c20", "Anuj Rawat",            "CSK",   3.5, "WK"),
    ("c21", "Vijay Shankar",         "CSK",   4.0, "AR"),
    ("c22", "Khaleel Ahmed",         "CSK",   5.0, "BOWL"),
    ("c23", "Prashant Veer",         "CSK",  14.2, "BOWL"),  # record uncapped buy
    ("c24", "Kartik Sharma",         "CSK",  14.2, "AR"),
    ("c25", "Sanju Samson",          "CSK",  14.0, "WK"),   # traded from RR

    # ══ DC ══
    ("d01", "Rishabh Pant",          "DC",   16.0, "WK"),
    ("d02", "David Warner",          "DC",   10.0, "BAT"),
    ("d03", "Axar Patel",            "DC",   12.0, "AR"),
    ("d04", "Kuldeep Yadav",         "DC",   11.0, "BOWL"),
    ("d05", "Anrich Nortje",         "DC",   10.0, "BOWL"),
    ("d06", "Mitchell Marsh",        "DC",    9.0, "AR"),
    ("d07", "Prithvi Shaw",          "DC",    5.0, "BAT"),
    ("d08", "Abishek Porel",         "DC",    6.0, "WK"),
    ("d09", "Tristan Stubbs",        "DC",    7.0, "BAT"),
    ("d10", "Ishant Sharma",         "DC",    4.0, "BOWL"),
    ("d11", "Khaleel Ahmed",         "DC",    5.0, "BOWL"),
    ("d12", "Jake Fraser-McGurk",    "DC",    9.0, "BAT"),
    ("d13", "Mukesh Kumar",          "DC",    5.5, "BOWL"),
    ("d14", "Kumar Kushagra",        "DC",    3.5, "WK"),
    ("d15", "Lalit Yadav",           "DC",    3.5, "AR"),
    ("d16", "Shai Hope",             "DC",    7.0, "WK"),
    ("d17", "Ricky Bhui",            "DC",    3.0, "BAT"),
    ("d18", "Sumit Kumar",           "DC",    3.0, "BOWL"),
    ("d19", "Vipraj Nigam",          "DC",    3.0, "AR"),
    ("d20", "Harry Brook",           "DC",   12.0, "BAT"),
    ("d21", "Faf du Plessis",        "DC",    5.0, "BAT"),
    ("d22", "Lungi Ngidi",           "DC",    6.5, "BOWL"),
    ("d23", "Pathum Nissanka",       "DC",    6.0, "BAT"),  # IPL debut M5

    # ══ GT ══
    ("g01", "Shubman Gill",          "GT",   14.0, "BAT"),
    ("g02", "Sai Sudharsan",         "GT",    9.0, "BAT"),
    ("g03", "Rashid Khan",           "GT",   15.0, "BOWL"),
    ("g04", "Mohit Sharma",          "GT",    5.0, "BOWL"),
    ("g05", "Noor Ahmad",            "GT",    7.0, "BOWL"),
    ("g06", "Wriddhiman Saha",       "GT",    4.0, "WK"),
    ("g07", "Rahul Tewatia",         "GT",    8.0, "AR"),
    ("g08", "David Miller",          "GT",    8.0, "BAT"),
    ("g09", "Azmatullah Omarzai",    "GT",    7.0, "AR"),
    ("g10", "Darshan Nalkande",      "GT",    3.5, "BOWL"),
    ("g11", "Jos Buttler",           "GT",   11.0, "WK"),
    ("g12", "B Sai Kishore",         "GT",    4.0, "BOWL"),
    ("g13", "Spencer Johnson",       "GT",    6.0, "BOWL"),
    ("g14", "Kane Williamson",       "GT",    5.0, "BAT"),
    ("g15", "Matthew Wade",          "GT",    4.0, "WK"),
    ("g16", "Jayant Yadav",          "GT",    3.5, "AR"),
    ("g17", "R Sai Kishore",         "GT",    5.0, "BOWL"),
    ("g18", "Umesh Yadav",           "GT",    4.0, "BOWL"),
    ("g19", "Abhinav Manohar",       "GT",    4.0, "BAT"),
    ("g20", "Josh Little",           "GT",    6.0, "BOWL"),
    ("g21", "Shahrukh Khan",         "GT",    5.0, "BAT"),
    ("g22", "Kagiso Rabada",         "GT",   12.0, "BOWL"),

    # ══ KKR ══
    ("k01", "Sunil Narine",          "KKR",  12.0, "AR"),
    ("k02", "Andre Russell",         "KKR",  12.0, "AR"),
    ("k03", "Rinku Singh",           "KKR",  10.0, "BAT"),
    ("k04", "Varun Chakravarthy",    "KKR",  10.0, "BOWL"),
    ("k05", "Phil Salt",             "KKR",  11.0, "WK"),
    ("k06", "Mitchell Starc",        "KKR",  14.0, "BOWL"),
    ("k07", "Venkatesh Iyer",        "KKR",   8.0, "AR"),
    ("k08", "Nitish Rana",           "KKR",   6.0, "BAT"),
    ("k09", "Harshit Rana",          "KKR",   7.0, "BOWL"),
    ("k10", "Ramandeep Singh",       "KKR",   4.0, "AR"),
    ("k11", "Angkrish Raghuvanshi",  "KKR",   5.0, "BAT"),
    ("k12", "Manish Pandey",         "KKR",   4.0, "BAT"),
    ("k13", "Vaibhav Arora",         "KKR",   4.0, "BOWL"),
    ("k14", "Cameron Green",         "KKR",  25.2, "AR"),  # most expensive overseas IPL 2026
    ("k15", "Suyash Sharma",         "KKR",   3.0, "BOWL"),
    ("k16", "Quinton de Kock",       "KKR",  11.0, "WK"),
    ("k17", "Chetan Sakariya",       "KKR",   4.0, "BOWL"),
    ("k18", "Blessing Muzarabani",   "KKR",   5.0, "BOWL"),  # IPL debut M2
    ("k19", "Ajinkya Rahane",        "KKR",   5.0, "BAT"),
    ("k20", "Finn Allen",            "KKR",   6.0, "BAT"),   # IPL debut M2
    ("k21", "Allah Ghazanfar",       "KKR",   5.0, "BOWL"),  # IPL debut M2
    ("k22", "Lockie Ferguson",       "KKR",   9.0, "BOWL"),

    # ══ LSG ══
    ("l01", "Rishabh Pant",          "LSG",  14.0, "WK"),
    ("l02", "Nicholas Pooran",       "LSG",  11.0, "WK"),
    ("l03", "Marcus Stoinis",        "LSG",  10.0, "AR"),
    ("l04", "Ravi Bishnoi",          "LSG",   8.0, "BOWL"),
    ("l05", "Mohsin Khan",           "LSG",   5.0, "BOWL"),
    ("l06", "Ayush Badoni",          "LSG",   6.0, "BAT"),
    ("l07", "Krunal Pandya",         "LSG",   7.0, "AR"),
    ("l08", "Deepak Hooda",          "LSG",   5.0, "AR"),
    ("l09", "Matt Henry",            "LSG",   6.0, "BOWL"),
    ("l10", "Naveen-ul-Haq",         "LSG",   7.0, "BOWL"),
    ("l11", "Kyle Mayers",           "LSG",   5.0, "AR"),
    ("l12", "Arshin Kulkarni",       "LSG",   3.5, "BAT"),
    ("l13", "Yash Thakur",           "LSG",   3.5, "BOWL"),
    ("l14", "Prerak Mankad",         "LSG",   3.0, "AR"),
    ("l15", "Devdutt Padikkal",      "LSG",   7.0, "BAT"),
    ("l16", "Mayank Yadav",          "LSG",   8.0, "BOWL"),
    ("l17", "Manimaran Siddharth",   "LSG",   3.0, "BOWL"),
    ("l18", "David Willey",          "LSG",   4.0, "AR"),
    ("l19", "Yudhvir Charak",        "LSG",   3.0, "BOWL"),
    ("l20", "Shahbaz Ahmed",         "LSG",   4.0, "AR"),
    ("l21", "Mitchell Marsh",        "LSG",   9.0, "AR"),
    ("l22", "Quinton de Kock",       "LSG",  11.0, "WK"),

    # ══ MI ══
    ("m01", "Rohit Sharma",          "MI",   14.0, "BAT"),
    ("m02", "Suryakumar Yadav",      "MI",   14.0, "BAT"),
    ("m03", "Jasprit Bumrah",        "MI",   16.0, "BOWL"),
    ("m04", "Hardik Pandya",         "MI",   14.0, "AR"),
    ("m05", "Ishan Kishan",          "MI",   10.0, "WK"),
    ("m06", "Tim David",             "MI",    8.0, "BAT"),
    ("m07", "Tilak Varma",           "MI",   10.0, "BAT"),
    ("m08", "Naman Dhir",            "MI",    5.0, "BAT"),
    ("m09", "Dewald Brevis",         "MI",    7.0, "BAT"),
    ("m10", "Trent Boult",           "MI",   10.0, "BOWL"),
    ("m11", "Piyush Chawla",         "MI",    4.0, "BOWL"),
    ("m12", "Ryan Rickelton",        "MI",    6.0, "WK"),
    ("m13", "Akash Madhwal",         "MI",    4.0, "BOWL"),
    ("m14", "Kumar Kartikeya",       "MI",    4.0, "BOWL"),
    ("m15", "Nuwan Thushara",        "MI",    4.0, "BOWL"),
    ("m16", "Gerald Coetzee",        "MI",    7.0, "BOWL"),
    ("m17", "Nehal Wadhera",         "MI",    5.0, "BAT"),
    ("m18", "Arjun Tendulkar",       "MI",    3.0, "AR"),
    ("m19", "Romario Shepherd",      "MI",    6.0, "AR"),
    ("m20", "Mohammad Nabi",         "MI",    4.0, "AR"),
    ("m21", "Will Jacks",            "MI",    8.0, "AR"),
    ("m22", "Shashank Singh",        "MI",    5.0, "BAT"),

    # ══ PBKS ══
    ("p01", "Shreyas Iyer",          "PBKS",  12.0, "BAT"),
    ("p02", "Shikhar Dhawan",        "PBKS",   5.0, "BAT"),
    ("p03", "Liam Livingstone",      "PBKS",  10.0, "AR"),
    ("p04", "Kagiso Rabada",         "PBKS",  12.0, "BOWL"),
    ("p05", "Jonny Bairstow",        "PBKS",   8.0, "WK"),
    ("p06", "Arshdeep Singh",        "PBKS",  10.0, "BOWL"),
    ("p07", "Jitesh Sharma",         "PBKS",   5.0, "WK"),
    ("p08", "Rahul Chahar",          "PBKS",   5.0, "BOWL"),
    ("p09", "Prabhsimran Singh",     "PBKS",   4.0, "WK"),
    ("p10", "Harpreet Brar",         "PBKS",   4.0, "AR"),
    ("p11", "Rilee Rossouw",         "PBKS",   6.0, "BAT"),
    ("p12", "Nathan Ellis",          "PBKS",   5.0, "BOWL"),
    ("p13", "Chris Woakes",          "PBKS",   4.0, "AR"),
    ("p14", "Sikandar Raza",         "PBKS",   4.0, "AR"),
    ("p15", "Ashutosh Sharma",       "PBKS",   5.0, "BAT"),
    ("p16", "Shashank Singh",        "PBKS",   5.0, "BAT"),
    ("p17", "Vishnu Vinod",          "PBKS",   3.0, "WK"),
    ("p18", "Harshal Patel",         "PBKS",   7.0, "BOWL"),
    ("p19", "Vidwath Kaverappa",     "PBKS",   3.0, "BOWL"),
    ("p20", "Atharva Taide",         "PBKS",   3.0, "BAT"),
    ("p21", "Marco Jansen",          "PBKS",   9.0, "AR"),
    ("p22", "Sam Curran",            "PBKS",  11.0, "AR"),
    ("p23", "Cooper Connolly",       "PBKS",   5.0, "AR"),   # IPL debut M4
    ("p24", "Ashok Sharma",          "PBKS",   3.0, "BOWL"), # IPL debut M4

    # ══ RCB ══
    ("r01", "Virat Kohli",           "RCB",  15.0, "BAT"),
    ("r02", "Glenn Maxwell",         "RCB",  11.0, "AR"),
    ("r03", "Mohammed Siraj",        "RCB",  10.0, "BOWL"),
    ("r04", "Wanindu Hasaranga",     "RCB",  10.0, "AR"),
    ("r05", "Rajat Patidar",         "RCB",   9.0, "BAT"),
    ("r06", "Dinesh Karthik",        "RCB",   5.0, "WK"),
    ("r07", "Bhuvneshwar Kumar",     "RCB",   8.0, "BOWL"),
    ("r08", "Will Jacks",            "RCB",   8.0, "AR"),
    ("r09", "Yash Dayal",            "RCB",   6.0, "BOWL"),
    ("r10", "Karn Sharma",           "RCB",   3.5, "BOWL"),
    ("r11", "Reece Topley",          "RCB",   5.0, "BOWL"),
    ("r12", "Suyash Prabhudessai",   "RCB",   3.5, "BAT"),
    ("r13", "Manoj Bhandage",        "RCB",   3.0, "AR"),
    ("r14", "Swapnil Singh",         "RCB",   3.0, "AR"),
    ("r15", "Alzarri Joseph",        "RCB",   7.0, "BOWL"),
    ("r16", "Tom Curran",            "RCB",   5.0, "AR"),
    ("r17", "Rajan Kumar",           "RCB",   3.0, "BAT"),
    ("r18", "Akash Deep",            "RCB",   5.0, "BOWL"),
    ("r19", "Himanshu Sharma",       "RCB",   3.0, "BOWL"),
    ("r20", "Shimron Hetmyer",       "RCB",   7.0, "BAT"),
    ("r21", "Liam Livingstone",      "RCB",  10.0, "AR"),
    ("r22", "Tim David",             "RCB",   8.0, "BAT"),
    # IPL 2026 debutants confirmed from Match 1 (SRH vs RCB, 28 Mar 2026)
    ("r23", "Jacob Duffy",           "RCB",   5.0, "BOWL"), # NZ pace, IPL debut
    ("r24", "Abhinandan Singh",      "RCB",   3.0, "BOWL"), # T20 debut
    ("r25", "Aniket Verma",          "RCB",   3.0, "BAT"),  # debut

    # ══ RR ══
    ("rr01", "Riyan Parag",          "RR",    9.0, "AR"),   # captain 2026
    ("rr02", "Yashasvi Jaiswal",     "RR",   14.0, "BAT"),
    ("rr03", "Jos Buttler",          "RR",   11.0, "WK"),
    ("rr04", "Shimron Hetmyer",      "RR",    7.0, "BAT"),
    ("rr05", "Trent Boult",          "RR",   10.0, "BOWL"),
    ("rr06", "Yuzvendra Chahal",     "RR",    8.0, "BOWL"),
    ("rr07", "R Ashwin",             "RR",    5.0, "BOWL"),
    ("rr08", "Dhruv Jurel",          "RR",    7.0, "WK"),
    ("rr09", "Sandeep Sharma",       "RR",    4.0, "BOWL"),
    ("rr10", "Shimron Rutherford",   "RR",    5.0, "BAT"),
    ("rr11", "Navdeep Saini",        "RR",    4.0, "BOWL"),
    ("rr12", "Avesh Khan",           "RR",    6.0, "BOWL"),
    ("rr13", "Rovman Powell",        "RR",    5.0, "BAT"),
    ("rr14", "Vaibhav Suryavanshi",  "RR",    7.0, "BAT"),
    ("rr15", "Nandre Burger",        "RR",    4.0, "BOWL"),
    ("rr16", "Tom Kohler-Cadmore",   "RR",    4.0, "WK"),
    ("rr17", "Adam Zampa",           "RR",    5.0, "BOWL"),
    ("rr18", "Donovan Ferreira",     "RR",    3.0, "AR"),
    ("rr19", "Kunal Rathore",        "RR",    3.0, "BAT"),
    ("rr20", "Abid Mushtaq",         "RR",    3.0, "BOWL"),
    ("rr21", "Tanush Kotian",        "RR",    3.0, "AR"),
    ("rr22", "Ravindra Jadeja",      "RR",   14.0, "AR"),  # traded from CSK

    # ══ SRH ══
    ("s01", "Heinrich Klaasen",      "SRH",  14.0, "WK"),
    ("s02", "Abhishek Sharma",       "SRH",  10.0, "AR"),
    ("s03", "Pat Cummins",           "SRH",  14.0, "BOWL"),
    ("s04", "Bhuvneshwar Kumar",     "SRH",   8.0, "BOWL"),
    ("s05", "Travis Head",           "SRH",  12.0, "BAT"),
    ("s06", "Aiden Markram",         "SRH",   8.0, "BAT"),
    ("s07", "Rahul Tripathi",        "SRH",   5.0, "BAT"),
    ("s08", "Washington Sundar",     "SRH",   6.0, "AR"),
    ("s09", "T Natarajan",           "SRH",   5.0, "BOWL"),
    ("s10", "Umran Malik",           "SRH",   5.0, "BOWL"),
    ("s11", "Abdul Samad",           "SRH",   4.0, "BAT"),
    ("s12", "Glenn Phillips",        "SRH",   7.0, "WK"),
    ("s13", "Sanvir Singh",          "SRH",   3.0, "BAT"),
    ("s14", "Jaydev Unadkat",        "SRH",   4.0, "BOWL"),
    ("s15", "Mayank Agarwal",        "SRH",   4.0, "BAT"),
    ("s16", "Wanindu Hasaranga",     "SRH",  10.0, "AR"),
    ("s17", "Marco Jansen",          "SRH",   9.0, "AR"),
    ("s18", "Anmolpreet Singh",      "SRH",   3.0, "BAT"),
    ("s19", "Upendra Yadav",         "SRH",   3.0, "WK"),
    ("s20", "Nitish Reddy",          "SRH",   7.0, "AR"),
    ("s21", "Harshal Patel",         "SRH",   7.0, "BOWL"),
    ("s22", "Mohammed Shami",        "SRH",  10.0, "BOWL"),
    # IPL 2026 debutants confirmed from Match 1 (SRH vs RCB, 28 Mar 2026)
    ("s23", "David Payne",           "SRH",   5.0, "BOWL"), # ENG pace, IPL debut
    ("s24", "Harsh Dubey",           "SRH",   3.0, "BOWL"), # domestic
    ("s25", "Eshan Malinga",         "SRH",   3.0, "BOWL"), # in Cricbuzz scorecard
]


def seed(reset: bool = False):
    (BASE_DIR / "data").mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode = WAL")

    if reset:
        print("  Clearing existing players...")
        conn.execute("DELETE FROM player_match_points")
        conn.execute("DELETE FROM match_scores")
        conn.execute("DELETE FROM players")
        conn.commit()

    inserted = skipped = 0
    for pid, name, team, price, role in PLAYERS:
        try:
            conn.execute(
                "INSERT OR REPLACE INTO players (id, name, team, price, role) VALUES (?,?,?,?,?)",
                (pid, name, team, price, role)
            )
            inserted += 1
        except sqlite3.IntegrityError as e:
            print(f"  Skip {pid} ({name}): {e}")
            skipped += 1

    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    conn.close()
    print(f"\n✅ Players seeded: {inserted} inserted, {skipped} skipped")
    print(f"  Total players in DB: {total}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed IPL 2026 player roster")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    print("\n--- IPL 2026 Player Roster Seeder ---")
    seed(reset=args.reset)
