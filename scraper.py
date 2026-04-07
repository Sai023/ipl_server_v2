#!/usr/bin/env python3
import asyncio
import json
import re
import sqlite3
from pathlib import Path
from playwright.async_api import async_playwright
from db_manager import GoldenDB

# --- CONFIGURATION ---
DB_PATH     = Path("data/fantasy.db")
MATCHES_DIR = Path("data/matches")

def clean_id(raw_id) -> int:
    """
    Architectural Fail-safe: Extracts digits from any string format.
    """
    if not raw_id:
        return 0
    m = re.search(r'(\d+)$', str(raw_id))
    if not m:
        return 0
    try:
        val = int(m.group(1))
        if val > 1000000:
            return val - 1527673
        return val
    except ValueError:
        return 0

async def _scrape_scorecard(page, url: str) -> dict | None:
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
        print(f"  \u2717 Scrape error ({url}): {exc}")
        return None

async def main():
    MATCHES_DIR.mkdir(parents=True, exist_ok=True)
    
    # --- ARCHITECTURAL DIAGNOSTICS ---
    if not DB_PATH.exists():
        print(f"CRITICAL ERROR: Database not found at {DB_PATH.absolute()}")
        return

    db = GoldenDB(DB_PATH)

    # 1. Debug: What is actually in the DB?
    with db._read() as con:
        con.row_factory = sqlite3.Row
        total_rows = con.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        sample = con.execute("SELECT id, status FROM matches LIMIT 1").fetchone()
        
        print(f"--- SYSTEM DIAGNOSIS ---")
        print(f"DATABASE PATH: {DB_PATH.absolute()}")
        print(f"TOTAL MATCHES IN DB: {total_rows}")
        if sample:
            print(f"SAMPLE MATCH: ID={sample['id']}, STATUS='{sample['status']}'")
        else:
            print("WARNING: 'matches' table is EMPTY.")
        print(f"------------------------")

    # 2. Bulletproof Query (Case Insensitive + Explicit Columns)
    with db._read() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT id, week_no, title, teams_json, date_label, scorecard_url, status
            FROM   matches
            WHERE  LOWER(status) IN ('completed', 'upcoming')
            ORDER  BY id
        """).fetchall()
        matches = [dict(r) for r in rows]

    if not matches:
        print("No matches to process after applying filters.")
        return

    print(f"Found {len(matches)} matches to evaluate...")
    count, skipped = 0, 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page    = await browser.new_page()

        for match in matches:
            try:
                raw_id = match.get("id")
                match_num = clean_id(raw_id)

                if match_num <= 0:
                    print(f"  \u26a0 Skipping unresolvable ID: {raw_id!r}")
                    skipped += 1
                    continue

                json_path = MATCHES_DIR / f"match_{match_num:02d}.json"

                # 1. Check Cache
                if json_path.exists() and json_path.stat().st_size > 100:
                    with open(json_path) as f:
                        cached = json.load(f)
                    db.upsert_match(cached)
                    print(f"  \u2713 Ingested cached match {match_num}")
                    count += 1
                    continue

                # 2. Only scrape if status is 'completed' (case-insensitive check)
                current_status = str(match.get("status", "")).lower()
                if current_status != "completed":
                    skipped += 1
                    continue

                url = match.get("scorecard_url") or ""
                if not url:
                    print(f"  \u26a0 No URL for match {match_num}")
                    skipped += 1
                    continue

                print(f"  \u2192 Scraping match {match_num}: {url}")
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

                with open(json_path, "w") as f:
                    json.dump(match_data, f, indent=2)

                db.upsert_match(match_data)
                count += 1
                await asyncio.sleep(1)

            except Exception as exc:
                print(f"  \u2717 Error for match {match.get('id')}: {exc}")
                skipped += 1

        await browser.close()
    print(f"\nSync complete: {count} processed, {skipped} skipped.")

if __name__ == "__main__":
    asyncio.run(main())
