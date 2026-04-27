# IPL Fantasy League 2026

A self-hosted fantasy cricket league for IPL 2026.  
Pick your XI each week, earn points from real matches, compete on a live leaderboard.

> **Current version:** `2.1.0-stable` — decoupled Blueprint architecture, non-destructive startup, team-aware fuzzy matching, season_pts scouting badges.

---

## Quick Start

### Prerequisites
- **Python 3.11+** ([download](https://www.python.org/downloads/))
- **Git** ([download](https://git-scm.com/downloads))

### 1 — Clone
```bash
git clone https://github.com/Sai023/ipl_server_v2.git
cd ipl_server_v2
pip install -r requirements.txt
```

### 2 — Seed (first run only)
```bash
python Seed_Players.py    # ~220 IPL 2026 players
python Seed_Matches.py    # 74 match schedule (auto-discovers Cricbuzz IDs)
```

### 3 — Scrape & run
```bash
python scraper.py         # fetch scorecards + calculate fantasy points
python server.py          # local: http://localhost:5000
python server.py --tunnel # public URL via Cloudflare / ngrok / Pinggy
```

**After `git pull` (subsequent restarts):** `week_pts` and `season_pts` are preserved — the leaderboard shows correct historical values immediately without re-scraping. Only run `scraper.py` to score new matches.

---

## Architecture

```
config.py          ← Single source of truth (constants, versions)
base.py            ← Shared state (Flask app, db singleton, logging)
routes.py          ← 24 API endpoints in 8 groups (Blueprint)
server.py          ← Thin init: registers blueprint, starts tunnel
db_manager.py      ← Pure DAO: SELECT / INSERT / UPDATE only
logic/             ← scoring_engine, rollover_engine, fuzzy_match
tasks.py           ← Background scrape daemon
scraper.py         ← Cricbuzz JSON → fantasy points
ipl_glue.js        ← Frontend: polling, rollover, badges, picker
```

**Dependency rule:** `config → base → routes → server`. No module imports from a module above it in this chain. All shared state lives in `base.py`; no circular imports.

### Logic Engine

`logic/` modules are pure Python with zero project imports:

| Module | Purpose |
|--------|---------|
| `scoring_engine.py` | `calc_pts()` — authoritative scoring rules; `CAP_MULT=2.0`, `VC_MULT=1.5` |
| `rollover_engine.py` | Monday 14:00 UTC deadline detection |
| `fuzzy_match.py` | 6-tier team-aware player name resolution + dynamic player generation |

**Team-Aware Fuzzy Matching:** When a player name is ambiguous (e.g. "Noor Ahmad" plays for both CSK and GT), the resolver uses the batting/bowling team code from the scorecard as a tiebreaker. This eliminated phantom zero-scores from misattributed player stats.

**Dynamic Player Generation:** Unknown players (not in `Seed_Players.py`) are auto-added with collision-safe `ext_{cricbuzz_id}` IDs via `_generate_dynamic_player()`. The scraper never crashes on an unknown name.

### Frontend — `ipl_glue.js`

- **60-second ETag polling** — only fetches state when it changes.
- **Auto-rollover** — fires Monday 14:00 UTC in the browser without manual intervention.
- **`_patchXiGrid()`** — wraps the inline `_buildXiGrid()` function to inject `season_pts` badges on Next Week player cards at template render time. Cards and the `team[]` array are co-indexed (1:1), so each card receives the correct player's badge without any DOM search. Source: `_state.player_pts` embedded in `/api/state` — zero extra HTTP fetch.
- **Mobile keyboard dismiss** — `document.activeElement.blur()` fires inside `requestAnimationFrame` after pick/swap actions, ensuring the DOM update completes before the keyboard drops.

---

## How Data Flows

1. `Seed_Players.py` → `players` table (id, name, team, price, role)
2. `Seed_Matches.py` → `matches` table (Cricbuzz URLs + status)
3. `scraper.py` → Cricbuzz JSON → fuzzy-match names → `match_scores`
4. `calc_pts()` → `player_match_points` (base pts per player per match)
5. Users pick teams → `user_selections` (this_week + next_week, cap, vc)
6. Leaderboard: `SUM(week_pts)` from `user_selections` — authoritative, no join fan-out

---

## Database Schema

| Table | Purpose | Persistent? |
|-------|---------|-------------|
| `players` | Roster (id, name, team, role, price, season_pts, points) | ✅ Always |
| `matches` | Schedule + status + Cricbuzz URLs | ✅ Always |
| `user_selections` | Weekly picks + **week_pts** (leaderboard source of truth) | ✅ Always |
| `match_scores` | Raw per-player stats from scraper | ⚡ Cleared on restart |
| `player_match_points` | Calculated base pts per player per match | ⚡ Cleared on restart |
| `user_match_points` | Per-match team totals (used for matches_counted) | ⚡ Cleared on restart |
| `meta` | Timestamps, seed version, rollover tracking | ✅ Always |

**Ephemeral tables** are cleared on each server restart and repopulated by `scraper.py`. **Persistent tables** survive restarts and are the authoritative data source.

---

## Weekly Rollover

Every **Monday at 14:00 UTC** (16:00 SAST):
- `next_week` selections become `this_week`
- A new week row is created (full history preserved)
- If no `next_week` draft was set, `this_week` carries forward
- Season caps at 8 weeks

Manual trigger:
```bash
curl -X POST http://localhost:5000/api/rollover
curl -X POST http://localhost:5000/api/rollover?force=1
```

---

## Fantasy Points Scoring

| Category | Rule | Points |
|----------|------|--------|
| Playing | Appearing in XI | +4 |
| Batting | Per run | +1 |
| Batting | Per four | +1 bonus |
| Batting | Per six | +2 bonus |
| Batting | 30 / 50 / 100 | +4 / +8 / +16 |
| Batting | Duck (out for 0) | −2 |
| Batting | SR > 125 (min 10 balls) | +6 |
| Bowling | Per wicket | +25 |
| Bowling | LBW / Bowled bonus | +8 |
| Bowling | Maiden over | +12 |
| Bowling | Economy < 5 (min 2 ov) | +6 |
| Fielding | Catch | +8 (3+ → +4 bonus) |
| Fielding | Stumping | +12 |
| Fielding | Direct run-out | +12 |
| Fielding | Run-out assist | +6 |
| Multiplier | Captain | ×2 |
| Multiplier | Vice-Captain | ×1.5 |

---

## Player Matching (6-Tier Fuzzy Resolution)

| Tier | Method | Example |
|------|--------|---------|
| 1 | Exact ID | `r01`, `k16` |
| 2 | Exact name + team | "Noor Ahmad" + CSK |
| 3 | Exact name | "Virat Kohli" |
| 4 | Semantic shorthand | `vk` → Virat Kohli |
| 5 | Token-set fuzzy (≥40%) | "V Kohli" → Virat Kohli |
| 6 | Surname match | "Kohli" → Virat Kohli |
| Auto | Dynamic generation | Unknown → `ext_{cricbuzz_id}` |

**Team-aware disambiguation** is applied at Tiers 2 and 5 — the scorecard's batting/bowling team code resolves name conflicts before falling through to lower tiers.

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/version` | Module version pins |
| GET | `/api/ping` | Health check + config |
| GET | `/api/state` | Full app state (includes `player_pts`) |
| GET | `/api/players` | Player roster sorted by `season_pts DESC` |
| GET | `/api/leaderboard` | Cumulative rankings — pure `SUM(week_pts)` |
| GET | `/api/leaderboard?week=N` | Rankings for specific week |
| GET | `/api/current-week` | Current week number |
| GET | `/api/history/<n>` | User's week-by-week team history |
| GET | `/api/player-points/<n>` | Per-player points breakdown |
| GET | `/api/audit-scores/<n>` | Step-by-step scoring audit |
| GET | `/api/debug-points/<n>` | Ghost/unscored player check |
| GET | `/api/matches-status` | All match IDs, weeks, statuses |
| POST | `/api/save-next-week/<n>` | Save next week draft |
| POST | `/api/rollover` | Trigger weekly rollover |
| POST | `/api/recalculate-points` | Force full points recalculation |
| POST | `/api/update-match-url` | Set Cricbuzz scorecard URL + trigger scrape |
| POST | `/api/resolve-player` | Test fuzzy player name matching |

---

## Configuration

All constants are defined in `config.py` — single source of truth.

| Setting | Default |
|---------|---------|
| Budget | 100.0 CR |
| Squad size | 11 |
| Season length | 8 weeks |
| Rollover deadline | Monday 14:00 UTC (16:00 SAST) |
| DB path | `data/fantasy.db` |

---

## GitHub Actions (Daily Sync)

`.github/workflows/daily_sync.yml` runs at **18:30 UTC and 21:30 UTC**:
1. Seeds players (if empty)
2. Updates match statuses
3. Runs `scraper.py` for newly completed matches
4. Commits updated data

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| "0 players loaded" | Empty players table | Run `python Seed_Players.py` |
| "0 completed matches" | Stale status | Run `python Seed_Matches.py --force` |
| Leaderboard total ≠ sum of weekly columns | **Fan-out bug** — old SQL joined `user_match_points` inside `user_totals`, multiplying `week_pts` by match count. Fixed in v2.1.0 via two independent CTEs. | `git pull` + restart |
| W3 / any historical week shows 0 after restart | **Startup wipe bug** — old `server.py` ran `UPDATE user_selections SET week_pts=0` on every boot. Fixed in v13.2. | `git pull` + restart (no re-scrape needed) |
| season_pts badges not showing in Next Week tab | **Missing data-pid** — old approach used MutationObserver on `[data-pid]` but `_buildXiGrid()` never stamped those attrs. Fixed in v7.7 via `_patchXiGrid()` template injection. | `git pull` + hard-refresh (`Ctrl+Shift+R`) |
| Leaderboard shows 0 for all | Check player IDs match between selections and match_scores; startup logs any ghosts | Check startup log for `⚠ TRUE GHOST` lines |
| Points not updating after scraper | `player_match_points` cleared on restart — need a fresh scrape | Run `python scraper.py` |
| Server won't start — SyntaxError | Stale file with escaped quotes (`row[\"wn\"]`) | `git pull` (fixed in v5.9 patch) |
| Server won't start — ImportError | Circular import | Ensure `base.py` is present — `git pull` |
| Tunnel not starting | cloudflared not in PATH | Install: https://github.com/cloudflare/cloudflared/releases |
| Scraper returns no scorecard data | Bad Cricbuzz ID | Test: `python Seed_Matches.py --verify {id}` |
