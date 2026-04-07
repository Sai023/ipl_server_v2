#!/usr/bin/env python3
"""
IPL 2026 Unified Scraper - State Machine Architecture
Robust ID handling, status transitions, and data validation
"""
import json
import sqlite3
import re
import sys
from datetime import datetime
from pathlib import Path
import asyncio
from playwright.async_api import async_playwright

DB_PATH = Path("data/fantasy.db")
MATCHES_DIR = Path("data/matches")
MATCH_ID_OFFSET = 1527673  # Match 1 = 1527674

def get_clean_id(raw_id):
    """
    Sanitize match ID to integer match number
    Handles: '1527674', 'ipl2026_12', 'ipl26_m01', etc.
    Returns: Integer match number (1-70)
    """
    raw_str = str(raw_id).strip()
    
    # Extract numeric portion
    match = re.search(r'(\d+)$', raw_str)
    if not match:
        return None
    
    num_str = match.group(1)
    
    try:
        num = int(num_str)
        
        # If it's a full ID (>1527673), calculate match number
        if num > MATCH_ID_OFFSET:
            return num - MATCH_ID_OFFSET
        
        # Otherwise it's already a match number
        return num
        
    except ValueError:
        return None

def update_match_status(conn, match_id, new_status):
    """Update match status in database"""
    conn.execute(
        "UPDATE matches SET status = ? WHERE id = ?",
        (new_status, match_id)
    )
    conn.commit()

async def scrape_match_scorecard(page, url):
    """Scrape match data from ESPN Cricinfo"""
    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=60000)
        await page.wait_for_selector('table', timeout=20000)
        
        # Extract structured match data
        data = await page.evaluate("""() => {
            const players = {};
            
            // Parse batting innings
            document.querySelectorAll('table').forEach((table, idx) => {
                const rows = table.querySelectorAll('tbody tr');
                rows.forEach(tr => {
                    const cells = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim());
                    
                    // Batting row: [name, dismissal, runs, balls, 4s, 6s, SR]
                    if (cells.length >= 7) {
                        const playerName = cells[0];
                        const playerId = playerName.toLowerCase().replace(/[^a-z0-9]/g, '');
                        
                        if (!players[playerId]) {
                            players[playerId] = {
                                played: true,
                                runs: 0, balls: 0, fours: 0, sixes: 0,
                                gotOut: false, duck: false,
                                overs: 0, runsConceded: 0, wickets: 0,
                                maidens: 0, lbwBowled: 0,
                                catches: 0, stumpings: 0,
                                runOutDirect: 0, runOutAssist: 0
                            };
                        }
                        
                        players[playerId].runs = parseInt(cells[2]) || 0;
                        players[playerId].balls = parseInt(cells[3]) || 0;
                        players[playerId].fours = parseInt(cells[4]) || 0;
                        players[playerId].sixes = parseInt(cells[5]) || 0;
                    }
                });
            });
            
            return { players };
        }""")
        
        return data
        
    except Exception as e:
        return None

async def process_match(conn, match, page):
    """Process single match through state machine"""
    match_id = match['id']
    match_num = get_clean_id(match_id)
    
    if match_num is None:
        print(f"  ✗ Invalid ID format: {match_id}")
        return False
    
    json_path = MATCHES_DIR / f"match_{match_num:02d}.json"
    
    # State: completed → scrape → completed_scraped_data
    if match['status'] == 'completed':
        
        # Check if already scraped and valid
        if json_path.exists() and json_path.stat().st_size > 100:
            # Verify data integrity
            try:
                with open(json_path, 'r') as f:
                    data = json.load(f)
                if data.get('players'):
                    # Mark as scraped
                    update_match_status(conn, match_id, 'completed_scraped_data')
                    return True
            except:
                pass  # File corrupted, re-scrape
        
        # Scrape match data
        print(f"  → Scraping Match {match_num}: {match['title']}")
        
        scraped = await scrape_match_scorecard(page, match['scorecard_url'])
        
        if not scraped:
            print(f"  ✗ Scrape failed for Match {match_num}")
            return False
        
        # Build match JSON
        match_data = {
            'id': match_id,
            'week': match['week_no'],
            'title': match['title'],
            'teams': json.loads(match['teams_json']),
            'date': match['date_label'],
            'status': 'completed',
            'scorecard_url': match['scorecard_url'],
            'players': scraped.get('players', {})
        }
        
        # Save JSON
        with open(json_path, 'w') as f:
            json.dump(match_data, f, indent=2)
        
        # Transition to completed_scraped_data
        update_match_status(conn, match_id, 'completed_scraped_data')
        
        print(f"  ✓ Match {match_num} scraped and saved")
        return True
    
    return False

async def main():
    """Main scraper loop with state machine"""
    MATCHES_DIR.mkdir(parents=True, exist_ok=True)
    
    # Check if database exists
    if not DB_PATH.exists():
        print("Error: Database not found at data/fantasy.db")
        sys.exit(0)
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Get all completed matches (not yet scraped)
    matches = conn.execute("""
        SELECT id, week_no, title, teams_json, date_label, scorecard_url, status
        FROM matches 
        WHERE status IN ('completed', 'completed_scraped_data')
        ORDER BY id
    """).fetchall()
    
    if len(matches) == 0:
        print("No matches to scrape.")
        conn.close()
        sys.exit(0)
    
    print(f"Found {len(matches)} matches to process")
    
    count = 0
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        
        for match in matches:
            if await process_match(conn, match, page):
                count += 1
                await asyncio.sleep(1)
        
        await browser.close()
    
    conn.close()
    
    print(f"\nSync complete: {count} matches processed")

if __name__ == "__main__":
    asyncio.run(main())
