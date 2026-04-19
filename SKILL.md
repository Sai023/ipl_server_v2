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
| db_manager.py | v5.7 | Schema, scoring, players.points, user_selections.points_per_match |
| server.py | v12.7 | /api/player-points self-contained with weeks[]+points+season_pts |
| scraper.py | v10.8 | Per-match atomic point update after every _upsert_match |
| ipl_glue.js | v7.4 | _playerMap cache, picker points badges, match-by-match totals |
| Seed_Players.py | v2 | Player roster (rr11=Sooryavanshi, c11 price=8.0) |
| Seed_Matches.py | v3.3 | Match schedule (74 matches, correct week labels W1-W10) |

---

## FULL-STACK POINTS ARCHITECTURE

### Data Layer — Two Distinct Player Point Columns

| Column | Table | Meaning | Source |
|--------|-------|---------|--------|
| `season_pts` | `players` | Base pts, **no cap/vc multiplier** — raw season total | SUM(`player_match_points.base_pts`) |
| `points` | `players` | Cap/VC-weighted pts earned when in a user's active XI | SUM of awarded pts from `user_match_points` |

- `update_player_season_pts()` → writes `players.season_pts` (base only). Called once at end of scraper run.
- `update_player_points()` → writes `players.points` (cap/vc-aware). Called inside `update_week_points()` after every match.
- **Picker UI** shows `★ points` (gold, cap/vc) as primary form guide and `Nb` (muted, base) as subtitle.
- **Leaderboard** Total column is cap/vc-weighted; labelled "Total ★" with tooltip.

### Storage — `user_selections.points_per_match` (Source of Truth)

```
user_selections.points_per_match  TEXT  NOT NULL  DEFAULT '{}'
```

- JSON blob `{match_id: awarded_pts}` written per **(display_name, week_no)** row.
- Each week row owns its own isolated blob — W3 data cannot bleed into W4.
- Written atomically by `update_week_points()` alongside `week_pts`.
- Returned by `/api/history/<n>` as `weeks[].points_per_match`.
- Returned by `/api/player-points/<n>` as `weeks[].points_per_match` (same shape).
- **Powers** the "📊 Match-by-Match Team Totals" section in the Points tab.

### API Strategy — `/api/player-points/<n>` is Self-Contained (v12.7)

Single call returns everything the Points tab needs:

```json
{
  "ok": true,
  "name": "Sai",
  "total_pts": 412,
  "players": [
    {
      "id": "c09", "name": "Sanju Samson", "team": "CSK",
      "season_pts": 187,
      "points": 374,
      "total_pts": 98,
      "is_cap": true, "is_vc": false,
      "matches": [{"match_id": "ipl26_m04", "base_pts": 49, "multiplier": 2.0, "final_pts": 98}]
    }
  ],
  "weeks": [
    {
      "week_no": 2,
      "week_pts": 412,
      "points_per_match": {"ipl26_m03": 210, "ipl26_m04": 202}
    }
  ]
}
```

- `players[].season_pts` — base pts from `players.season_pts` (no multiplier)
- `players[].points`     — cap/vc-weighted season total from `players.points`
- `players[].total_pts`  — this user's awarded pts from per-match breakdown
- `weeks[].points_per_match` — team total per match for match-by-match UI section
- Frontend **does not need a separate `/api/players` call** for the Points tab.

### Atomic Per-Match Update Pipeline (FIX-014, scraper v10.8)

After every `_upsert_match()` call in the scraper loop:
```
_upsert_match(wc, payload)              # writes match_scores rows for this match
    ↓
db.recalculate_points(match_id=iid)     # scoped to this match only → player_match_points
    ↓
db.update_week_points()                 # updates user_match_points, user_selections
                                        # .{week_pts, points_per_match}, players.points
    ↓
← scraper moves to next match           # user_selections is fully current
```

- `iid` is always the Seed_Matches.py ID (e.g. `ipl26_m04`) passed as `match_id=`.
- `recalculate_points(match_id=iid)` joins `match_scores → matches` to scope `week_no` — no cross-week leakage.
- Final `update_player_season_pts()` runs once at end for `players.season_pts`.

### W1-W10 Variable Isolation (enforced since seed v8)

**RULE: Every week in `_HISTORY_SEED` MUST use its own named variable. Never alias W3=W2.**

```python
# CORRECT — each week is independent
_SAI_W3_TEAM = ["d22","p10","c12","c02","g03","rr14","rr11","l11","c09","p03","s04"]
_SAI_W3_CAP  = "c09"
_SAI_W3_VC   = "rr11"

# WRONG — aliasing causes shared mutations if one week is updated
# _SAI_W3_TEAM = _SAI_W2_TEAM  ← NEVER DO THIS
```

To add W5: define `_SAI_W5_TEAM/_MOE_W5_TEAM` with their own list literals, add entries to `_HISTORY_SEED`, bump `_SEED_VERSION`.

---

## DB SCHEMA (db_manager.py v5.7)

```
players         (id, name, team, price, role,
                 season_pts INTEGER DEFAULT 0,   ← base pts, no multiplier
                 points     INTEGER DEFAULT 0)   ← cap/vc-weighted, updated per match

matches         (id, week_no, title, teams_json, date_label, status, scorecard_url, raw_json)

user_selections (display_name, week_no,
                 tw_team_json, tw_cap_id, tw_vc_id,
                 nw_team_json, nw_cap_id, nw_vc_id,
                 week_pts     INTEGER DEFAULT 0,
                 points_per_match TEXT DEFAULT '{}')  ← {match_id: pts} per week row

match_scores    (match_id, player_id, runs, balls, fours, sixes, got_out, duck, overs,
                 runs_conceded, wickets, maidens, lbw_bowled, catches, stumpings,
                 run_out_direct, run_out_assist, played, raw_score_json)

player_match_points (match_id, player_id, week_no, base_pts, multiplier, final_pts, calculated_at)

user_match_points   (display_name, week_no, match_id, pts)
                      PK: (display_name, match_id)  ← cap/vc baked in

meta            (key, value)
```

### Points Flow (full pipeline)
1. `scraper._upsert_match()` → `match_scores`
2. `db.recalculate_points(match_id=iid)` → `player_match_points` (scoped to this match)
3. `db.update_week_points()` →
   - `user_match_points` (per match, cap/vc applied)
   - `user_selections.week_pts` (week total)
   - `user_selections.points_per_match` ({match_id: pts} blob)
   - `players.points` (cap/vc-weighted season total via `update_player_points()`)
4. After all matches: `db.update_player_season_pts()` → `players.season_pts` (base only)
5. **Leaderboard total** = `SUM(user_match_points.pts)` — exact, per-match, cap/vc-aware

---

## API ENDPOINTS

| Method | Endpoint | Returns | Notes |
|--------|----------|---------|-------|
| GET | /api/state | Full app state | members + matches |
| GET | /api/players | All players | `id, name, team, price, role, season_pts, points` |
| GET | /api/leaderboard[?week=N] | Standings | from user_match_points (cap/vc exact) |
| GET | /api/player-points/{n} | **Self-contained** | players[]+weeks[points_per_match] |
| GET | /api/user-match-points/{n} | Per-match pts | from user_match_points table |
| GET | /api/history/{n} | Weekly history | includes points_per_match per week |
| GET | /api/matches-status | All match statuses | |
| GET | /api/current-week | Current week_no | |
| POST | /api/rollover | Season rollover | |
| POST | /api/recalculate-points | Rebuild all pts | calls season_pts + points |
| POST | /api/clean-scores | Wipe all scoring | resets season_pts AND points |
| POST | /api/update-match-url | Set scorecard URL | triggers bg scrape |
| POST | /api/save-next-week/{n} | Save draft | |

---

## FRONTEND (ipl_glue.js v7.4)

### `_playerMap` — Shared Player Stats Cache
```js
_playerMap[pid] = { id, name, team, role, price, points, season_pts }
// Loaded from /api/players on init and refreshed on ipl:state-updated
```

### Picker Injection (`_injectStatsToPicker`)
- **Gold badge** `★ N` = `players.points` (cap/vc-weighted) — primary form guide
- **Muted badge** `Nb` = `players.season_pts` (base) — secondary reference
- Injected via MutationObserver on all `[data-pid], .prow, .player-row` elements

### Points Tab (`_buildPointsTab`)
- Player table: "Pts (cap/vc)" column = `p.total_pts` from `/api/player-points`
- Player table: "Base" column = `p.season_pts` from same response
- **Match-by-Match Team Totals** section reads `d.weeks[].points_per_match` from `/api/player-points` response (no extra call)
- Falls back to `_historyData.weeks[].points_per_match` if `d.weeks` not available

### Leaderboard (`_buildLeaderboardCard`)
- Per-week columns (W1, W2, …) from `weekly[]` array
- Total column header: `Total ★` with tooltip `"Cap ×2 + VC ×1.5 applied"`
- League avg / top score legend below table

### Matches Tab (`_buildMatchesTab`)
- Clean match titles (no raw Cricbuzz timestamps)
- "My Pts" column from `/api/user-match-points/<n>` via `_umpData` cache
- Cache invalidated on `ipl:state-updated`

---

## SCORING RULES

| Category | Rule |
|----------|------|
| Playing | +4 |
| Batting | +runs, +fours, +sixes×2; SR>125 +6, SR>110 +4, SR>100 +2, SR<70 -2, SR<60 -4 (≥10 balls) |
| Milestones | 30+ +4, 50+ +8, 100+ +16; duck (got out, ≥1 ball, 0 runs) -2 |
| Bowling | wickets×25, lbw/bowled +8 each, maidens +12; eco<5 +6, <6 +4, <7 +2, >12 -6, >11 -4, >10 -2 (≥2 overs) |
| Wicket milestones | 2wkt +4, 3wkt +4, 4wkt +8, 5wkt +8 |
| Fielding | catch +8 (3+ catches: +4 bonus), stumping +12, direct run-out +12, assist +6 |
| Multipliers | Captain ×2.0, Vice-Captain ×1.5 |

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

## HISTORY SEED (server.py v12.7 — seed version "2026.v8.w3w4-defined")

```python
_SAI_W1_TEAM = ["k04","k19","s04","s05","s07","r01","r03","r11","m04","m07","m12"] | cap=k04 vc=s05
_SAI_W2_TEAM = ["d22","p10","c12","c02","g03","rr14","rr11","l11","c09","p03","s04"] | cap=c09 vc=rr11
_SAI_W3_TEAM = [same list as W2 — own variable]                                        | cap=c09 vc=rr11
_SAI_W4_TEAM = [same list as W2 — own variable]                                        | cap=c09 vc=rr11
_MOE_W1_TEAM = ["k04","m04","m07","m17","r02","r03","r12","s01","s04","k07","r16"]  | cap=r03 vc=s04
_MOE_W2_TEAM = ["m03","r05","k09","r16","p07","c11","rr04","s05","m11","s04","l01"] | cap=l01 vc=s04
_MOE_W3_TEAM = [same list as W2 — own variable]                                        | cap=l01 vc=s04
_MOE_W4_TEAM = [same list as W2 — own variable]                                        | cap=l01 vc=s04
# W5–W10: stub comments in place; define own variables when teams are known
```

---

## SCRAPER FIX HISTORY

| Fix | Version | Description |
|-----|---------|-------------|
| FIX-006 | v10.2 | `_m` anchor regex for match ID parsing |
| FIX-007 | v10.3 | `update_week_points()` after `recalculate_points()` |
| FIX-008 | v10.4 | Team-aware fuzzy match — Noor Ahmad CSK/GT collision |
| FIX-009 | v10.5 | IPL team validation — rejects non-IPL scorecards |
| FIX-010 | v10.6 | Column `teams` → `teams_json` crash fix |
| FIX-011 | v10.6 | SyntaxWarning escape sequences cleaned |
| FIX-012 | v10.7 | No-result/abandoned → empty scores, 0 pts |
| FIX-013 | v10.7 | `c and b X` caught-and-bowled dismissal handled |
| FIX-014 | v10.8 | Per-match atomic point update after every `_upsert_match()` |

---

## WEEK BOUNDARIES (Monday 14:00 IST rollover)

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
| W9 | May 18 – May 25 14:00 IST | M63-M70 |
| W10 | May 27+ | M71-M74 Playoffs |

---

## POST-RESTART WORKFLOW

```powershell
git pull
python Seed_Players.py        # (first time or after player changes)
python server.py --tunnel cloudflare   # clears JSON cache; run scraper after
python scraper.py             # re-scrapes all completed matches
                              # each match triggers per-match atomic update
```

## INTEGRITY GUARDRAILS

- **Ghost IDs** = IDs NOT in `players` table (absence from `player_match_points` after restart is normal — pmp is cleared on every startup).
- **Week isolation**: each `user_selections` row has its own `points_per_match` blob; W3 and W4 cannot share state even if `tw_team_json` is identical.
- **Audit flow**: query `user_selections.points_per_match` for the week → cross-check against `user_match_points` rows → verify `week_pts` = sum of blob values.
- **Cricbuzz timestamps** (`matchStartTimestamp`) are NOT Unix ms — never parse as dates. Display match title + week number instead.
- **Match 12** (KKR vs PBKS, ipl26_m12): rained out, no-result. v10.7 persists with empty scores, 0 pts.
- After every server restart: pmp is cleared → run `python scraper.py` before trusting any points totals.
- `players.points` is reset to 0 on restart and rebuilt by the scraper run.
