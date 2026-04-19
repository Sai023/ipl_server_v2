---
name: ipl-fantasy-sync
description: "End-to-end orchestrator for the IPL 2026 Fantasy system. Manages the lifecycle of player data from scraper.py ingestion to db_manager.py processing and server.py API delivery."
---

## Project: IPL Fantasy 2026 — ipl_server_v2 (GitHub: Sai023/ipl_server_v2)

### Stack
Python Flask (server.py) + SQLite (db_manager.py) + vanilla JS (ipl_glue.js) + Jinja2 (templates/index.html). PowerShell tunnel via cloudflared.

---

## KEY FILE VERSIONS (current on main)
| File | Version | Key role |
|------|---------|----------|
| db_manager.py | v5.6 | Schema, scoring, user_match_points, season_pts |
| server.py | v12.6 | Flask routes, startup rebuild, seed history |
| scraper.py | v10.7 | Cricbuzz JSON scraper, IPL validation, no-result handling |
| ipl_glue.js | v7.3 | Frontend API layer, matches tab, player pts in picker |
| Seed_Players.py | v2 | Player roster (rr11=Sooryavanshi, c11 price=8.0) |
| Seed_Matches.py | v3.3 | Match schedule (74 matches, correct week labels W1-W10) |

---

## ARCHITECTURE

### DB Schema (db_manager.py v5.6)
```
players         (id, name, team, price, role, season_pts)  ← season_pts = SUM(base_pts) from PMP
matches         (id, week_no, title, teams_json, date_label, status, scorecard_url, raw_json)
user_selections (display_name, week_no, tw_team_json, tw_cap_id, tw_vc_id, nw_team_json, nw_cap_id, nw_vc_id, week_pts)
match_scores    (match_id, player_id, runs, balls, fours, sixes, got_out, duck, overs, runs_conceded,
                 wickets, maidens, lbw_bowled, catches, stumpings, run_out_direct, run_out_assist, played, raw_score_json)
player_match_points (match_id, player_id, week_no, base_pts, multiplier, final_pts, calculated_at)
user_match_points   (display_name, week_no, match_id, pts)  ← NEW v5.6: per-match user pts (cap/vc applied)
meta            (key, value)
```

### Points Flow
1. scraper.py → match_scores (raw stats)
2. db.recalculate_points() → player_match_points (base_pts per player per match)
3. db.update_week_points() → user_match_points (per match, cap/vc applied) + user_selections.week_pts
4. db.update_player_season_pts() → players.season_pts (SUM base_pts season total)
5. Leaderboard total = SUM of user_match_points.pts (not weekly buckets)

### Week Boundaries (Monday 14:00 IST rollover)
| Week | Date Range | Matches |
|------|------------|---------|
| W1 | Mar 28 – Mar 30 14:00 IST | M1-M2 |
| W2 | Mar 30 – Apr 6 14:00 IST | M3-M11 |
| W3 | Apr 6 – Apr 13 14:00 IST | M12-M20 |
| W4 | Apr 13 – Apr 20 14:00 IST | M21-M29 |
| W5 | Apr 20 – Apr 27 14:00 IST | M30-M38 |
| W6 | Apr 27 – May 4 14:00 IST | M39-M46 |
| W7 | May 4 – May 11 14:00 IST | M47-M54 |
| W8 | May 11 – May 18 14:00 IST | M55-M62 |

---

## CRITICAL: Every History Week Needs Its Own Variable
Never alias `_SAI_W3_TEAM = _SAI_W2_TEAM` — define each week independently.
To add W5: define `_SAI_W5_TEAM/_MOE_W5_TEAM`, add to `_HISTORY_SEED`, bump `_SEED_VERSION`.

---

## POST-RESTART WORKFLOW
```powershell
git pull
python Seed_Players.py        # (first time or after player changes)
python server.py --tunnel cloudflare   # clears JSON cache on startup
python scraper.py             # re-scrapes all completed matches fresh
```

## INTEGRITY GUARDRAILS
- Ghost IDs = IDs NOT in players table (not absence from pmp after restart — that’s expected)
- Each history week MUST use its own team variable
- Cricbuzz timestamps are not standard Unix ms — use match title for display
- After restart, pmp is empty — run scraper before trusting points totals
