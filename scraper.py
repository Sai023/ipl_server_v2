#!/usr/bin/env python3
import asyncio
import json
import re
import sqlite3
import os
import sys
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError
from db_manager import GoldenDB

# --- ARCHITECTURAL CONTAINER FIX ---
# Inside a GitHub Action container, relative paths like 'data/fantasy.db' can fail.
# This forces the script to look relative to its own physical location.
BASE_DIR    = Path(__file__).resolve().parent
DB_PATH     = BASE_DIR / "data" / "fantasy.db"
MATCHES_DIR = BASE_DIR / "data" / "matches"
MAX_RETRIES = 3

def clean_id(raw_id) -> int:
    """Extracts match number and handles ESPN ID offsets."""
    if not raw_id: return 0
    m = re.search(r'(\d+)$', str(raw_id))
    if not m: return 0
    try:
        val = int(m.group(1))
        return val - 1527673 if val > 1000000 else val
    except ValueError: return 0

async def parse_detailed_scorecard(page):
    """
    Injected JavaScript to extract granular player data.
    Captures Batting, Bowling, Fielding, and Fall of Wickets.
    """
    return await page.evaluate("""
        () => {
            const results = {
                innings: [],
                metadata: {
                    scraped_at: new Date().toISOString(),
                    match_notes: []
                }
            };

            // Select all tables with the specific ESPN data class
            const tables = document.querySelectorAll('table.ds-table');
            
            tables.forEach((tbl) => {
                const rows = [];
                const header = tbl.querySelector('thead tr');
                
                // Identify table type for fantasy calculation
                let type = 'general';
                if (header) {
                    const txt = header.innerText;
                    if (txt.includes('R') && txt.includes('B')) type = 'batting';
                    else if (txt.includes('O') && txt.includes('M')) type = 'bowling';
                }

                tbl.querySelectorAll('tbody tr').forEach(tr => {
                    // Skip hidden rows but keep info-rich cells
                    if (tr.classList.contains('ds-hidden')) return;
                    
                    const cells = [...tr.querySelectorAll('td')].map(td => td.innerText.trim());
                    // Capture everything with at least name and status/runs
                    if (cells.length >= 2) rows.push(cells);
                });

                if (rows.length > 0) {
                    results.innings.push({ type, data: rows });
                }
            });

            // Capture Match Summary (Toss, Venue, Result)
            document.querySelectorAll('.ds-p-4 .ds-text-tight-m').forEach(note => {
                results.metadata.match_notes.push(note.innerText.trim());
            });

            return results;
        }
    """)

async def scrape_match_with_retry(page, url, match_num):
    """Guard against Container/Network timeouts with exponential backoff."""
    # Force 'full-scorecard' view to get detailed player stats
    target_url = url.replace("match-report", "full-scorecard")
    
    for attempt in range(MAX_RETRIES):
        try:
            print(f"  → [{attempt+1}/{MAX_RETRIES}] Accessing Match {match_num}...")
            await page.goto(target_url, wait_until="domcontentloaded", timeout=45_000)
            
            # Wait for data tables to appear in DOM
            await page.wait_for_selector("table.ds-table", timeout=15_000)
            
            # Brief pause for dynamic content hydration
            await asyncio.sleep(2)
            
            data = await parse_detailed_scorecard(page)
            if data and len(data['innings']) > 0:
                return data
                
        except TimeoutError:
            print(f"  ⚠ Timeout on attempt {attempt+1}. Retrying...")
            await asyncio.sleep(5 * (attempt + 1))
        except Exception as e:
            print(f"  ❌ Error on Match {match_num}: {e}")
            break
    return None

async def main():
    """Main Scraper Orchestrator for IPL 2026."""
    print(f"\n{'='*60}")
    print(f"IPL 2026 CONTAINERIZED SCRAPER ENGINE")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")
    
    # Ensure local directory exists inside the container volume
    MATCHES_DIR.mkdir(parents=True, exist_ok=True)
    
    if not DB_PATH.exists():
        print(f"FATAL: Database not found at {DB_PATH.absolute()}")
        sys.exit(1)

    db = GoldenDB(DB_PATH)

    # 1. Database & Schema Verification
    try:
        with db._read() as con:
            con.row_factory = sqlite3.Row
            count_res = con.execute("SELECT COUNT(*) FROM matches").fetchone()
            total_matches = count_res[0] if count_res else 0
            
            print(f"DB STATUS: Connected.")
            print(f"DB STATUS: {total_matches} records found.")

            # Load targets: prioritizes completed but un-scraped matches
            rows = con.execute("""
                SELECT id, week_no, title, teams_json, date_label, scorecard_url, status
                FROM   matches
                WHERE  LOWER(status) IN ('completed', 'upcoming')
                ORDER  BY id ASC
            """).fetchall()
            target_list = [dict(r) for r in rows]
    except Exception as db_err:
        print(f"DATABASE ERROR: {db_err}")
        return

    if not target_list:
        print("LOG: No matches meet criteria for scraping.")
        return

    processed = 0
    
    async with async_playwright() as pw:
        # CRITICAL CONTAINER FLAGS: These prevent Chromium from crashing in Docker
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox", 
                "--disable-setuid-sandbox", 
                "--disable-dev-shm-usage", # Essential for containers
                "--disable-gpu"
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        for match in target_list:
            match_id = match.get("id")
            match_num = clean_id(match_id)
            
            # Logic Guard: Only process 'completed' matches
            if str(match.get("status")).lower() != "completed":
                continue

            json_path = MATCHES_DIR / f"match_{match_num:02d}.json"
            
            # Smart Cache: Skip if file is already detailed (>5KB)
            if json_path.exists() and json_path.stat().st_size > 5000:
                print(f"  ✓ Match {match_num}: Cached.")
                continue

            print(f"INGESTING: {match.get('title')}...")
            
            payload = await scrape_match_with_retry(page, match.get('scorecard_url'), match_num)
            
            if payload:
                # Build production-ready JSON object
                match_data = {
                    "metadata": {
                        "id": match_id,
                        "match_no": match_num,
                        "week": match.get("week_no"),
                        "teams": json.loads(match.get("teams_json") or "[]"),
                        "date": match.get("date_label"),
                        "info": payload['metadata']
                    },
                    "scorecard": payload['innings'],
                    "status": "completed"
                }

                # Save to filesystem
                with open(json_path, "w", encoding='utf-8') as f:
                    json.dump(match_data, f, indent=2, ensure_ascii=False)
                
                # Sync back to DB for other app modules
                db.upsert_match(match_data)
                
                processed += 1
                print(f"  ✅ SUCCESS: Match {match_num} synced.")
                await asyncio.sleep(1.5)
            else:
                print(f"  ❌ FAILED: Skipping Match {match_num} after retries.")

        await browser.close()

    print(f"\nSUMMARY: {processed} matches successfully updated.")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown signal received.")
