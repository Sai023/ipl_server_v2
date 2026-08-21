#!/usr/bin/env python3
"""
IPL Fantasy 2026 — Match Schedule Sync                Seed_Matches v4.0
================================================
v4.0 — JSON-aware shim. Source of truth moved to data/schedule.json.
       Discovery logic extracted to logic/cricbuzz_discovery.py.

Pipeline
--------
  data/schedule.json  ← logic.cricbuzz_discovery.run_discovery()  (live ID hunt)
         │
         ▼
  seed_to_db()        ← idempotent matches-table sync from JSON
         │
         ▼
  scraper.py          ← reads matches.scorecard_url, fetches scorecards

Usage
-----
  python Seed_Matches.py                  # discover + sync (DEFAULT)
  python Seed_Matches.py --no-live        # sync JSON → DB only (GitHub Actions path)
  python Seed_Matches.py --completed 17   # override auto-detection
  python Seed_Matches.py --verify 149618  # test a scorecard URL (standalone)
  python Seed_Matches.py --debug          # verbose

Public surface used elsewhere:
  _auto_count_completed() — auto-detect # completed by current IST time.
                            Imported by scraper._presync_schedule().

Week boundaries (Monday 14:00 IST rollover) — unchanged from v3.3:
  Week 1: Mar 28-29           (M1-M2)
  Week 2: Mar 30 - Apr 6 14:00 (M3-M11)
  ...
  Week 10: May 27 onwards     (M71-M74 playoffs)
"""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' required.  pip install requests")
    sys.exit(1)

from logic.cricbuzz_discovery import (
    load_schedule, run_discovery, CRICBUZZ_DISCOVERY_VER,
)

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_DIR           = Path(__file__).resolve().parent
DB_PATH            = BASE_DIR / "data" / "fantasy.db"
IST                = timezone(timedelta(hours=5, minutes=30))
MATCH_DURATION_HRS = 4
SEED_MATCHES_VER   = "5.0"   # multi-competition: per-comp schedule + anchor + ids

# Fallback anchor if a competition row lacks week1_anchor_utc.
SEASON_DEFAULT_ANCHOR = datetime(2026, 3, 30, 14, 0, tzinfo=timezone.utc)

def _schedule_json(comp):
    return BASE_DIR / "data" / comp / "schedule.json"

def _comp_config(comp):
    """Read (week1_anchor, series_id, year) for a competition from the DB.
    week1_anchor is a tz-aware datetime parsed from competitions.week1_anchor_utc."""
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    try:
        row = conn.execute(
            "SELECT week1_anchor_utc, series_id, year FROM competitions WHERE slug=?",
            (comp,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise SystemExit("Unknown competition %r (no competitions row)." % comp)
    anchor = datetime.fromisoformat(row[0]) if row[0] else SEASON_DEFAULT_ANCHOR
    return anchor, row[1], row[2]


# ══════════════════════════════════════════════════════════════════════════════
# WEEK CALCULATOR  (unchanged from v3.3 — exported for scraper.py)
# ══════════════════════════════════════════════════════════════════════════════

def _week_no_for_match(date_str: str, time_str: str, anchor: datetime) -> int:
    """
    Return the fantasy week number for a match, based on Monday 14:00 IST rollover.
    Week 1: before first rollover (Mar 30 14:00 IST)
    Week 2: Mar 30 14:00 – Apr 6 14:00 IST  ...etc.
    """
    try:
        h, m = map(int, time_str.split(":"))
        y, mo, d = map(int, date_str.split("-"))
        match_dt = datetime(y, mo, d, h, m, tzinfo=IST)
        if match_dt < anchor:
            return 1
        elapsed_days = (match_dt - anchor).total_seconds() / 86400.0
        return 2 + int(elapsed_days // 7)
    except Exception:
        return 1


# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULE LOADER — backward-compat shim for IPL_2026_SCHEDULE
# ══════════════════════════════════════════════════════════════════════════════

def _load_schedule_tuples(schedule_json) -> list:
    """
    Read schedule.json and return list of
        (match_no, cricbuzz_id, title, date, time_ist)
    tuples in the legacy IPL_2026_SCHEDULE shape.

    Returns [] if schedule.json is missing — caller must check.
    """
    try:
        data = load_schedule(schedule_json)
    except FileNotFoundError:
        print(f"  ⚠ schedule.json missing at {schedule_json}")
        print(f"     Restore from repo, or run:  python -m logic.cricbuzz_discovery --debug")
        return []
    except Exception as e:
        print(f"  ⚠ schedule.json unreadable: {e}")
        return []

    return [
        (m["match_no"],
         m.get("cricbuzz_id"),
         m["title"],
         m["date"],
         m["time_ist"])
        for m in data.get("matches", [])
    ]


def _auto_count_completed(schedule: list) -> int:
    """Count matches whose end (start + 4h) has passed in IST."""
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


# ══════════════════════════════════════════════════════════════════════════════
# DB SYNC — idempotent (only writes when something changed)
# ══════════════════════════════════════════════════════════════════════════════

def seed_to_db(schedule: list, completed: int, comp: str, anchor: datetime) -> None:
    """
    Sync schedule tuples → matches table.

    Idempotency
    -----------
    For each match: if the row exists and status/url/week_no all match, no
    write happens.  Safe to call on every server startup, every GitHub
    Actions run, every manual sync.  Never overwrites a real Cricbuzz URL
    with a /00000 placeholder.
    """
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.execute("PRAGMA busy_timeout = 30000")
    with_id = no_id = inserted = updated = 0
    week_summary: dict = {}

    # Format a "Mon DD" date_label from the schedule's "YYYY-MM-DD" string.
    # Matches the format the scraper writes in _extract_meta — keeps the UI consistent.
    _MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    def _fmt_date_label(date_str: str) -> str:
        try:
            y, mo, d = map(int, date_str.split("-"))
            return f"{_MONTHS[mo-1]} {d}"
        except (ValueError, AttributeError, IndexError):
            return ""

    for no, cid, title, date, time_ist in schedule:
        iid = f"{comp}_m{no:02d}"
        wk  = _week_no_for_match(date, time_ist, anchor)
        week_summary[wk] = week_summary.get(wk, 0) + 1
        st  = "completed" if no <= completed else "upcoming"
        url = (f"https://www.cricbuzz.com/live-cricket-scorecard/{cid}"
               if cid else "https://www.cricbuzz.com/live-cricket-scorecard/00000")
        if cid: with_id += 1
        else:   no_id  += 1

        row = conn.execute(
            "SELECT scorecard_url, status, week_no FROM matches WHERE id=? AND competition_id=?",
            (iid, comp)
        ).fetchone()

        # Extract teams from schedule.json title e.g. "SRH vs RCB, 1st Match" -> ["SRH","RCB"]
        _tm = re.match(r'^([A-Z]+)\s+vs\s+([A-Z]+)', title or "")
        teams_json = json.dumps([_tm.group(1), _tm.group(2)]) if _tm else "[]"
        date_label = _fmt_date_label(date)

        if row is None:
            conn.execute(
                "INSERT INTO matches (competition_id,id,week_no,title,status,scorecard_url,teams_json,date_label) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (comp, iid, wk, title, st, url, teams_json, date_label)
            )
            inserted += 1
        else:
            old_url, old_st, old_wk = row
            # Never overwrite a real URL with /00000
            new_url = url if cid else old_url
            # Always refresh title, teams_json, date_label from schedule.json
            # (source of truth — keeps the matches table in sync even after the
            # scraper writes stale values via a wrong-scorecard scrape).
            conn.execute(
                "UPDATE matches SET title=?, teams_json=?, date_label=? WHERE id=? AND competition_id=?",
                (title, teams_json, date_label, iid, comp)
            )
            if old_st != st or new_url != old_url or old_wk != wk:
                conn.execute(
                    "UPDATE matches SET status=?, scorecard_url=?, week_no=? WHERE id=? AND competition_id=?",
                    (st, new_url, wk, iid, comp)
                )
                updated += 1

    # Derive this competition's week count from the schedule itself — match
    # dates + the week-1 anchor already define each match's week, so max_weeks
    # is never hand-set. Stored on the competition row for rollover/UI access.
    max_wk = max(week_summary) if week_summary else 1
    conn.execute("UPDATE competitions SET max_weeks=? WHERE slug=?", (max_wk, comp))
    conn.commit()
    conn.close()

    print(f"\n{'=' * 55}")
    print(f"  [OK] Match schedule synced to DB:")
    print(f"     Inserted : {inserted} new rows")
    print(f"     Updated  : {updated} status/URL/week_no changes")
    print(f"     With real Cricbuzz ID : {with_id}  (scrapable)")
    print(f"     Without ID (TBD)      : {no_id}  (placeholder, skipped)")
    print(f"     Completed             : {completed}")
    print(f"     Upcoming              : {len(schedule) - completed}")
    print(f"\n  Week breakdown (calendar, Mon 14:00 IST rollover):")
    for wk_no in sorted(week_summary):
        print(f"     Week {wk_no}: {week_summary[wk_no]} matches")
    if no_id > 0:
        print(f"\n  WARN: {no_id} matches still missing Cricbuzz IDs.")
        print(f"     Run discovery:   python Seed_Matches.py")
        print(f"     Or directly:     python -m logic.cricbuzz_discovery --debug")
    print(f"{'=' * 55}")
    print(f"\n  Next: python scraper.py  (fetches scorecards for completed matches)")


# ══════════════════════════════════════════════════════════════════════════════
# VERIFY MODE — standalone, no schedule.json needed
# ══════════════════════════════════════════════════════════════════════════════

def verify_scorecard_url(cricbuzz_id: str) -> None:
    """Confirm that a Cricbuzz match ID returns a real scorecard payload."""
    _UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
           "AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/124.0.0.0 Safari/537.36")
    url = f"https://www.cricbuzz.com/live-cricket-scorecard/{cricbuzz_id}"
    print(f"\n[Verify] {url}")
    try:
        r = requests.get(url, headers={"User-Agent": _UA, "Referer":
                         "https://www.cricbuzz.com/"}, timeout=20)
        print(f"  HTTP {r.status_code} | {len(r.text):,} chars")
        sa = "scorecardApiData" in r.text
        bd = "batsmenData"      in r.text
        print(f"  scorecardApiData : {'✅' if sa else '❌'}")
        print(f"  batsmenData      : {'✅' if bd else '❌'}")
        print(f"  {'PASS' if (sa or bd) else 'FAIL'}")
    except requests.RequestException as e:
        print(f"  ❌ {e}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    pa = argparse.ArgumentParser(
        description="Match schedule sync (multi-competition) v%s" % SEED_MATCHES_VER)
    pa.add_argument("--comp", default="ipl_2026", help="competition slug (default: ipl_2026)")
    pa.add_argument("--completed", type=int, default=None,
                    help="Override auto-detected completed count")
    pa.add_argument("--no-live", action="store_true",
                    help="Skip Cricbuzz discovery; sync DB from current schedule.json")
    pa.add_argument("--debug", action="store_true")
    pa.add_argument("--verify", metavar="CB_ID",
                    help="Test that a Cricbuzz scorecard ID is valid (standalone)")
    args = pa.parse_args()

    if args.verify:
        verify_scorecard_url(args.verify)
        return

    comp = args.comp
    anchor, series_id, year = _comp_config(comp)
    schedule_json = _schedule_json(comp)

    print("")
    print("--- Match Sync v%s (%s, discovery v%s) ---"
          % (SEED_MATCHES_VER, comp, CRICBUZZ_DISCOVERY_VER))
    schedule_json.parent.mkdir(parents=True, exist_ok=True)

    if not schedule_json.exists():
        print("  ERROR: %s not found." % schedule_json)
        print("  Create a schedule.json for %r (or run discovery)." % comp)
        sys.exit(1)

    if not args.no_live:
        print("[1/3] Cricbuzz discovery -> updating %s..." % schedule_json.name)
        res = run_discovery(schedule_json, year=year, debug=args.debug)
        if res.get("ok"):
            print("  +%s new IDs (had %s, %s unfilled)"
                  % (res.get("filled"), res.get("already_had"), res.get("unfilled_known")))
        else:
            print("  ! discovery failed: %s (keeping existing schedule.json)" % res.get("error"))
    else:
        print("[1/3] Skipping discovery (--no-live)")

    print("[2/3] Loading schedule from %s..." % schedule_json.name)
    schedule = _load_schedule_tuples(schedule_json)
    if not schedule:
        print("  ERROR: schedule is empty - aborting.")
        sys.exit(1)
    confirmed = sum(1 for _, cid, *_ in schedule if cid)
    print("  %d matches | %d confirmed IDs | %d unfilled"
          % (len(schedule), confirmed, len(schedule) - confirmed))

    if args.completed is not None:
        completed = args.completed
        print("[3/3] Manual --completed %d" % completed)
    else:
        completed = _auto_count_completed(schedule)
        print("[3/3] Auto-detected %d completed (IST now: %s)"
              % (completed, datetime.now(IST).strftime("%Y-%m-%d %H:%M")))

    seed_to_db(schedule, completed, comp, anchor)

if __name__ == "__main__":
    main()
