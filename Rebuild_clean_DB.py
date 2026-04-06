"""
Rebuild_clean_DB.py — Full database repair for IPL Fantasy 2026
================================================================
Addresses ALL five screenshot bugs:

  Screenshot 1  Sanju Samson missing from Players table
  Screenshot 2  Sai Week 1 "No XI locked" despite seeded data
  Screenshot 3  Leaderboard empty (zero rows)
  Screenshot 4  History tab stuck on "loading"
  Screenshot 5  Login click appears to fail (post-login data empty)

ROOT CAUSE (single, cascading):
  server.py seeds user_selections with x-alias IDs (x06, x08, x20 …)
  Seed_ipl2026.py seeds match_scores with canonical IDs (r01, s03, m09 …)
  The leaderboard SQL INNER JOINs these two ID spaces → zero overlap →
  empty leaderboard, empty history points, and the UI appears broken.

SECONDARY CAUSES:
  • Seed_ipl2026.py PLAYERS list has only 27 of ~230 players
    → Sanju Samson (rr03) and many others missing from DB
  • m20 ID conflict: seed says "Deepak Chahar", EMBEDDED says "Dewald Brevis"
    → match scores attributed to wrong player in frontend

THIS SCRIPT:
  1. Seeds the COMPLETE player roster (all teams, all roles)
  2. Fixes the m20/m12 Chahar conflict in match_scores
  3. Rewrites user_selections with canonical player IDs
  4. Recalculates player_match_points from scratch
  5. Verifies leaderboard output before exiting

Usage:
    python Rebuild_clean_DB.py
    python Rebuild_clean_DB.py --db /path/to/data/fantasy.db
"""

import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE       = Path(__file__).resolve().parent
DATA_DIR   = HERE / "data"
DEFAULT_DB = DATA_DIR / "fantasy.db"


# ═══════════════════════════════════════════════════════════════════════════════
# POINTS ENGINE  (verbatim from db_manager.py)
# ═══════════════════════════════════════════════════════════════════════════════

def _normalise_overs(raw: float) -> float:
    if raw <= 0: return 0.0
    full = math.floor(raw)
    ball = min(5, max(0, round((raw - full) * 10)))
    return full + ball / 6

def calc_pts(s: dict) -> int:
    if not s or not s.get("played"): return 0
    runs    = max(0, int(s.get("runs",  0)))
    balls   = max(0, int(s.get("balls", 0)))
    fours   = max(0, min(runs, int(s.get("fours",  0))))
    sixes   = max(0, int(s.get("sixes",  0)))
    wickets = max(0, min(10, int(s.get("wickets",  0))))
    overs   = _normalise_overs(max(0.0, float(s.get("overs", 0))))
    rc      = max(0, int(s.get("runsConceded", s.get("runs_conceded", 0))))
    maidens = max(0, int(s.get("maidens", 0)))
    catches = max(0, min(10, int(s.get("catches", 0))))
    stump   = max(0, int(s.get("stumpings", 0)))
    rod     = max(0, int(s.get("runOutDirect", s.get("run_out_direct", 0))))
    roa     = max(0, int(s.get("runOutAssist", s.get("run_out_assist", 0))))
    lbwb    = max(0, min(wickets, int(s.get("lbwBowled", s.get("lbw_bowled", 0)))))
    duck    = bool(s.get("duck", False))
    got_out = bool(s.get("gotOut", s.get("got_out", False)))
    pts = 4
    pts += runs + fours + sixes * 2
    if   runs >= 100: pts += 16
    elif runs >= 50:  pts += 8
    elif runs >= 30:  pts += 4
    if duck and got_out and balls >= 1: pts -= 2
    if balls >= 10:
        sr = (runs / balls) * 100
        if   sr >  125: pts += 6
        elif sr >= 110: pts += 4
        elif sr >= 100: pts += 2
        elif sr <  60:  pts -= 4
        elif sr <  70:  pts -= 2
    pts += wickets * 25 + lbwb * 8 + maidens * 12
    if wickets >= 2: pts += 4
    if wickets >= 3: pts += 4
    if wickets >= 4: pts += 8
    if wickets >= 5: pts += 8
    if overs >= 2:
        eco = rc / overs
        if   eco >  12: pts -= 6
        elif eco >= 11: pts -= 4
        elif eco >= 10: pts -= 2
        elif eco <   5: pts += 6
        elif eco <   6: pts += 4
        elif eco <   7: pts += 2
    pts += catches * 8
    if catches >= 3: pts += 4
    pts += stump * 12 + rod * 12 + roa * 6
    return round(pts)


# ═══════════════════════════════════════════════════════════════════════════════
# x-ALIAS → CANONICAL ID MAPPING
# ═══════════════════════════════════════════════════════════════════════════════
# Maps every x-prefix alias (from EMBEDDED_PLAYERS historical section)
# to the canonical player ID used in the full roster.

X_ALIAS_MAP = {
    "x01": "rr08",  # Chahal (RR)        → Yuzvendra Chahal
    "x02": "g11",   # Noor Ahmad (GT)    → Noor Ahmad
    "x03": "c05",   # Dube (CSK)         → Shivam Dube
    "x04": "rr05",  # Hetmyer (RR)       → Shimron Hetmyer
    "x05": "s22",   # Markram (SRH)      → Aiden Markram
    "x06": "rr03",  # Samson (RR)        → Sanju Samson
    "x07": "rr15",  # Suryavanshi (RR)   → Vaibhav Suryavanshi
    "x08": "m10",   # Bumrah (MI)        → Jasprit Bumrah
    "x09": "r15",   # Kumar (RCB)        → Bhuvneshwar Kumar
    "x10": "k14",   # Rana (KKR)         → Nitish Rana
    "x11": "m11",   # Jansen (MI)        → Marco Jansen
    "x12": "m20",   # Brevis (MI)        → Dewald Brevis
    "x13": "m04",   # Rickelton (MI)     → Ryan Rickelton
    "x14": "d01",   # Pant (DC)          → Rishabh Pant
    "x15": "l12",   # Ngidi (LSG)        → Lungi Ngidi
    "x16": "s13",   # Patel (SRH)        → Harshal Patel
    "x17": "r01",   # Kohli (RCB)        → Virat Kohli
    "x18": "k01",   # Rahane (KKR)       → Ajinkya Rahane
    "x19": "m03",   # Varma (MI)         → Tilak Varma
    "x20": "s03",   # Kishan (SRH)       → Ishan Kishan
    "x21": "r04",   # Salt (RCB)         → Phil Salt
    "x22": "m09",   # H. Pandya (MI)     → Hardik Pandya
    "x23": "k16",   # Chakravarthy (KKR) → Varun Chakravarthy
    "x24": "m12",   # Chahar (MI)        → Deepak Chahar
    "x25": "r20",   # Duffy (RCB)        → Jacob Duffy
    "x26": "k11",   # Ravindra (KKR)     → Rachin Ravindra
    "x27": "r08",   # David (RCB)        → Tim David
    "x28": "r09",   # Shepherd (RCB)     → Romario Shepherd
    "x29": "m07",   # Rutherford (MI)    → Ryan Rutherford
    "x30": "r02",   # Patidar (RCB)      → Rajat Patidar
    "x31": "s04",   # Klaasen (SRH)      → Heinrich Klaasen
    "x32": "m01",   # Sharma (MI)        → Rohit Sharma
}


def resolve_alias(pid: str) -> str:
    """Map an x-alias or any known alias to the canonical player ID."""
    return X_ALIAS_MAP.get(pid, pid)


# ═══════════════════════════════════════════════════════════════════════════════
# COMPLETE PLAYER ROSTER
# ═══════════════════════════════════════════════════════════════════════════════
# Mirrors EMBEDDED_PLAYERS from index.html — EXCLUDES x-prefix aliases
# (those are resolved at seed time, not stored as separate player rows).

ALL_PLAYERS = [
    # ── RCB ──
    ("r01","Virat Kohli","RCB",9.5,"BAT"),
    ("r02","Rajat Patidar","RCB",9.0,"BAT"),
    ("r03","Devdutt Padikkal","RCB",8.5,"BAT"),
    ("r04","Phil Salt","RCB",8.0,"WK"),
    ("r05","Jitesh Sharma","RCB",7.5,"WK"),
    ("r06","Liam Livingstone","RCB",8.5,"AR"),
    ("r07","Krunal Pandya","RCB",8.0,"AR"),
    ("r08","Tim David","RCB",8.5,"BAT"),
    ("r09","Romario Shepherd","RCB",7.5,"AR"),
    ("r10","Swapnil Singh","RCB",6.0,"AR"),
    ("r11","Rasikh Dar","RCB",6.5,"BOWL"),
    ("r12","Nuwan Thushara","RCB",7.0,"BOWL"),
    ("r13","Yash Dayal","RCB",6.5,"BOWL"),
    ("r14","Josh Hazlewood","RCB",8.5,"BOWL"),
    ("r15","Bhuvneshwar Kumar","RCB",8.0,"BOWL"),
    ("r16","Lungi Ngidi","RCB",7.5,"BOWL"),
    ("r17","Suyash Sharma RCB","RCB",6.5,"BOWL"),
    ("r18","Suyash Sharma","RCB",6.5,"BOWL"),
    ("r19","Manoj Bhandage","RCB",6.0,"AR"),
    ("r20","Jacob Duffy","RCB",7.0,"BOWL"),
    ("r21","Swastik Chikara","RCB",5.5,"BOWL"),
    ("r22","Abhinandan Singh","RCB",5.5,"BAT"),
    # ── SRH ──
    ("s01","Travis Head","SRH",9.5,"BAT"),
    ("s02","Abhishek Sharma","SRH",8.5,"BAT"),
    ("s03","Ishan Kishan","SRH",9.0,"WK"),
    ("s04","Heinrich Klaasen","SRH",9.0,"WK"),
    ("s05","Aniket Verma","SRH",6.5,"BAT"),
    ("s06","Atharva Taide","SRH",6.0,"BAT"),
    ("s07","Zeeshan Ansari","SRH",6.0,"BOWL"),
    ("s08","Pat Cummins","SRH",9.5,"BOWL"),
    ("s09","Nitish Reddy","SRH",8.5,"AR"),
    ("s10","Adam Zampa","SRH",7.5,"BOWL"),
    ("s11","Mohammed Shami","SRH",9.0,"BOWL"),
    ("s12","Simarjeet Singh","SRH",6.0,"BOWL"),
    ("s13","Harshal Patel","SRH",7.5,"BOWL"),
    ("s14","Sachin Baby","SRH",5.5,"BAT"),
    ("s15","Kamindu Mendis","SRH",7.5,"BAT"),
    ("s16","Harsh Dubey","SRH",7.0,"BOWL"),
    ("s17","Jaydev Unadkat SRH","SRH",7.5,"BOWL"),
    ("s18","Jaydev Unadkat","SRH",7.5,"BOWL"),
    ("s19","Brydon Carse","SRH",7.0,"BOWL"),
    ("s20","Eshan Malinga","SRH",6.5,"BOWL"),
    ("s21","Rahul Chahar","SRH",7.0,"BOWL"),
    ("s22","Aiden Markram","SRH",8.0,"BAT"),
    # ── MI ──
    ("m01","Rohit Sharma","MI",9.0,"BAT"),
    ("m02","Suryakumar Yadav","MI",9.5,"BAT"),
    ("m03","Tilak Varma","MI",8.5,"BAT"),
    ("m04","Ryan Rickelton","MI",8.5,"WK"),
    ("m05","Naman Dhir","MI",6.5,"AR"),
    ("m06","Will Jacks","MI",7.5,"AR"),
    ("m07","Ryan Rutherford","MI",7.0,"BAT"),
    ("m08","Trent Boult","MI",8.5,"BOWL"),
    ("m09","Hardik Pandya","MI",9.0,"AR"),
    ("m10","Jasprit Bumrah","MI",10.0,"BOWL"),
    ("m11","Marco Jansen","MI",8.5,"AR"),
    ("m12","Deepak Chahar","MI",7.5,"BOWL"),
    ("m13","Robin Minz","MI",6.0,"WK"),
    ("m14","Vignesh Puthur","MI",5.5,"BOWL"),
    ("m15","Arjun Tendulkar","MI",6.5,"BOWL"),
    ("m16","Reece Topley","MI",7.0,"BOWL"),
    ("m17","Bevon Jacobs","MI",6.0,"BAT"),
    ("m18","Karn Sharma","MI",6.5,"BOWL"),
    ("m19","Raj Angad Bawa","MI",6.0,"AR"),
    ("m20","Dewald Brevis","MI",8.0,"BAT"),
    ("m21","Mujeeb ur Rahman","MI",7.5,"BOWL"),
    ("m22","Mitchell Santner","MI",7.5,"AR"),
    # ── KKR ──
    ("k01","Ajinkya Rahane","KKR",8.0,"BAT"),
    ("k02","Quinton de Kock","KKR",9.0,"WK"),
    ("k03","Angkrish Raghuvanshi","KKR",7.5,"BAT"),
    ("k04","Rinku Singh","KKR",8.5,"BAT"),
    ("k05","Venkatesh Iyer","KKR",8.5,"AR"),
    ("k06","Andre Russell","KKR",9.0,"AR"),
    ("k07","Sunil Narine","KKR",9.0,"AR"),
    ("k08","Moeen Ali","KKR",8.0,"AR"),
    ("k09","Spencer Johnson","KKR",7.5,"BOWL"),
    ("k10","Harshit Rana","KKR",7.5,"BOWL"),
    ("k11","Rachin Ravindra","KKR",8.5,"BAT"),
    ("k12","Anrich Nortje","KKR",8.5,"BOWL"),
    ("k13","Mitchell Starc","KKR",9.0,"BOWL"),
    ("k14","Nitish Rana","KKR",8.0,"BAT"),
    ("k15","Manish Pandey","KKR",7.5,"BAT"),
    ("k16","Varun Chakravarthy","KKR",8.5,"BOWL"),
    ("k17","Rovman Powell","KKR",8.0,"BAT"),
    ("k18","Rahmanullah Gurbaz","KKR",8.5,"WK"),
    ("k19","Luvnith Sisodia","KKR",6.0,"WK"),
    ("k20","Suyash Sharma KKR","KKR",6.5,"BOWL"),
    ("k21","Mayank Markande","KKR",6.5,"BOWL"),
    ("k22","Chetan Sakariya","KKR",6.5,"BOWL"),
    # ── CSK ──
    ("c01","Ruturaj Gaikwad","CSK",9.0,"BAT"),
    ("c02","Rahul Tripathi","CSK",7.5,"BAT"),
    ("c03","Devon Conway","CSK",8.5,"WK"),
    ("c04","Ravindra Jadeja","CSK",9.0,"AR"),
    ("c05","Shivam Dube","CSK",8.5,"AR"),
    ("c06","Shardul Thakur","CSK",7.5,"AR"),
    ("c07","Moeen Ali CSK","CSK",7.5,"AR"),
    ("c08","Matheesha Pathirana","CSK",8.0,"BOWL"),
    ("c09","Deepak Chahar CSK","CSK",7.5,"BOWL"),
    ("c10","Tushar Deshpande","CSK",7.0,"BOWL"),
    ("c11","Noor Ahmad","CSK",7.5,"BOWL"),
    ("c12","Mukesh Choudhary","CSK",6.5,"BOWL"),
    ("c13","Simarjeet Singh CSK","CSK",6.0,"BOWL"),
    ("c14","Prashant Solanki","CSK",6.0,"AR"),
    ("c15","Vijay Shankar","CSK",7.0,"AR"),
    ("c16","Rilee Rossouw","CSK",8.0,"BAT"),
    ("c17","Sameer Rizvi","CSK",7.0,"BAT"),
    ("c18","MS Dhoni","CSK",8.5,"WK"),
    ("c19","Jamie Overton","CSK",7.5,"AR"),
    ("c20","Anshul Kamboj","CSK",6.5,"BOWL"),
    ("c21","Khaleel Ahmed","CSK",7.5,"BOWL"),
    ("c22","Ajay Mandal","CSK",5.5,"AR"),
    # ── RR ──
    ("rr01","Yashasvi Jaiswal","RR",9.5,"BAT"),
    ("rr02","Jos Buttler","RR",9.5,"WK"),
    ("rr03","Sanju Samson","RR",9.5,"WK"),
    ("rr04","Riyan Parag","RR",8.5,"BAT"),
    ("rr05","Shimron Hetmyer","RR",8.5,"BAT"),
    ("rr06","Dhruv Jurel","RR",7.5,"WK"),
    ("rr07","Rovman Powell RR","RR",8.0,"BAT"),
    ("rr08","Yuzvendra Chahal","RR",8.0,"BOWL"),
    ("rr09","Jofra Archer","RR",9.0,"BOWL"),
    ("rr10","Trent Boult RR","RR",8.0,"BOWL"),
    ("rr11","Sandeep Sharma","RR",7.0,"BOWL"),
    ("rr12","Ravichandran Ashwin","RR",8.5,"BOWL"),
    ("rr13","Maheesh Theekshana","RR",7.5,"BOWL"),
    ("rr14","Kuldeep Sen","RR",6.5,"BOWL"),
    ("rr15","Vaibhav Suryavanshi","RR",9.0,"BAT"),
    ("rr16","Nitish Kumar Reddy","RR",7.5,"AR"),
    ("rr17","Akash Madhwal","RR",7.0,"BOWL"),
    ("rr18","Tom Kohler-Cadmore","RR",7.0,"BAT"),
    ("rr19","Kunal Rathore","RR",6.0,"WK"),
    ("rr20","Adam Zampa RR","RR",7.0,"BOWL"),
    ("rr21","Fazalhaq Farooqi","RR",7.5,"BOWL"),
    ("rr22","Shubham Dubey","RR",6.5,"BAT"),
    # ── DC ──
    ("d01","Rishabh Pant","DC",10.0,"WK"),
    ("d02","David Warner","DC",9.0,"BAT"),
    ("d03","Mitchell Marsh","DC",8.5,"AR"),
    ("d04","Axar Patel","DC",8.5,"AR"),
    ("d05","Tristan Stubbs","DC",7.5,"BAT"),
    ("d06","Abishek Porel","DC",7.0,"WK"),
    ("d07","Harry Brook","DC",8.5,"BAT"),
    ("d08","Anrich Nortje DC","DC",8.0,"BOWL"),
    ("d09","Mukesh Kumar","DC",7.0,"BOWL"),
    ("d10","Kuldeep Yadav","DC",8.5,"BOWL"),
    ("d11","Ishant Sharma","DC",7.0,"BOWL"),
    ("d12","Lungi Ngidi DC","DC",7.5,"BOWL"),
    ("d13","Vipraj Nigam","DC",6.0,"BOWL"),
    ("d14","Sameer Rizvi DC","DC",6.5,"BAT"),
    ("d15","Jake Fraser-McGurk","DC",8.0,"BAT"),
    ("d16","Faf du Plessis","DC",8.5,"BAT"),
    ("d17","T Natarajan","DC",7.5,"BOWL"),
    ("d18","Karun Nair","DC",7.5,"BAT"),
    ("d19","Swastik Chikara DC","DC",5.5,"BOWL"),
    ("d20","Darshan Nalkande","DC",6.0,"BOWL"),
    ("d21","Ashutosh Sharma","DC",7.0,"AR"),
    ("d22","KL Rahul","DC",9.0,"WK"),
    # ── PBKS ──
    ("p01","Prabhsimran Singh","PBKS",8.5,"WK"),
    ("p02","Shashank Singh","PBKS",8.0,"BAT"),
    ("p03","Rilee Rossouw PBKS","PBKS",8.0,"BAT"),
    ("p04","Jonny Bairstow","PBKS",9.0,"WK"),
    ("p05","Glenn Maxwell","PBKS",9.0,"AR"),
    ("p06","Sam Curran","PBKS",8.5,"AR"),
    ("p07","Arshdeep Singh","PBKS",8.5,"BOWL"),
    ("p08","Kagiso Rabada","PBKS",9.0,"BOWL"),
    ("p09","Harshal Patel PBKS","PBKS",7.5,"BOWL"),
    ("p10","Rahul Chahar PBKS","PBKS",7.0,"BOWL"),
    ("p11","Shreyas Iyer","PBKS",8.5,"BAT"),
    ("p12","Liam Livingstone PBKS","PBKS",8.0,"AR"),
    ("p13","Josh Inglis","PBKS",7.5,"WK"),
    ("p14","Azmatullah Omarzai","PBKS",7.5,"AR"),
    ("p15","Marcus Stoinis","PBKS",8.5,"AR"),
    ("p16","Harpreet Brar","PBKS",7.0,"AR"),
    ("p17","Vishwanath Shankar","PBKS",6.5,"AR"),
    ("p18","Suryansh Shedge","PBKS",6.0,"AR"),
    ("p19","Nehal Wadhera","PBKS",7.0,"BAT"),
    ("p20","Musheer Khan","PBKS",7.0,"BAT"),
    ("p21","Lockie Ferguson","PBKS",8.0,"BOWL"),
    ("p22","Pravin Dubey","PBKS",6.0,"BOWL"),
    # ── LSG ──
    ("l01","KL Rahul LSG","LSG",9.0,"WK"),
    ("l02","Quinton de Kock LSG","LSG",8.5,"WK"),
    ("l03","Nicholas Pooran","LSG",9.0,"WK"),
    ("l04","Deepak Hooda","LSG",7.5,"AR"),
    ("l05","Krunal Pandya LSG","LSG",7.5,"AR"),
    ("l06","Marcus Stoinis LSG","LSG",8.5,"AR"),
    ("l07","Ayush Badoni","LSG",7.5,"BAT"),
    ("l08","Ravi Bishnoi","LSG",8.0,"BOWL"),
    ("l09","Avesh Khan","LSG",7.5,"BOWL"),
    ("l10","Mohsin Khan","LSG",7.0,"BOWL"),
    ("l11","Mark Wood","LSG",8.5,"BOWL"),
    ("l12","Lungi Ngidi LSG","LSG",7.5,"BOWL"),
    ("l13","Shamar Joseph","LSG",7.5,"BOWL"),
    ("l14","Matt Henry","LSG",7.5,"BOWL"),
    ("l15","Mayank Yadav","LSG",8.0,"BOWL"),
    ("l16","Yash Thakur","LSG",6.5,"BOWL"),
    ("l17","Himmat Singh","LSG",6.0,"BAT"),
    ("l18","M Siddharth","LSG",6.0,"BOWL"),
    ("l19","Manimaran Siddharth","LSG",6.0,"BOWL"),
    ("l20","Aryan Juyal","LSG",5.5,"WK"),
    ("l21","Aiden Markram LSG","LSG",7.5,"BAT"),
    ("l22","David Miller","LSG",8.5,"BAT"),
    # ── GT ──
    ("g01","Shubman Gill","GT",9.5,"BAT"),
    ("g02","Wriddhiman Saha","GT",7.0,"WK"),
    ("g03","Abhinav Manohar","GT",7.5,"BAT"),
    ("g04","David Miller GT","GT",8.5,"BAT"),
    ("g05","Vijay Shankar GT","GT",7.0,"AR"),
    ("g06","Hardik Pandya GT","GT",8.5,"AR"),
    ("g07","Rahul Tewatia","GT",8.0,"AR"),
    ("g08","Rashid Khan","GT",9.0,"BOWL"),
    ("g09","Mohammed Siraj","GT",8.5,"BOWL"),
    ("g10","Alzarri Joseph","GT",8.0,"BOWL"),
    ("g11","Noor Ahmad","GT",7.5,"BOWL"),
    ("g12","Darshan Nalkande GT","GT",6.0,"BOWL"),
    ("g13","Arshad Khan","GT",6.5,"BOWL"),
    ("g14","Sai Kishore","GT",7.0,"BOWL"),
    ("g15","Jayant Yadav","GT",7.0,"AR"),
    ("g16","B Sai Sudharsan","GT",8.5,"BAT"),
    ("g17","Shahrukh Khan","GT",7.5,"BAT"),
    ("g18","Azmatullah Omarzai GT","GT",7.5,"AR"),
    ("g19","Manav Suthar","GT",6.0,"BOWL"),
    ("g20","Sanvir Singh","GT",6.0,"AR"),
    ("g21","Kartik Tyagi","GT",6.5,"BOWL"),
    ("g22","Jos Buttler GT","GT",9.0,"WK"),
]


# ═══════════════════════════════════════════════════════════════════════════════
# CANONICAL SEED TEAMS  (x-aliases resolved to real player IDs)
# ═══════════════════════════════════════════════════════════════════════════════

# --- W0 Pre-season ---
SAI_W0 = {
    "team": ["k16","m12","r20","m09","s13","m01","r01","k01","m03","s03","r04"],
    "cap": "r01", "vc": "k16",
}
MOE_W0 = {
    "team": ["k16","m09","k11","r08","r09","m07","r02","m03","s03","r04","s04"],
    "cap": "r04", "vc": "k16",
}

# --- W1 First live week ---
SAI_W1 = {
    "team": ["l12","rr08","g11","c05","g08","rr15","rr05","s22","rr03","p01","s03"],
    "cap": "rr03", "vc": "rr15",
}
MOE_W1 = {
    "team": ["m10","r15","k14","r09","m11","m20","rr05","m01","m04","s03","d01"],
    "cap": "d01", "vc": "s03",
}

HISTORY_SEED = [
    ("Sai", 0, SAI_W0),
    ("Moe", 0, MOE_W0),
    ("Sai", 1, SAI_W1),
    ("Moe", 1, MOE_W1),
]


# ═══════════════════════════════════════════════════════════════════════════════
# m20/m12 FIX FOR MATCH SCORECARDS
# ═══════════════════════════════════════════════════════════════════════════════
# The original Seed_ipl2026.py used m20 for "Deepak Chahar" but the canonical
# EMBEDDED_PLAYERS list uses m12 for Chahar and m20 for "Dewald Brevis".
# This function patches existing match_scores to fix the ID.

def fix_chahar_id_conflict(con):
    """Rename player_id m20 → m12 in match_scores and player_match_points
    where the data actually represents Deepak Chahar's bowling stats."""
    # Check if m20 rows exist with bowling stats (Chahar's signature)
    rows = con.execute(
        "SELECT match_id FROM match_scores WHERE player_id='m20' AND wickets > 0"
    ).fetchall()
    if not rows:
        # Also check for m20 with overs > 0 (Chahar bowled but took 0 wickets)
        rows = con.execute(
            "SELECT match_id FROM match_scores WHERE player_id='m20' AND overs > 0"
        ).fetchall()

    if rows:
        print(f"  ⚠  Fixing m20→m12 conflict in {len(rows)} match_scores rows (Chahar)")
        # Delete any existing m12 rows first to avoid PK conflict
        con.execute("DELETE FROM match_scores WHERE player_id='m12'")
        con.execute("DELETE FROM player_match_points WHERE player_id='m12'")
        # Rename m20 → m12
        con.execute("UPDATE match_scores SET player_id='m12' WHERE player_id='m20'")
        con.execute("UPDATE player_match_points SET player_id='m12' WHERE player_id='m20'")
    else:
        print("  ✓  No m20/m12 conflict found (already fixed or no Chahar data)")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN REPAIR
# ═══════════════════════════════════════════════════════════════════════════════

def run_repair(db_path: str) -> None:
    print(f"\n{'='*60}")
    print(f"  IPL Fantasy 2026 — Database Repair")
    print(f"  {db_path}")
    print(f"{'='*60}\n")

    if not Path(db_path).exists():
        print(f"  ERROR: database not found at {db_path}")
        print(f"  Start server.py once to create the schema, then re-run.")
        sys.exit(1)

    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")

    # ── Step 1: Seed complete player roster ─────────────────────────────────
    print("  Step 1: Seeding complete player roster...")
    con.executemany(
        "INSERT OR REPLACE INTO players (id, name, team, price, role) VALUES (?,?,?,?,?)",
        ALL_PLAYERS,
    )
    con.commit()
    cnt = con.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    print(f"  ✓  Players table: {cnt} rows (was 27, now {len(ALL_PLAYERS)})")

    # ── Step 2: Fix m20/m12 Chahar ID conflict ──────────────────────────────
    print("\n  Step 2: Checking m20/m12 Chahar ID conflict...")
    fix_chahar_id_conflict(con)
    con.commit()

    # ── Step 3: Rewrite user_selections with canonical IDs ──────────────────
    print("\n  Step 3: Rewriting user_selections with canonical player IDs...")

    # Check what currently exists
    existing = con.execute(
        "SELECT display_name, week_no, tw_team_json FROM user_selections ORDER BY display_name, week_no"
    ).fetchall()
    if existing:
        print(f"         Found {len(existing)} existing rows")
        for row in existing:
            team = json.loads(row["tw_team_json"] or "[]")
            aliases = [pid for pid in team if pid.startswith("x")]
            if aliases:
                print(f"         {row['display_name']}/W{row['week_no']}: {len(aliases)} x-alias IDs → resolving")

    # Delete and re-insert with canonical IDs
    con.execute("DELETE FROM user_selections")
    now_iso = datetime.now(timezone.utc).isoformat()

    for name, week_no, picks in HISTORY_SEED:
        team_json = json.dumps(picks["team"])
        cap = picks["cap"]
        vc  = picks["vc"]
        con.execute("""
            INSERT INTO user_selections
                (display_name, week_no,
                 tw_team_json, tw_cap_id, tw_vc_id,
                 nw_team_json, nw_cap_id, nw_vc_id)
            VALUES (?,?,?,?,?,?,?,?)
        """, (name, week_no, team_json, cap, vc, team_json, cap, vc))
        print(f"  ✓  {name}/W{week_no}: {len(picks['team'])} players, C={cap}, VC={vc}")

    con.execute(
        "INSERT OR REPLACE INTO meta (key,value) VALUES ('_saved',?)",
        (now_iso,),
    )
    con.commit()

    # ── Step 4: Recalculate player_match_points ─────────────────────────────
    print("\n  Step 4: Recalculating player_match_points...")

    con.execute("DELETE FROM player_match_points")
    rows = con.execute("""
        SELECT ms.match_id, ms.player_id, m.week_no, ms.raw_score_json
        FROM   match_scores ms
        JOIN   matches m ON m.id = ms.match_id
    """).fetchall()

    pmp_count = 0
    for row in rows:
        sc = json.loads(row["raw_score_json"] or "{}")
        pts = calc_pts(sc)
        con.execute("""
            INSERT INTO player_match_points
                (match_id, player_id, week_no, base_pts, multiplier, final_pts, calculated_at)
            VALUES (?,?,?,?,1.0,?,?)
            ON CONFLICT(match_id, player_id) DO UPDATE SET
                week_no=excluded.week_no, base_pts=excluded.base_pts,
                final_pts=excluded.final_pts, calculated_at=excluded.calculated_at
        """, (row["match_id"], row["player_id"], row["week_no"],
              pts, float(pts), now_iso))
        pmp_count += 1

    con.commit()
    active = con.execute(
        "SELECT COUNT(*) FROM player_match_points WHERE base_pts > 0"
    ).fetchone()[0]
    print(f"  ✓  player_match_points: {pmp_count} rows ({active} with pts > 0)")

    # ── Step 5: Verify leaderboard ──────────────────────────────────────────
    print("\n  Step 5: Verifying leaderboard...")

    # Check overlap between user picks and scored players
    for name, week_no, picks in HISTORY_SEED:
        if week_no == 0:
            continue  # W0 is pre-season, no matches
        team = picks["team"]
        cap  = picks["cap"]
        vc   = picks["vc"]
        total = 0
        details = []
        for pid in team:
            # Sum base_pts across all matches for this week
            row = con.execute("""
                SELECT COALESCE(SUM(base_pts), 0) AS pts
                FROM player_match_points
                WHERE player_id = ? AND week_no = ?
            """, (pid, week_no)).fetchone()
            base = row["pts"]
            if pid == cap:
                awarded = round(base * 2.0)
                tag = " (C×2)"
            elif pid == vc:
                awarded = round(base * 1.5)
                tag = " (VC×1.5)"
            else:
                awarded = base
                tag = ""
            if awarded > 0:
                pname = con.execute(
                    "SELECT name FROM players WHERE id=?", (pid,)
                ).fetchone()
                pname = pname["name"] if pname else pid
                details.append(f"    {pname} ({pid}): {awarded} pts{tag}")
            total += awarded

        print(f"  {name} W{week_no}: {total} total pts")
        for d in details:
            print(d)

    # ── Step 6: Run the actual leaderboard SQL ──────────────────────────────
    print("\n  Step 6: Running leaderboard query...")
    try:
        lb_rows = con.execute("""
            WITH
            current_picks AS (
                SELECT us.display_name, je.value AS player_id,
                       us.tw_cap_id AS cap_id, us.tw_vc_id AS vc_id
                FROM user_selections us, JSON_EACH(us.tw_team_json) AS je
                WHERE us.week_no = (
                    SELECT MAX(week_no) FROM user_selections u2
                    WHERE u2.display_name = us.display_name
                )
            ),
            scored_points AS (
                SELECT cp.display_name, pmp.match_id, cp.player_id,
                    CASE
                        WHEN cp.player_id = cp.cap_id THEN ROUND(pmp.base_pts * 2.0)
                        WHEN cp.player_id = cp.vc_id  THEN ROUND(pmp.base_pts * 1.5)
                        ELSE pmp.base_pts
                    END AS awarded_pts
                FROM current_picks cp
                INNER JOIN player_match_points pmp ON pmp.player_id = cp.player_id
            ),
            user_totals AS (
                SELECT display_name, SUM(awarded_pts) AS total_pts,
                       COUNT(DISTINCT match_id) AS matches_counted
                FROM scored_points
                GROUP BY display_name
            )
            SELECT display_name, total_pts, matches_counted
            FROM user_totals
            ORDER BY total_pts DESC
        """).fetchall()

        if lb_rows:
            print(f"  ✓  Leaderboard has {len(lb_rows)} entries:")
            for row in lb_rows:
                print(f"     {row['display_name']}: {row['total_pts']} pts "
                      f"({row['matches_counted']} matches)")
        else:
            print("  ⚠  Leaderboard still empty — checking diagnostics...")
            # Diagnostics
            picks = con.execute("""
                SELECT display_name, je.value AS pid
                FROM user_selections us, JSON_EACH(us.tw_team_json) AS je
                WHERE us.week_no = (
                    SELECT MAX(week_no) FROM user_selections u2
                    WHERE u2.display_name = us.display_name
                )
            """).fetchall()
            pick_ids = set(r["pid"] for r in picks)
            pmp_ids  = set(r[0] for r in con.execute(
                "SELECT DISTINCT player_id FROM player_match_points WHERE base_pts > 0"
            ).fetchall())
            overlap = pick_ids & pmp_ids
            print(f"     Pick IDs: {len(pick_ids)}")
            print(f"     Scored player IDs (pts>0): {len(pmp_ids)}")
            print(f"     Overlap: {len(overlap)} → {overlap}")

    except Exception as e:
        print(f"  ✗  Leaderboard query failed: {e}")

    # ── Summary ─────────────────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("  Table counts:")
    for tbl in ("players","matches","match_scores","player_match_points","user_selections","meta"):
        n = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"    {tbl:<30} {n}")

    con.close()

    print(f"\n  ✓  REPAIR COMPLETE")
    print(f"     Restart server.py to pick up changes.")
    print(f"     Visit /api/leaderboard to verify.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Rebuild IPL Fantasy 2026 database")
    p.add_argument("--db", default=str(DEFAULT_DB), help="Path to fantasy.db")
    args = p.parse_args()
    run_repair(args.db)
