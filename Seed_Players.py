#!/usr/bin/env python3
"""
IPL Fantasy 2026 — Player Roster Seeder
========================================
Populates the `players` table with IPL 2026 squad players.

ID convention:  {team_prefix}{number:02d}
  c=CSK  d=DC  g=GT  k=KKR  l=LSG  m=MI  p=PBKS  r=RCB  rr=RR  s=SRH

Roles: BAT, BOWL, AR (all-rounder), WK (wicketkeeper)

Usage:
    python Seed_Players.py          # wipe players + reseed (default)
    python Seed_Players.py --reset  # wipe players + match data + reseed

v2 changes:
  rr11: "Vaibhav Suryavanshi" -> "Vaibhav Sooryavanshi" (Cricbuzz/Cricinfo spelling)
  c11:  price 2.2 -> 8.0 (Dewald Brevis corrected)
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
    ("c02", "Shivam Dube",           "CSK",  10.5, "AR"),
    ("c03", "MS Dhoni",              "CSK",   4.0, "WK"),   # Retained (Uncapped)
    ("c04", "Mukesh Choudhary",      "CSK",   4.0, "BOWL"),
    ("c05", "Anshul Kamboj",         "CSK",   3.0, "BOWL"), # Key 2026 Retention
    ("c06", "Khaleel Ahmed",         "CSK",   5.0, "BOWL"),
    ("c07", "Prashant Veer",         "CSK",  7.0, "AR"),   # Record Uncapped Buy
    ("c08", "Kartik Sharma",         "CSK",  7.0, "WK"),   # Record Uncapped Buy
    ("c09", "Sanju Samson",          "CSK",  10.0, "WK"),   # Traded from RR
    ("c10", "Ayush Mhatre",          "CSK",   8.0, "BAT"),  # Breakout Young Opener
    ("c11", "Dewald Brevis",         "CSK",   9.0, "BAT"),  # Key Overseas Signing (price corrected)
    ("c12", "Noor Ahmad",            "CSK",  10.0, "BOWL"), # Lead Spinner
    ("c13", "Jamie Overton",         "CSK",   1.5, "AR"),
    ("c14", "Spencer Johnson",       "CSK",   1.5, "BOWL"), # Injury Replacement
    ("c15", "Sarfaraz Khan",         "CSK",   1.0, "BAT"),
    ("c16", "Urvil Patel",           "CSK",   1.0, "WK"),
    ("c17", "Ramakrishna Ghosh",     "CSK",   1.0, "AR"),
    ("c18", "Shreyas Gopal",         "CSK",   1.0, "BOWL"),
    ("c19", "Matt Henry",            "CSK",   2.0, "BOWL"),
    ("c20", "Akeal Hosein",          "CSK",   2.0, "BOWL"),
    ("c21", "Rahul Chahar",          "CSK",   7.0, "BOWL"),
    ("c22", "Matthew Short",          "CSK",  7.0, "AR"),


    # ══ DC ══
    ("d01", "KL Rahul",              "DC",   14.0, "WK"),
    ("d02", "David Warner",          "DC",   10.0, "BAT"),
    ("d03", "Axar Patel",            "DC",   12.0, "AR"),
    ("d04", "Kuldeep Yadav",         "DC",   11.0, "BOWL"),
    ("d07", "Prithvi Shaw",          "DC",    5.0, "BAT"),
    ("d08", "Abishek Porel",         "DC",    6.0, "WK"),
    ("d09", "Tristan Stubbs",        "DC",    7.0, "BAT"),
    ("d10", "Ishant Sharma",         "DC",    4.0, "BOWL"),
    ("d12", "Jake Fraser-McGurk",    "DC",    9.0, "BAT"),
    ("d13", "Mukesh Kumar",          "DC",    5.5, "BOWL"),
    ("d14", "Kumar Kushagra",        "DC",    3.5, "WK"),
    ("d15", "Lalit Yadav",           "DC",    3.5, "AR"),
    ("d16", "Shai Hope",             "DC",    7.0, "WK"),
    ("d17", "Ricky Bhui",            "DC",    3.0, "BAT"),
    ("d18", "Sumit Kumar",           "DC",    3.0, "BOWL"),
    ("d19", "Vipraj Nigam",          "DC",    3.0, "AR"),
    ("d20", "Harry Brook",           "DC",   12.0, "BAT"),
    ("d22", "Lungi Ngidi",           "DC",    6.5, "BOWL"),
    ("d23", "Pathum Nissanka",       "DC",    6.0, "BAT"),  # IPL debut M5
    ("d24", "T Natarajan",           "DC",    6.0, "BAT"), 
    ("d25", "Sameer Rizvi",          "DC",    5.0, "BAT"), 

    # ══ GT ══
    ("g01", "Shubman Gill",          "GT",   13.0, "BAT"),
    ("g02", "Sai Sudharsan",         "GT",    9.0, "BAT"),
    ("g03", "Rashid Khan",           "GT",   13.0, "BOWL"),
    ("g04", "Mohit Sharma",          "GT",    5.0, "BOWL"),
    ("g06", "Wriddhiman Saha",       "GT",    4.0, "WK"),
    ("g07", "Rahul Tewatia",         "GT",    8.0, "AR"),
    ("g08", "David Miller",          "GT",    8.0, "BAT"),
    ("g09", "Azmatullah Omarzai",    "GT",    7.0, "AR"),
    ("g10", "Darshan Nalkande",      "GT",    3.0, "BOWL"),
    ("g11", "Jos Buttler",           "GT",   11.0, "WK"),
    ("g12", "B Sai Kishore",         "GT",    4.0, "BOWL"),
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
    ("k04", "Varun Chakaravarthy",   "KKR",  10.0, "BOWL"),
    ("k05", "Rahul Tripathi",        "KKR",   8.0, "BAT"),
    ("k06", "Umran Malik",           "KKR",   7.0, "BOWL"),
    ("k07", "Rachin Ravindra",       "PBKS",  9.0, "AR"),
    ("k08", "Rovman Powell",         "KKR",   7.0, "BAT"),
    ("k09", "Harshit Rana",          "KKR",   7.0, "BOWL"),
    ("k10", "Ramandeep Singh",       "KKR",   4.0, "AR"),
    ("k12", "Manish Pandey",         "KKR",   4.0, "BAT"),
    ("k13", "Vaibhav Arora",         "KKR",   4.0, "BOWL"),
    ("k14", "Cameron Green",         "KKR",  11.0, "AR"),
    ("k15", "Tim Seifert",           "KKR",   7.0, "WK"),
    ("k17", "Angkrish Raghuvanshi",  "KKR",   6.0, "WK"),
    ("k18", "Blessing Muzarabani",   "KKR",   5.0, "BOWL"),
    ("k19", "Ajinkya Rahane",        "KKR",   5.0, "BAT"),
    ("k20", "Finn Allen",            "KKR",   6.0, "WK"),
    ("k22", "Kartik Tyagi",          "KKR",   5.0, "BOWL"),

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
    ("l11", "Aiden Markram",         "LSG",   9.0, "BAT"), # Retention
    ("l12", "Shahbaz Ahmed",         "LSG",   4.0, "AR"),
    ("l13", "Arshin Kulkarni",       "LSG",   3.5, "BAT"),
    ("l14", "Manimaran Siddharth",   "LSG",   3.0, "BOWL"),
    ("l15", "Arjun Tendulkar",       "LSG",   3.0, "BOWL"), # Traded from MI
    ("l16", "Mukul Choudhary",       "LSG",   3.0, "WK"),   # Auction Buy
    ("l17", "Abdul Samad",           "LSG",   3.0, "BAT"),
    ("l18", "Digvesh Singh Rathi",   "LSG",   8.0, "BAT"),
    ("l19", "Prince Yadav",          "LSG",   4.0, "BAT"),

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
    ("m16", "Shardul Thakur",        "MI",    4.0, "AR"),   # Traded from LSG
    ("m17", "Sherfane Rutherford",   "MI",    4.0, "BAT"),  # Traded from GT
    ("m18", "Mayank Markande",       "MI",    4.0, "BOWL"), # Traded from KKR
    ("m19", "Robin Minz",            "MI",    3.0, "WK"),   # Retained
    ("m20", "AM Ghazanfar",          "MI",    3.0, "BOWL"), # Retained
    ("m21", "Danish Malewar",        "MI",    2.0, "BAT"),  # Signed at 2026 Auction

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


# ══ RCB — Royal Challengers Bengaluru (2026) ══
    ("r01", "Virat Kohli",           "RCB",  15.0, "BAT"),  # Star Retainer
    ("r02", "Rajat Patidar",         "RCB",  11.0, "BAT"), # Captain
    ("r03", "Phil Salt",             "RCB",  11.0, "WK"),
    ("r04", "Jitesh Sharma",         "RCB",  10.0, "WK"),
    ("r05", "Bhuvneshwar Kumar",     "RCB",  10.0, "BOWL"),
    ("r06", "Josh Hazlewood",        "RCB",  12.0, "BOWL"),
    ("r07", "Venkatesh Iyer",        "RCB",   9.0, "AR"),
    ("r08", "Rasikh Salam",          "RCB",   7.0, "BOWL"),
    ("r09", "Krunal Pandya",         "RCB",   8.0, "AR"),
    ("r10", "Mangesh Yadav",         "RCB",   6.0, "AR"),
    ("r11", "Yash Dayal",            "RCB",   7.0, "BOWL"),
    ("r12", "Tim David",             "RCB",   8.0, "AR"),
    ("r13", "Jacob Duffy",           "RCB",   6.0, "BOWL"),
    ("r14", "Devdutt Padikkal",      "RCB",   5.0, "BAT"),
    ("r15", "Jacob Bethell",         "RCB",   5.0, "AR"),
    ("r16", "Romario Shepherd",      "RCB",   6.0, "AR"),
    ("r17", "Nuwan Thushara",        "RCB",   6.0, "BOWL"),
    ("r18", "Suyash Sharma",         "RCB",   5.0, "BOWL"),
    ("r19", "Jordan Cox",            "RCB",   4.0, "WK"),
    ("r20", "Swapnil Singh",         "RCB",   3.0, "AR"),
    ("r21", "Abhinandan Singh",      "RCB",   3.0, "BOWL"),

    # ══ RR ══
    ("rr01", "Riyan Parag",          "RR",    9.0, "AR"),   # captain 2026
    ("rr02", "Yashasvi Jaiswal",     "RR",   14.0, "BAT"),
    ("rr03", "Adam Milne",           "RR",    5.0, "BOWL"),
    ("rr04", "Shimron Hetmyer",      "RR",   10.0, "BAT"),
    ("rr05", "Tushar Deshpande",     "RR",    7.0, "BOWL"),
    ("rr06", "Dhruv Jurel",          "RR",    7.0, "WK"),
    ("rr07", "Sandeep Sharma",       "RR",    4.0, "BOWL"),
    ("rr08", "Jofra Archer",         "RR",   10.0, "BOWL"),
    ("rr09", "Ravi Bishnoi",         "RR",    9.0, "BOWL"),
    ("rr10", "Avesh Khan",           "RR",    6.0, "BOWL"),
    ("rr11", "Vaibhav Sooryavanshi", "RR",    9.0, "BAT"),  # Cricbuzz/Cricinfo: Sooryavanshi (double-o)
    ("rr12", "Nandre Burger",        "RR",    6.0, "BOWL"),
    ("rr13", "Tom Kohler-Cadmore",   "RR",    4.0, "WK"),
    ("rr14", "Sam Curran",           "RR",    2.0, "AR"),
    ("rr15", "Donovan Ferreira",     "RR",    3.0, "AR"),
    ("rr16", "Kunal Rathore",        "RR",    3.0, "BAT"),
    ("rr17", "Abid Mushtaq",         "RR",    3.0, "BOWL"),
    ("rr18", "Tanush Kotian",        "RR",    3.0, "AR"),
    ("rr19", "Ravindra Jadeja",      "RR",   10.0, "AR"),  # traded from CSK

   # ══ SRH — Sunrisers Hyderabad ══
    ("s01", "Heinrich Klaasen",      "SRH",  13.0, "WK"),   # Retained
    ("s02", "Pat Cummins",           "SRH",  13.0, "BOWL"), # Captain
    ("s03", "Travis Head",           "SRH",  13.0, "BAT"),  # Retained
    ("s04", "Ishan Kishan",          "SRH",  12.0, "WK"),   # Vice-Captain
    ("s05", "Abhishek Sharma",       "SRH",  12.0, "AR"),   # Retained
    ("s06", "Liam Livingstone",      "SRH",  10.0, "AR"),   # Massive Auction Buy
    ("s07", "Harshal Patel",         "SRH",   9.0, "BOWL"), # Retained
    ("s08", "Nitish Kumar Reddy",    "SRH",   8.0, "AR"),   # Retained
    ("s09", "Shivam Mavi",           "SRH",   6.0, "BOWL"), # Auction Buy
    ("s10", "Dilshan Madushanka",    "SRH",   7.0, "BOWL"), # Replacement for Brydon Carse
    ("s11", "David Payne",           "SRH",   7.0, "BOWL"), # Replacement for Jack Edwards
    ("s12", "Kamindu Mendis",        "SRH",   6.0, "AR"),   # Retained
    ("s13", "Jaydev Unadkat",        "SRH",   5.0, "BOWL"), # Retained
    ("s14", "Harsh Dubey",           "SRH",   5.0, "AR"),   # Breakout Spin Star
    ("s15", "Eshan Malinga",         "SRH",   6.0, "BOWL"), # Retained
    ("s16", "Salil Arora",           "SRH",   4.0, "WK"),   # High-Value Uncapped Buy
    ("s17", "Aniket Verma",          "SRH",   3.0, "BAT"),  # Retained
    ("s18", "Smaran Ravichandran",   "SRH",   3.0, "BAT"),  # Retained
    ("s19", "Zeeshan Ansari",        "SRH",   4.0, "BOWL"), # Retained
    ("s20", "Shivang Kumar",         "SRH",   3.0, "AR"),   # Auction Buy
    ("s21", "Sakib Hussain",         "SRH",   3.0, "BOWL"), # Auction Buy

]


def seed(reset: bool = False):
    (BASE_DIR / "data").mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode = WAL")

    if reset:
        print("  Clearing match data (player_match_points, match_scores)...")
        conn.execute("DELETE FROM player_match_points")
        conn.execute("DELETE FROM match_scores")

    # Always wipe the players table before reseeding to remove stale/duplicate data
    print("  Clearing players table...")
    conn.execute("DELETE FROM players")
    conn.commit()

    inserted = skipped = 0
    for pid, name, team, price, role in PLAYERS:
        try:
            conn.execute(
                "INSERT INTO players (id, name, team, price, role) VALUES (?,?,?,?,?)",
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
    parser.add_argument("--reset", action="store_true",
                        help="Also wipe match_scores and player_match_points")
    args = parser.parse_args()
    print("\n--- IPL 2026 Player Roster Seeder ---")
    seed(reset=args.reset)
