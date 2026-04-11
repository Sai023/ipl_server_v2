# IPL Fantasy League 2026

A self-hosted fantasy cricket league for IPL 2026.  
Pick your XI each week, earn points from real match performances, and compete with friends on a live leaderboard.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌───────────────┐
│  index.html │────▶│  server.py  │────▶│  fantasy.db   │
│  ipl_glue.js│◀────│  (Flask)    │◀────│  (SQLite/WAL) │
└─────────────┘     └──────┬──────┘     └───────────────┘
                           │
                    ┌──────┴──────┐
                    │ db_manager  │
                    │   .py       │
                    └─────────────┘

┌──────────────┐     GitHub Actions (daily cron)
│  scraper.py  │────▶  Cricbuzz JSON API
│  (requests)  │────▶  data/matches/*.json
└──────────────┘────▶  fantasy.db updates
```

## Key Components

| File | Purpose |
|---|---|
| `server.py` | Flask API server — routes, fuzzy player matching, tunnel support |
| `db_manager.py` | SQLite manager — schema, CRUD, points engine, rollover |
| `scraper.py` | Cricbuzz JSON scraper — no browser needed, pure HTTP |
| `Seed_Matches.py` | Seeds the match schedule from Cricbuzz series page |
| `ipl_glue.js` | Frontend integration — polling, rollover scheduler, API wrapper |
| `templates/index.html` | Main UI |

## Quick Start (Local)

```bash
# 1. Clone
git clone https://github.com/Sai023/ipl_server_v2.git
cd ipl_server_v2

# 2. Install dependencies
pip install -r requirements.txt

# 3. Seed match schedule (first time only)
python Seed_Matches.py --completed 12

# 4. Run the scraper to fetch match data
python scraper.py

# 5. Start the server
python server.py

# 6. (Optional) Start with public tunnel for remote access
python server.py --tunnel
```

Open `http://localhost:5000` in your browser.

## How It Works

### Data Pipeline

1. **Seed**: `Seed_Matches.py` populates the `matches` table with Cricbuzz match IDs and URLs
2. **Scrape**: `scraper.py` fetches scorecard JSON from Cricbuzz (no browser, no Playwright)
3. **Process**: Batting, bowling, and fielding stats are parsed from the JSON and matched to players via fuzzy name resolution
4. **Store**: Stats go into `match_scores`, fantasy points are calculated and stored in `player_match_points`
5. **Serve**: `server.py` serves the UI and APIs

### Fantasy Points Engine

Defined in `db_manager.py → calc_pts()`. Key scoring:

- **Batting**: 1pt/run, +1/four, +2/six, +4/+8/+16 for 30/50/100, SR bonuses
- **Bowling**: 25pt/wicket, +8 LBW/bowled bonus, +12/maiden, economy bonuses
- **Fielding**: 8pt/catch (+4 bonus for 3+), 12pt/stumping, 12pt/direct run-out
- **Multipliers**: Captain = 2×, Vice-Captain = 1.5×
- **Base**: 4 pts for playing

### Weekly Rollover

Every **Monday at 14:00 UTC**:
- `next_week` selections become `this_week`
- A new week row is created (history-preserving)
- If no `next_week` was set, `this_week` carries forward
- Season caps at 8 weeks

Triggered automatically by `ipl_glue.js` in the browser, or manually via:
```bash
curl -X POST http://localhost:5000/api/rollover
curl -X POST http://localhost:5000/api/rollover?force=1  # bypass deadline
```

### Player Matching (Fuzzy Resolution)

The system resolves player inputs through 6 tiers:
1. **Exact ID** — `r01`, `k16`
2. **Exact name + team** — "Virat Kohli" + RCB
3. **Exact name** — "Virat Kohli"
4. **Semantic shorthand** — "vk" → Virat Kohli, "bumpy" → Jasprit Bumrah
5. **Token-set fuzzy** — "V Kohli" matches "Virat Kohli" (≥40% threshold)
6. **Surname match** — "Kohli" → Virat Kohli

The scraper uses the same fuzzy engine to match Cricbuzz player names to your `players` table.

## GitHub Actions (Automated Daily Sync)

The `.github/workflows/daily_sync.yml` workflow runs daily at midnight UTC:

1. Checks out the repo
2. Installs Python + `requests` + `Flask` (no Playwright/Chromium)
3. Runs `scraper.py` to fetch new match data from Cricbuzz
4. Commits updated `data/matches/*.json` and `data/fantasy.db`

**Run time: ~10-20 seconds** (vs 2-4 minutes with the old Playwright scraper).

Trigger manually: Actions tab → "IPL 2026 Daily Sync" → Run workflow.

## Database Schema

6 tables in `data/fantasy.db` (SQLite, WAL mode):

| Table | Purpose |
|---|---|
| `players` | Player registry (~200 players): id, name, team, price, role |
| `matches` | Match schedule: id, week_no, title, status, scorecard_url |
| `match_scores` | Per-player per-match raw stats (runs, wickets, catches...) |
| `player_match_points` | Calculated fantasy points per player per match |
| `user_selections` | Weekly team picks: this_week + next_week, captain/VC |
| `meta` | Key-value config store |

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/state` | Full app state (members, matches, scores) |
| GET | `/api/players` | Player roster with fuzzy lookup indices |
| GET | `/api/leaderboard` | Rankings with MVP, league avg |
| GET | `/api/current-week` | Current week number |
| GET | `/api/history/<name>` | Full week-by-week history for a user |
| GET | `/api/ping` | Health check + config |
| POST | `/api/save-next-week/<name>` | Save next week team (fuzzy-resolves inputs) |
| POST | `/api/rollover` | Trigger weekly rollover |
| POST | `/api/resolve-player` | Test fuzzy player matching |
| POST | `/api/state` | Bulk state save |
| PUT | `/api/member/<name>` | Upsert a member's selections |

## Configuration

In `server.py`:
- `BUDGET_TOTAL = 100.0` — Max team cost in CR
- `XI_SIZE = 11` — Squad size
- `MAX_WEEKS = 8` — Season length
- `DEADLINE_HOUR = 14` — Rollover time (UTC)

## Updating Match IDs

When new Cricbuzz match IDs become available:

1. Edit the `CB_MATCH_IDS` list in `Seed_Matches.py`
2. Run `python Seed_Matches.py --completed N` (where N = completed matches)
3. Run `python scraper.py` to fetch scorecard data

Or if the Cricbuzz series page is live:
```bash
python Seed_Matches.py --series-id XXXX --completed N
```

## Tech Stack

- **Backend**: Python 3.11, Flask, SQLite (WAL mode)
- **Scraper**: `requests` library → Cricbuzz JSON (no browser)
- **Frontend**: Vanilla HTML/JS, `ipl_glue.js` integration layer
- **CI/CD**: GitHub Actions (daily cron)
- **Tunnel**: Optional Cloudflare/ngrok/Pinggy for public access
