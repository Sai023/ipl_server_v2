#!/usr/bin/env python3
"""
IPL Fantasy 2026 — Daily Scraper                            Golden File v2
===========================================================================
Detects completed matches, scrapes ESPN Cricinfo scorecards via Playwright,
and persists to SQLite via GoldenDB.upsert_match() (triggers point recalc).

Usage:
    python3 scraper.py

Deps:
    pip install playwright
    playwright install chromium --with-deps

v2 fixes:
  • _clean_id() handles all ESPN ID formats:
      '1527685'        → '12'   (large numeric ESPN ID)
      'ipl2026_12'     → '12'   (prefixed numeric)
      'ipl26_m01'      → '1'    (prefixed alpha-numeric, strips leading alpha)
      '12'             → '12'   (plain numeric passthrough)
  • int(match_num) wrapped in try/except everywhere it is used.
  • Per-match try/except so one bad row never aborts the entire sync.
  • Graceful handling of missing scorecard_url.
"""
import asyncio
import json
import re
from pathlib import Path

from playwright.async_api import async_playwright

from db_manager import GoldenDB

DB_PATH     = Path("data/fantasy.db")
MATCHES_DIR = Path("data/matches")
    def _clean_id(self, raw_id) -> int:
        """
        Normalizes any ESPN / Internal match ID to a plain integer.
        Uses Regex to strip all non-numeric characters for production resilience.
        """
        
        if not raw_id:
            return 0
            
        raw_id_str = str(raw_id)
        
        # Strip all non-digits (e.g., 'ipl26_m01' -> '01', '1527674' -> '1527674')
        clean_str = re.sub(r'\D', '', raw_id_str)
        
        try:
            val = int(clean_str)
            # Apply ESPN offset only for large numeric IDs
            if val > 1000000:
                return val - 1527673
            return val
        except ValueError:
            return 0

    # Strip prefix before last underscore (e.g. 'ipl2026_12' -> '12')
    if "_" in s:
        s = s.split("_")[-1]

    # Strip any leading alpha characters (e.g. 'm01' -> '01')
    s = s.lstrip("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")

    if not s:
        return None

    try:
        n = int(s)
        # Large ESPN CricInfo IDs → sequential match number
        return str(n - 1527673 if n > 1527673 else n)
    except ValueError:
        return None


async def _scrape_scorecard(page, url: str) -> dict | None:
    """Return raw innings table data from an ESPN CricInfo scorecard URL."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_selector("table", timeout=20_000)
        return await page.evaluate("""
            () => {
                const innings = [];
                document.querySelectorAll('table').forEach(tbl => {
                    tbl.querySelectorAll('tbody tr').forEach(tr => {
                        const cells = [...tr.querySelectorAll('td')]
                            .map(td => td.innerText.trim());
                        if (cells.length >= 6) innings.push(cells);
                    });
                });
                return { innings };
            }
        """)
    except Exception as exc:
        print(f"  ✗ Scrape error ({url}): {exc}")
        return None


async def main():
    MATCHES_DIR.mkdir(parents=True, exist_ok=True)
    db = GoldenDB(DB_PATH)

    # Read pending matches via GoldenDB context manager (read-only)
    with db._read() as con:
        rows = con.execute("""
            SELECT id, week_no, title, teams_json, date_label,
                   scorecard_url, status
            FROM   matches
            WHERE  status IN ('completed', 'upcoming')
            ORDER  BY id
        """).fetchall()
        matches = [dict(r) for r in rows]

    if not matches:
        print("No matches to process.")
        return

    count   = 0
    skipped = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page    = await browser.new_page()

        for match in matches:
            try:
                raw_id    = match.get("id")
               match_num = self._clean_id(raw_id)

                if match_num is None:
                    print(f"  ⚠ Skipping unresolvable ID: {raw_id!r}")
                    skipped += 1
                    continue

                # Safe int conversion for zero-padded filename
                try:
                    num_int = int(match_num)
                except ValueError:
                    print(f"  ⚠ Non-integer match_num {match_num!r} for ID {raw_id!r} — skipping")
                    skipped += 1
                    continue

                json_path = MATCHES_DIR / f"match_{num_int:02d}.json"

                # 1. Serve from local JSON cache if available
                if json_path.exists() and json_path.stat().st_size > 100:
                    with open(json_path) as f:
                        cached = json.load(f)
                    db.upsert_match(cached)   # triggers recalculate_points
                    print(f"  ✓ Ingested cached match {match_num}")
                    count += 1
                    continue

                # 2. Only scrape matches marked as completed
                if match.get("status") != "completed":
                    continue

                url = match.get("scorecard_url") or ""
                if not url:
                    print(f"  ⚠ No scorecard URL for match {match_num} — skipping")
                    skipped += 1
                    continue

                print(f"  → Scraping match {match_num}: {url}")
                raw = await _scrape_scorecard(page, url)

                match_data = {
                    "id":            raw_id,
                    "wk":            match.get("week_no", 1),
                    "title":         match.get("title", ""),
                    "teams":         json.loads(match.get("teams_json") or "[]"),
                    "date":          match.get("date_label", ""),
                    "status":        "completed",
                    "scorecard_url": url,
                    "scores":        raw.get("innings", {}) if raw else {},
                }

                # Persist JSON cache
                with open(json_path, "w") as f:
                    json.dump(match_data, f, indent=2)

                # Persist to DB — upsert_match also triggers recalculate_points
                db.upsert_match(match_data)
                count += 1

                await asyncio.sleep(1)   # polite delay

            except Exception as exc:
                print(f"  ✗ Unexpected error for match {match.get('id')!r}: {exc}")
                skipped += 1
                continue

        await browser.close()

    print(f"\nDaily sync complete: {count} processed, {skipped} skipped.")


if __name__ == "__main__":
    asyncio.run(main())
