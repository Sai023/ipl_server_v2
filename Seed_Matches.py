#!/usr/bin/env python3
"""
IPL Fantasy 2026 \u2014 Match Schedule Seeder (Cricbuzz)
===================================================
Seeds the matches table with Cricbuzz scorecard URLs.

Usage:
    python Seed_Matches.py                      # Use default series ID
    python Seed_Matches.py --series-id 9237
    python Seed_Matches.py --completed 15
"""

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' required. pip install requests")
    sys.exit(1)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH  = BASE_DIR / "data" / "fantasy.db"
DEFAULT_SERIES_ID = "9237"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def fetch_series_matches(series_id: str) -> list:
    url = f"https://www.cricbuzz.com/cricket-series/{series_id}/indian-premier-league-2026/matches"
    print(f"  Fetching: {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}"); return []
        matches = []; seen = set()
        for m in re.finditer(r'/cricket-scores/(\d+)/([^"]+)', r.text):
            cid = m.group(1)
            if cid in seen: continue
            seen.add(cid)
            matches.append({"cb_match_id": cid, "title": m.group(2).replace("-"," ").title()})
        print(f"  Found {len(matches)} matches")
        return matches
    except requests.RequestException as e:
        print(f"  Error: {e}"); return []

def seed_manual(completed: int = 12):
    # Replace entries below with real Cricbuzz match IDs as they become available
    CB_MATCH_IDS = [
        # (match_no, cricbuzz_id, "Team1 vs Team2, Match N")
    ]
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    if CB_MATCH_IDS:
        for no, cid, title in CB_MATCH_IDS:
            st = "completed" if no <= completed else "upcoming"
            conn.execute("INSERT OR REPLACE INTO matches (id,week_no,title,status,scorecard_url,teams_json) VALUES (?,?,?,?,?,?)",
                (f"ipl26_m{no:02d}", ((no-1)//7)+1, title, st,
                 f"https://www.cricbuzz.com/live-cricket-scorecard/{cid}", "[]"))
    else:
        print("  No IDs configured \u2014 seeding 74 placeholder matches")
        print("  \u26a0 Update CB_MATCH_IDS in Seed_Matches.py with real Cricbuzz IDs")
        for i in range(1, 75):
            st = "completed" if i <= completed else "upcoming"
            # DEF-003 FIX: Use 4+ digit placeholder so regex \d{4,} won't silently skip,
            # but scraper also validates > 0, so these will be explicitly skipped with a message
            conn.execute("INSERT OR REPLACE INTO matches (id,week_no,title,status,scorecard_url,teams_json) VALUES (?,?,?,?,?,?)",
                (f"ipl26_m{i:02d}", ((i-1)//7)+1, f"Match {i}", st,
                 f"https://www.cricbuzz.com/live-cricket-scorecard/00000", "[]"))
    conn.commit(); conn.close()
    print("\u2705 Match schedule seeded.")

def seed_from_series(series_id: str, completed: int = 12):
    matches = fetch_series_matches(series_id)
    if not matches:
        print("  Falling back to manual seed..."); seed_manual(completed); return
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    for i, m in enumerate(matches, 1):
        st = "completed" if i <= completed else "upcoming"
        conn.execute("INSERT OR REPLACE INTO matches (id,week_no,title,status,scorecard_url,teams_json) VALUES (?,?,?,?,?,?)",
            (f"ipl26_m{i:02d}", ((i-1)//7)+1, m.get("title",f"Match {i}"), st,
             f"https://www.cricbuzz.com/live-cricket-scorecard/{m['cb_match_id']}", "[]"))
    conn.commit(); conn.close()
    print(f"\u2705 Seeded {len(matches)} matches from series {series_id}.")

def main():
    p = argparse.ArgumentParser(description="Seed IPL 2026 schedule")
    p.add_argument("--series-id", default=DEFAULT_SERIES_ID)
    p.add_argument("--completed", type=int, default=12)
    args = p.parse_args()
    print("\n--- IPL 2026 Match Seeder (Cricbuzz) ---")
    (BASE_DIR / "data").mkdir(exist_ok=True)
    seed_from_series(args.series_id, args.completed)

if __name__ == "__main__":
    main()
