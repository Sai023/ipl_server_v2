#!/usr/bin/env python3
import asyncio
import json
import re
from pathlib import Path
from playwright.async_api import async_playwright
from db_manager import GoldenDB

# --- CONFIGURATION ---
DB_PATH     = Path("data/fantasy.db")
MATCHES_DIR = Path("data/matches")

def clean_id(raw_id) -> int:
    """
    Architectural Fail-safe: Extracts digits from any string format.
    Uses trailing-anchor regex to prevent year-digit contamination.
    Handles 'ipl26_m01' -> 1, '1527685' -> 12, etc.
    """
    if not raw_id:
        return 0
    m = re.search(r'(\d+)$', str(raw_id))
    if not m:
        return 0
    try:
        val = int(m.group(1))
        # Apply ESPN offset only for large numeric IDs
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
    db = GoldenDB(DB_PATH)

    # ── Cold-start hydration: rebuild DB from JSON archives if empty ──────
    try:
        with db._read() as con:
            n = con.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        if n == 0 and MATCHES_DIR.exists() and any(MATCHES_DIR.glob("*.json")):
            print("  [scraper] Cold DB — hydrating from JSON archives...")
            db.hydrate_from_json(MATCHES_DIR)
    except Exception as e:
        print(f"  [scraper] Hydration check failed: {e}")

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

    count, skipped = 0, 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page    = await browser.new_page()

        for match in matches:
            try:
                raw_id = match.get("id")
                # ARCHITECTURAL FIX: trailing-anchor regex prevents year-digit bleed
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

                # 2. Scrape if completed
                if match.get("status") != "completed":
                    continue

                url = match.get("scorecard_url") or ""
                if not url:
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
