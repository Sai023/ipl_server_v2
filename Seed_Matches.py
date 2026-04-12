#!/usr/bin/env python3
"""
IPL Fantasy 2026 — Match Schedule Seeder  v3.2
================================================
v3.2: Calendar-based week_no (Monday 14:00 IST rollover boundaries).
  OLD: wk = ((no - 1) // 7) + 1   <- buckets of 7 matches, wrong
  NEW: wk = _week_no_for_match(date, time)  <- actual Mon-14:00-IST windows

  Week 1: Mar 28-29 (before Mar 30 14:00 IST rollover)  = M1, M2
  Week 2: Mar 30 14:00 - Apr 6 14:00                    = M3-M11
  Week 3: Apr 6 14:00 - Apr 13 14:00                    = M12-M17
  Week 4: Apr 13 14:00 onwards                          = M18+

Strategies for Cricbuzz ID discovery unchanged from v3.1.

Usage:
    python Seed_Matches.py                    # fully automatic
    python Seed_Matches.py --force            # re-seed and update statuses + week_no
    python Seed_Matches.py --completed 17     # override auto-detection
    python Seed_Matches.py --no-live          # skip Cricbuzz, hardcoded only
    python Seed_Matches.py --verify 149618    # test a scorecard URL
    python Seed_Matches.py --debug
"""

import argparse
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

BASE_DIR           = Path(__file__).resolve().parent
DB_PATH            = BASE_DIR / "data" / "fantasy.db"
DEFAULT_SERIES_ID  = "9237"
IST                = timezone(timedelta(hours=5, minutes=30))
MATCH_DURATION_HRS = 4

# v3.2: First Monday rollover at 14:00 IST after season start.
# Season starts Mar 28 2026 (Saturday). First rollover = Mar 30 14:00 IST.
SEASON_WEEK1_END = datetime(2026, 3, 30, 14, 0, tzinfo=IST)


def _week_no_for_match(date_str: str, time_str: str) -> int:
    """
    Return the fantasy week number for a match, based on Monday 14:00 IST rollover.
    Week 1: before first rollover (Mar 30 14:00 IST)
    Week 2: Mar 30 14:00 – Apr 6 14:00 IST
    Week 3: Apr 6 14:00 – Apr 13 14:00 IST  ... and so on.
    """
    try:
        h, m = map(int, time_str.split(":"))
        y, mo, d = map(int, date_str.split("-"))
        match_dt = datetime(y, mo, d, h, m, tzinfo=IST)
        if match_dt < SEASON_WEEK1_END:
            return 1
        elapsed_days = (match_dt - SEASON_WEEK1_END).total_seconds() / 86400.0
        return 2 + int(elapsed_days // 7)
    except Exception:
        return 1


_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
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


# ==============================================================================
# OFFICIAL IPL 2026 SCHEDULE (74 matches)
# ==============================================================================
IPL_2026_SCHEDULE = [
    # Phase 1: Matches 1-20 (Mar 28 – Apr 12)
    (1,  "149618", "SRH vs RCB, 1st Match",   "2026-03-28", "19:30"),
    (2,  "149629", "KKR vs MI, 2nd Match",     "2026-03-29", "19:30"),
    (3,  "149640", "CSK vs RR, 3rd Match",     "2026-03-30", "19:30"),
    (4,  "149651", "GT vs PBKS, 4th Match",    "2026-03-31", "19:30"),
    (5,  "149662", "LSG vs DC, 5th Match",     "2026-04-01", "19:30"),
    (6,  "149673", "SRH vs KKR, 6th Match",    "2026-04-02", "19:30"),
    (7,  "149684", "CSK vs PBKS, 7th Match",   "2026-04-03", "19:30"),
    (8,  "149695", "MI vs DC, 8th Match",      "2026-04-04", "15:30"),
    (9,  "149699", "RR vs GT, 9th Match",      "2026-04-04", "19:30"),
    (10, "149710", "SRH vs LSG, 10th Match",   "2026-04-05", "15:30"),
    (11, "149721", "RCB vs CSK, 11th Match",   "2026-04-05", "19:30"),
    (12, "149732", "KKR vs PBKS, 12th Match",  "2026-04-06", "19:30"),
    (13, "149743", "RR vs MI, 13th Match",     "2026-04-07", "19:30"),
    (14, "149746", "GT vs DC, 14th Match",     "2026-04-08", "19:30"),
    (15, "149757", "KKR vs LSG, 15th Match",   "2026-04-09", "19:30"),
    (16, "149768", "RCB vs RR, 16th Match",    "2026-04-10", "19:30"),
    (17, "149779", "PBKS vs SRH, 17th Match",  "2026-04-11", "15:30"),
    (18, None,     "CSK vs DC, 18th Match",    "2026-04-11", "19:30"),
    (19, None,     "LSG vs GT, 19th Match",    "2026-04-12", "15:30"),
    (20, None,     "MI vs RCB, 20th Match",    "2026-04-12", "19:30"),
    # Phase 2: Matches 21-70 (Apr 13 – May 24)
    (21, None,  "SRH vs RR, 21st Match",    "2026-04-13", "19:30"),
    (22, None,  "CSK vs KKR, 22nd Match",   "2026-04-14", "19:30"),
    (23, None,  "RCB vs LSG, 23rd Match",   "2026-04-15", "19:30"),
    (24, None,  "MI vs PBKS, 24th Match",   "2026-04-16", "19:30"),
    (25, None,  "GT vs KKR, 25th Match",    "2026-04-17", "19:30"),
    (26, None,  "DC vs RCB, 26th Match",    "2026-04-18", "15:30"),
    (27, None,  "CSK vs SRH, 27th Match",   "2026-04-18", "19:30"),
    (28, None,  "RR vs KKR, 28th Match",    "2026-04-19", "15:30"),
    (29, None,  "LSG vs PBKS, 29th Match",  "2026-04-19", "19:30"),
    (30, None,  "MI vs GT, 30th Match",     "2026-04-20", "19:30"),
    (31, None,  "SRH vs DC, 31st Match",    "2026-04-21", "19:30"),
    (32, None,  "RR vs LSG, 32nd Match",    "2026-04-22", "19:30"),
    (33, None,  "MI vs CSK, 33rd Match",    "2026-04-23", "19:30"),
    (34, None,  "GT vs RCB, 34th Match",    "2026-04-24", "19:30"),
    (35, None,  "PBKS vs DC, 35th Match",   "2026-04-25", "15:30"),
    (36, None,  "RR vs SRH, 36th Match",    "2026-04-25", "19:30"),
    (37, None,  "GT vs CSK, 37th Match",    "2026-04-26", "15:30"),
    (38, None,  "LSG vs KKR, 38th Match",   "2026-04-26", "19:30"),
    (39, None,  "DC vs RCB, 39th Match",    "2026-04-27", "19:30"),
    (40, None,  "PBKS vs RR, 40th Match",   "2026-04-28", "19:30"),
    (41, None,  "MI vs SRH, 41st Match",    "2026-04-29", "19:30"),
    (42, None,  "GT vs RCB, 42nd Match",    "2026-04-30", "19:30"),
    (43, None,  "RR vs DC, 43rd Match",     "2026-05-01", "19:30"),
    (44, None,  "CSK vs MI, 44th Match",    "2026-05-02", "19:30"),
    (45, None,  "SRH vs KKR, 45th Match",   "2026-05-03", "15:30"),
    (46, None,  "GT vs PBKS, 46th Match",   "2026-05-03", "19:30"),
    (47, None,  "MI vs LSG, 47th Match",    "2026-05-04", "19:30"),
    (48, None,  "DC vs CSK, 48th Match",    "2026-05-05", "19:30"),
    (49, None,  "SRH vs PBKS, 49th Match",  "2026-05-06", "19:30"),
    (50, None,  "LSG vs RCB, 50th Match",   "2026-05-07", "19:30"),
    (51, None,  "DC vs KKR, 51st Match",    "2026-05-08", "19:30"),
    (52, None,  "RR vs GT, 52nd Match",     "2026-05-09", "19:30"),
    (53, None,  "CSK vs LSG, 53rd Match",   "2026-05-10", "15:30"),
    (54, None,  "RCB vs MI, 54th Match",    "2026-05-10", "19:30"),
    (55, None,  "PBKS vs DC, 55th Match",   "2026-05-11", "19:30"),
    (56, None,  "GT vs SRH, 56th Match",    "2026-05-12", "19:30"),
    (57, None,  "RCB vs KKR, 57th Match",   "2026-05-13", "19:30"),
    (58, None,  "PBKS vs MI, 58th Match",   "2026-05-14", "19:30"),
    (59, None,  "LSG vs CSK, 59th Match",   "2026-05-15", "19:30"),
    (60, None,  "KKR vs GT, 60th Match",    "2026-05-16", "19:30"),
    (61, None,  "PBKS vs RCB, 61st Match",  "2026-05-17", "15:30"),
    (62, None,  "DC vs RR, 62nd Match",     "2026-05-17", "19:30"),
    (63, None,  "CSK vs SRH, 63rd Match",   "2026-05-18", "19:30"),
    (64, None,  "RR vs LSG, 64th Match",    "2026-05-19", "19:30"),
    (65, None,  "KKR vs MI, 65th Match",    "2026-05-20", "19:30"),
    (66, None,  "CSK vs GT, 66th Match",    "2026-05-21", "19:30"),
    (67, None,  "SRH vs RCB, 67th Match",   "2026-05-22", "19:30"),
    (68, None,  "LSG vs PBKS, 68th Match",  "2026-05-23", "19:30"),
    (69, None,  "MI vs RR, 69th Match",     "2026-05-24", "15:30"),
    (70, None,  "KKR vs DC, 70th Match",    "2026-05-24", "19:30"),
    # Playoffs
    (71, None,  "Qualifier 1",              "2026-05-27", "19:30"),
    (72, None,  "Eliminator",               "2026-05-28", "19:30"),
    (73, None,  "Qualifier 2",              "2026-05-30", "19:30"),
    (74, None,  "Final",                    "2026-05-31", "19:30"),
]


def _merge_discovered(discovered: list) -> list:
    result = []
    for idx, (no, cid, title, date, t) in enumerate(IPL_2026_SCHEDULE):
        if cid is None and idx < len(discovered):
            cid = discovered[idx]["cb_match_id"]
        result.append((no, cid, title, date, t))
    return result


def _auto_count_completed(schedule: list) -> int:
    now_ist = datetime.now(IST)
    count = 0
    for no, cid, title, date, time_ist in schedule:
        try:
            h, m     = map(int, time_ist.split(":"))
            y, mo, d = map(int, date.split("-"))
            start    = datetime(y, mo, d, h, m, tzinfo=IST)
            end      = start + timedelta(hours=MATCH_DURATION_HRS)
            if now_ist > end:
                count += 1
        except (ValueError, AttributeError):
            continue
    return count


# ==============================================================================
# STRATEGY 1: Cricbuzz Next.js embedded JSON
# ==============================================================================
def _scrape_nextjs_json(html: str, debug: bool = False) -> list:
    matches = []
    seen = set()
    for block in re.findall(r'self\.__next_f\.push\(\[.*?\]\)', html, re.S):
        for m in re.finditer(r'/live-cricket-scores/(\d{5,})/([^"\\<>\s]+)', block):
            cid = m.group(1)
            if cid not in seen:
                seen.add(cid)
                matches.append({"cb_match_id": cid,
                                "title": m.group(2).replace("-", " ").title()})
    if debug:
        print(f"    [nextjs] {len(matches)} IDs found")
    return matches


# ==============================================================================
# STRATEGY 2: Cricbuzz series API
# ==============================================================================
def _fetch_series_api(series_id: str, debug: bool = False) -> list:
    for url in [
        f"https://www.cricbuzz.com/api/cricket-series/{series_id}/matches",
        f"https://www.cricbuzz.com/api/html/cricket-series/{series_id}/matches",
    ]:
        try:
            if debug: print(f"    [api] {url}")
            r = requests.get(url, headers=_hdrs(), timeout=15)
            if r.status_code == 200 and r.text.strip()[:1] in ("{", "["):
                data = r.json()
                matches, seen = [], set()
                items = data if isinstance(data, list) else data.get("matches", data.get("matchDetails", []))
                for item in (items if isinstance(items, list) else []):
                    for key in ("id", "matchId", "match_id"):
                        cid = str(item.get(key, ""))
                        if cid and len(cid) >= 4 and cid not in seen:
                            seen.add(cid)
                            title = item.get("matchDescription") or item.get("title") or f"Match {len(matches)+1}"
                            matches.append({"cb_match_id": cid, "title": title})
                            break
                if matches: return matches
        except Exception as e:
            if debug: print(f"    [api] error: {e}")
    return []


# ==============================================================================
# STRATEGY 3: HTML regex
# ==============================================================================
def _scrape_html_regex(html: str, debug: bool = False) -> list:
    matches, seen = [], set()
    for pat in [
        r'/live-cricket-scores/(\d{5,})/([^"\'<>\s\\]+)',
        r'/cricket-scores/(\d{5,})/([^"\'<>\s\\]+)',
    ]:
        for m in re.finditer(pat, html):
            cid = m.group(1)
            if cid not in seen:
                seen.add(cid)
                matches.append({"cb_match_id": cid,
                                "title": m.group(2).replace("-", " ").title()})
    if debug: print(f"    [regex] {len(matches)} matches")
    return matches


# ==============================================================================
# ORCHESTRATOR
# ==============================================================================
def fetch_series_matches(series_id: str, debug: bool = False) -> list:
    api = _fetch_series_api(series_id, debug)
    if api:
        print(f"  \u2705 [API] {len(api)} matches")
        return api
    url = (f"https://www.cricbuzz.com/cricket-series/{series_id}/"
           f"indian-premier-league-2026/matches")
    print(f"  Fetching: {url}")
    html = ""
    for attempt in range(1, 4):
        try:
            r = requests.get(url, headers=_hdrs(), timeout=25)
            if r.status_code == 200: html = r.text; break
            print(f"  HTTP {r.status_code} (attempt {attempt}/3)")
        except requests.RequestException as e:
            print(f"  Network error: {e} (attempt {attempt}/3)")
        if attempt < 3: time.sleep(2 ** attempt)
    if not html:
        print("  \u274c Could not fetch series page")
        return []
    nj = _scrape_nextjs_json(html, debug)
    if nj: print(f"  \u2705 [Next.js JSON] {len(nj)} matches"); return nj
    rx = _scrape_html_regex(html, debug)
    if rx: print(f"  \u2705 [HTML regex] {len(rx)} matches"); return rx
    if "cf-browser-verification" in html or "Just a moment" in html:
        print("  \u26a0  Cloudflare challenge")
    else:
        print("  \u26a0  No match IDs found")
    return []


# ==============================================================================
# DB WRITE (v3.2: uses _week_no_for_match instead of fixed formula)
# ==============================================================================
def seed_to_db(schedule: list, completed: int) -> None:
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.execute("PRAGMA busy_timeout = 30000")
    with_id = no_id = inserted = updated = 0
    week_summary = {}

    for no, cid, title, date, time_ist in schedule:
        iid = f"ipl26_m{no:02d}"
        # v3.2: calendar-based week
        wk  = _week_no_for_match(date, time_ist)
        week_summary[wk] = week_summary.get(wk, 0) + 1
        st  = "completed" if no <= completed else "upcoming"
        url = (f"https://www.cricbuzz.com/live-cricket-scorecard/{cid}"
               if cid else "https://www.cricbuzz.com/live-cricket-scorecard/00000")
        if cid: with_id += 1
        else:   no_id  += 1

        row = conn.execute("SELECT scorecard_url, status, week_no FROM matches WHERE id=?", (iid,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO matches (id,week_no,title,status,scorecard_url,teams_json) VALUES (?,?,?,?,?,?)",
                (iid, wk, title, st, url, "[]"))
            inserted += 1
        else:
            old_url, old_st, old_wk = row
            new_url = url if cid else old_url
            if old_st != st or new_url != old_url or old_wk != wk:
                conn.execute("UPDATE matches SET status=?, scorecard_url=?, week_no=? WHERE id=?",
                             (st, new_url, wk, iid))
                updated += 1

    conn.commit()
    conn.close()

    print(f"\n{'='*55}")
    print(f"  \u2705 Match schedule seeded/updated:")
    print(f"     Inserted : {inserted} new rows")
    print(f"     Updated  : {updated} status/URL/week_no changes")
    print(f"     With real Cricbuzz ID : {with_id}  (will be scraped)")
    print(f"     Without ID (TBD)      : {no_id}  (placeholder, skipped)")
    print(f"     Completed             : {completed}")
    print(f"     Upcoming              : {len(schedule) - completed}")
    print(f"")
    print(f"  Week breakdown (calendar-based, Mon 14:00 IST rollover):")
    for wk_no in sorted(week_summary):
        print(f"     Week {wk_no}: {week_summary[wk_no]} matches")
    if no_id > 0:
        print(f"\n  \u26a0  {no_id} matches need real Cricbuzz IDs.")
        print(r"     ID source: cricbuzz.com/live-cricket-scores/{ID}/slug")
    print(f"{'='*55}")
    print(f"\n  IMPORTANT: Run 'python scraper.py' next to update")
    print(f"  player_match_points.week_no with the new values.")


# ==============================================================================
# VERIFY MODE
# ==============================================================================
def verify_scorecard_url(cricbuzz_id: str) -> None:
    url = f"https://www.cricbuzz.com/live-cricket-scorecard/{cricbuzz_id}"
    print(f"\n[Verify] {url}")
    try:
        r = requests.get(url, headers=_hdrs(), timeout=20)
        print(f"  HTTP {r.status_code} | {len(r.text):,} chars")
        ok = "scorecardApiData" in r.text or "batsmenData" in r.text
        print(f"  scorecardApiData : {'\u2705' if 'scorecardApiData' in r.text else '\u274c'}")
        print(f"  batsmenData      : {'\u2705' if 'batsmenData' in r.text else '\u274c'}")
        print(f"  {'PASS' if ok else 'FAIL'}")
    except requests.RequestException as e:
        print(f"  \u274c {e}")


# ==============================================================================
# MAIN
# ==============================================================================
def main():
    p = argparse.ArgumentParser(description="Seed IPL 2026 match schedule")
    p.add_argument("--series-id", default=DEFAULT_SERIES_ID)
    p.add_argument("--completed", type=int, default=None)
    p.add_argument("--force",   action="store_true",
                   help="Re-seed even if data exists (updates statuses + week_no)")
    p.add_argument("--no-live", action="store_true")
    p.add_argument("--debug",   action="store_true")
    p.add_argument("--verify",  metavar="CB_ID")
    args = p.parse_args()

    if args.verify:
        verify_scorecard_url(args.verify)
        return

    print("\n--- IPL 2026 Match Seeder v3.2 (calendar weeks) ---")
    (BASE_DIR / "data").mkdir(exist_ok=True)

    if not args.force:
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            count = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
            conn.close()
            if count > 0:
                print(f"  DB has {count} matches \u2014 updating statuses + week_no (use --force for full re-seed)")
                completed = args.completed if args.completed is not None \
                    else _auto_count_completed(IPL_2026_SCHEDULE)
                seed_to_db(IPL_2026_SCHEDULE, completed)
                return
        except Exception:
            pass

    # Step 1: Live Cricbuzz discovery
    discovered = []
    if not args.no_live:
        print("\n[1/3] Discovering match IDs from Cricbuzz...")
        discovered = fetch_series_matches(args.series_id, args.debug)
        if not discovered:
            print("  \u2192 Using hardcoded schedule")
    else:
        print("[1/3] Skipping live discovery (--no-live)")

    # Step 2: Merge
    print("\n[2/3] Building schedule...")
    schedule  = _merge_discovered(discovered)
    confirmed = sum(1 for _, cid, *_ in schedule if cid)
    print(f"  {len(schedule)} matches | {confirmed} confirmed IDs")

    # Step 3: Count completed
    if args.completed is not None:
        completed = args.completed
        print(f"\n[3/3] Manual --completed {completed}")
    else:
        completed = _auto_count_completed(schedule)
        now_ist   = datetime.now(IST)
        print(f"\n[3/3] Auto-detected {completed} completed")
        print(f"      IST now: {now_ist.strftime('%Y-%m-%d %H:%M')}")

    seed_to_db(schedule, completed)


if __name__ == "__main__":
    main()
