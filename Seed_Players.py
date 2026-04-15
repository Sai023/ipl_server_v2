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
# ══ CSK — Chennai Super Kings (2026) ══
    ("c01", "Ruturaj Gaikwad",       "CSK",  10.0, "BAT"),  # Captain
    ("c02", "Shivam Dube",           "CSK",  10.0, "AR"),
    ("c03", "MS Dhoni",              "CSK",   4.0, "WK"),   # Retained (Uncapped)
    ("c04", "Mukesh Choudhary",      "CSK",   4.0, "BOWL"),
    ("c05", "Anshul Kamboj",         "CSK",   3.4, "BOWL"), # Key 2026 Retention
    ("c06", "Khaleel Ahmed",         "CSK",   5.0, "BOWL"),
    ("c07", "Prashant Veer",         "CSK",  7.0, "AR"),   # Record Uncapped Buy
    ("c08", "Kartik Sharma",         "CSK",  7.0, "WK"),   # Record Uncapped Buy
    ("c09", "Sanju Samson",          "CSK",  14.0, "WK"),   # Traded from RR
    ("c10", "Ayush Mhatre",          "CSK",   0.3, "BAT"),  # Breakout Young Opener
    ("c11", "Dewald Brevis",         "CSK",   8.0, "BAT"),  # Key Overseas Signing
    ("c12", "Noor Ahmad",            "CSK",  10.0, "BOWL"), # Lead Spinner
    ("c13", "Jamie Overton",         "CSK",   1.0, "AR"),
    ("c14", "Spencer Johnson",       "CSK",   1.0, "BOWL"), # Injury Replacement
    ("c15", "Sarfaraz Khan",         "CSK",   7.0, "BAT"),
    ("c16", "Urvil Patel",           "CSK",   0.3, "WK"),
    ("c17", "Ramakrishna Ghosh",     "CSK",   0.3, "AR"),
    ("c18", "Shreyas Gopal",         "CSK",   7.0, "BOWL"),
    ("c19", "Matt Henry",            "CSK",   9.0, "BOWL"),
    ("c20", "Akeal Hosein",          "CSK",   2.0, "BOWL"),
    ("c21", "Rahul Chahar",          "CSK",   5.2, "BOWL"),

    # ══ DC ══
    ("d01", "KL Rahul",              "DC",   14.0, "WK"),
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
    ("d20", "Harry Brook",           "DC",   12.0, "BAT"),,
    ("d22", "Lungi Ngidi",           "DC",    6.5, "BOWL"),
    ("d23", "Pathum Nissanka",       "DC",    6.0, "BAT"),  # IPL debut M5

    # ══ GT ══
    ("g01", "Shubman Gill",          "GT",   13.0, "BAT"),
    ("g02", "Sai Sudharsan",         "GT",    9.0, "BAT"),
    ("g03", "Rashid Khan",           "GT",   13.0, "BOWL"),
    ("g04", "Mohit Sharma",          "GT",    5.0, "BOWL"),
    ("g05", "Noor Ahmad",            "GT",    7.0, "BOWL"),
    ("g06", "Wriddhiman Saha",       "GT",    4.0, "WK"),
    ("g07", "Rahul Tewatia",         "GT",    8.0, "AR"),
    ("g08", "David Miller",          "GT",    8.0, "BAT"),
    ("g09", "Azmatullah Omarzai",    "GT",    7.0, "AR"),
    ("g10", "Darshan Nalkande",      "GT",    3.0, "BOWL"),
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
    ("k14", "Cameron Green",         "KKR",  11.0, "AR"),  # most expensive overseas IPL 2026
    ("k15", "Suyash Sharma",         "KKR",   3.0, "BOWL"),
    ("k17", "Chetan Sakariya",       "KKR",   4.0, "BOWL"),
    ("k18", "Blessing Muzarabani",   "KKR",   5.0, "BOWL"),  # IPL debut M2
    ("k19", "Ajinkya Rahane",        "KKR",   5.0, "BAT"),
    ("k20", "Finn Allen",            "KKR",   6.0, "BAT"),   # IPL debut M2
    ("k21", "Allah Ghazanfar",       "KKR",   5.0, "BOWL"),  # IPL debut M2
    ("k22", "Lockie Ferguson",       "KKR",   9.0, "BOWL"),

    # ══ LSG ══
    ("l01", "Rishabh Pant",          "LSG",  14.0, "WK"),   # Captain
    ("l02", "Nicholas Pooran",       "LSG",  11.0, "WK"),   # Vice-Captain
    ("l03", "Mayank Yadav",          "LSG",   8.0, "BOWL"), # Retention
    ("l04", "Mitchell Marsh",        "LSG",   9.0, "AR"),   # Retention
    ("l05", "Ayush Badoni",          "LSG",   6.0, "BAT"),
    ("l06", "Mohsin Khan",           "LSG",   5.0, "BOWL"),
    ("l07", "Mohammed Shami",        "LSG",   8.0, "BOWL"), # Traded from SRH
    ("l08", "Anrich Nortje",         "LSG",   7.0, "BOWL"), # Auction Buy
    ("l09", "Wanindu Hasaranga",     "LSG",   7.0, "AR"),   # Auction Buy
    ("l10", "Josh Inglis",           "LSG",   6.0, "WK"),   # Auction Buy
    ("l11", "Aiden Markram",         "LSG",   5.0, "BAT"), # Retention
    ("l12", "Shahbaz Ahmed",         "LSG",   4.0, "AR"),
    ("l13", "Arshin Kulkarni",       "LSG",   3.5, "BAT"),
    ("l14", "Manimaran Siddharth",   "LSG",   3.0, "BOWL"),
    ("l15", "Arjun Tendulkar",       "LSG",   3.0, "BOWL"), # Traded from MI
    ("l16", "Mukul Choudhary",       "LSG",   3.0, "WK"),   # Auction Buy
    ("l17", "Abdul Samad",           "LSG",   3.0, "BAT"),

# ══ MI — Mumbai Indians ══
    ("m01", "Rohit Sharma",          "MI",   14.0, "BAT"),  # Retained
    ("m02", "Suryakumar Yadav",      "MI",   14.0, "BAT"),  # Retained
    ("m03", "Jasprit Bumrah",        "MI",   15.0, "BOWL"), # Lead Retainer
    ("m04", "Hardik Pandya",         "MI",   14.0, "AR"),   # Captain
    ("m07", "Tilak Varma",           "MI",   10.0, "BAT"),  # Retained
    ("m08", "Naman Dhir",            "MI",    5.0, "AR"),   # Updated Role: All-Rounder
    ("m10", "Trent Boult",           "MI",   10.0, "BOWL"), # Retained (Back since 2025)
    ("m11", "Ryan Rickelton",        "MI",    9.0, "WK"),   # Retained
    ("m12", "Deepak Chahar",         "MI",    6.0, "BOWL"), # Retained
    ("m13", "Will Jacks",            "MI",    8.0, "AR"),   # Retained
    ("m14", "Quinton de Kock",       "MI",   11.0, "WK"),   # Back with MI 2026 (Auction)
    ("m15", "Mitchell Santner",      "MI",    4.0, "AR"),   # Retained
    ("m16", "Shardul Thakur",        "MI",    4.0, "AR"),   # Traded from LSG
    ("m17", "Sherfane Rutherford",   "MI",    4.0, "BAT"),  # Traded from GT
    ("m18", "Mayank Markande",       "MI",    4.0, "BOWL"), # Traded from KKR
    ("m19", "Robin Minz",            "MI",    3.0, "WK"),   # Retained
    ("m20", "Allah Ghazanfar",       "MI",    3.0, "BOWL"), # Retained
    ("m21", "Danish Malewar",        "MI",    0.5, "BAT"),  # Signed at 2026 Auction

# ══ PBKS — Punjab Kings ══
    ("p01", "Shreyas Iyer",          "PBKS",  12.0, "BAT"),  # Captain
    ("p02", "Arshdeep Singh",        "PBKS",  10.0, "BOWL"), # Lead Retainer
    ("p03", "Prabhsimran Singh",     "PBKS",   4.0, "WK"),   # Retained
    ("p04", "Harpreet Brar",         "PBKS",   4.0, "AR"),   # Retained
    ("p05", "Shashank Singh",        "PBKS",   5.5, "BAT"),  # Retained
    ("p06", "Vyshak Vijaykumar",     "PBKS",   2.0, "BOWL"), # Replaces Harshal Patel
    ("p07", "Marco Jansen",          "PBKS",   9.0, "AR"),   # Retained
    ("p08", "Marcus Stoinis",        "PBKS",  11.0, "AR"),   # Retained
    ("p09", "Lockie Ferguson",       "PBKS",   7.0, "BOWL"), # Retained
    ("p10", "Yuzvendra Chahal",      "PBKS",  10.0, "BOWL"), # Retained
    ("p11", "Azmatullah Omarzai",    "PBKS",   4.0, "AR"),   # Retained
    ("p12", "Nehal Wadhera",         "PBKS",   6.0, "AR"),   # Updated Role: All-rounder
    ("p13", "Priyansh Arya",         "PBKS",   3.8, "BAT"),  # Retained
    ("p14", "Cooper Connolly",       "PBKS",   8.0, "AR"),   # 2026 Auction Buy
    ("p15", "Ben Dwarshuis",         "PBKS",   4.4, "BOWL"), # 2026 Auction Buy
    ("p16", "Xavier Bartlett",       "PBKS",   3.0, "BOWL"), # Retained
    ("p17", "Musheer Khan",          "PBKS",   1.0, "AR"),   # Retained


    # ══ RCB — Royal Challengers Bengaluru ══
    ("r01", "Virat Kohli",           "RCB",  15.0, "BAT"),
    ("r02", "Rajat Patidar",         "RCB",  11.0, "BAT"),  # Captain
    ("r03", "Phil Salt",             "RCB",  11.5, "WK"),   # 2026 Lead Opener
    ("r04", "Tim David",             "RCB",   8.0, "BAT"),
    ("r05", "Bhuvneshwar Kumar",     "RCB",   8.0, "BOWL"), # New Lead Indian Pacer
    ("r06", "Josh Hazlewood",        "RCB",   7.0, "BOWL"),
    ("r07", "Jacob Duffy",           "RCB",   5.0, "BOWL"), # NZ Star / Debut Hero
    ("r09", "Yash Dayal",            "RCB",   6.0, "BOWL"),
    ("r10", "Venkatesh Iyer",        "RCB",   7.0, "AR"),   # Major 2026 Signing
    ("r11", "Jitesh Sharma",         "RCB",   5.0, "WK"),   # New Signing
    ("r12", "Krunal Pandya",         "RCB",   5.75, "AR"),  # 2025 Final Hero
    ("r13", "Akash Deep",            "RCB",   5.0, "BOWL"),
    ("r14", "Abhinandan Singh",      "RCB",   3.0, "BOWL"), # Retained Uncapped
    ("r15", "Mangesh Yadav",         "RCB",   5.2, "AR"),   # Big Uncapped Buy
    ("r16", "Karn Sharma",           "RCB",   3.5, "BOWL"),
    ("r17", "Swapnil Singh",         "RCB",   3.0, "AR"),
    ("r18", "Jordan Cox",            "RCB",   3.0, "WK"),


    # ══ RR ══
    ("rr01", "Riyan Parag",          "RR",    9.0, "AR"),   # captain 2026
    ("rr02", "Yashasvi Jaiswal",     "RR",   14.0, "BAT"),
    ("rr03", "Adam Milne",           "RR",    5.0, "BOWL"),
    ("rr04", "Shimron Hetmyer",      "RR",    10.0, "BAT"),
    ("rr05", "Tushar Deshpande",     "RR",    7.0, "BOWL"),
    ("rr06", "Yuzvendra Chahal",     "RR",    8.0, "BOWL")
    ("rr08", "Dhruv Jurel",          "RR",    7.0, "WK"),
    ("rr09", "Sandeep Sharma",       "RR",    4.0, "BOWL"),
    ("rr10", "Jofra Archer",         "RR",   10.0, "BOWL"),
    ("rr11", "Ravi Bishnoi",         "RR",    9.0, "BOWL"),
    ("rr12", "Avesh Khan",           "RR",    6.0, "BOWL"),
    ("rr14", "Vaibhav Suryavanshi",  "RR",    7.0, "BAT"),
    ("rr15", "Nandre Burger",        "RR",    6.0, "BOWL"),
    ("rr16", "Tom Kohler-Cadmore",   "RR",    4.0, "WK"),
    ("rr17", "Sam Curran",           "RR",    2.0, "AR"),
    ("rr18", "Donovan Ferreira",     "RR",    3.0, "AR"),
    ("rr19", "Kunal Rathore",        "RR",    3.0, "BAT"),
    ("rr20", "Abid Mushtaq",         "RR",    3.0, "BOWL"),
    ("rr21", "Tanush Kotian",        "RR",    3.0, "AR"),
    ("rr22", "Ravindra Jadeja",      "RR",   14.0, "AR"),  # traded from CSK

   # ══ SRH — Sunrisers Hyderabad ══
    ("s01", "Heinrich Klaasen",      "SRH",  14.0, "WK"),   # Retained
    ("s02", "Pat Cummins",           "SRH",  14.0, "BOWL"), # Captain
    ("s03", "Travis Head",           "SRH",  12.0, "BAT"),  # Retained
    ("s04", "Ishan Kishan",          "SRH",  11.5, "WK"),   # New Core
    ("s05", "Abhishek Sharma",       "SRH",  10.0, "AR"),   # Retained
    ("s06", "Liam Livingstone",      "SRH",  10.0, "AR"),   # Star Auction Buy
    ("s07", "Harshal Patel",         "SRH",   8.0, "BOWL"), # Retention
    ("s08", "Nitish Kumar Reddy",    "SRH",   7.0, "AR"),   # Retention
    ("s09", "Shivam Mavi",           "SRH",   4.0, "BOWL"), # Auction Buy
    ("s10", "T Natarajan",           "SRH",   5.0, "BOWL"),
    ("s11", "Jaydev Unadkat",        "SRH",   4.0, "BOWL"),
    ("s12", "Umran Malik",           "SRH",   5.0, "BOWL"),
    ("s13", "Abdul Samad",           "SRH",   3.0, "BAT"),
    ("s14", "Glenn Phillips",        "SRH",   2.0, "WK"),
    ("s15", "Kamindu Mendis",        "SRH",   3.0, "AR"),
    ("s16", "Dilshan Madushanka",    "SRH",   3.0, "BOWL"), # Injury Replacement
    ("s17", "David Payne",           "SRH",   3.0, "BOWL"), # IPL Debut Hero
    ("s18", "Harsh Dubey",           "SRH",   3.0, "AR"),   # Breakout Spinner
    # IPL 2026 debutants confirmed from Match 1 (SRH vs RCB, 28 Mar 2026)
    ("19", "David Payne",           "SRH",   5.0, "BOWL"), # ENG pace, IPL debut
    ("s20", "Harsh Dubey",           "SRH",   3.0, "BOWL"), # domestic
    ("s21", "Eshan Malinga",         "SRH",   3.0, "BOWL"), # in Cricbuzz scorecard
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
