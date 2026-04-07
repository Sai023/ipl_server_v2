#!/usr/bin/env python3
"""
IPL Fantasy 2026 — Daily Scraper                            Golden File v1
===========================================================================
Detects completed matches, scrapes ESPN Cricinfo scorecards via Playwright,
and persists to SQLite via GoldenDB.upsert_match() (triggers point recalc).

Usage:
    python3 scraper.py

Deps:
    pip install playwright
    playwright install chromium --with-deps
"""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

from db_manager import GoldenDB

DB_PATH     = Path("data/fantasy.db")
MATCHES_DIR = Path("data/matches")


def _clean_id(raw_id) -> str | None:
    """
    Normalise a match ID to a short numeric string.
    Strips prefix (e.g. 'ipl2026_12' → '12').
    ESPN CricInfo IDs > 1527673 are mapped to sequential match numbers.
    """
    s = str(raw_id)
    if "_" in s:
        s = s.split("_")[-1]
    try:
        n = int(s)
        return str(n - 1527673 if n > 1527673 else n)
    except ValueError:
        return s or None


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

    count = 0
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page    = await browser.new_page()

        for match in matches:
            match_num = _clean_id(match["id"])
            if match_num is None:
                continue

            json_path = MATCHES_DIR / f"match_{int(match_num):02d}.json"

            # 1. Serve from local JSON cache if available
            if json_path.exists() and json_path.stat().st_size > 100:
                with open(json_path) as f:
                    cached = json.load(f)
                db.upsert_match(cached)   # triggers recalculate_points
                print(f"  ✓ Ingested cached match {match_num}")
                count += 1
                continue

            # 2. Only scrape matches marked as completed
            if match["status"] != "completed":
                continue

            url = match.get("scorecard_url", "")
            if not url:
                continue

            print(f"  → Scraping match {match_num}: {url}")
            raw = await _scrape_scorecard(page, url)

            match_data = {
                "id":            match["id"],
                "wk":            match["week_no"],
                "title":         match["title"],
                "teams":         json.loads(match["teams_json"] or "[]"),
                "date":          match["date_label"],
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

        await browser.close()

    print(f"\nDaily sync complete: {count} matches processed.")


if __name__ == "__main__":
    asyncio.run(main())
