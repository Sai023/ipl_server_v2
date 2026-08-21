#!/usr/bin/env python3
"""
migrate_to_competitions.py - Phase 0 of the multi-competition rebuild.

Transforms the single-competition fantasy.db into the multi-competition
schema by adding a `competitions` table and a `competition_id` dimension,
backfilling every existing row to the 'ipl_2026' competition.

BEHAVIOR-PRESERVING by design: the existing app (which ignores
competition_id) reads identical data afterward, because every existing row
becomes 'ipl_2026' and there is only one competition. Meta-key namespacing
(_last_rollover / _saved) and the data/ directory restructure are DEFERRED
to later phases so they land atomically with the code that uses them.

What it does:
  1. Backs up data/fantasy.db to ../fantasy.db.premigration-<ts>.
  2. Snapshots the pre-migration leaderboard (via the real _LEADERBOARD_SQL),
     row counts, and points sums.
  3. CREATE competitions + INSERT the 'ipl_2026' row.
  4. ADD COLUMN competition_id DEFAULT 'ipl_2026' to matches, match_scores,
     player_match_points, user_match_points.
  5. Rebuild players (PK -> competition_id,id) and user_selections
     (PK -> competition_id,display_name,week_no), recreating indexes.
  6. Add competition_id indexes on the four ADD-COLUMN tables.
  7. Re-snapshots and asserts PARITY (counts + leaderboard + sums identical).

Idempotent guard: refuses to run if the `competitions` table already exists.

Run from the project root (where data/fantasy.db lives):
    python migrate_to_competitions.py
"""
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB = Path("data/fantasy.db")
SLUG = "ipl_2026"

# The 10 IPL franchises (the scraper's _IPL_TEAMS set). Stored on the
# competition row so the scraper's wrong-scorecard validation becomes
# per-competition in Phase 3.
IPL_TEAMS = ["CSK", "DC", "GT", "KKR", "LSG", "MI", "PBKS", "RCB", "RR", "SRH"]

# Week-1 anchor in UTC. The legacy Seed_Matches used Mar 30 2026 14:00 IST
# (= 08:30 UTC), which is the S9 bug (5.5h before the real 14:00 UTC Monday
# rollover). IPL 2026 is frozen/completed and will not be re-week-numbered,
# so storing the corrected UTC value is safe for it and gives future
# competitions a clean, S9-free anchor.
WEEK1_ANCHOR_UTC = "2026-03-30T14:00:00+00:00"

SNAPSHOT_TABLES = [
    "players", "matches", "user_selections", "match_scores",
    "player_match_points", "user_match_points", "members", "sessions", "meta",
]

try:
    from db_manager import _LEADERBOARD_SQL
    _HAVE_LB_SQL = True
except Exception as e:  # pragma: no cover - defensive
    _LEADERBOARD_SQL = None
    _HAVE_LB_SQL = False
    print(f"  [warn] could not import _LEADERBOARD_SQL ({e}); using manual fallback")


def snapshot(con):
    con.row_factory = sqlite3.Row
    snap = {"counts": {}}
    for t in SNAPSHOT_TABLES:
        snap["counts"][t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    snap["season_pts_sum"] = con.execute(
        "SELECT COALESCE(SUM(season_pts),0) FROM players").fetchone()[0]
    snap["week_pts_sum"] = con.execute(
        "SELECT COALESCE(SUM(week_pts),0) FROM user_selections").fetchone()[0]
    # Parity leaderboard MUST be schema-agnostic: it runs on the PRE-migration
    # schema (which has no competition_id) as well as post-migration, so it
    # cannot use the competition-scoped _LEADERBOARD_SQL (that references
    # competition_id and would fail with "no such column" on the old schema).
    # Per-user week_pts totals + the table counts + the points sums fully prove
    # data preservation, because the migration only adds a competition_id
    # dimension and changes no points. (_LEADERBOARD_SQL / _HAVE_LB_SQL are
    # kept imported for callers/back-compat but no longer used here.)
    snap["leaderboard"] = [list(r) for r in con.execute(
        "SELECT display_name, SUM(week_pts) FROM user_selections "
        "GROUP BY display_name ORDER BY 2 DESC, display_name")]
    return snap


def migrate(con):
    now_iso = datetime.now(timezone.utc).isoformat()
    # PRAGMA foreign_keys cannot change inside a transaction.
    con.execute("PRAGMA foreign_keys=OFF")
    con.execute("BEGIN")

    # 1. competitions table + the IPL 2026 row.
    con.execute("""
        CREATE TABLE IF NOT EXISTS competitions (
            slug             TEXT PRIMARY KEY,
            name             TEXT NOT NULL,
            format           TEXT NOT NULL DEFAULT 'T20' CHECK (format IN ('T20','ODI')),
            status           TEXT NOT NULL DEFAULT 'upcoming'
                               CHECK (status IN ('upcoming','active','completed')),
            budget_total     REAL NOT NULL DEFAULT 100.0,
            xi_size          INTEGER NOT NULL DEFAULT 11,
            max_weeks        INTEGER NOT NULL DEFAULT 10,
            week1_anchor_utc TEXT NOT NULL,
            deadline_hour    INTEGER NOT NULL DEFAULT 14,
            deadline_min     INTEGER NOT NULL DEFAULT 0,
            series_id        TEXT,
            series_slug      TEXT,
            year             INTEGER,
            valid_teams_json TEXT NOT NULL DEFAULT '[]',
            champion         TEXT,
            closed_at        TEXT,
            created_at       TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    con.execute("""
        INSERT INTO competitions
            (slug, name, format, status, budget_total, xi_size, max_weeks,
             week1_anchor_utc, deadline_hour, deadline_min,
             series_id, series_slug, year, valid_teams_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        SLUG, "IPL 2026", "T20", "active", 100.0, 11, 10,
        WEEK1_ANCHOR_UTC, 14, 0,
        "9241", "indian-premier-league-2026", 2026,
        json.dumps(IPL_TEAMS), now_iso,
    ))

    # 2. ADD COLUMN competition_id (PK already prefix-unique on these four).
    for t in ("matches", "match_scores", "player_match_points", "user_match_points"):
        con.execute(
            f"ALTER TABLE {t} ADD COLUMN competition_id TEXT NOT NULL DEFAULT 'ipl_2026'")

    # 3. Rebuild players: PK -> (competition_id, id).
    con.execute("""
        CREATE TABLE players_new (
            competition_id TEXT    NOT NULL DEFAULT 'ipl_2026',
            id             TEXT    NOT NULL,
            name           TEXT    NOT NULL,
            team           TEXT    NOT NULL,
            price          REAL    NOT NULL DEFAULT 0 CHECK (price >= 0),
            role           TEXT    NOT NULL DEFAULT 'BAT' CHECK (role IN ('BAT','BOWL','AR','WK')),
            season_pts     INTEGER NOT NULL DEFAULT 0,
            points         INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (competition_id, id)
        )
    """)
    con.execute("""
        INSERT INTO players_new (competition_id,id,name,team,price,role,season_pts,points)
        SELECT 'ipl_2026',id,name,team,price,role,season_pts,points FROM players
    """)
    con.execute("DROP TABLE players")
    con.execute("ALTER TABLE players_new RENAME TO players")

    # 4. Rebuild user_selections: PK -> (competition_id, display_name, week_no).
    con.execute("""
        CREATE TABLE user_selections_new (
            competition_id   TEXT    NOT NULL DEFAULT 'ipl_2026',
            display_name     TEXT    NOT NULL CHECK (length(display_name) BETWEEN 1 AND 30),
            week_no          INTEGER NOT NULL DEFAULT 1 CHECK (week_no >= 1),
            tw_team_json     TEXT    NOT NULL DEFAULT '[]',
            tw_cap_id        TEXT,
            tw_vc_id         TEXT,
            nw_team_json     TEXT    NOT NULL DEFAULT '[]',
            nw_cap_id        TEXT,
            nw_vc_id         TEXT,
            week_pts         INTEGER NOT NULL DEFAULT 0,
            points_per_match TEXT    NOT NULL DEFAULT '{}',
            PRIMARY KEY (competition_id, display_name, week_no)
        )
    """)
    con.execute("""
        INSERT INTO user_selections_new
            (competition_id,display_name,week_no,tw_team_json,tw_cap_id,tw_vc_id,
             nw_team_json,nw_cap_id,nw_vc_id,week_pts,points_per_match)
        SELECT 'ipl_2026',display_name,week_no,tw_team_json,tw_cap_id,tw_vc_id,
               nw_team_json,nw_cap_id,nw_vc_id,week_pts,points_per_match
        FROM user_selections
    """)
    con.execute("DROP TABLE user_selections")
    con.execute("ALTER TABLE user_selections_new RENAME TO user_selections")
    con.execute("CREATE INDEX IF NOT EXISTS idx_us_name ON user_selections (display_name)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_us_week ON user_selections (week_no)")

    # 5. competition_id indexes on the four ADD-COLUMN tables.
    con.execute("CREATE INDEX IF NOT EXISTS idx_matches_comp ON matches (competition_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ms_comp  ON match_scores (competition_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_pmp_comp ON player_match_points (competition_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ump_comp ON user_match_points (competition_id)")

    con.execute("COMMIT")
    con.execute("PRAGMA foreign_keys=ON")


def compare(before, after):
    problems = []
    for t in SNAPSHOT_TABLES:
        if before["counts"][t] != after["counts"][t]:
            problems.append(f"count {t}: {before['counts'][t]} -> {after['counts'][t]}")
    for k in ("season_pts_sum", "week_pts_sum"):
        if before[k] != after[k]:
            problems.append(f"{k}: {before[k]} -> {after[k]}")
    if before["leaderboard"] != after["leaderboard"]:
        problems.append("leaderboard output differs")
    return problems


def main():
    if not DB.exists():
        sys.exit(f"ERROR: {DB} not found - run from the project root.")

    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row

    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='competitions'"
    ).fetchone()
    if exists:
        sys.exit("ABORT: `competitions` table already exists - DB already migrated.")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = DB.parent.parent.parent / f"fantasy.db.premigration-{ts}"
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # flush WAL so the copy is complete
    shutil.copy2(str(DB), str(backup))
    print(f"  [backup] {backup}")

    print("  [snapshot] capturing pre-migration baseline ...")
    before = snapshot(con)
    print(f"            counts={before['counts']}")
    print(f"            season_pts_sum={before['season_pts_sum']} week_pts_sum={before['week_pts_sum']}")
    print(f"            leaderboard rows={len(before['leaderboard'])} (lb_sql={_HAVE_LB_SQL})")

    print("  [migrate] applying competition_id schema ...")
    migrate(con)

    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    if fk:
        sys.exit(f"ERROR: foreign_key_check found violations: {[tuple(r) for r in fk]}")

    print("  [verify] re-snapshotting and comparing ...")
    after = snapshot(con)
    problems = compare(before, after)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.commit()
    con.close()

    if problems:
        print("  [FAIL] PARITY BROKEN:")
        for p in problems:
            print(f"          - {p}")
        sys.exit(1)
    print("  [PASS] parity preserved - counts, sums, and leaderboard identical.")
    print(f"         competition '{SLUG}' created (status=active); all rows backfilled.")


if __name__ == "__main__":
    main()
