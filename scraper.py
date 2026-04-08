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
from db_manager import DatabaseManager, _upsert_match
import Seed_Matches

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
    """Maps raw web tables to the specific dictionary format for db_manager.py."""
    player_stats = {}
    for table in raw_innings:
        rows = table.get('data', [])
        table_type = table.get('type', 'general')

        if table_type == 'batting':
            for row in rows:
                # Batting tables: Player[0], Dismissal[1], R[2], B[3], M[4], 4s[5], 6s[6], SR[7]
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
                # Bowling tables: Bowler[0], O[1], M[2], R[3], W[4], Econ[5], 0s[6], 4s[7], 6s[8]
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
    """Navigates with retries and extracts scorecard tables."""
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
                        let type = header.includes('R') && header.includes('B') ? 'batting' : (header.includes('O') ? 'bowling' : 'other');
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
    print(f"\n{'='*60}\nIPL 2026 MASTER ENGINE v5.0 (TOTAL SYNC)\n{'='*60}")
    MATCHES_DIR.mkdir(parents=True, exist_ok=True)
    db = DatabaseManager(DB_PATH)

    # 1. FAIL-SAFE: SELF-HEALING DATABASE
    with db._read() as con:
        res = con.execute("SELECT COUNT(*) FROM matches").fetchone()
        count = res[0] if res else 0
    
    if count == 0:
        print("DATABASE EMPTY: Triggering Emergency Seed...")
        Seed_Matches.seed()

    # 2. IDENTIFY COMPLETED TARGETS
    with db._read() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM matches WHERE LOWER(status) = 'completed'").fetchall()
        targets = [dict(r) for r in rows]

    if not targets:
        print("DIAGNOSTIC: No matches marked 'completed' in DB. Scraper idling.")
        return

    print(f"PROCESSING {len(targets)} COMPLETED MATCHES...")

    async with async_playwright() as pw:
        # Flags optimized for Ubuntu-Jammy Container performance
        browser = await pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"])
        page = await browser.new_page()

        for m in targets:
            m_num = clean_id(m['id'])
            json_path = MATCHES_DIR / f"match_{m_num:02d}.json"
            
            # Caching check: skip if high-detail data already exists
            if json_path.exists() and json_path.stat().st_size > 5000:
                continue
            
            print(f"Syncing Match {m_num}: {m['title']}")
            raw_data = await scrape_match(page, m['scorecard_url'], m_num)
            
            if raw_data:
                match_payload = {
                    "id": m['id'], 
                    "wk": m['week_no'], 
                    "title": m['title'], 
                    "teams": json.loads(m['teams_json'] or "[]"), 
                    "date": m['date_label'], 
                    "status": "completed", 
                    "scores": process_fantasy_stats(raw_data['innings']) 
                }

                # Save JSON for audit/frontend
                with open(json_path, "w", encoding='utf-8') as f:
                    json.dump(match_payload, f, indent=2, ensure_ascii=False)
                
                # Sync back to Database using write lock
                try:
                    with db._write() as write_con:
                        _upsert_match(write_con, match_payload)
                    print(f"  ✅ SUCCESS: Match {m_num} persisted.")
                except Exception as db_err:
                    print(f"  ❌ DB ERROR: Match {m_num} could not be saved: {db_err}")

        await browser.close()
    print(f"\n{'='*60}\nSYNC COMPLETE\n{'='*60}\n")

if __name__ == "__main__":
    asyncio.run(main())
