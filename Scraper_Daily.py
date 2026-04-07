#!/usr/bin/env python3
"""
IPL 2026 Daily Scraper - Update-Aware (Fixed ID handling)
Automatically detects and scrapes completed matches
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
import asyncio
from playwright.async_api import async_playwright

DB_PATH = Path("data/fantasy.db")
MATCHES_DIR = Path("data/matches")

def extract_match_number(match_id):
    """Extract numeric match number from ID (handles both numeric and prefixed IDs)"""
    match_id_str = str(match_id)
    
    # Strip prefix if present (e.g., 'ipl2026_12' -> '12')
    if '_' in match_id_str:
        match_id_str = match_id_str.split('_')[-1]
    
    # Try to extract numeric ID
    try:
        # If it's a full numeric ID like '1527674', calculate match number
        numeric_id = int(match_id_str)
        if numeric_id > 1527673:
            return numeric_id - 1527673
        return numeric_id
    except ValueError:
        return None

async def scrape_match_data(page, url):
    """Scrape match scorecard and extract player stats"""
    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=60000)
        await page.wait_for_selector('table', timeout=20000)
        
        data = await page.evaluate("""() => {
            const result = { innings: [], players: {} };
            document.querySelectorAll('table').forEach(table => {
                const rows = table.querySelectorAll('tbody tr');
                rows.forEach(tr => {
                    const cells = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim());
                    if (cells.length >= 6) result.innings.push(cells);
                });
            });
            return result;
        }""")
        return data
    except Exception as e:
        return None

async def main():
    MATCHES_DIR.mkdir(parents=True, exist_ok=True)
    
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    
    matches = con.execute("""
        SELECT id, week_no, title, teams_json, date_label, scorecard_url, status
        FROM matches 
        WHERE (status IN ('completed', 'upcoming'))
        ORDER BY id
    """).fetchall()
    
    con.close()
    
    count = 0
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        
        for match in matches:
            match_num = extract_match_number(match['id'])
            
            if match_num is None:
                continue
            
            json_path = MATCHES_DIR / f"match_{match_num:02d}.json"
            
            # Skip if file exists and is not empty
            if json_path.exists() and json_path.stat().st_size > 100:
                continue
            
            # Only scrape if status is completed
            if match['status'] != 'completed':
                continue
            
            try:
                scraped_data = await scrape_match_data(page, match['scorecard_url'])
                
                match_data = {
                    'id': match['id'],
                    'week': match['week_no'],
                    'title': match['title'],
                    'teams': json.loads(match['teams_json']),
                    'date': match['date_label'],
                    'status': 'completed',
                    'scorecard_url': match['scorecard_url'],
                    'players': scraped_data.get('players', {}) if scraped_data else {}
                }
                
                with open(json_path, 'w') as f:
                    json.dump(match_data, f, indent=2)
                
                count += 1
                await asyncio.sleep(1)
                
            except Exception as e:
                continue
        
        await browser.close()
    
    print(f"Daily sync complete: {count} matches updated")

if __name__ == "__main__":
    asyncio.run(main())
