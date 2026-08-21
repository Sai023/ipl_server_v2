#!/usr/bin/env python3
"""
Player Roster Seeder (multi-competition)
========================================
Populates the `players` table with IPL 2026 squad players.

ID convention:  {team_prefix}{number:02d}
  c=CSK  d=DC  g=GT  k=KKR  l=LSG  m=MI  p=PBKS  r=RCB  rr=RR  s=SRH

Roles: BAT, BOWL, AR (all-rounder), WK (wicketkeeper)

Usage:
    python Seed_Players.py          # wipe players + reseed (default)
    python Seed_Players.py --reset  # wipe players + match data + reseed

v2 changes:
  rr11: "Vaibhav Suryavanshi" -> "Vaibhav Sooryavanshi" (Cricbuzz/Cricinfo spelling)
  c11:  price 2.2 -> 8.0 (Dewald Brevis corrected)
"""

import argparse
import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH  = BASE_DIR / "data" / "fantasy.db"

def _load_roster(comp):
    """Load a competition\'s roster from data/<comp>/players.json.
    Each entry is a dict {"id","name","team","price","role"} (legacy
    [id,name,team,price,role] tuples are also accepted)."""
    fp = BASE_DIR / "data" / comp / "players.json"
    if not fp.exists():
        raise SystemExit(
            "Roster file not found: %s\n"
            "Create a JSON list of {id,name,team,price,role} there before "
            "seeding '%s'." % (fp, comp))
    raw = json.loads(fp.read_text(encoding="utf-8"))
    rows = []
    for e in raw:
        if isinstance(e, dict):
            rows.append((e["id"], e["name"], e["team"], float(e["price"]), e["role"]))
        else:
            rows.append((e[0], e[1], e[2], float(e[3]), e[4]))
    return rows


def seed(comp="ipl_2026", reset=False):
    players = _load_roster(comp)
    (BASE_DIR / "data").mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")

    if reset:
        print("  Clearing match data for %s (player_match_points, match_scores)..." % comp)
        conn.execute("DELETE FROM player_match_points WHERE competition_id=?", (comp,))
        conn.execute("DELETE FROM match_scores WHERE competition_id=?", (comp,))

    # Wipe ONLY this competition's roster before reseeding.
    print("  Clearing players for %s..." % comp)
    conn.execute("DELETE FROM players WHERE competition_id=?", (comp,))
    conn.commit()

    inserted = skipped = 0
    for pid, name, team, price, role in players:
        try:
            conn.execute(
                "INSERT INTO players (competition_id, id, name, team, price, role) "
                "VALUES (?,?,?,?,?,?)",
                (comp, pid, name, team, price, role))
            inserted += 1
        except sqlite3.IntegrityError as e:
            print("  Skip %s (%s): %s" % (pid, name, e))
            skipped += 1

    conn.commit()
    total = conn.execute(
        "SELECT COUNT(*) FROM players WHERE competition_id=?", (comp,)).fetchone()[0]
    conn.close()
    print("\n  Players seeded for %s: %d inserted, %d skipped "
          "(competition roster now %d)." % (comp, inserted, skipped, total))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Seed a competition's player roster from data/<comp>/players.json")
    parser.add_argument("--comp", default="ipl_2026",
                        help="competition slug (default: ipl_2026)")
    parser.add_argument("--reset", action="store_true",
                        help="also wipe this competition's match_scores + player_match_points")
    args = parser.parse_args()
    print("\n--- Player Roster Seeder (%s) ---" % args.comp)
    seed(comp=args.comp, reset=args.reset)
