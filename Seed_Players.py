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
    ("c01", "Ruturaj Gaikwad",       "CSK",  10.0, "BAT"),
    ("c02", "Devon Conway",          "CSK",  10.0, "BAT"),
    ("c03", "Shivam Dube",           "CSK",  10.5, "AR"),
    ("c05", "MS Dhoni",              "CSK",   4.0, "WK"),
    ("c06", "Matheesha Pathirana",   "CSK",  11.0, "BOWL"),
    ("c08", "Maheesh Theekshana",    "CSK",   8.0, "BOWL"),
    ("c09", "Deepak Chahar",         "CSK",   9.0, "BOWL"),
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
    ("c23", "Prashant Veer",         "CSK",  6.0, "BOWL"),  # record uncapped buy
    ("c24", "Kartik Sharma",         "CSK",  6.0, "AR"),
    ("c25", "Sanju Samson",          "CSK",  14.0, "WK"),   # traded from RR

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
    ("l11", "Mayank Yadav",          "LSG",   8.0, "BOWL"), # Retention
    ("l21", "Mitchell Marsh",        "LSG",   9.0, "AR"),   # Retention
    ("l06", "Ayush Badoni",          "LSG",   6.0, "BAT"),
    ("l05", "Mohsin Khan",           "LSG",   5.0, "BOWL"),
    ("l22", "Mohammed Shami",        "LSG",   8.0, "BOWL"), # Traded from SRH
    ("l23", "Anrich Nortje",         "LSG",   7.0, "BOWL"), # Auction Buy
    ("l24", "Wanindu Hasaranga",     "LSG",   7.0, "AR"),   # Auction Buy
    ("l25", "Josh Inglis",           "LSG",   6.0, "WK"),   # Auction Buy
    ("l26", "Aiden Markram",         "LSG",   5.0, "BAT"), # Retention
    ("l20", "Shahbaz Ahmed",         "LSG",   4.0, "AR"),
    ("l12", "Arshin Kulkarni",       "LSG",   3.5, "BAT"),
    ("l17", "Manimaran Siddharth",   "LSG",   3.0, "BOWL"),
    ("l27", "Arjun Tendulkar",       "LSG",   3.0, "BOWL"), # Traded from MI
    ("l28", "Mukul Choudhary",       "LSG",   3.0, "WK"),   # Auction Buy
    ("l29", "Abdul Samad",           "LSG",   3.0, "BAT"),

 # ══ MI — Mumbai Indians ══
    ("m03", "Jasprit Bumrah",        "MI",   15.0, "BOWL"), # Lead Retainer
    ("m01", "Rohit Sharma",          "MI",   14.0, "BAT"),  # Retained
    ("m02", "Suryakumar Yadav",      "MI",   14.0, "BAT"),  # Retained
    ("m04", "Hardik Pandya",         "MI",   14.0, "AR"),   # Captain
    ("m23", "Quinton de Kock",       "MI",   11.0, "WK"),   # Back with MI 2026
    ("m10", "Trent Boult",           "MI",   10.0, "BOWL"), # Back with MI 2026
    ("m07", "Tilak Varma",           "MI",   10.0, "BAT"),  # Retained
    ("m21", "Will Jacks",            "MI",    8.0, "AR"),   # Retention
    ("m08", "Naman Dhir",            "MI",    5.0, "BAT"),  # Retention
    ("m19", "Deepak Chahar",         "MI",    6.0, "BOWL"), # Retention
    ("m24", "Mitchell Santner",      "MI",    4.0, "AR"),   # Retention
    ("m25", "Shardul Thakur",        "MI",    4.0, "AR"),   # Traded from LSG
    ("m26", "Sherfane Rutherford",   "MI",    4.0, "BAT"),  # Traded from GT
    ("m27", "Mayank Markande",       "MI",    4.0, "BOWL"), # Traded from KKR
    ("m12", "Ryan Rickelton",        "MI",    9.0, "WK"),   # Retained
    ("m28", "Robin Minz",            "MI",    3.0, "WK"),   # Retention
    ("m29", "Allah Ghazanfar",       "MI",    3.0, "BOWL"), # Retention
    ("m30", "Danish Malewar",        "MI",    0.5, "BAT"), 

     # ══ PBKS — Punjab Kings ══
    ("p01", "Shreyas Iyer",          "PBKS",  12.0, "BAT"),  # Captain
    ("p06", "Arshdeep Singh",        "PBKS",  10.0, "BOWL"), # Lead Retainer
    ("p21", "Marco Jansen",          "PBKS",   9.0, "AR"),   # Star All-rounder
    ("p18", "Harshal Patel",         "PBKS",   7.0, "BOWL"),
    ("p16", "Shashank Singh",        "PBKS",   5.5, "BAT"),  # 2026 Retention
    ("p09", "Prabhsimran Singh",     "PBKS",   4.0, "WK"),
    ("p10", "Harpreet Brar",         "PBKS",   4.0, "AR"),
    ("p22", "Marcus Stoinis",        "PBKS",  11.0, "AR"),   # Huge 2026 Buy
    ("p23", "Lockie Ferguson",       "PBKS",   2.0, "BOWL"), # Pacer
    ("p24", "Yuzvendra Chahal",      "PBKS",  10.0, "BOWL"), # Star Spinner
    ("p25", "Azmatullah Omarzai",    "PBKS",   4.0, "AR"),
    ("p26", "Nehal Wadhera",         "PBKS",   4.2, "BAT"),
    ("p27", "Priyansh Arya",         "PBKS",   3.8, "BAT"),  # Breakout Star
    ("p28", "Cooper Connolly",       "PBKS",   5.0, "AR"),   # Auction Buy
    ("p29", "Ben Dwarshuis",         "PBKS",   4.4, "BOWL"), # Auction Buy
    ("p30", "Xavier Bartlett",       "PBKS",   3.0, "BOWL"),
    ("p31", "Musheer Khan",          "PBKS",   1.0, "AR"),   # Young Talent


    # ══ RCB — Royal Challengers Bengaluru ══
    ("r01", "Virat Kohli",           "RCB",  15.0, "BAT"),
    ("r05", "Rajat Patidar",         "RCB",  11.0, "BAT"),  # Captain
    ("r21", "Phil Salt",             "RCB",  11.5, "WK"),   # 2026 Lead Opener
    ("r22", "Tim David",             "RCB",   8.0, "BAT"),
    ("r07", "Bhuvneshwar Kumar",     "RCB",   8.0, "BOWL"), # New Lead Indian Pacer
    ("r15", "Josh Hazlewood",        "RCB",   7.0, "BOWL"),
    ("r23", "Jacob Duffy",           "RCB",   5.0, "BOWL"), # NZ Star / Debut Hero
    ("r09", "Yash Dayal",            "RCB",   6.0, "BOWL"),
    ("r26", "Venkatesh Iyer",        "RCB",   7.0, "AR"),   # Major 2026 Signing
    ("r27", "Jitesh Sharma",         "RCB",   5.0, "WK"),   # New Signing
    ("r28", "Krunal Pandya",         "RCB",   5.75, "AR"),  # 2025 Final Hero
    ("r18", "Akash Deep",            "RCB",   5.0, "BOWL"),
    ("r24", "Abhinandan Singh",      "RCB",   3.0, "BOWL"), # Retained Uncapped
    ("r29", "Mangesh Yadav",         "RCB",   5.2, "AR"),   # Big Uncapped Buy
    ("r10", "Karn Sharma",           "RCB",   3.5, "BOWL"),
    ("r14", "Swapnil Singh",         "RCB",   3.0, "AR"),
    ("r30", "Jordan Cox",            "RCB",   3.0, "WK"),


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
    ("s03", "Pat Cummins",           "SRH",  14.0, "BOWL"), # Captain
    ("s05", "Travis Head",           "SRH",  12.0, "BAT"),  # Retained
    ("s22", "Ishan Kishan",          "SRH",  11.5, "WK"),   # New Core
    ("s02", "Abhishek Sharma",       "SRH",  10.0, "AR"),   # Retained
    ("s23", "Liam Livingstone",      "SRH",  10.0, "AR"),   # Star Auction Buy
    ("s21", "Harshal Patel",         "SRH",   8.0, "BOWL"), # Retention
    ("s20", "Nitish Kumar Reddy",    "SRH",   7.0, "AR"),   # Retention
    ("s24", "Shivam Mavi",           "SRH",   4.0, "BOWL"), # Auction Buy
    ("s09", "T Natarajan",           "SRH",   5.0, "BOWL"),
    ("s14", "Jaydev Unadkat",        "SRH",   4.0, "BOWL"),
    ("s10", "Umran Malik",           "SRH",   5.0, "BOWL"),
    ("s11", "Abdul Samad",           "SRH",   3.0, "BAT"),
    ("s12", "Glenn Phillips",        "SRH",   2.0, "WK"),
    ("s29", "Kamindu Mendis",        "SRH",   3.0, "AR"),
    ("s26", "Dilshan Madushanka",    "SRH",   3.0, "BOWL"), # Injury Replacement
    ("s27", "David Payne",           "SRH",   3.0, "BOWL"), # IPL Debut Hero
    ("s28", "Harsh Dubey",           "SRH",   3.0, "AR"),   # Breakout Spinner
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
