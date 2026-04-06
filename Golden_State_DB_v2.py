"""
Golden State DB v2 - Automated Ingestion with UPSERT
Scans data/matches/*.json and updates database
"""
import sqlite3
import json
from pathlib import Path
from glob import glob

DB_PATH = Path(__file__).parent / 'data' / 'fantasy.db'
MATCHES_DIR = Path(__file__).parent / 'data' / 'matches'

def init_database():
    """Initialize database with tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            id TEXT PRIMARY KEY,
            week_no INTEGER NOT NULL DEFAULT 1 CHECK (week_no >= 1),
            title TEXT NOT NULL DEFAULT '',
            teams_json TEXT NOT NULL DEFAULT '[]',
            date_label TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'upcoming'
                CHECK (status IN ('upcoming','live','completed')),
            scorecard_url TEXT,
            raw_json TEXT NOT NULL DEFAULT '{}'
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS match_data (
            match_id TEXT PRIMARY KEY,
            players_json TEXT NOT NULL DEFAULT '{}',
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (match_id) REFERENCES matches(id)
        )
    ''')
    
    conn.commit()
    conn.close()

def ingest_match_files():
    """Scan and ingest all match JSON files with UPSERT logic"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    json_files = sorted(glob(str(MATCHES_DIR / 'match_*.json')))
    ingested = 0
    
    for json_file in json_files:
        try:
            with open(json_file, 'r') as f:
                data = json.load(f)
            
            # UPSERT into match_data table
            cursor.execute('''
                INSERT INTO match_data (match_id, players_json, last_updated)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(match_id) DO UPDATE SET
                    players_json = excluded.players_json,
                    last_updated = CURRENT_TIMESTAMP
            ''', (data['id'], json.dumps(data.get('players', {}))))
            
            # Update match status if completed
            if data.get('status') == 'completed':
                cursor.execute('''
                    UPDATE matches 
                    SET status = 'completed'
                    WHERE id = ?
                ''', (data['id'],))
            
            ingested += 1
            
        except (json.JSONDecodeError, KeyError, IOError) as e:
            continue  # Skip corrupted files
    
    conn.commit()
    conn.close()
    
    return ingested

def seed_matches(matches_data):
    """Initial seed of matches table"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM matches')
    
    for match in matches_data:
        cursor.execute('''
            INSERT INTO matches (id, week_no, title, teams_json, date_label, status, scorecard_url, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            match['id'], match['week_no'], match['title'],
            match['teams_json'], match['date_label'], match['status'],
            match['scorecard_url'], match['raw_json']
        ))
    
    conn.commit()
    inserted = cursor.execute('SELECT COUNT(*) FROM matches').fetchone()[0]
    conn.close()
    
    return inserted

if __name__ == '__main__':
    DB_PATH.parent.mkdir(exist_ok=True)
    
    init_database()
    
    # Check if initial seed is needed
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute('SELECT COUNT(*) FROM matches').fetchone()[0]
    conn.close()
    
    if count == 0:
        # Initial seed
        with open('ipl_2026_matches_dataset.json', 'r') as f:
            matches = json.load(f)
        seed_count = seed_matches(matches)
        print(f"✓ Initial seed: {seed_count} matches")
    
    # Ingest match data from JSON files
    ingested = ingest_match_files()
    print(f"✓ Ingested {ingested} match files from /data/matches/")
