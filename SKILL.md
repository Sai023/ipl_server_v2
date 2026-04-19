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
1. `scraper.py` → `match_scores` (raw stats)
2. `db.recalculate_points()` → `player_match_points` (base_pts per player per match)
3. `db.update_week_points()` → `user_match_points` (per match, cap/vc applied) + `user_selections.week_pts`
4. `db.update_player_season_pts()` → `players.season_pts` (SUM base_pts season total)
5. Leaderboard total = SUM of `user_match_points.pts` (not weekly buckets)

### Scoring Rules
- Playing: +4 pts
- Batting: runs + fours + sixes×2; SR bonus/penalty (>=10 balls); 30/50/100 milestones; duck -2
- Bowling: wickets×25, lbw/bowled extra +8, maidens +12, economy bonus/penalty (>=2 overs)
- Fielding: catch +8 (3+ catches +4 bonus), stumping +12, direct run-out +12, assist +6
- Captain: ×2.0 | Vice-Captain: ×1.5

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

## PLAYER ID CONVENTION
`{team_prefix}{num:02d}` — c=CSK, d=DC, g=GT, k=KKR, l=LSG, m=MI, p=PBKS, r=RCB, rr=RR, s=SRH

### Critical IDs
| ID | Player | Team | Notes |
|----|--------|------|-------|
| c02 | Shivam Dube | CSK | |
| c09 | Sanju Samson | CSK | Traded from RR |
| c11 | Dewald Brevis | CSK | price 8.0 CR |
| c12 | Noor Ahmad | CSK | name conflict w/ g05 → team-resolved |
| d22 | Lungi Ngidi | DC | |
| g03 | Rashid Khan | GT | |
| g05 | Noor Ahmad | GT | name conflict w/ c12 → team-resolved |
| k04 | Varun Chakravarthy | KKR | |
| l01 | Rishabh Pant | LSG | |
| l11 | Aiden Markram | LSG | |
| p03 | Prabhsimran Singh | PBKS | |
| p07 | Marco Jansen | PBKS | |
| p10 | Yuzvendra Chahal | PBKS | |
| r05 | Bhuvneshwar Kumar | RCB | |
| rr04 | Shimron Hetmyer | RR | |
| rr11 | Vaibhav Sooryavanshi | RR | Double-o (Cricbuzz/Cricinfo official) |
| rr14 | Sam Curran | RR | |
| s04 | Ishan Kishan | SRH | |
| s05 | Abhishek Sharma | SRH | |

---

## HISTORY SEED (server.py v12.6 — seed version "2026.v8.w3w4-defined")
Every week MUST have its own explicit variable to avoid aliasing bugs:
```python
_SAI_W1_TEAM = ["k04","k19","s04","s05","s07","r01","r03","r11","m04","m07","m12"]
_SAI_W1_CAP  = "k04" | _SAI_W1_VC = "s05"
_SAI_W2_TEAM = ["d22","p10","c12","c02","g03","rr14","rr11","l11","c09","p03","s04"]
_SAI_W2_CAP  = "c09" | _SAI_W2_VC = "rr11"
_SAI_W3_TEAM = [same as W2] | _SAI_W3_CAP = "c09" | _SAI_W3_VC = "rr11"
_SAI_W4_TEAM = [same as W2] | _SAI_W4_CAP = "c09" | _SAI_W4_VC = "rr11"
_MOE_W1_TEAM = ["k04","m04","m07","m17","r02","r03","r12","s01","s04","k07","r16"]
_MOE_W1_CAP  = "r03" | _MOE_W1_VC = "s04"
_MOE_W2_TEAM = ["m03","r05","k09","r16","p07","c11","rr04","s05","m11","s04","l01"]
_MOE_W2_CAP  = "l01" | _MOE_W2_VC = "s04"
_MOE_W3_TEAM = [same as W2] | _MOE_W3_CAP = "l01" | _MOE_W3_VC = "s04"
_MOE_W4_TEAM = [same as W2] | _MOE_W4_CAP = "l01" | _MOE_W4_VC = "s04"
```
To add W5: define `_SAI_W5_TEAM` and `_MOE_W5_TEAM` with their own variables,
then add `("Sai", 5, _SAI_W5_TEAM, ...)` to `_HISTORY_SEED` and bump `_SEED_VERSION`.

---

## API ENDPOINTS
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | /api/state | Full app state (members + matches) |
| GET | /api/players | All players with season_pts |
| GET | /api/leaderboard[?week=N] | Standings from user_match_points |
| GET | /api/player-points/{n} | Per-player match breakdown for user n |
| GET | /api/user-match-points/{n} | Per-match pts for user n (new v5.6) |
| GET | /api/matches-status | All match statuses |
| GET | /api/current-week | Current week_no |
| GET | /api/history/{n} | Historical weekly selections |
| POST | /api/rollover | Season rollover |
| POST | /api/recalculate-points | Rebuild all pts from match_scores |
| POST | /api/clean-scores | Clear match_scores + pmp + week_pts |
| POST | /api/update-match-url | Set scorecard URL for a match |
| POST | /api/save-next-week/{n} | Save next week draft |

---

## SCRAPER FIXES APPLIED
| Fix | Version | Description |
|-----|---------|-------------|
| FIX-006 | v10.2 | `_m` anchor regex for match ID parsing |
| FIX-007 | v10.3 | `update_week_points()` called after `recalculate_points()` |
| FIX-008 | v10.4 | Team-aware fuzzy match — fixes Noor Ahmad CSK/GT collision |
| FIX-009 | v10.5 | IPL team validation — rejects non-IPL scorecards |
| FIX-010 | v10.6 | Column name `teams` → `teams_json` crash fix |
| FIX-011 | v10.6 | SyntaxWarning escape sequences cleaned |
| FIX-012 | v10.7 | No-result/abandoned → empty scores, 0 pts |
| FIX-013 | v10.7 | "c and b X" caught-and-bowled dismissal handled |

---

## POST-RESTART WORKFLOW
```powershell
git pull
python Seed_Players.py        # re-seed with corrected names/prices (if first time)
python server.py --tunnel cloudflare   # clears JSON cache on startup
python scraper.py             # re-scrapes all completed matches fresh
```

## MATCH 12 NOTE
Match 12 (KKR vs PBKS, ipl26_m12) was rained out — "No result (due to rain)".
v10.7 correctly handles: 0 pts awarded, match persisted as completed with empty scores.

## CRICBUZZ TIMESTAMP NOTE
Cricbuzz `matchStartTimestamp` is NOT a standard Unix ms timestamp. Raw values
(e.g. 17774706400000) should NOT be parsed as dates in the UI. The Matches tab
override in ipl_glue.js v7.3 displays match title + week instead of the raw timestamp.

## SCORING / AUDIT PROTOCOL
1. Query `match_scores` for specific `match_id`.
2. Cross-reference `user_selections.tw_team_json` for that week.
3. Apply cap (x2) / vc (x1.5) multipliers.
4. Verify against `user_match_points` (per-match total) and `user_selections.week_pts` (weekly sum).
5. `players.season_pts` = SUM(base_pts) across all matches (no multipliers — base only).

## INTEGRITY GUARDRAILS
- If scraper auto-adds a player not in seed, review and update Seed_Players.py.
- Ghost IDs = IDs not in `players` table (NOT absence from pmp after restart).
- After restart, pmp is always empty — run `python scraper.py` before trusting any points totals.
- Each history week MUST use its own team variable (never alias W3 = W2).
