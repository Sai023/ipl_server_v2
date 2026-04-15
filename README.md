# IPL Fantasy League 2026

A self-hosted fantasy cricket league for IPL 2026.  
Pick your XI each week, earn points from real matches, compete on a live leaderboard.

---

## Quick Start (Step by Step)

### Prerequisites
- **Python 3.11+** installed ([download](https://www.python.org/downloads/))
- **Git** installed ([download](https://git-scm.com/downloads))

### Step 1 — Clone the repository
```bash
git clone https://github.com/Sai023/ipl_server_v2.git
cd ipl_server_v2
```

### Step 2 — Install dependencies
```bash
pip install -r requirements.txt
```
Installs `Flask` and `requests` only. No browser or Playwright needed.

### Step 3 — Seed the player roster (first time only)
```bash
python Seed_Players.py
```
Populates ~220 IPL 2026 players with IDs, names, teams, prices, and roles.

### Step 4 — Seed the match schedule (first time only)
```bash
python Seed_Matches.py
```
**This is now fully automated.** It will:
1. Try to discover all 74 match IDs from Cricbuzz automatically
2. Fall back to the built-in IPL 2026 schedule (Match 1 ID `149618` confirmed)
3. Auto-detect how many matches are completed based on IST timestamps
4. Write the schedule to the database — no manual flags needed

**Optional flags:**
```bash
python Seed_Matches.py --no-live      # Skip Cricbuzz fetch (use hardcoded only)
python Seed_Matches.py --force        # Re-seed and refresh all statuses
python Seed_Matches.py --completed 15 # Override auto-detected completed count
python Seed_Matches.py --verify 149618  # Test a Cricbuzz scorecard URL
python Seed_Matches.py --debug        # Verbose output for troubleshooting
```

**Adding new Cricbuzz match IDs as the season progresses:**
1. Open `Seed_Matches.py`
2. Find the `IPL_2026_SCHEDULE` list
3. Update the `None` entry to the real ID for that match
4. ID is the number in the Cricbuzz URL: `cricbuzz.com/live-cricket-scores/**149618**/srh-vs-rcb...`
5. Run `python Seed_Matches.py --force` to apply

### Step 5 — Fetch match data from Cricbuzz
```bash
python scraper.py
```
Fetches scorecards for all completed matches and calculates fantasy points (~10 seconds).

### Step 6 — Start the server
```bash
python server.py
```

You'll see:
```
+=========================================================+
|  IPL FANTASY 2026                                       |
+=========================================================+
|| Local:    http://localhost:5000                        ||
|| Network:  http://192.168.1.X:5000  (same Wi-Fi)       ||
+=========================================================+
```

**On every startup**, the server automatically:
- Detects any completed match scores that have not yet been converted to fantasy points
- Recalculates `player_match_points` for all missing rows before accepting any requests
- Logs a per-week summary so you can confirm all weeks are scored correctly

This means the leaderboard is always accurate the moment the server starts, even if `scraper.py` ran while the server was offline.

### Step 7 — Share with friends via public tunnel
```bash
python server.py --tunnel
```
Auto-detects and uses the first available tunnel provider.

**Install a tunnel provider first (pick one):**

| Provider | Install | Notes |
|----------|---------|-------|
| **Cloudflare** (recommended) | [Download cloudflared](https://github.com/cloudflare/cloudflared/releases/latest) → add to PATH | Free, fast, stable |
| **ngrok** | [Download ngrok](https://ngrok.com/download) → add to PATH | Free tier available |
| **Pinggy** | No install — uses SSH | `python server.py --tunnel pinggy` |
| **localhost.run** | No install — uses SSH | `python server.py --tunnel localhostrun` |

For Cloudflare on Windows:
```powershell
# Download cloudflared.exe, place in e.g. C:\tools\ then:
$env:PATH += ";C:\tools"
python server.py --tunnel
```

---

## Architecture

```
Seed_Players.py   ──▶  players table (~220 players)
Seed_Matches.py   ──▶  matches table (74 IPL 2026 slots, auto-discovered IDs)
scraper.py        ──▶  Cricbuzz JSON → match_scores → player_match_points
server.py         ──▶  Flask API + UI  (reads all tables)
ipl_glue.js       ──▶  Frontend polling + auto-rollover
```

## How Data Flows

1. **Players**: `Seed_Players.py` → `players` table (id, name, team, price, role)
2. **Matches**: `Seed_Matches.py` → `matches` table (Cricbuzz scorecard URLs + status)
3. **Scores**: `scraper.py` → Cricbuzz JSON → fuzzy-match names to player IDs → `match_scores`
4. **Points**: `db_manager.calc_pts()` → `player_match_points` (base points per player per match)
5. **Selections**: Users pick teams via UI → `user_selections` (this_week + next_week)
6. **Leaderboard**: SQL joins selections × points with cap/VC multipliers — cumulative across all weeks

## Database Schema

| Table | Purpose |
|-------|---------|
| `players` | Player roster (id, name, team, role, price) |
| `matches` | Match schedule and status (id, week_no, title, status, scorecard_url) |
| `match_scores` | Raw per-player stats per match (runs, wickets, etc.) from scraper |
| `player_match_points` | Calculated fantasy points per player per match (base_pts, week_no) |
| `user_selections` | Weekly team picks (tw_team_json / nw_team_json, cap, vc) per user per week |
| `meta` | Key/value store for timestamps, seed version, rollover tracking |

**Points calculation flow:**  
`match_scores` (raw stats) → `calc_pts()` → `player_match_points.base_pts`  
Leaderboard applies cap (×2) / VC (×1.5) multipliers at query time from `_LEADERBOARD_SQL`.

## Weekly Rollover

Every **Monday at 14:00 UTC**:
- `next_week` selections become `this_week`
- A new week row is created (history preserved)
- If no `next_week` was set, `this_week` carries forward
- Season caps at 8 weeks

Manual trigger:
```bash
curl -X POST http://localhost:5000/api/rollover
curl -X POST http://localhost:5000/api/rollover?force=1
```

## Fantasy Points Scoring

| Category | Rule | Points |
|----------|------|--------|
| Playing | Appearing in XI | +4 |
| Batting | Per run | +1 |
| Batting | Per four | +1 bonus |
| Batting | Per six | +2 bonus |
| Batting | 30 / 50 / 100 | +4 / +8 / +16 |
| Batting | Duck (out for 0) | -2 |
| Batting | SR > 125 (min 10 balls) | +6 |
| Bowling | Per wicket | +25 |
| Bowling | LBW / Bowled bonus | +8 |
| Bowling | Maiden over | +12 |
| Bowling | Economy < 5 (min 2 ov) | +6 |
| Fielding | Catch | +8 |
| Fielding | 3+ catches bonus | +4 |
| Fielding | Stumping | +12 |
| Fielding | Direct run-out | +12 |
| Fielding | Run-out assist | +6 |
| Multiplier | Captain | 2× |
| Multiplier | Vice-Captain | 1.5× |

## Player Matching (Fuzzy Resolution)

The system resolves player inputs through 6 tiers:
1. **Exact ID** — `r01`, `k16`
2. **Exact name + team** — "Virat Kohli" + RCB
3. **Exact name** — "Virat Kohli"
4. **Semantic shorthand** — `vk` → Virat Kohli, `bumpy` → Jasprit Bumrah
5. **Token-set fuzzy** — "V Kohli" → Virat Kohli (≥40%)
6. **Surname match** — "Kohli" → Virat Kohli

## GitHub Actions (Automated Daily Sync)

The `.github/workflows/daily_sync.yml` runs daily at **18:30 UTC and 21:30 UTC**:
1. Installs dependencies
2. Seeds players (if empty)
3. Updates match statuses from IST schedule (no live Cricbuzz fetch — GitHub IPs are often blocked)
4. Runs `scraper.py` to fetch scorecards for newly completed matches
5. Commits updated data

**Run time: ~15–30 seconds.**

## Cricbuzz URL Reference

| URL pattern | Used for | Example |
|-------------|----------|---------|
| `/live-cricket-scores/{ID}/slug` | Series page links (source of match IDs) | `/live-cricket-scores/149618/srh-vs-rcb-...` |
| `/live-cricket-scorecard/{ID}` | Scorecard data (what scraper.py fetches) | `/live-cricket-scorecard/149618` |

Both use the same numeric ID. Seed_Matches.py discovers IDs from the first pattern and stores the second.

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/ping` | Health check + config |
| GET | `/api/state` | Full app state |
| GET | `/api/players` | Player roster |
| GET | `/api/leaderboard` | Cumulative rankings (all weeks) |
| GET | `/api/leaderboard?week=N` | Rankings for a specific week |
| GET | `/api/current-week` | Current week number |
| GET | `/api/history/<n>` | User's week-by-week team history |
| GET | `/api/player-points/<n>` | Per-player points breakdown for user |
| GET | `/api/debug-points/<n>` | Full debug: teams, caps, weekly totals |
| GET | `/api/matches-status` | All match IDs, weeks, and statuses |
| POST | `/api/save-next-week/<n>` | Save next week team draft |
| POST | `/api/rollover` | Trigger weekly rollover |
| POST | `/api/recalculate-points` | Force full points recalculation |
| POST | `/api/update-match-url` | Set/update a Cricbuzz scorecard URL |
| POST | `/api/resolve-player` | Test fuzzy player name matching |

## Configuration

| Setting | File | Default |
|---------|------|---------|
| Budget | `server.py` | 100.0 CR |
| Squad size | `server.py` | 11 |
| Season length | `server.py` | 8 weeks |
| Rollover time | `server.py` | Monday 14:00 UTC |
| DB path | `server.py` | `data/fantasy.db` |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "0 players loaded" | Run `python Seed_Players.py` |
| "0 completed matches" | Run `python Seed_Matches.py --force` |
| "no valid Cricbuzz ID" | Run `python Seed_Matches.py` to auto-discover IDs |
| Seed_Matches shows "Found 0 matches" | Cricbuzz blocked the request — use `--no-live` flag, then add IDs manually |
| Scraper returns no scorecard data | Test with `python Seed_Matches.py --verify 149618` |
| Leaderboard shows 0 | Check player IDs match between selections and match_scores; startup will log any ghosts |
| Points not updating after scraper run | Restart server — startup auto-detects and recalculates any missing pmp rows |
| Server won't start | Check `data/fantasy.db` exists and isn't locked |
| Tunnel not starting | Install cloudflared: https://github.com/cloudflare/cloudflared/releases |
