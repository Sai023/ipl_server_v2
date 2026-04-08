#!/usr/bin/env python3
import asyncio
import json
import re
import sqlite3
import os
import sys
import random
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError
from db_manager import DatabaseManager, _upsert_match

# --- ARCHITECTURAL ALIGNMENT ---
BASE_DIR    = Path(__file__).resolve().parent
DB_PATH     = BASE_DIR / "data" / "fantasy.db" 
MATCHES_DIR = BASE_DIR / "data" / "matches"
MAX_RETRIES = 3

def force_seed(db):
    """FAIL-SAFE: Ensures the 70-match schedule exists."""
    print("!!! SELF-HEALING: INITIALIZING DATABASE SEED !!!")
    start_id = 1527674
    base_url = "https://espncricinfo.com"
    
    with db._write() as con:
        for i in range(1, 71):
            m_id_val = start_id + (i - 1)
            status = "completed" if i <= 12 else "upcoming"
            con.execute("""
                INSERT OR REPLACE INTO matches (id, week_no, title, status, scorecard_url, teams_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (f"ipl26_m{i:02d}", ((i-1)//7)+1, f"Match {i}", status, 
                  f"{base_url}/match-{m_id_val}/full-scorecard", "[]"))
    print(f"!!! SEED COMPLETE: Database sync successful.")

def clean_id(raw_id) -> int:
    m = re.search(r'(\d+)$', str(raw_id))
    if not m: return 0
    val = int(m.group(1))
    return val - 1527673 if val > 1000000 else val

def process_fantasy_stats(raw_innings):
    player_stats = {}
    for table in raw_innings:
        rows = table.get('data', [])
        table_type = table.get('type', 'general')

        if table_type == 'batting':
            for row in rows:
                if len(row) < 7 or "total" in row[0].lower(): continue
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

async def scrape_match(page, url):
    """Full-scorecard crawler with Stealth and Retry logic."""
    for attempt in range(MAX_RETRIES):
        try:
            # Jitter: Random delay to mimic human browsing
            await asyncio.sleep(random.uniform(2.5, 5.0))
            
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_selector("table.ds-table", timeout=15000)
            
            return await page.evaluate("""
                () => {
                    const results = { innings: [] };
                    document.querySelectorAll('table.ds-table').forEach(tbl => {
                        const rows = [];
                        const h = tbl.querySelector('thead tr')?.innerText || "";
                        let type = h.includes('R') && h.includes('B') ? 'batting' : (h.includes('O') ? 'bowling' : 'other');
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
            print(f"  ⚠️ Attempt {attempt+1} failed. Retrying...")
            await asyncio.sleep(5)
    return None

async def main():
    print(f"\n--- IPL 2026 MASTER ENGINE v8.5 (STEALTH) STARTING ---")
    MATCHES_DIR.mkdir(parents=True, exist_ok=True)
    
    db = DatabaseManager(DB_PATH)
    force_seed(db)

    with db._read() as con:
        con.row_factory = sqlite3.Row
        targets = [dict(r) for r in con.execute("SELECT * FROM matches WHERE LOWER(status) = 'completed'").fetchall()]

    print(f"IDENTIFIED: {len(targets)} matches for processing.")

    async with async_playwright() as pw:
        # Container-optimized flags + No-Sandbox
        browser = await pw.chromium.launch(
            headless=True, 
            args=[
                "--no-sandbox", 
                "--disable-dev-shm-usage", 
                "--disable-gpu", 
                "--disable-software-rasterizer"
            ]
        )
        
        # New Context with Realistic User-Agent
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            viewport={'width': 1920, 'height': 1080}
        )
        
        page = await context.new_page()
        
        # MASK WEBDRIVER: Prevents ESPN from identifying the browser as a bot
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        for m in targets:
            m_num = clean_id(m['id'])
            json_path = MATCHES_DIR / f"match_{m_num:02d}.json"
            if json_path.exists() and json_path.stat().st_size > 5000: continue
            
            print(f"SCRAPING: Match {m_num} - {m['title']}")
            raw = await scrape_match(page, m['scorecard_url'])
            
            if raw:
                payload = { 
                    "id": m['id'], "wk": m['week_no'], "title": m['title'], 
                    "teams": json.loads(m['teams_json'] or "[]"), 
                    "date": m.get('date_label', '2026-04-08'), "status": "completed", 
                    "scores": process_fantasy_stats(raw['innings']) 
                }
                with open(json_path, "w", encoding='utf-8') as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
                with db._write() as w_con:
                    _upsert_match(w_con, payload)
                    print(f"  ✅ PERSISTED: Match {m_num}")
            else:
                print(f"  ❌ FAILED: Match {m_num} (Possible Block)")

        await browser.close()
    print("--- SYNC COMPLETE ---")

if __name__ == "__main__":
    asyncio.run(main())
