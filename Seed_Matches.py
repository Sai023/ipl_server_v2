import sqlite3
import json

def seed():
    conn = sqlite3.connect('data/fantasy.db')
    cursor = conn.cursor()

    # Matches 1-13 (Sequence based on your provided construction)
    # Using 'ipl26_mXX' format to match your scraper's clean_id logic
    base_url = "https://espncricinfo.com"
    start_id = 1527674

    print("Force-Syncing Matches table...")

    for i in range(1, 71):
        match_id_val = start_id + (i - 1)
        # 1-12 Completed, 13+ Upcoming
        status = 'completed' if i <= 12 else 'upcoming'
        
        # We use INSERT OR REPLACE to avoid primary key conflicts
        cursor.execute("""
            INSERT OR REPLACE INTO matches (id, week_no, title, status, scorecard_url, teams_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            f"ipl26_m{i:02d}", 
            ((i - 1) // 7) + 1,
            f"Match {i}",
            status,
            f"{base_url}/match-{match_id_val}/match-report",
            json.dumps([])
        ))

    conn.commit()
    conn.close()
    print("✅ Local Database Ready.")

if __name__ == "__main__":
    seed()
