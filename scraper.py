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

# --- ARCHITECTURAL PATHING FOR CONTAINERS ---
BASE_DIR    = Path(__file__).resolve().parent
DB_DIR      = BASE_DIR / "data"
DB_PATH     = DB_DIR / "fantasy.db"
MATCHES_DIR = DB_DIR / "matches"
MAX_RETRIES = 3

def force_seed(db):
    """Bypasses external files to ensure 70 matches exist in the container environment."""
    print("!!! SELF-HEALING: SEEDING DATABASE !!!")
    start_id = 1527674
    # Standardized URL pattern for IPL 2026
    base_url = "https://espncricinfo.com"
    
    with db._write() as con:
        # We force matches 1-12 to 'completed' to trigger the immediate data harvest
        for i in range(1, 71):
            m_id_val = start_id + (i - 1)
            status = "completed" if i <= 12 else "upcoming"
            con.execute("""
                INSERT OR REPLACE INTO matches (id, week_no, title, status, scorecard_url, teams_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (f"ipl26_m{i:02d}", ((i-1)//7)+1, f"Match {i}", status, 
                  f"{base_url}/match-{m_id_val}/full-scorecard", "[]"))
    print("!!! SEED COMPLETE: 70 MATCHES READY !!!")

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
    """Maps raw HTML tables into granular dictionaries for the match_scores table."""
    player_stats = {}
    for table in raw_innings:
        rows = table.get('data', [])
        table_type = table.get('type', 'general')

        if table_type == 'batting':
            for row in rows:
                # Batting: [0]Player, [1]Dismissal, [2]R, [3]B, [4]M, [5]4s, [6]6s, [7]SR
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
                # Bowling: [0]Bowler, [1]O, [2]M, [3]R, [4]W, [5]Econ...
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
    """Full-scorecard extraction with retry logic and DOM waiting."""
    # Ensure URL points to the detailed scorecard
    target_url = url.replace("match-report", "full-scorecard")
    if "full-scorecard" not in target_url:
        target_url = f"{target_url}/full-scorecard"

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
        except Exception:
            await asyncio.sleep(2)
    return None

async def main():
    print(f"\n--- IPL 2026 ULTRA ENGINE STARTING ---")
    DB_DIR.mkdir(parents=True, exist_ok=True)
    MATCHES_DIR.mkdir(parents=True, exist_ok=True)
    
    db = DatabaseManager(DB_PATH)

    # 1. Verification & Auto-Seed
    with db._read() as con:
        res = con.execute("SELECT COUNT(*) FROM matches").fetchone()
        count = res[0] if res else 0
    
    if count == 0:
        force_seed(db)

    # 2. Extract Targets
    with db._read() as con:
        con.row_factory = sqlite3.Row
        targets = [dict(r) for r in con.execute("SELECT * FROM matches WHERE LOWER(status) = 'completed'").fetchall()]

    print(f"HARVESTING {len(targets)} MATCHES...")
    if not targets:
        print("DIAGNOSTIC: No matches in 'completed' state found.")
        return

    async with async_playwright() as pw:
        # Launch flags specifically for GitHub Ubuntu Runners / Docker Containers
        browser = await pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"])
        page = await browser.new_page()
        
        for m in targets:
            m_num = clean_id(m['id'])
            json_path = MATCHES_DIR / f"match_{m_num:02d}.json"
            
            # Skip if we already have detailed data
            if json_path.exists() and json_path.stat().st_size > 5000:
                continue
            
            print(f"Syncing Match {m_num}: {m['title']}")
            raw = await scrape_match(page, m['scorecard_url'], m_num)
            
            if raw:
                payload = { 
                    "id": m['id'], "wk": m['week_no'], "title": m['title'], 
                    "teams": json.loads(m['teams_json'] or "[]"), 
                    "date": m['date_label'], "status": "completed", 
                    "scores": process_fantasy_stats(raw['innings']) 
                }
                # Save JSON for audit
                with open(json_path, "w", encoding='utf-8') as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
                
                # Use db_manager's write lock to sync back to SQLite
                with db._write() as w_con:
                    _upsert_match(w_con, payload)
                    print(f"  ✅ SUCCESS: Match {m_num} persisted.")
        
        await browser.close()
    print("--- SYNC COMPLETE ---")

if __name__ == "__main__":
    asyncio.run(main())
