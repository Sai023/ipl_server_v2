#!/usr/bin/env python3
import asyncio
import json
import re
import sqlite3
import os
import sys
import threading
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError
from db_manager import DatabaseManager, _upsert_match

# --- ARCHITECTURAL CONFIGURATION ---
BASE_DIR    = Path(__file__).resolve().parent
DB_PATH     = BASE_DIR / "data" / "fantasy.db"
MATCHES_DIR = BASE_DIR / "data" / "matches"
MAX_RETRIES = 3

def clean_id(raw_id) -> int:
    """Handles both sequential 'm01' and large ESPN numeric IDs."""
    if not raw_id: return 0
    m = re.search(r'(\d+)$', str(raw_id))
    if not m: return 0
    try:
        val = int(m.group(1))
        return val - 1527673 if val > 1000000 else val
    except ValueError: return 0

def process_fantasy_stats(raw_innings):
    """
    Architectural Bridge: Maps raw HTML table cells to the specific
    dictionary keys required by _upsert_match in db_manager.py.
    """
    player_stats = {}

    for table in raw_innings:
        rows = table.get('data', [])
        table_type = table.get('type', 'general')

        if table_type == 'batting':
            for row in rows:
                # Batting tables usually have: Player, Dismissal, R, B, M, 4s, 6s, SR
                if len(row) < 7 or "total" in row[0].lower() or "extras" in row[0].lower(): 
                    continue
                
                name = row[0].replace('†', '').replace('(c)', '').strip()
                pid = name.lower().replace(' ', '_')
                
                player_stats[pid] = {
                    "played": True,
                    "runs": int(row[2]) if row[2].isdigit() else 0,
                    "balls": int(row[3]) if row[3].isdigit() else 0,
                    "fours": int(row[5]) if row[5].isdigit() else 0,
                    "sixes": int(row[6]) if row[6].isdigit() else 0,
                    "got_out": 0 if "not out" in row[1].lower() else 1,
                    "duck": 1 if row[2] == "0" and "not out" not in row[1].lower() else 0
                }

        elif table_type == 'bowling':
            for row in rows:
                # Bowling tables usually have: Bowler, O, M, R, W, Econ, 0s, 4s, 6s, WD, NB
                if len(row) < 5: continue
                
                name = row[0].replace('†', '').replace('(c)', '').strip()
                pid = name.lower().replace(' ', '_')
                
                stats = player_stats.get(pid, {"played": True, "runs":0, "balls":0, "fours":0, "sixes":0})
                stats.update({
                    "overs": float(row[1]) if '.' in row[1] else float(row[1]),
                    "maidens": int(row[2]) if row[2].isdigit() else 0,
                    "runs_conceded": int(row[3]) if row[3].isdigit() else 0,
                    "wickets": int(row[4]) if row[4].isdigit() else 0,
                })
                player_stats[pid] = stats

    return player_stats

async def scrape_match(page, url, match_num):
    """Container-hardened extraction targeting specific ESPN table classes."""
    target_url = url.replace("match-report", "full-scorecard")
    for attempt in range(MAX_RETRIES):
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_selector("table.ds-table", timeout=15_000)
            
            return await page.evaluate("""
                () => {
                    const results = { innings: [] };
                    document.querySelectorAll('table.ds-table').forEach(tbl => {
                        const rows = [];
                        const header = tbl.querySelector('thead tr')?.innerText || "";
                        let type = 'other';
                        if (header.includes('R') && header.includes('B')) type = 'batting';
                        else if (header.includes('O') && header.includes('M')) type = 'bowling';
                        
                        tbl.querySelectorAll('tbody tr').forEach(tr => {
                            const cells = [...tr.querySelectorAll('td')].map(td => td.innerText.trim());
                            if (cells.length >= 2) rows.push(cells);
                        });
                        results.innings.push({ type, data: rows });
                    });
                    return results;
                }
            """)
        except Exception as e:
            print(f"  ⚠ Attempt {attempt+1} failed for Match {match_num}: {e}")
            await asyncio.sleep(2)
    return None

async def main():
    print(f"\n{'='*60}\nIPL 2026 SUPER SCRAPER ENGINE v3.0\n{'='*60}")
    MATCHES_DIR.mkdir(parents=True, exist_ok=True)
    
    if not DB_PATH.exists():
        print(f"FATAL: Database not found at {DB_PATH}")
        return

    db = DatabaseManager(DB_PATH)

    with db._read() as con:
        # Fetch only completed matches that still need detailed scores
        rows = con.execute("SELECT * FROM matches WHERE LOWER(status) = 'completed'").fetchall()
        targets = [dict(r) for r in rows]

    if not targets:
        print("INFO: No completed matches found for processing.")
        return

    async with async_playwright() as pw:
        # Launch with flags for GitHub Action Container stability
        browser = await pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"])
        page = await browser.new_page()

        for m in targets:
            m_num = clean_id(m['id'])
            json_path = MATCHES_DIR / f"match_{m_num:02d}.json"
            
            # Skip if match data is already detailed (>5KB)
            if json_path.exists() and json_path.stat().st_size > 5000:
                print(f"  ✓ Match {m_num} is already fully synced.")
                continue
            
            print(f"SYNCING: Match {m_num} - {m['title']}")
            raw_data = await scrape_match(page, m['scorecard_url'], m_num)
            
            if raw_data:
                # Transform raw HTML tables into structured player dictionaries
                scores_dict = process_fantasy_stats(raw_data['innings'])
                
                match_payload = {
                    "id": m['id'],
                    "wk": m['week_no'],
                    "title": m['title'],
                    "teams": json.loads(m['teams_json'] or "[]"),
                    "date": m['date_label'],
                    "status": "completed",
                    "scores": scores_dict
                }

                # 1. Write to JSON for frontend/audit
                with open(json_path, "w", encoding='utf-8') as f:
                    json.dump(match_payload, f, indent=2, ensure_ascii=False)
                
                # 2. Sync to Database (using db_manager's write lock)
                try:
                    with db._write() as write_con:
                        _upsert_match(write_con, match_payload)
                    print(f"  ✅ SUCCESS: Match {m_num} data persisted to DB.")
                except Exception as e:
                    print(f"  ❌ DB ERROR: Match {m_num} could not be saved: {e}")

        await browser.close()
    print(f"\n{'='*60}\nWORKFLOW SYNC COMPLETE\n{'='*60}\n")

if __name__ == "__main__":
    asyncio.run(main())
