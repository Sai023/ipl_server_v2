import sqlite3
import json

def seed():
    conn = sqlite3.connect('data/fantasy.db')
    cursor = conn.cursor()

    # Define the teams and schedule structure
    teams = {
        "RCB": "royal-challengers-bengaluru",
        "SRH": "sunrisers-hyderabad",
        "MI":  "mumbai-indians",
        "KKR": "kolkata-knight-riders",
        "RR":  "rajasthan-royals",
        "CSK": "chennai-super-kings",
        "PBKS":"punjab-kings",
        "GT":  "gujarat-titans",
        "LSG": "lucknow-super-giants",
        "DC":  "delhi-capitals"
    }

    # Matches 1-13 (First two weeks)
    schedule = [
        ("RCB", "SRH"), ("MI", "KKR"), ("RR", "CSK"), ("PBKS", "GT"), ("LSG", "DC"),
        ("KKR", "SRH"), ("MI", "RR"), ("GT", "CSK"), ("PBKS", "LSG"), ("DC", "RCB"),
        ("RCB", "CSK"), ("MI", "SRH"), ("RR", "PBKS")
    ]

    base_url = "https://www.espncricinfo.com/series/ipl-2026-1510719"
    start_id = 1527674

    print("Cleaning and Seeding Matches...")
    cursor.execute("DELETE FROM matches")

    for i in range(1, 71):
        match_id_val = start_id + (i - 1)
        # Week logic: 1-7 = Wk 1, 8-14 = Wk 2, etc.
        week_no = ((i - 1) // 7) + 1
        
        # Matchup logic for seeding (looping our sample schedule for demonstration)
        t1_key, t2_key = schedule[(i-1) % len(schedule)]
        t1, t2 = teams[t1_key], teams[t2_key]
        
        # URL Construction
        url = f"{base_url}/{t1}-vs-{t2}-{i}th-match-{match_id_val}/match-report"
        
        # Status Logic: 1-12 Completed, 13+ Upcoming
        status = "completed" if i <= 12 else "upcoming"
        
        cursor.execute("""
            INSERT INTO matches (id, week_no, title, teams_json, date_label, status, scorecard_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            f"ipl26_m{i:02d}", 
            week_no, 
            f"Match {i}: {t1_key} vs {t2_key}", 
            json.dumps([t1_key, t2_key]),
            f"April {i}, 2026",
            status,
            url
        ))

    conn.commit()
    conn.close()
    print("Success: 70 Matches seeded into fantasy.db")

if __name__ == "__main__":
    seed()
