#!/usr/bin/env python3
"""
IPL Fantasy 2026 — Match Schedule Seeder (Cricbuzz)
===================================================
Fetches the IPL 2026 match schedule from Cricbuzz and populates the
matches table with Cricbuzz scorecard URLs.

Usage:
    python Seed_Matches.py                      # Use default series ID
    python Seed_Matches.py --series-id 9237      # Override series ID
    python Seed_Matches.py --completed 15        # Mark first 15 as completed

The series ID can be found in Cricbuzz URLs like:
    cricbuzz.com/cricket-series/XXXX/indian-premier-league-2026/matches
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
    print("ERROR: 'requests' package required. Run: pip install requests")
    sys.exit(1)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "fantasy.db"

# ── Default Cricbuzz series ID for IPL 2026 ──────────────────────────────────
# Update this when the IPL 2026 series page becomes available on Cricbuzz.
# Find it at: cricbuzz.com → IPL 2026 → Matches tab → check the URL
DEFAULT_SERIES_ID = "9237"  # Placeholder — update with real IPL 2026 ID

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
}


def fetch_series_matches(series_id: str) -> list[dict]:
    """
    Attempt to fetch match list from Cricbuzz series page.
    Returns list of {match_no, cb_match_id, title, date, venue}.
    """
    url = f"https://www.cricbuzz.com/cricket-series/{series_id}/indian-premier-league-2026/matches"
    print(f"  Fetching: {url}")

    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"  HTTP {r.status_code} — series page not available yet")
            return []

        # Extract match IDs from href patterns like /cricket-scores/XXXXX/...
        matches = []
        pattern = re.compile(r'/cricket-scores/(\d+)/([^"]+)')
        seen_ids = set()

        for m in pattern.finditer(r.text):
            cb_id = m.group(1)
            slug = m.group(2)
            if cb_id in seen_ids:
                continue
            seen_ids.add(cb_id)
            # Extract match info from slug
            title = slug.replace("-", " ").title()
            matches.append({
                "cb_match_id": cb_id,
                "title": title,
                "slug": slug,
            })

        print(f"  Found {len(matches)} matches from series page")
        return matches

    except requests.RequestException as e:
        print(f"  Network error: {e}")
        return []


def seed_from_manual_ids(completed_count: int = 12) -> None:
    """
    Fallback seeder using manually configured Cricbuzz match IDs.
    When the IPL 2026 series page is not yet available, this seeds
    placeholder entries with the URL pattern ready for Cricbuzz IDs.

    Update the CB_MATCH_IDS list as matches are scheduled on Cricbuzz.
    """
    # ── CONFIGURATION ────────────────────────────────────────────────────
    # Replace these with real Cricbuzz match IDs as they become available.
    # Format: (match_number, cricbuzz_match_id, "Team1 vs Team2, Match N")
    # You can find IDs from: cricbuzz.com/live-cricket-scorecard/XXXXX
    CB_MATCH_IDS = [
        # Example entries — replace with real IPL 2026 Cricbuzz IDs:
        # (1,  "98765", "CSK vs MI, 1st Match"),
        # (2,  "98770", "RCB vs KKR, 2nd Match"),
    ]

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    if CB_MATCH_IDS:
        print(f"  Seeding {len(CB_MATCH_IDS)} matches with Cricbuzz IDs...")
        for match_no, cb_id, title in CB_MATCH_IDS:
            status = "completed" if match_no <= completed_count else "upcoming"
            cursor.execute("""
                INSERT OR REPLACE INTO matches (id, week_no, title, status, scorecard_url, teams_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                f"ipl26_m{match_no:02d}",
                ((match_no - 1) // 7) + 1,
                title,
                status,
                f"https://www.cricbuzz.com/live-cricket-scorecard/{cb_id}",
                json.dumps([]),
            ))
    else:
        # Placeholder seed — 74 matches with empty Cricbuzz URLs
        print("  No Cricbuzz IDs configured yet — seeding placeholder schedule...")
        print("  ⚠ Update CB_MATCH_IDS in Seed_Matches.py with real Cricbuzz IDs")
        for i in range(1, 75):
            status = "completed" if i <= completed_count else "upcoming"
            cursor.execute("""
                INSERT OR REPLACE INTO matches (id, week_no, title, status, scorecard_url, teams_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                f"ipl26_m{i:02d}",
                ((i - 1) // 7) + 1,
                f"Match {i}",
                status,
                f"https://www.cricbuzz.com/live-cricket-scorecard/0",  # placeholder
                json.dumps([]),
            ))

    conn.commit()
    conn.close()
    print("✅ Match schedule seeded.")


def seed_from_series(series_id: str, completed_count: int = 12) -> None:
    """
    Fetch matches from Cricbuzz series page and seed into DB.
    Falls back to manual seeding if series page is unavailable.
    """
    matches = fetch_series_matches(series_id)

    if not matches:
        print("  Falling back to manual seed...")
        seed_from_manual_ids(completed_count)
        return

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    for i, m in enumerate(matches, 1):
        status = "completed" if i <= completed_count else "upcoming"
        cursor.execute("""
            INSERT OR REPLACE INTO matches (id, week_no, title, status, scorecard_url, teams_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            f"ipl26_m{i:02d}",
            ((i - 1) // 7) + 1,
            m.get("title", f"Match {i}"),
            status,
            f"https://www.cricbuzz.com/live-cricket-scorecard/{m['cb_match_id']}",
            json.dumps([]),
        ))

    conn.commit()
    conn.close()
    print(f"✅ Seeded {len(matches)} matches from Cricbuzz series {series_id}.")


def main():
    parser = argparse.ArgumentParser(description="Seed IPL 2026 match schedule from Cricbuzz")
    parser.add_argument("--series-id", default=DEFAULT_SERIES_ID, help="Cricbuzz series ID")
    parser.add_argument("--completed", type=int, default=12, help="Number of matches to mark completed")
    args = parser.parse_args()

    print("\n--- IPL 2026 Match Seeder (Cricbuzz) ---")
    (BASE_DIR / "data").mkdir(exist_ok=True)
    seed_from_series(args.series_id, args.completed)


if __name__ == "__main__":
    main()
