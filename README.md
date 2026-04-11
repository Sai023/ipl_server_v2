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
This installs `Flask` and `requests` only. No browser or Playwright needed.

### Step 3 — Seed the player roster (first time only)
```bash
python Seed_Players.py
```
This populates ~220 IPL 2026 players into the database with IDs, names, teams, prices, and roles.

### Step 4 — Seed the match schedule (first time only)
```bash
python Seed_Matches.py --completed 12
```
This creates 74 match slots. Replace `12` with however many matches have been completed.

**Important:** Edit `Seed_Matches.py` and add real Cricbuzz match IDs to the `CB_MATCH_IDS` list as matches are scheduled. Find IDs from Cricbuzz URLs like `cricbuzz.com/live-cricket-scorecard/XXXXX`.

### Step 5 — Fetch match data from Cricbuzz
```bash
python scraper.py
```
This fetches scorecards for all completed matches and calculates fantasy points. Takes ~10 seconds.

### Step 6 — Start the server
```bash
python server.py
```

You'll see a banner like:
```
+=========================================================+
|  IPL FANTASY 2026                                       |
+=========================================================+
|| Local:    http://localhost:5000                        ||
|| Network:  http://192.168.1.X:5000  (same Wi-Fi)       ||
+=========================================================+
```

**Open the Local URL** in your browser: [http://localhost:5000](http://localhost:5000)

### Step 7 (Optional) — Share with friends via public tunnel
```bash
python server.py --tunnel
```
This auto-detects Cloudflare, ngrok, or Pinggy and gives you a public URL to share.

---

## Architecture

```
Seed_Players.py   ──▶  players table (~220 players)
Seed_Matches.py   ──▶  matches table (74 match slots)
scraper.py        ──▶  Cricbuzz JSON → match_scores → player_match_points
server.py         ──▶  Flask API + UI  (reads all tables)
ipl_glue.js       ──▶  Frontend polling + auto-rollover
```

## How Data Flows

1. **Players**: `Seed_Players.py` → `players` table (id, name, team, price, role)
2. **Matches**: `Seed_Matches.py` → `matches` table (Cricbuzz URLs + status)
3. **Scores**: `scraper.py` → Cricbuzz JSON → fuzzy-match names to player IDs → `match_scores`
4. **Points**: `db_manager.calc_pts()` → `player_match_points` (base points per player per match)
5. **Selections**: Users pick teams via UI → `user_selections` (this_week + next_week)
6. **Leaderboard**: SQL joins selections × points with cap/VC multipliers

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
4. **Semantic shorthand** — "vk" → Virat Kohli, "bumpy" → Jasprit Bumrah
5. **Token-set fuzzy** — "V Kohli" → Virat Kohli (≥40%)
6. **Surname match** — "Kohli" → Virat Kohli

## GitHub Actions (Automated Daily Sync)

The `.github/workflows/daily_sync.yml` runs daily at midnight UTC:
1. Installs `requests` + `Flask` (no Playwright)
2. Runs `scraper.py`
3. Commits updated data

**Run time: ~15 seconds** (vs 3+ minutes with old Playwright scraper).

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/ping` | Health check + config |
| GET | `/api/state` | Full app state |
| GET | `/api/players` | Player roster |
| GET | `/api/leaderboard` | Rankings |
| GET | `/api/current-week` | Current week number |
| GET | `/api/history/<name>` | User's week history |
| POST | `/api/save-next-week/<name>` | Save next week team |
| POST | `/api/rollover` | Trigger rollover |
| POST | `/api/resolve-player` | Test fuzzy matching |

## Updating Match IDs

As IPL 2026 matches are scheduled on Cricbuzz:
1. Open `Seed_Matches.py`
2. Add entries to `CB_MATCH_IDS`: `(match_no, "cricbuzz_id", "Title")`
3. Run `python Seed_Matches.py --completed N`
4. Run `python scraper.py`

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
| "0 players loaded" | Run `python Seed_Players.py` first |
| "0 completed matches" | Run `python Seed_Matches.py --completed N` |
| "no valid Cricbuzz ID" | Update `CB_MATCH_IDS` in `Seed_Matches.py` |
| Leaderboard shows 0 | Check that player IDs match between selections and match_scores |
| Server won't start | Check `data/fantasy.db` exists and isn't locked |
