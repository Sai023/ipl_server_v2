#!/usr/bin/env python3
"""
IPL 2026 Daily Scraper - Update-Aware
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
    
    # Find matches that should be completed (date passed) or already completed
    today = datetime.now().strftime('%Y-%m-%d')
    
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
            match_num = int(match['id']) - 1527673
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
