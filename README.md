# IPL Fantasy League 2026

A self-hosted fantasy cricket league for IPL 2026.  
Pick your XI each week, earn points from real matches, compete on a live leaderboard.

> **Current version:** see `APP_VERSION` in [config.py](config.py) (live source of truth — surfaced by `GET /api/version`).
>
> The app is a decoupled Blueprint architecture with non-destructive startup,
> team-aware fuzzy matching, season_pts scouting badges, daily auto-sync
> (local APScheduler + cloud safety-net workflow), and a fan-out-proof
> leaderboard SQL.

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
- **Inline `season_pts` badges** — Next Week player cards display each player's season points inline in `_buildNwSquad()` ([templates/index.html:343](templates/index.html:343)). Source: `_state.player_pts` embedded in `/api/state` — zero extra HTTP fetch. (A pre-v7.8 `_patchXiGrid()` MutationObserver patch did the same job; it has since been replaced by direct inline rendering.)
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

## Daily Sync — two-tier architecture

The league stays current overnight via a **split** pipeline. Each half
runs in a different place because Cricbuzz blocks GitHub Actions egress IPs.

| Half | Where | When | Job |
|------|-------|------|-----|
| **Discovery** (find new Cricbuzz match IDs, write to `data/schedule.json`) | Operator's box only — APScheduler inside `server.py` ([tasks.start_daily_discovery_scheduler](tasks.py)) | **23:55 IST** daily | `logic.cricbuzz_discovery.run_discovery()` |
| **Scrape + commit** (fetch scorecards for known IDs, recalc points, push data) | GitHub Actions runner | **18:30 UTC** and **21:30 UTC** daily | `.github/workflows/daily_sync.yml` |

The cloud half can only scrape matches whose `cricbuzz_id` is already in
the committed `schedule.json`. If the operator's box has been offline since
before the latest match's discovery window, the cloud workflow simply
logs `SKIP: M…` for the missing IDs — the discovery half catches them
once the local box is online again.

**Manual trigger:** click the **🔄 Refresh** button on any tab (header
bar) — it `POST`s `/api/sync-now`, which runs the same
`run_discovery_and_scrape()` pipeline as the daily cron, in a daemon
thread so the UI returns immediately.

---

## HOSTED mode (Render / cloud deploy)

The app can run on **Render** (or any Linux host that sets `HOSTED=true`) instead of, or alongside, the operator's local box. Render's free tier is enough for a friends-only league.

**What HOSTED=true changes:**
- APScheduler discovery is skipped (Cricbuzz blocks Azure egress)
- The `cloudflared` tunnel is refused — the host platform already provides a public URL
- `_rebuild_scores_and_points()` is skipped on boot; ephemeral tables come from the committed `fantasy.db` via `git pull`
- `/api/sync-now` does `git pull --ff-only` instead of a Cricbuzz scrape
- Write endpoints (`save-next-week`, `member`, `rollover`, `recalculate-points`, `update-match-url`) commit + push the updated `fantasy.db` back to git via `cloud_sync.commit_and_push()`

**Data flow:**
```
Local box (Windows, APScheduler 23:55 IST):  discovery → push schedule.json
GitHub Actions monday_rollover.yml (Mon 14:00 UTC):  POSTs /api/rollover to host
GitHub Actions daily_sync.yml (18:30 + 21:30 UTC):  scrape known IDs → push fantasy.db (pull-rebase + retry)
Render host (HOSTED=true):  serves UI; git-pulls on /api/sync-now; git-pushes on user writes
```

**Deploy steps:**
1. Push the repo to GitHub (already done if you're reading this).
2. Connect the repo as a **Render Blueprint** — Render reads `render.yaml` and creates the service.
3. In the Render dashboard, set two secrets:
   - `GITHUB_TOKEN` — fine-grained PAT scoped to `Sai023/ipl_server_v2` with `contents: write`
   - `ROLLOVER_TOKEN` — any random string (e.g. `openssl rand -hex 24`)
4. In the GitHub repo Settings → Secrets and variables → Actions, add the same `ROLLOVER_TOKEN` plus `HOSTED_URL` (your Render URL, no trailing slash).
5. Render auto-deploys on `git push`. Cold start ~30s; subsequent reads are fast.

**Sleep behaviour:** Render free web services sleep after 15 min idle and auto-wake on incoming HTTP (~20-30s cold start). The `monday_rollover.yml` workflow retries 5x with 30s gaps to give the host time to wake.

**Failure modes & recovery:**
- Host asleep at 14:00 UTC Monday: workflow retries 5x; if all fail, the in-browser `setTimeout` in `Static/ipl_glue.js` fires when the next user logs in. `roll_week()` is idempotent.
- Git push conflict during user save: `cloud_sync.commit_and_push()` does pull-rebase and retries once. Failure leaves the change in the local container DB; next successful write catches it up.
- `daily_sync.yml` push race: workflow does pull-rebase + 3 retries with `concurrency: ipl-sync` serializing against `monday_rollover.yml`.

---

## Cloudflare Tunnel (public URL)

The project ships a vendored `cloudflared.exe` (Windows amd64) so a fresh
clone can run `python server.py --tunnel cloudflare` immediately. If
you're on a different OS or want the latest binary, run
`setup_cloudflare.ps1` — it downloads the right architecture from
Cloudflare's official releases page.

The tunnel URL changes on every restart. For a stable URL, register a
named tunnel via `cloudflared tunnel login` (not shipped by default).

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "0 players loaded" | Run `python Seed_Players.py` |
| "0 completed matches" or rows missing teams/dates | Run `python Seed_Matches.py --no-live` (re-sync from `schedule.json` without hitting Cricbuzz) |
| Admin tab shows a match with the wrong teams/date | Same as above — `Seed_Matches.py --no-live` rewrites title, teams, date_label from `schedule.json`. The scraper also auto-re-syncs on every run via `_presync_schedule` (which now delegates to `seed_to_db`). |
| Match has `⚠ No ID` even after Save & Scrape | The Cricbuzz scorecard for that URL was in `Preview` state or returned non-IPL teams. The scraper auto-resets the URL with a clear log line — paste a fresh Cricbuzz scorecard URL and try again. |
| Leaderboard shows 0 for everyone | Check `/api/audit-player-ids` for ghost player IDs in selections; run `Audit_Scores.ps1` |
| Points not updating after scraper | `player_match_points` is wiped on each restart and rebuilt by `scraper.py` — run `python scraper.py` |
| Tunnel not starting | Install cloudflared: see "Cloudflare Tunnel" section above |
| Scraper says "Cricbuzz returned no data" | Verify the ID: `python Seed_Matches.py --verify <id>` |
| Boot prints crash on Windows console with `UnicodeEncodeError` | Should not happen anymore — `server.py` reconfigures stdout/stderr to UTF-8 at startup. If you still see this, you're running an older revision; `git pull`. |
