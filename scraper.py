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

# --- ARCHITECTURAL CONFIGURATION ---
BASE_DIR    = Path(__file__).resolve().parent
DB_PATH     = BASE_DIR / "data" / "fantasy.db"
MATCHES_DIR = BASE_DIR / "data" / "matches"
MAX_RETRIES = 3

def clean_id(raw_id) -> int:
    if not raw_id: return 0
    m = re.search(r'(\d+)$', str(raw_id))
    if not m: return 0
    try:
        val = int(m.group(1))
        return val - 1527673 if val > 1000000 else val
    except ValueError: return 0

def process_fantasy_stats(raw_innings):
    """Refined indexing to ensure match_scores table is populated correctly."""
    player_stats = {}
    for table in raw_innings:
        rows = table.get('data', [])
        table_type = table.get('type', 'general')

        if table_type == 'batting':
            for row in rows:
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
        except Exception:
            await asyncio.sleep(2)
    return None

async def main():
    print(f"\n{'='*60}\nIPL 2026 MASTER ENGINE v4.0\n{'='*60}")
    MATCHES_DIR.mkdir(parents=True, exist_ok=True)
    db = DatabaseManager(DB_PATH)

    # --- FAIL-SAFE: SELF-HEALING SEED ---
    with db._read() as con:
        count = con.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    if count == 0:
        print("Empty DB detected. Running emergency seed...")
        try:
            from Seed_Matches import seed
            seed()
        except ImportError:
            print("CRITICAL: Seed_Matches.py missing.")
            return

    with db._read() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM matches WHERE LOWER(status) = 'completed'").fetchall()
        targets = [dict(r) for r in rows]

    if not targets:
        print("Status: Seeded but no matches marked 'completed' yet.")
        return

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"])
        page = await browser.new_page()

        for m in targets:
            m_num = clean_id(m['id'])
            json_path = MATCHES_DIR / f"match_{m_num:02d}.json"
            if json_path.exists() and json_path.stat().st_size > 5000: continue
            
            print(f"Syncing Match {m_num}...")
            raw_data = await scrape_match(page, m['scorecard_url'], m_num)
            
            if raw_data:
                match_payload = {
                    "id": m['id'], "wk": m['week_no'], "title": m['title'],
                    "teams": json.loads(m['teams_json'] or "[]"), "date": m['date_label'],
                    "status": "completed", "scores": process_fantasy_stats(raw_data['innings'])
                }
                with open(json_path, "w", encoding='utf-8') as f:
                    json.dump(match_payload, f, indent=2, ensure_ascii=False)
                with db._write() as write_con:
                    _upsert_match(write_con, match_payload)
        await browser.close()
    print("Sync Complete.")

if __name__ == "__main__":
    asyncio.run(main())
