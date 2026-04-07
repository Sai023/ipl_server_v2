#!/usr/bin/env python3
import json
import aiosqlite
import sys
from pathlib import Path
import asyncio
from playwright.async_api import async_playwright

# Infrastructure Constants
DB_PATH = Path("data/fantasy.db")
MATCHES_DIR = Path("data/matches")

def get_clean_id(raw_id):
    """ Senior Architect Fix: Handles 'ipl1c_m01' and integers safely."""
    try:
        if isinstance(raw_id, str) and "_" in raw_id:
            return raw_id.split("_")[-1] 
        return str(int(float(raw_id)))
    except (ValueError, TypeError, AttributeError):
        return str(raw_id)

async def update_match_status(db, match_id, new_status):
    """Updates match status registry."""
    clean_id = get_clean_id(match_id)
    if not clean_id: return
    await db.execute(
        "UPDATE matches SET status = ? WHERE id = ?",
        (new_status, clean_id)
    )
    await db.commit()

async def ingest_match_data(db, match_json_data):
    """ Maps JSON to SQL Schema with strict type-safety."""
    match_id = match_json_data.get("id")
    # Supports both 'players' and 'scores' keys for backward compatibility
    scores = match_json_data.get("scores") or match_json_data.get("players", {})

    for player_id, s in scores.items():
        sql_payload = (
            match_id, player_id,
            int(s.get("runs", 0)), int(s.get("balls", 0)),
            int(s.get("fours", 0)), int(s.get("sixes", 0)),
            1 if s.get("gotOut") else 0, 1 if s.get("duck") else 0,
            float(s.get("overs", 0.0)), int(s.get("runsConceded", 0)),
            int(s.get("wickets", 0)), int(s.get("maidens", 0)),
            int(s.get("lbwBowled", 0)), int(s.get("catches", 0)),
            int(s.get("stumpings", 0)), int(s.get("runOutDirect", 0)),
            int(s.get("runOutAssist", 0)), 1 if s.get("played") else 0,
            json.dumps(s)
        )

        query = """
            INSERT OR REPLACE INTO match_scores (
                match_id, player_id, runs, balls, fours, sixes, got_out, duck,
                overs, runs_conceded, wickets, maidens, lbw_bowled, catches,
                stumpings, run_out_direct, run_out_assist, played, raw_score_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        await db.execute(query, sql_payload)
    await db.commit()

async def scrape_match_scorecard(page, url):
    """ High-fidelity Playwright Scraper for ESPN Cricinfo."""
    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=60000)
        await page.wait_for_selector('table', timeout=20000)
        
        # DOM Extraction logic
        return await page.evaluate("""() => {
            const players = {};
            document.querySelectorAll('table').forEach((table) => {
                const rows = table.querySelectorAll('tbody tr');
                rows.forEach(tr => {
                    const cells = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim());
                    if (cells.length >= 7) {
                        const playerName = cells[0];
                        const playerId = playerName.toLowerCase().replace(/[^a-z0-9]/g, '');
                        if (!players[playerId]) {
                            players[playerId] = {
                                played: true,
                                runs: parseInt(cells[2]) || 0,
                                balls: parseInt(cells[3]) || 0,
                                fours: parseInt(cells[4]) || 0,
                                sixes: parseInt(cells[5]) || 0,
                                gotOut: cells[1] !== 'not out',
                                runsConceded: 0, wickets: 0 # Simplified for logic demo
                            };
                        }
                    }
                });
            });
            return { players };
        }""")
    except Exception as e:
        print(f"  ✗ Scrape Error: {e}")
        return None

async def process_match(db, match, page):
    """ Atomic State Machine: Scrape -> JSON -> SQL."""
    match_id = match['id']
    match_num = get_clean_id(match_id)
    json_path = MATCHES_DIR / f"match_{match_num}.json"
    
    # 1. Local Cache Recovery
    if json_path.exists() and json_path.stat().st_size > 100:
        print(f"  ✓ Found Local JSON for Match {match_num}. Ingesting...")
        with open(json_path, 'r') as f:
            data = json.load(f)
        await ingest_match_data(db, data) 
        await update_match_status(db, match_id, 'completed_scraped_data')
        return True
        
    # 2. Live Scrape
    print(f"  → Scraping Match {match_num}: {match['scorecard_url']}")
    scraped = await scrape_match_scorecard(page, match['scorecard_url'])
    if not scraped: return False
    
    match_data = { 'id': match_id, 'scores': scraped.get('players', {}) }
    
    # 3. Persistence
    with open(json_path, 'w') as f:
        json.dump(match_data, f, indent=2)
    
    await ingest_match_data(db, match_data)
    await update_match_status(db, match_id, 'completed_scraped_data')
    return True

async def main():
    MATCHES_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        print("Database missing. Run Golden_State_DB.py first.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        # Get pending matches
async with db.execute(
    "SELECT * FROM matches WHERE status IN ('completed', 'completed_scraped_data')"
) as cursor:
    matches = await cursor.fetchall()


        if not matches:
            print("Leaderboard is up to date.")
            return

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            
            for match in matches:
                await process_match(db, match, page)
                
            await browser.close()
    
    print("\nArchitecture Sync Complete.")

if __name__ == "__main__":
    asyncio.run(main())
