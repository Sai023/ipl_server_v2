---
name: ipl-fantasy-sync
description: "End-to-end orchestrator for the IPL 2026 Fantasy system (v2.0-decoupled). Governs the layered architecture across config.py, logic/ engines, db_manager.py DAO, tasks.py, scraper.py, server.py, and ipl_glue.js. Senior System Architect persona — prioritise modularity, one-file-at-a-time pushes, zero logic duplication."
---

# IPL Fantasy 2026 — ipl_server_v2  (`Sai023/ipl_server_v2`)
## Skill Version: v2.0-decoupled  |  Commit: `0411331`  |  Branch: `main`

---

## 1. ARCHITECTURE OVERVIEW — v2.0-decoupled

The system was refactored across 6 phases from a monolithic Flask app into a clean, layered architecture. Every layer has a single responsibility.

```
┌──────────────────────────────────────────────────────────────┐
│  ipl_glue.js  (v7.5)  — Browser / UI                        │
│  index.html + templates/  — Jinja2 rendering                 │
└───────────────────────┬──────────────────────────────────────┘
                        │ HTTP / JSON
┌───────────────────────▼──────────────────────────────────────┐
│  server.py  (v12.8)  — Thin Flask Controller                 │
│  • /api/* route handlers only                                │
│  • Calls db_manager DAO + logic/ engines                     │
│  • No scoring math. No rollover business logic.              │
└──────────┬─────────────────────┬────────────────────────────┘
           │                     │
┌──────────▼──────────┐  ┌───────▼────────────────────────────┐
│  db_manager.py      │  │  logic/  package                   │
│  (v5.9 — pure DAO)  │  │  ┌──────────────────────────────┐  │
│  SELECT/INSERT/     │  │  │ scoring_engine.py  (v1.1.0)  │  │
│  UPDATE only.       │  │  │ rollover_engine.py (v1.0.0)  │  │
│  No IPL rules.      │  │  │ fuzzy_match.py     (v1.0.0)  │  │
└──────────┬──────────┘  └──┴──────────────────────────────┴──┘
           │                     │
┌──────────▼──────────────────── ▼────────────────────────────┐
│  tasks.py (v1.0.0)  — Background Thread Orchestrator        │
│  scraper.py (v10.10) — Cricbuzz ingestion                   │
│  init_db.py (v1.0.0) — Startup auto-seed                   │
└───────────────────────┬──────────────────────────────────────┘
                        │ All import from ↓
┌───────────────────────▼──────────────────────────────────────┐
│  config.py  (v1.0.0)  — Single Source of Truth              │
│  DB_PATH, DEADLINE_HOUR/MIN, IPL_YEAR, APP_VERSION,         │
│  VERSION_MAP, per-module version pins                        │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. DEPENDENCY GRAPH (Import Hierarchy — NEVER violate)

```
config.py
    ↑ imported by:
    ├── logic/scoring_engine.py     (no project imports — stdlib only)
    ├── logic/rollover_engine.py    (no project imports — stdlib only)
    ├── logic/fuzzy_match.py        (no project imports — stdlib only)
    ├── db_manager.py               (imports config + logic/scoring_engine)
    ├── init_db.py                  (imports config + db_manager)
    ├── scraper.py                  (imports config + db_manager + logic/fuzzy_match)
    ├── tasks.py                    (imports config + db_manager + scraper)
    └── server.py                   (imports config + db_manager + init_db +
                                     tasks + logic/rollover_engine +
                                     logic/scoring_engine)
```

**Circular import rule:** `logic/` modules must never import from `db_manager`, `server`, `tasks`, or `scraper`. `config.py` must never import any project module.

---

## 3. FILE VERSIONS (current on `main`, tag `v2.0-decoupled`)

| File | Version | Role |
|------|---------|------|
| `config.py` | 1.0.0 | Global constants + VERSION_MAP |
| `logic/scoring_engine.py` | 1.1.0 | `calc_pts`, `apply_multiplier`, `debug_calc_pts`, `CAP_MULT`, `VC_MULT` |
| `logic/rollover_engine.py` | 1.0.0 | `last_monday_deadline`, `already_rolled`, `pick_active_team` |
| `logic/fuzzy_match.py` | 1.0.0 | `_norm`, `_build_player_index`, `_fuzzy_match`, `_fuzzy_fielder` |
| `db_manager.py` | 5.9 | Pure DAO (CRUD only) |
| `init_db.py` | 1.0.0 | `_auto_seed_*`, `run_all_sync()` |
| `tasks.py` | 1.0.0 | `start_bg_scrape()` daemon thread |
| `scraper.py` | 10.10 | Cricbuzz ingestion, `run_full_scrape()` export |
| `server.py` | 12.8 | Flask routes, `/api/version` |
| `ipl_glue.js` | 7.5 | `_checkVersionHandshake()`, `IplApi.getVersion()` |
| `Seed_Players.py` | v2 | Player roster (rr11=Sooryavanshi, c11 price=8.0) |
| `Seed_Matches.py` | v3.3 | 74 matches, week labels W1-W10 |

---

## 4. LAYER RESPONSIBILITIES

### 4.1 `config.py` — The Ground Truth
```python
from config import DB_PATH, DEADLINE_HOUR, DEADLINE_MIN, IPL_YEAR
from config import APP_VERSION, VERSION_MAP
from config import SERVER_VER, DB_VER, SCRAPER_VER, INIT_DB_VER, TASKS_VER
from config import SCORING_ENGINE_VER, ROLLOVER_ENGINE_VER, FUZZY_MATCH_VER
```
- Only file with `from pathlib import Path`. Zero project imports.
- **Always check `APP_VERSION` here before proposing any change.**
- `DEADLINE_HOUR = 14` means **14:00 UTC** (= 16:00 SAST). See §8 for details.

### 4.2 `logic/` — The Brains

**`scoring_engine.py` v1.1.0**
```python
from logic.scoring_engine import calc_pts, apply_multiplier, debug_calc_pts
from logic.scoring_engine import CAP_MULT, VC_MULT, _normalise_overs
```
- `calc_pts(s: dict) -> int` — authoritative scoring. Never duplicate this elsewhere.
- `apply_multiplier(base_pts, player_id, cap_id, vc_id) -> float`
- `debug_calc_pts(s, player_id, cap_id, vc_id) -> dict` — step-by-step trace for audit.
- `CAP_MULT = 2.0`, `VC_MULT = 1.5` — use constants, never hardcode `2.0`/`1.5`.

**`rollover_engine.py` v1.0.0**
```python
from logic.rollover_engine import last_monday_deadline, already_rolled, pick_active_team
```
- `last_monday_deadline(now, deadline_hour, deadline_min) -> datetime`
- `already_rolled(last_rollover_iso, lmd) -> bool`
- `pick_active_team(nw_team_json, nw_cap_id, nw_vc_id, tw_team_json, tw_cap_id, tw_vc_id, jloads) -> tuple`

**`fuzzy_match.py` v1.0.0**
```python
from logic.fuzzy_match import _norm, _build_player_index, _fuzzy_match, _fuzzy_fielder
```
- `_build_player_index(con)` — call once per scraper run.
- `_fuzzy_match(name, idx, team_hint)` — batter/bowler name → player ID.
- `_fuzzy_fielder(name, idx, bowling_team)` — fielder name → player ID.
- **Low-confidence matches (score < 0.45) return `None`** — the caller must log the unresolved name, not silently drop it. See §7 for the error-handling pattern.

### 4.3 `db_manager.py` v5.9 — Pure DAO

**What it does:** SELECT, INSERT, UPDATE rows. Nothing else.

**What it does NOT do:**
- ❌ No `rollover_season()` (removed Phase 5 — moved to `server.py api_rollover`)
- ❌ No `do_rollover()` (removed Phase 5)
- ❌ No local `calc_pts()` definition (removed Phase 4 — imported from `logic/`)
- ❌ No rollover business logic

**Key methods:**
```python
# Rollover DAO (called by server.py api_rollover)
db.get_users_and_max_weeks()          # → [{"display_name", "cur_wk"}]
db.get_selection_row(name, week_no)   # → dict | None
db.insert_rollover_week(name, new_wk, team_json, cap_id, vc_id)
db.set_last_rollover(iso)

# Scoring pipeline (called by scraper + server)
db.recalculate_points(match_id=None) # → int rows_written
db.update_week_points()              # → int rows_updated
db.update_player_season_pts()        # → int players updated
db.update_player_points()            # → int players with pts > 0

# Queries
db.get_state()          db.save_state(payload)
db.get_players()        db.get_history(name)
db.get_leaderboard(week_no) db.validate_budget(ids, budget)
db.save_next_week(name, team, cap, vc)
db.get_user_match_points(name)
db.ping_stats()         db.get_etags()     db.get_current_week()
db.get_meta(key)        db.set_meta(key, value)
```

### 4.4 `tasks.py` v1.0.0 — Background Thread Orchestrator

```python
from tasks import start_bg_scrape
tasks.start_bg_scrape(match_id, BASE_DIR)
```
- Spawns a **named daemon thread** `scrape-{match_id}`.
- The thread deletes stale JSON cache for that match, then calls `scraper.run_full_scrape(db)` in-process.
- Server calls this from `api_update_match_url` instead of a subprocess.

### 4.5 `scraper.py` v10.10 — Ingestion Utility

```python
from scraper import run_full_scrape
result = run_full_scrape(db=None)  # returns {"processed", "failed", "skipped_non_ipl", "no_result_count"}
```
- `main()` is a thin CLI wrapper around `run_full_scrape()`.
- Uses `logic/fuzzy_match` for all player name resolution.
- All DB writes go through `db_manager` (no raw sqlite3 except `_auto_add_player`).
- Raises `RuntimeError` (not `sys.exit`) on fatal errors so callers can handle.

### 4.6 `server.py` v12.8 — Thin Flask Controller

Route handlers follow the **pass-through pattern**:
1. Validate input
2. Fetch data via `db_manager` DAO
3. Process via `logic/` engines if needed
4. Return JSON

**`api_rollover` is now fully in-controller:**
```python
from logic.rollover_engine import last_monday_deadline, already_rolled, pick_active_team
# No db.rollover_season() — server orchestrates directly
lmd  = last_monday_deadline(now, DEADLINE_HOUR, DEADLINE_MIN)
if already_rolled(db.get_meta("_last_rollover",""), lmd): return ...
for u in db.get_users_and_max_weeks():
    nw_team, nw_cap, nw_vc = pick_active_team(...)
    db.insert_rollover_week(...)
db.set_last_rollover(now_iso)
```

**New endpoint (Phase 6):**
```
GET /api/version  → {ok, app_version, modules:{...}, version_map:{...}}
```

### 4.7 `ipl_glue.js` v7.5 — Frontend

`_checkVersionHandshake()` called from `_init()` on every page load:
- Hits `GET /api/version`
- Logs `APP_VERSION` + `VERSION_MAP` to browser console (styled group)
- Dispatches `ipl:version-ok` event with full payload

```js
IplApi.getVersion()   // → Promise<{ok, app_version, modules, version_map}>
```

Lock scheduler: `ROLLOVER_HOUR_UTC = 14`, `ROLLOVER_MIN_UTC = 0` — **matches backend exactly**.

---

## 5. OPERATIONAL PROCEDURES

### 5.1 Version Handshake (always do first)
```python
# In Python
from config import APP_VERSION, VERSION_MAP
print(APP_VERSION)   # e.g. "6.0.0"
```
```bash
# Via HTTP
curl http://localhost:5000/api/version
```
In the browser: open DevTools → Console → look for the `🏏 IPL Fantasy 6.0.0 — Decoupled v2.0 Backend ✓` group.

### 5.2 Post-Restart Workflow
```powershell
git pull
python Seed_Players.py           # first time, or after player roster changes
python server.py --tunnel cloudflare
# On startup: clears match_scores, pmp, user_match_points, resets season_pts/points
# Wait for banner, then:
python scraper.py                # re-scrapes all completed matches
                                 # each match triggers per-match atomic update
```

### 5.3 Adding a New Week's History
```python
# In init_db.py — NEVER alias W3=W2:
_SAI_W5_TEAM = ["k04","c09",...]   # explicit list literal
_SAI_W5_CAP  = "c09"
_SAI_W5_VC   = "k04"
# Add to _HISTORY_SEED, bump _SEED_VERSION
```

### 5.4 Adding a New Logic Rule
1. Add the function to the appropriate `logic/` engine.
2. Bump the engine version in `config.py` (e.g. `SCORING_ENGINE_VER = "1.2.0"`).
3. Update `VERSION_MAP` in `config.py` with a Phase-7 entry.
4. Import into `server.py` or `db_manager.py` as needed.
5. Push `config.py` first, then the engine, then the consumer — one file per commit.

### 5.5 Moe & Sai Audit
```bash
curl http://localhost:5000/api/audit-scores/Sai
curl http://localhost:5000/api/audit-scores/Moe
```
For a step-by-step trace of any score:
```python
from logic.scoring_engine import debug_calc_pts
t = debug_calc_pts(score_dict, player_id="k04", cap_id="k04", vc_id="s05")
print(t["steps"])    # per-component breakdown
print(t["base_pts"], t["multiplier"], t["final_pts"])
```

### 5.6 Adding a New API Endpoint
- Route handler fetches via `db.*`, processes via `logic.*`, returns `jsonify(...)`.
- No business logic inline in the route handler.
- Add to the API table in §6 of this skill.

---

## 6. API ENDPOINTS

| Method | Endpoint | Returns | Notes |
|--------|----------|---------|-------|
| GET | `/api/version` | `{app_version, modules, version_map}` | Phase 6 — version handshake |
| GET | `/api/state` | Full app state | members + matches |
| GET | `/api/players` | All players | `id, name, team, price, role, season_pts, points` |
| GET | `/api/leaderboard[?week=N]` | Standings | from `user_match_points` (cap/vc exact) |
| GET | `/api/player-points/{n}` | **Self-contained** | `players[]+weeks[points_per_match]` |
| GET | `/api/user-match-points/{n}` | Per-match pts | from `user_match_points` table |
| GET | `/api/history/{n}` | Weekly history | includes `points_per_match` per week |
| GET | `/api/audit-scores/{n}` | Step audit | `_calc_pts` from `logic.scoring_engine` |
| GET | `/api/matches-status` | All match statuses | |
| GET | `/api/current-week` | `week_no` | |
| GET | `/api/debug-points/{n}` | Ghost/unscored check | |
| POST | `/api/rollover[?force=1]` | Rollover result | orchestrated in-controller via rollover_engine |
| POST | `/api/recalculate-points` | Rebuild all pts | calls season_pts + points |
| POST | `/api/clean-scores` | Wipe scoring | resets season_pts AND points |
| POST | `/api/update-match-url` | Set scorecard URL | triggers `tasks.start_bg_scrape()` |
| POST | `/api/save-next-week/{n}` | Save draft | |
| POST | `/api/seed-history` | Re-seed history | idempotent, draft-preserving |

---

## 7. ERROR HANDLING PATTERNS

### 7.1 Fuzzy Match — Low Confidence Logging (mandatory)
When `_fuzzy_match` or `_fuzzy_fielder` returns `None`, the scraper **must log**, not silently drop:

```python
fid = _fuzzy_fielder(d["caught_by"], pidx, bowl_code)
if fid:
    fc.setdefault(fid, {...})
    fc[fid]["catches"] += 1
else:
    dropped_fielding.append(f"catch: '{d['caught_by']}'")   # ← REQUIRED

# At end of innings:
if dropped_fielding:
    print(f"    ⚠ DROPPED FIELDING CREDITS ({len(dropped_fielding)}):")
    for df in dropped_fielding:
        print(f"      - {df}")
```

Unresolved batters/bowlers are auto-added via `_auto_add_player()`. Unresolved fielders are logged as dropped credits. Neither case should silently pass.

### 7.2 Rate Limiter
`_write_limiter = _RateLimiter(30, 60)` — 30 writes per 60 seconds per IP. All POST/PUT routes check `_check_rate(_write_limiter)` before processing.

### 7.3 Background Scrape Error Isolation
`tasks._scrape_bg()` wraps everything in try/except. A scrape failure does not kill the Flask process.

### 7.4 startup Audit — Ghost ID Detection
On every server start, `_audit_player_id_coverage()` logs any player ID selected by a user that is absent from the `players` table. These are "true ghosts" — IDs that need fixing in `init_db._HISTORY_SEED` or `Seed_Players.py`.

---

## 8. LESSONS LEARNED — Internal Context

### 8.1 subprocess → Daemon Threads (Phase 3)

**Before (v12.7 and earlier):**
```python
# server.py api_update_match_url — old approach
def _scrape_bg():
    subprocess.run([sys.executable, "scraper.py"], cwd=str(BASE_DIR), timeout=120)
threading.Thread(target=_scrape_bg, daemon=True).start()
```

**Why it was wrong:**
- Spawned a new Python interpreter on every URL update.
- The child process opened its own DB connections — race conditions with the main WAL.
- No structured error reporting back to the parent.
- Wasted ~400ms per invocation on interpreter startup.

**After (Phase 3, `tasks.py` v1.0.0):**
```python
# tasks.py
def _scrape_bg(match_id, base_dir):
    db = DatabaseManager(DB_PATH)
    result = scraper.run_full_scrape(db)      # in-process, shared WAL pool

tasks.start_bg_scrape(match_id, BASE_DIR)    # named daemon thread
```

**Result:** In-process daemon thread shares the WAL-safe connection pool. Named `scrape-{match_id}` for debugging. `run_full_scrape()` raises `RuntimeError` instead of `sys.exit()` so the daemon can catch and log cleanly.

### 8.2 Timezone Alignment — 14:00 UTC vs 14:00 SAST

**The bug:** Early versions of `config.py` had this comment:
```python
DEADLINE_HOUR = 14  # Monday 14:00 SAST (12:00 UTC)  ← WRONG
```

**The truth:** SAST = UTC+2. So 14:00 SAST = 12:00 UTC. But `rollover_engine.py` compares against `datetime.now(timezone.utc)` with `DEADLINE_HOUR=14`. This means the deadline fires at **14:00 UTC = 16:00 SAST**.

**Fixed in Phase 6:**
```python
DEADLINE_HOUR = 14  # Monday 14:00 UTC = 16:00 SAST  ← CORRECT
```

**Verification:** `ipl_glue.js` uses `ROLLOVER_HOUR_UTC = 14` with `setUTCHours(14, 0)` — both sides lock at the same moment. Always cross-check the two when changing deadline logic.

### 8.3 rollover_season() Removal (Phase 5)

`db_manager.rollover_season()` was a 50-line method that mixed business logic (deadline checking, team selection) with DB writes. Violated the DAO principle.

**Resolution:** Business logic extracted to `logic/rollover_engine.py`. DB writes split into 4 thin DAO methods on `DatabaseManager`. `server.py api_rollover` orchestrates them. `db_manager.py` dropped from 40KB to 37KB with cleaner separation.

### 8.4 W1-W10 Variable Aliasing Bug

If `_SAI_W3_TEAM = _SAI_W2_TEAM` (reference alias), modifying one week in a future seed bump silently modifies the other. The seed system explicitly prohibits aliases:

```python
# WRONG — aliasing
_SAI_W3_TEAM = _SAI_W2_TEAM

# CORRECT — own literal, even if currently identical
_SAI_W3_TEAM = ["d22","p10","c12","c02","g03","rr14","rr11","l11","c09","p03","s04"]
```

---

## 9. FULL-STACK POINTS ARCHITECTURE

### 9.1 Two Distinct Player Point Columns

| Column | Table | Meaning | Updated by |
|--------|-------|---------|-----------|
| `season_pts` | `players` | Base pts, **no cap/vc** | `update_player_season_pts()` — end of scraper run |
| `points` | `players` | Cap/VC-weighted earned pts | `update_player_points()` — inside `update_week_points()` |

### 9.2 Atomic Per-Match Pipeline (FIX-014)

```
_upsert_match(wc, payload)              # writes match_scores for this match
    ↓
db.recalculate_points(match_id=iid)     # scoped to this match → player_match_points
    ↓
db.update_week_points()                 # user_match_points + user_selections
                                        # .{week_pts, points_per_match} + players.points
    ↓
← scraper moves to next match (user_selections is fully current)

# After all matches:
db.update_player_season_pts()           # players.season_pts (base, no multiplier)
```

### 9.3 `user_selections.points_per_match` — The Source of Truth

```
user_selections.points_per_match  TEXT  NOT NULL  DEFAULT '{}'
```

- JSON blob `{match_id: awarded_pts}` per **(display_name, week_no)** row.
- Each week row owns its own isolated blob — W3 cannot bleed into W4.
- Returned by `/api/history/<n>` as `weeks[].points_per_match`.
- Powers the "📈 Match-by-Match Team Totals" section in the Points tab.

### 9.4 `/api/player-points/<n>` — Self-Contained Response

```json
{
  "ok": true, "name": "Sai", "total_pts": 412,
  "players": [{
    "id": "k04", "name": "Varun Chakravarthy", "team": "KKR",
    "season_pts": 187, "points": 374,
    "total_pts": 218, "is_cap": true, "is_vc": false,
    "matches": [{"match_id": "ipl26_m04", "base_pts": 109, "multiplier": 2.0, "final_pts": 218}]
  }],
  "weeks": [{"week_no": 1, "week_pts": 412, "points_per_match": {"ipl26_m04": 218}}]
}
```

Frontend does **not** need a separate `/api/players` call for the Points tab.

### 9.5 Leaderboard Total Source
```sql
-- user_match_points is the authoritative cap/vc total
SELECT COALESCE(SUM(ump.pts), 0) AS total_pts
FROM user_selections us
LEFT JOIN user_match_points ump ON ump.display_name = us.display_name
```

---

## 10. SCORING RULES

| Category | Rule |
|----------|------|
| Playing | +4 |
| Batting | +runs, +fours, +sixes×2 |
| SR bonus | ≥10 balls: SR>125 +6, SR≥110 +4, SR≥100 +2, SR<70 -2, SR<60 -4 |
| Milestones | 30+ +4, 50+ +8, 100+ +16 |
| Duck penalty | got_out + ≥1 ball + 0 runs → -2 |
| Bowling | wickets×25, lbw/bowled +8 each, maidens +12 |
| Economy | ≥2 overs: eco<5 +6, <6 +4, <7 +2, >12 -6, >11 -4, >10 -2 |
| Wkt milestones | 2wkt +4, 3wkt +4, 4wkt +8, 5wkt +8 |
| Fielding | catch +8 (3+ bonus +4), stumping +12, direct RO +12, assist +6 |
| **Multipliers** | **Captain ×2.0 (`CAP_MULT`), Vice-Captain ×1.5 (`VC_MULT`)** |

**Audit trace:**
```python
from logic.scoring_engine import debug_calc_pts
# Moe — Phil Salt as CAP: 72 runs, 48 balls, 8 fours, 3 sixes
t = debug_calc_pts({"played":True,"runs":72,"balls":48,"fours":8,"sixes":3,"got_out":True},
                   player_id="r03", cap_id="r03", vc_id="s04")
# base_pts=104, multiplier=2.0, final_pts=208

# Sai — Varun Chakravarthy as CAP: 3 wkts, 1 lbw, 1 maiden, eco=6.0
t = debug_calc_pts({"played":True,"overs":4.0,"runs_conceded":24,"wickets":3,"lbw_bowled":1,"maidens":1},
                   player_id="k04", cap_id="k04", vc_id="s05")
# base_pts=109, multiplier=2.0, final_pts=218
```

---

## 11. DB SCHEMA (db_manager.py v5.9)

```
players         (id, name, team, price, role,
                 season_pts INTEGER DEFAULT 0,   ← base pts, no multiplier
                 points     INTEGER DEFAULT 0)   ← cap/vc-weighted, updated per match

matches         (id, week_no, title, teams_json, date_label, status, scorecard_url, raw_json)

user_selections (display_name, week_no,
                 tw_team_json, tw_cap_id, tw_vc_id,
                 nw_team_json, nw_cap_id, nw_vc_id,
                 week_pts     INTEGER DEFAULT 0,
                 points_per_match TEXT DEFAULT '{}')

match_scores    (match_id, player_id, runs, balls, fours, sixes, got_out, duck, overs,
                 runs_conceded, wickets, maidens, lbw_bowled, catches, stumpings,
                 run_out_direct, run_out_assist, played, raw_score_json)

player_match_points (match_id, player_id, week_no, base_pts, multiplier, final_pts, calculated_at)
                     PK: (match_id, player_id)

user_match_points   (display_name, week_no, match_id, pts)
                     PK: (display_name, match_id)  ← cap/vc baked in

meta            (key, value)
  _seed_version  — guards _HISTORY_SEED re-seed
  _last_rollover — Monday 14:00 UTC gate
  _saved         — UTC ISO timestamp of last write
```

---

## 12. PLAYER ID CONVENTION & CRITICAL IDs

`{team_prefix}{num:02d}` — `c`=CSK, `d`=DC, `g`=GT, `k`=KKR, `l`=LSG, `m`=MI, `p`=PBKS, `r`=RCB, `rr`=RR, `s`=SRH

| ID | Player | Team | Note |
|----|--------|------|------|
| `c09` | Sanju Samson | CSK | Traded from RR |
| `c11` | Dewald Brevis | CSK | price **9.0 CR** |
| `c12` | Noor Ahmad | CSK | Name conflict with `g03` (GT) → team-resolved |
| `g03` | Rashid Khan | GT | |
| `k04` | Varun Chakravarthy | KKR | Sai's W1 CAP |
| `l01` | Rishabh Pant | LSG | |
| `l11` | Aiden Markram | LSG | |
| `m03` | Jasprit Bumrah | MI | Moe's W2 CAP player |
| `r03` | Phil Salt | RCB | Moe's W1 CAP |
| `rr11` | Vaibhav **Sooryavanshi** | RR | Double-o — Cricbuzz/Cricinfo official spelling |
| `s04` | Ishan Kishan | SRH | Moe's W1 VC |
| `s05` | Abhishek Sharma | SRH | Sai's W1 VC |

---

## 13. HISTORY SEED (init_db.py — seed version `"2026.v8.w3w4-defined"`)

```python
# Sai
_SAI_W1_TEAM = ["k04","k19","s04","s05","s07","r01","r03","r11","m04","m07","m12"] # cap=k04 vc=s05
_SAI_W2_TEAM = ["d22","p10","c12","c02","g03","rr14","rr11","l11","c09","p03","s04"] # cap=c09 vc=rr11
_SAI_W3_TEAM = [same list — own variable]                                             # cap=c09 vc=rr11
_SAI_W4_TEAM = [same list — own variable]                                             # cap=c09 vc=rr11

# Moe
_MOE_W1_TEAM = ["k04","m04","m07","m17","r02","r03","r12","s01","s04","k07","r16"]  # cap=r03 vc=s04
_MOE_W2_TEAM = ["m03","r05","k09","r16","p07","c11","rr04","s05","m11","s04","l01"] # cap=l01 vc=s04
_MOE_W3_TEAM = [same list — own variable]                                             # cap=l01 vc=s04
_MOE_W4_TEAM = [same list — own variable]                                             # cap=l01 vc=s04
# W5-W10: add own variable + entry in _HISTORY_SEED + bump _SEED_VERSION
```

**Draft-preserving re-seed:** `_auto_seed_history_if_needed()` backs up `nw_team_json` for the current max week before wiping, then restores it after re-seeding. User's unsaved draft picks survive a seed version bump.

---

## 14. WEEK BOUNDARIES

| Week | Deadline | Matches |
|------|----------|---------|
| W1 | Mon Mar 31 14:00 UTC | M1-M2 |
| W2 | Mon Apr 7 14:00 UTC | M3-M11 |
| W3 | Mon Apr 14 14:00 UTC | M12-M20 |
| W4 | Mon Apr 21 14:00 UTC | M21-M29 |
| W5 | Mon Apr 28 14:00 UTC | M30-M38 |
| W6 | Mon May 5 14:00 UTC | M39-M46 |
| W7 | Mon May 12 14:00 UTC | M47-M54 |
| W8 | Mon May 19 14:00 UTC | M55-M62 |
| W9 | Mon May 26 14:00 UTC | M63-M70 |
| W10 | Season end | M71-M74 Playoffs |

**Deadline = 14:00 UTC = 16:00 SAST.** Both `rollover_engine.last_monday_deadline()` and `ipl_glue.js ROLLOVER_HOUR_UTC` agree on this value.

---

## 15. SCRAPER FIX HISTORY

| Fix | Version | Description |
|-----|---------|-------------|
| FIX-008 | v10.4 | Team-aware fuzzy match — Noor Ahmad CSK/GT collision |
| FIX-009 | v10.5 | IPL team validation — rejects non-IPL scorecards |
| FIX-010 | v10.6 | Column `teams` → `teams_json` crash fix |
| FIX-011 | v10.6 | SyntaxWarning escape sequences |
| FIX-012 | v10.7 | No-result/abandoned → empty scores, 0 pts |
| FIX-013 | v10.7 | `c and b X` caught-and-bowled dismissal |
| FIX-014 | v10.8 | Per-match atomic point update after every `_upsert_match()` |
| Phase 3 | v10.9 | `run_full_scrape()` export — callable by tasks.py |
| Phase 4 | v10.10 | Fuzzy functions extracted to `logic/fuzzy_match.py` |

---

## 16. INTEGRITY GUARDRAILS

- **Ghost IDs**: IDs selected by users but absent from `players` table. `_audit_player_id_coverage()` runs on every server start.
- **Week isolation**: each `user_selections` row has its own `points_per_match` blob. W3 and W4 cannot share state.
- **Audit flow**: `GET /api/audit-scores/{n}` → cross-checks `user_selections.points_per_match` vs `user_match_points` vs `week_pts`.
- **Match timestamps**: `matchStartTimestamp` is Cricbuzz's own format — not Unix ms. Display title + week number only.
- **No-result matches**: Match 12 (KKR vs PBKS) was rained out. `is_no_result=True` → empty scores, 0 pts, status=completed.
- **Post-restart**: `match_scores`, `player_match_points`, `user_match_points`, `season_pts`, `points` are all cleared on restart. Run `python scraper.py` before trusting any points totals.
- **Push order for changes**: `config.py` first → `logic/` engine → `db_manager.py` or `server.py` → `ipl_glue.js`. One file per commit.
