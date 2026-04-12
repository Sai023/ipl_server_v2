#!/usr/bin/env python3
"""
IPL Fantasy 2026 — Match Schedule Seeder  v3.0 (Fully Automated)
=================================================================
Multi-strategy Cricbuzz match ID discovery:

  Strategy 1: Cricbuzz Next.js embedded JSON (series page) — most robust
  Strategy 2: Cricbuzz series API endpoint (JSON)
  Strategy 3: HTML regex — FIXED pattern (/live-cricket-scores/ not /cricket-scores/)
  Strategy 4: Hardcoded IPL 2026 schedule (Match 1 ID 149618 confirmed)

Auto-computes 'completed' count from IST match times (no --completed flag needed).

Usage:
    python Seed_Matches.py                    # fully automatic
    python Seed_Matches.py --series-id 9237   # specify series
    python Seed_Matches.py --completed 15     # override auto-detection
    python Seed_Matches.py --force            # re-seed (updates statuses)
    python Seed_Matches.py --no-live          # skip Cricbuzz, use hardcoded only
    python Seed_Matches.py --debug            # verbose output
"""

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from random import choice

try:
    import requests
except ImportError:
    print("ERROR: 'requests' required.  pip install requests")
    sys.exit(1)

BASE_DIR          = Path(__file__).resolve().parent
DB_PATH           = BASE_DIR / "data" / "fantasy.db"
DEFAULT_SERIES_ID = "9237"
IST               = timezone(timedelta(hours=5, minutes=30))
MATCH_DURATION_HRS = 4   # hours after start before match is 'complete'

# Rotate User-Agents to reduce bot-detection failures
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


def _hdrs() -> dict:
    return {
        "User-Agent":      choice(_USER_AGENTS),
        "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer":         "https://www.cricbuzz.com/",
        "DNT":             "1",
        "Cache-Control":   "no-cache",
    }


# ══════════════════════════════════════════════════════════════════════════════
# IPL 2026 HARDCODED SCHEDULE  (74 matches)
# ══════════════════════════════════════════════════════════════════════════════
# Format: (match_no, cricbuzz_id | None, "Title", "YYYY-MM-DD", "HH:MM IST")
#
# cricbuzz_id = None  →  live-discovery will attempt to fill in
# CONFIRMED IDs sourced from live Cricbuzz scorecard URLs:
#   /live-cricket-scores/{ID}/slug
#
# HOW TO ADD NEW IDs as matches go live:
#   Cricbuzz URL:  /live-cricket-scores/149618/srh-vs-rcb-...
#                                        ^^^^^^ this is the ID
#   Update entry:  (1, "149618", ...
# ──────────────────────────────────────────────────────────────────────────────
IPL_2026_SCHEDULE = [
    # ── Week 1 (Mar 22-28) ──────────────────────────────────────────────────
    (1,  "149618", "SRH vs RCB, 1st Match",   "2026-03-22", "19:30"),
    (2,  None,     "CSK vs MI, 2nd Match",    "2026-03-23", "19:30"),
    (3,  None,     "DC vs RR, 3rd Match",     "2026-03-24", "19:30"),
    (4,  None,     "KKR vs PBKS, 4th Match",  "2026-03-25", "19:30"),
    (5,  None,     "GT vs LSG, 5th Match",    "2026-03-26", "19:30"),
    (6,  None,     "RCB vs CSK, 6th Match",   "2026-03-27", "19:30"),
    (7,  None,     "MI vs DC, 7th Match",     "2026-03-28", "19:30"),
    # ── Week 2 (Mar 29-Apr 4) ───────────────────────────────────────────────
    (8,  None,     "RR vs SRH, 8th Match",    "2026-03-29", "15:30"),
    (9,  None,     "PBKS vs GT, 9th Match",   "2026-03-29", "19:30"),
    (10, None,     "LSG vs KKR, 10th Match",  "2026-03-30", "19:30"),
    (11, None,     "MI vs RCB, 11th Match",   "2026-03-31", "19:30"),
    (12, None,     "CSK vs DC, 12th Match",   "2026-04-01", "19:30"),
    (13, None,     "SRH vs GT, 13th Match",   "2026-04-02", "19:30"),
    (14, None,     "KKR vs RR, 14th Match",   "2026-04-03", "19:30"),
    # ── Week 3 (Apr 4-11) ───────────────────────────────────────────────────
    (15, None,     "PBKS vs LSG, 15th Match", "2026-04-04", "15:30"),
    (16, None,     "RCB vs DC, 16th Match",   "2026-04-04", "19:30"),
    (17, None,     "MI vs SRH, 17th Match",   "2026-04-05", "15:30"),
    (18, None,     "GT vs KKR, 18th Match",   "2026-04-05", "19:30"),
    (19, None,     "RR vs CSK, 19th Match",   "2026-04-06", "19:30"),
    (20, None,     "LSG vs DC, 20th Match",   "2026-04-07", "19:30"),
    (21, None,     "PBKS vs SRH, 21st Match", "2026-04-08", "19:30"),
    # ── Week 4 (Apr 9-15) ───────────────────────────────────────────────────
    (22, None,     "RCB vs GT, 22nd Match",   "2026-04-09", "19:30"),
    (23, None,     "CSK vs KKR, 23rd Match",  "2026-04-10", "19:30"),
    (24, None,     "MI vs RR, 24th Match",    "2026-04-11", "15:30"),
    (25, None,     "DC vs SRH, 25th Match",   "2026-04-11", "19:30"),
    (26, None,     "LSG vs RCB, 26th Match",  "2026-04-12", "15:30"),
    (27, None,     "PBKS vs CSK, 27th Match", "2026-04-12", "19:30"),
    (28, None,     "GT vs RR, 28th Match",    "2026-04-13", "19:30"),
    # ── Week 5 (Apr 14-20) ──────────────────────────────────────────────────
    (29, None,     "KKR vs MI, 29th Match",   "2026-04-14", "19:30"),
    (30, None,     "SRH vs LSG, 30th Match",  "2026-04-15", "19:30"),
    (31, None,     "DC vs GT, 31st Match",    "2026-04-16", "19:30"),
    (32, None,     "RR vs PBKS, 32nd Match",  "2026-04-17", "19:30"),
    (33, None,     "RCB vs KKR, 33rd Match",  "2026-04-18", "15:30"),
    (34, None,     "MI vs CSK, 34th Match",   "2026-04-18", "19:30"),
    (35, None,     "LSG vs SRH, 35th Match",  "2026-04-19", "15:30"),
    # ── Week 6 (Apr 19-26) ──────────────────────────────────────────────────
    (36, None,     "GT vs DC, 36th Match",    "2026-04-19", "19:30"),
    (37, None,     "PBKS vs RR, 37th Match",  "2026-04-20", "19:30"),
    (38, None,     "KKR vs RCB, 38th Match",  "2026-04-21", "19:30"),
    (39, None,     "CSK vs SRH, 39th Match",  "2026-04-22", "19:30"),
    (40, None,     "MI vs GT, 40th Match",    "2026-04-23", "19:30"),
    (41, None,     "DC vs PBKS, 41st Match",  "2026-04-24", "19:30"),
    (42, None,     "RR vs LSG, 42nd Match",   "2026-04-25", "15:30"),
    (43, None,     "SRH vs KKR, 43rd Match",  "2026-04-25", "19:30"),
    # ── Week 7 (Apr 26-May 2) ───────────────────────────────────────────────
    (44, None,     "RCB vs CSK, 44th Match",  "2026-04-26", "15:30"),
    (45, None,     "GT vs MI, 45th Match",    "2026-04-26", "19:30"),
    (46, None,     "PBKS vs DC, 46th Match",  "2026-04-27", "19:30"),
    (47, None,     "LSG vs RR, 47th Match",   "2026-04-28", "19:30"),
    (48, None,     "KKR vs SRH, 48th Match",  "2026-04-29", "19:30"),
    (49, None,     "MI vs PBKS, 49th Match",  "2026-04-30", "19:30"),
    (50, None,     "CSK vs GT, 50th Match",   "2026-05-01", "19:30"),
    # ── Week 8 (May 2-8) ────────────────────────────────────────────────────
    (51, None,     "DC vs KKR, 51st Match",   "2026-05-02", "15:30"),
    (52, None,     "RR vs RCB, 52nd Match",   "2026-05-02", "19:30"),
    (53, None,     "SRH vs CSK, 53rd Match",  "2026-05-03", "15:30"),
    (54, None,     "LSG vs MI, 54th Match",   "2026-05-03", "19:30"),
    (55, None,     "GT vs PBKS, 55th Match",  "2026-05-04", "19:30"),
    (56, None,     "RCB vs DC, 56th Match",   "2026-05-05", "19:30"),
    (57, None,     "KKR vs LSG, 57th Match",  "2026-05-06", "19:30"),
    (58, None,     "RR vs SRH, 58th Match",   "2026-05-07", "19:30"),
    # ── Week 9 (May 8-15) ───────────────────────────────────────────────────
    (59, None,     "MI vs CSK, 59th Match",   "2026-05-08", "19:30"),
    (60, None,     "DC vs GT, 60th Match",    "2026-05-09", "15:30"),
    (61, None,     "PBKS vs RCB, 61st Match", "2026-05-09", "19:30"),
    (62, None,     "SRH vs LSG, 62nd Match",  "2026-05-10", "15:30"),
    (63, None,     "KKR vs DC, 63rd Match",   "2026-05-10", "19:30"),
    (64, None,     "RR vs MI, 64th Match",    "2026-05-11", "19:30"),
    (65, None,     "CSK vs PBKS, 65th Match", "2026-05-12", "19:30"),
    (66, None,     "GT vs SRH, 66th Match",   "2026-05-13", "19:30"),
    (67, None,     "RCB vs LSG, 67th Match",  "2026-05-14", "19:30"),
    (68, None,     "MI vs KKR, 68th Match",   "2026-05-15", "19:30"),
    (69, None,     "RR vs DC, 69th Match",    "2026-05-16", "15:30"),
    (70, None,     "PBKS vs GT, 70th Match",  "2026-05-16", "19:30"),
    # ── Playoffs ────────────────────────────────────────────────────────────
    (71, None,     "Qualifier 1",             "2026-05-19", "19:30"),
    (72, None,     "Eliminator",              "2026-05-21", "19:30"),
    (73, None,     "Qualifier 2",             "2026-05-23", "19:30"),
    (74, None,     "Final",                   "2026-05-25", "19:30"),
]


# ── Build index for fast lookup ───────────────────────────────────────────────
_SCHEDULE_BY_NO = {no: (no, cid, title, date, t)
                   for no, cid, title, date, t in IPL_2026_SCHEDULE}


def _merge_discovered(discovered: list) -> list:
    """
    Merge live-discovered match IDs into the hardcoded schedule.
    Uses position-based matching (Cricbuzz returns matches chronologically).
    Hardcoded IDs always take precedence over discovered IDs.
    """
    result = []
    for idx, (no, cid, title, date, t) in enumerate(IPL_2026_SCHEDULE):
        if cid is None and idx < len(discovered):
            cid = discovered[idx]["cb_match_id"]
        result.append((no, cid, title, date, t))
    return result


def _auto_count_completed(schedule: list) -> int:
    """Count matches whose end time (start + MATCH_DURATION_HRS) has passed IST now."""
    now_ist = datetime.now(IST)
    count = 0
    for no, cid, title, date, time_ist in schedule:
        try:
            h, m   = map(int, time_ist.split(":"))
            y, mo, d = map(int, date.split("-"))
            start  = datetime(y, mo, d, h, m, tzinfo=IST)
            end    = start + timedelta(hours=MATCH_DURATION_HRS)
            if now_ist > end:
                count += 1
        except (ValueError, AttributeError):
            continue
    return count


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 1 — Next.js embedded JSON  (most robust against bot detection)
# ══════════════════════════════════════════════════════════════════════════════
def _scrape_nextjs_json(html: str, debug: bool = False) -> list:
    """Extract match IDs from Cricbuzz's Next.js server-side JSON blobs."""
    matches = []
    seen = set()
    # Cricbuzz Next.js pages embed data in self.__next_f.push([...]) blocks
    for block in re.findall(r'self\.__next_f\.push\(\[.*?\]\)', html, re.S):
        for m in re.finditer(r'/live-cricket-scores/(\d{5,})/([^"\\<>\s]+)', block):
            cid = m.group(1)
            if cid not in seen:
                seen.add(cid)
                title = m.group(2).replace("-", " ").title()
                matches.append({"cb_match_id": cid, "title": title})
    if debug:
        print(f"    [nextjs] blocks scanned, {len(matches)} unique IDs found")
    return matches


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 2 — Cricbuzz series API (JSON endpoint)
# ══════════════════════════════════════════════════════════════════════════════
def _fetch_series_api(series_id: str, debug: bool = False) -> list:
    api_urls = [
        f"https://www.cricbuzz.com/api/cricket-series/{series_id}/matches",
        f"https://www.cricbuzz.com/api/html/cricket-series/{series_id}/matches",
    ]
    for url in api_urls:
        try:
            if debug:
                print(f"    [api] Trying {url}")
            r = requests.get(url, headers=_hdrs(), timeout=15)
            if r.status_code == 200:
                text = r.text.strip()
                if text.startswith("{") or text.startswith("["):
                    data = r.json()
                    matches = []
                    seen = set()
                    items = (data if isinstance(data, list)
                             else data.get("matches",
                             data.get("matchDetails", [])))
                    for item in (items if isinstance(items, list) else []):
                        for key in ("id", "matchId", "match_id"):
                            cid = str(item.get(key, ""))
                            if cid and len(cid) >= 4 and cid not in seen:
                                seen.add(cid)
                                title = (item.get("matchDescription")
                                         or item.get("title")
                                         or f"Match {len(matches)+1}")
                                matches.append({"cb_match_id": cid, "title": title})
                                break
                    if matches:
                        if debug:
                            print(f"    [api] {len(matches)} matches from {url}")
                        return matches
        except Exception as e:
            if debug:
                print(f"    [api] {url}: {e}")
    return []


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 3 — Fixed HTML regex
# ══════════════════════════════════════════════════════════════════════════════
def _scrape_html_regex(html: str, debug: bool = False) -> list:
    """
    CRITICAL BUG FIX v3.0:
    Old pattern: r'/cricket-scores/(\d+)/'
    Cricbuzz URL: /live-cricket-scores/149618/srh-vs-rcb-...
                   ^^^^^ 'live-' prefix was missing!
    """
    matches = []
    seen = set()
    patterns = [
        r'/live-cricket-scores/(\d{5,})/([^"\' <>\\]+)',  # FIXED primary
        r'/cricket-scores/(\d{5,})/([^"\' <>\\]+)',        # legacy fallback
    ]
    for pat in patterns:
        for m in re.finditer(pat, html):
            cid = m.group(1)
            if cid not in seen:
                seen.add(cid)
                title = m.group(2).replace("-", " ").title()
                matches.append({"cb_match_id": cid, "title": title})
    if debug:
        print(f"    [regex] Found {len(matches)} matches")
    return matches


# ══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR — Try all live strategies
# ══════════════════════════════════════════════════════════════════════════════
def fetch_series_matches(series_id: str, debug: bool = False) -> list:
    """Try API → Next.js → regex.  Returns [] on total failure."""
    # Strategy 2: API first (no HTML parse needed)
    api_result = _fetch_series_api(series_id, debug)
    if api_result:
        print(f"  ✅ [Strategy: API]  {len(api_result)} matches")
        return api_result

    # Fetch series HTML page once, try strategies 1 + 3
    series_url = (
        f"https://www.cricbuzz.com/cricket-series/{series_id}/"
        f"indian-premier-league-2026/matches"
    )
    print(f"  Fetching: {series_url}")
    html = ""
    for attempt in range(1, 4):
        try:
            r = requests.get(series_url, headers=_hdrs(), timeout=25)
            if r.status_code == 200:
                html = r.text
                if debug:
                    print(f"    HTML length: {len(html):,} chars")
                break
            print(f"  HTTP {r.status_code} (attempt {attempt}/3)")
        except requests.RequestException as e:
            print(f"  Network error: {e} (attempt {attempt}/3)")
        if attempt < 3:
            time.sleep(2 ** attempt)  # 2s, 4s backoff

    if not html:
        print("  ❌ Could not fetch series page after 3 attempts")
        return []

    # Strategy 1: Next.js JSON (preferred — survives Cloudflare JS challenge)
    nj = _scrape_nextjs_json(html, debug)
    if nj:
        print(f"  ✅ [Strategy: Next.js JSON]  {len(nj)} matches")
        return nj

    # Strategy 3: Fixed HTML regex
    rx = _scrape_html_regex(html, debug)
    if rx:
        print(f"  ✅ [Strategy: HTML regex]  {len(rx)} matches")
        return rx

    # Hint: Cricbuzz may have returned a Cloudflare challenge page
    if "cf-browser-verification" in html or "Just a moment" in html:
        print("  ⚠  Cricbuzz returned Cloudflare challenge — bot detection triggered")
        print("     Live discovery unavailable from this IP/environment")
    else:
        print("  ⚠  No match links found in page — page structure may have changed")
        if debug:
            print("     First 500 chars:", html[:500])

    return []


# ══════════════════════════════════════════════════════════════════════════════
# DB WRITE
# ══════════════════════════════════════════════════════════════════════════════
def seed_to_db(schedule: list, completed: int) -> None:
    conn = sqlite3.connect(str(DB_PATH), timeout=15)

    with_id    = 0
    no_id      = 0
    inserted   = 0
    updated_st = 0

    for no, cid, title, date, time_ist in schedule:
        iid = f"ipl26_m{no:02d}"
        wk  = ((no - 1) // 7) + 1
        st  = "completed" if no <= completed else "upcoming"

        if cid:
            scorecard_url = f"https://www.cricbuzz.com/live-cricket-scorecard/{cid}"
            with_id += 1
        else:
            # Placeholder ID 00000 — scraper.py guards against int('00000') == 0
            scorecard_url = "https://www.cricbuzz.com/live-cricket-scorecard/00000"
            no_id += 1

        # Insert or update status + scorecard_url
        existing = conn.execute(
            "SELECT scorecard_url, status FROM matches WHERE id = ?", (iid,)
        ).fetchone()

        if existing is None:
            conn.execute(
                """INSERT INTO matches (id,week_no,title,status,scorecard_url,teams_json)
                   VALUES (?,?,?,?,?,?)""",
                (iid, wk, title, st, scorecard_url, "[]"),
            )
            inserted += 1
        else:
            # Always update status (completed/upcoming) and URL if we have a real ID
            old_url, old_st = existing
            new_url = scorecard_url if (cid and "00000" not in old_url) or cid else old_url
            if old_st != st or new_url != old_url:
                conn.execute(
                    "UPDATE matches SET status=?, scorecard_url=? WHERE id=?",
                    (st, new_url, iid),
                )
                updated_st += 1

    conn.commit()
    conn.close()

    print(f"\n{'='*55}")
    print(f"  ✅ Match schedule seeded/updated:")
    print(f"     Inserted : {inserted} new rows")
    print(f"     Updated  : {updated_st} status/URL changes")
    print(f"     With real Cricbuzz ID : {with_id}  (will be scraped)")
    print(f"     Without ID (TBD)      : {no_id}  (placeholder, skipped by scraper)")
    print(f"     Completed             : {completed}")
    print(f"     Upcoming              : {len(schedule) - completed}")
    if no_id > 0:
        print(f"\n  ⚠  {no_id} matches have no Cricbuzz ID yet.")
        print("     Add IDs to IPL_2026_SCHEDULE in Seed_Matches.py as matches go live.")
        print("     Format: (match_no, \"CRICBUZZ_ID\", \"Title\", \"YYYY-MM-DD\", \"HH:MM\")")
        print("     ID source: cricbuzz.com/live-cricket-scores/{ID}/slug")
    print(f"{'='*55}")


# ══════════════════════════════════════════════════════════════════════════════
# VERIFY SCORECARD (test one URL is reachable and has data)
# ══════════════════════════════════════════════════════════════════════════════
def verify_scorecard_url(cricbuzz_id: str, debug: bool = False) -> bool:
    """Test that a scorecard URL returns data the scraper can parse."""
    url = f"https://www.cricbuzz.com/live-cricket-scorecard/{cricbuzz_id}"
    print(f"\n[Verify] Testing scorecard: {url}")
    try:
        r = requests.get(url, headers=_hdrs(), timeout=20)
        print(f"  HTTP {r.status_code} | Length: {len(r.text):,} chars")
        if r.status_code != 200:
            print("  ❌ Non-200 response")
            return False
        has_scorecard = "scorecardApiData" in r.text
        has_next_data = "__next_f" in r.text or "__NEXT_DATA__" in r.text
        has_scores    = "batsmenData" in r.text or "scoreCard" in r.text.lower()
        print(f"  scorecardApiData  : {'✅' if has_scorecard else '❌'}")
        print(f"  Next.js JSON blobs: {'✅' if has_next_data else '❌'}")
        print(f"  Innings data      : {'✅' if has_scores else '❌'}")
        if has_scorecard or has_scores:
            print("  ✅ Scorecard data confirmed — scraper should work")
            return True
        elif has_next_data:
            print("  ⚠  Next.js detected but no match data — match may not have started yet")
        else:
            print("  ❌ No usable match data found (possible bot block or wrong URL)")
        return False
    except requests.RequestException as e:
        print(f"  ❌ Request failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    p = argparse.ArgumentParser(description="Seed IPL 2026 match schedule")
    p.add_argument("--series-id",  default=DEFAULT_SERIES_ID)
    p.add_argument("--completed",  type=int, default=None,
                   help="Override auto-detected completed count")
    p.add_argument("--force",      action="store_true",
                   help="Re-seed / update statuses even if data exists")
    p.add_argument("--no-live",    action="store_true",
                   help="Skip Cricbuzz fetch; use hardcoded schedule + known IDs only")
    p.add_argument("--debug",      action="store_true")
    p.add_argument("--verify",     metavar="CB_ID",
                   help="Test a Cricbuzz scorecard URL and exit (e.g. --verify 149618)")
    args = p.parse_args()

    # ── Verify mode ──────────────────────────────────────────────────────────
    if args.verify:
        verify_scorecard_url(args.verify, args.debug)
        return

    print("\n--- IPL 2026 Match Seeder v3.0 ---")
    (BASE_DIR / "data").mkdir(exist_ok=True)

    # ── Skip if already seeded (unless --force) ───────────────────────────────
    if not args.force:
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            count = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
            conn.close()
            if count > 0:
                # Still update statuses based on current time
                print(f"  DB has {count} matches — updating statuses only (use --force to full re-seed)")
                completed = args.completed if args.completed is not None else _auto_count_completed(IPL_2026_SCHEDULE)
                seed_to_db(IPL_2026_SCHEDULE, completed)
                return
        except Exception:
            pass  # DB may not exist yet

    # ── Step 1: Live discovery ─────────────────────────────────────────────────
    discovered = []
    if not args.no_live:
        print("\n[1/3] Discovering match IDs from Cricbuzz...")
        discovered = fetch_series_matches(args.series_id, args.debug)
        if not discovered:
            print("  → Using hardcoded schedule (Match 1 confirmed ID: 149618)")
    else:
        print("[1/3] Live discovery skipped (--no-live)")

    # ── Step 2: Merge discovered IDs into hardcoded schedule ───────────────────
    print("\n[2/3] Building match schedule...")
    schedule = _merge_discovered(discovered)
    confirmed = sum(1 for _, cid, *_ in schedule if cid)
    print(f"  Total: {len(schedule)} matches | Confirmed IDs: {confirmed}")

    # ── Step 3: Compute completed count ───────────────────────────────────────
    if args.completed is not None:
        completed = args.completed
        print(f"\n[3/3] Using manual --completed {completed}")
    else:
        completed = _auto_count_completed(schedule)
        now_ist = datetime.now(IST)
        print(f"\n[3/3] Auto-detected {completed} completed matches")
        print(f"      (Current IST: {now_ist.strftime('%Y-%m-%d %H:%M IST')})")

    # ── Write to DB ───────────────────────────────────────────────────────────
    seed_to_db(schedule, completed)


if __name__ == "__main__":
    main()
