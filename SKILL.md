---
name: ipl-fantasy-sync
description: "End-to-end orchestrator for the IPL 2026 Fantasy system (v2.0-stable). Governs the layered architecture across config.py, logic/ engines, db_manager.py DAO, tasks.py, scraper.py, server.py, routes.py, and ipl_glue.js. Senior System Architect persona — prioritise modularity, one-file-at-a-time pushes, zero logic duplication."
---

# IPL Fantasy 2026 — ipl_server_v2  (`Sai023/ipl_server_v2`)
## Skill Version: v2.0-stable  |  APP_VERSION: 2.0.0-stable  |  Branch: `main`

---

## 1. ARCHITECTURE OVERVIEW — v2.0-stable

```
┌──────────────────────────────────────────────────────────────┐
│  ipl_glue.js  (v7.5)  — Browser / UI                        │
│  index.html + templates/  — Jinja2 rendering                 │
└───────────────────────┬──────────────────────────────────────┘
                        │ HTTP / JSON
┌───────────────────────▼──────────────────────────────────────┐
│  server.py  (v13.0)  — Thin Flask Initialiser                │
│  • Shared state, helpers, db singleton, Flask app            │
│  • Error handlers, middleware, startup functions             │
│  • Registers Blueprint:  from routes import bp               │
│  • Tunnel, banner, __main__                                  │
├──────────────────────────────────────────────────────────────┤
│  routes.py  (v1.0.0) — API Router (Blueprint)                │
│  • All 24 @bp.route handlers in 8 labelled groups            │
│  • Imports shared state from server.py (safe circular)       │
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
    ├── server.py                   (imports config + db_manager + init_db +
    │                                tasks + logic/rollover_engine +
    │                                logic/scoring_engine)
    └── routes.py                   (imports from server via safe deferred circular;
                                     also imports config, db_manager, init_db,
                                     tasks, logic/rollover_engine, logic/scoring_engine)
```

**Circular import safety:** `server.py` defines `db`, `app`, `_log`, `_write_limiter`, etc. at module level, then `from routes import bp` at the bottom of module-level code. By then Python's `sys.modules['server']` already has all those names set, so `routes.py`'s `from server import db, ...` resolves safely.

---

## 3. FILE VERSIONS (2.0.0-stable)

| File | Version | Role |
|------|---------|------|
| `config.py` | 1.0.0 | Global constants + VERSION_MAP |
| `logic/scoring_engine.py` | 1.1.0 | `calc_pts`, `apply_multiplier`, `debug_calc_pts`, `CAP_MULT=2.0`, `VC_MULT=1.5` |
| `logic/rollover_engine.py` | 1.0.0 | Monday 14:00 UTC deadline logic |
| `logic/fuzzy_match.py` | 1.0.0 | Player name resolution |
| `db_manager.py` | 5.9 | Pure DAO |
| `routes.py` | 1.0.0 | **NEW (Phase 7)** — 24 API handlers (Blueprint, 8 groups) |
| `server.py` | 13.0 | Flask init + middleware + blueprint registration (~350 lines) |
| `tasks.py` | 1.0.0 | Daemon thread orchestration |
| `scraper.py` | 10.10 | Cricbuzz ingestion |
| `init_db.py` | 1.0.0 | `_auto_seed_*`, `run_all_sync()` |
| `ipl_glue.js` | 7.5 | Version handshake, `IplApi.getVersion()` |
| `Seed_Players.py` | v2 | Player roster |
| `Seed_Matches.py` | v3.3 | 74 matches, week labels W1-W10 |

---

## 4. LAYER RESPONSIBILITIES

### 4.1 `config.py` — The Ground Truth
```python
from config import DB_PATH, DEADLINE_HOUR, DEADLINE_MIN, IPL_YEAR
from config import APP_VERSION, VERSION_MAP
from config import SERVER_VER, ROUTES_VER, DB_VER, SCRAPER_VER, INIT_DB_VER, TASKS_VER
from config import SCORING_ENGINE_VER, ROLLOVER_ENGINE_VER, FUZZY_MATCH_VER
```
- Only file with `from pathlib import Path`. Zero project imports.
- **Always check `APP_VERSION` here before proposing any change.**
- `DEADLINE_HOUR = 14` = **14:00 UTC** (= 16:00 SAST).

### 4.2 `logic/` — The Brains

**`scoring_engine.py` v1.1.0**
```python
from logic.scoring_engine import calc_pts, apply_multiplier, debug_calc_pts
from logic.scoring_engine import CAP_MULT, VC_MULT
```
- `calc_pts(s)` — authoritative scoring. Never duplicate.
- `debug_calc_pts(s, player_id, cap_id, vc_id)` — step-by-step audit trace.
- `CAP_MULT = 2.0`, `VC_MULT = 1.5` — use constants, never hardcode.

**`rollover_engine.py` v1.0.0**
```python
from logic.rollover_engine import last_monday_deadline, already_rolled, pick_active_team
```

**`fuzzy_match.py` v1.0.0**
```python
from logic.fuzzy_match import _norm, _build_player_index, _fuzzy_match, _fuzzy_fielder
```
- Low-confidence matches return `None` — caller MUST log, never silently drop.

### 4.3 `db_manager.py` v5.9 — Pure DAO

❌ No `rollover_season()` | ❌ No `do_rollover()` | ❌ No local `calc_pts()`

**Rollover DAO (called by `routes.api_rollover`):**
```python
db.get_users_and_max_weeks()          # [{display_name, cur_wk}]
db.get_selection_row(name, week_no)   # dict | None
db.insert_rollover_week(name, new_wk, team_json, cap_id, vc_id)
db.set_last_rollover(iso)
```

### 4.4 `routes.py` v1.0.0 — API Router (Phase 7)

```python
from routes import bp
app.register_blueprint(bp)  # in server.py
```

**8 route groups:**

| # | Group | Endpoints |
|---|-------|-----------|
| 1 | System | `/api/version`, `/api/ping`, `/api/poll`, `/api/current-week` |
| 2 | State | `GET/POST /api/state` |
| 3 | Players | `/api/players`, `/api/resolve-player`, `/api/leaderboard` |
| 4 | History | `/api/history/<n>`, `/api/player-points/<n>`, `/api/user-match-points/<n>`, `/api/debug-points/<n>` |
| 5 | Save | `/api/save-next-week/<n>`, `/api/member/<n>`, `/api/match` |
| 6 | Scoring | `/api/recalculate-points`, `/api/audit-scores/<n>`, `/api/clean-scores` |
| 7 | Admin | `/api/rollover`, `/api/seed-history`, `/api/matches-status`, `/api/update-match-url` |
| 8 | Static | `/`, `/static/<filename>`, `/manifest.json`, `/offline` |

Route handlers follow the pass-through pattern: validate → `db.*` → `logic.*` → `jsonify()`.

### 4.5 `server.py` v13.0 — Thin Initialiser

Contains: imports, shared state, resolver functions, rate limiter, logging, `_db_con()`, startup functions, `db` singleton, `app`, error handlers, `CURRENT_PUBLIC_URL`, `from routes import bp; app.register_blueprint(bp)`, tunnel code, banner, `__main__`.

Does NOT contain: any `@app.route` decorators (all in `routes.py`).

### 4.6 `tasks.py` v1.0.0 — Background Threads
```python
tasks.start_bg_scrape(match_id, BASE_DIR)  # named daemon thread
```
Calls `scraper.run_full_scrape(db)` in-process — no subprocess.

### 4.7 `ipl_glue.js` v7.5 — Frontend
`_checkVersionHandshake()` on every page load → `/api/version` → console group.
Lock: `ROLLOVER_HOUR_UTC = 14` (Monday 14:00 UTC = 16:00 SAST).

---

## 5. OPERATIONAL PROCEDURES

### 5.1 Version Handshake
```bash
curl http://localhost:5000/api/version
```
Browser: DevTools → Console → `🏏 IPL Fantasy 2.0.0-stable — Decoupled v2.0 Backend ✓`

### 5.2 Post-Restart Workflow
```powershell
git pull
python Seed_Players.py           # first time or after roster changes
python server.py --tunnel cloudflare
python scraper.py                # re-scrapes all completed matches
```

### 5.3 Adding a New Week
```python
# init_db.py — NEVER alias W3=W2
_SAI_W5_TEAM = [...]   # own literal
_SAI_W5_CAP  = "..."
# Add to _HISTORY_SEED, bump _SEED_VERSION
```

### 5.4 Adding a New Logic Rule
1. Add function to `logic/` engine.
2. Bump engine version in `config.py`.
3. Update `VERSION_MAP`.
4. Push: `config.py` → engine → consumer. One file per commit.

### 5.5 Adding a New Route
1. Add to appropriate group in `routes.py`.
2. Handler body: validate → `db.*` / `logic.*` → `jsonify()`.
3. Bump `ROUTES_VER` in `config.py`.
4. Add to API table in §6.

### 5.6 Moe & Sai Audit
```bash
curl http://localhost:5000/api/audit-scores/Sai
curl http://localhost:5000/api/audit-scores/Moe
```
```python
from logic.scoring_engine import debug_calc_pts
t = debug_calc_pts(score, player_id="k04", cap_id="k04", vc_id="s05")
print(t["steps"], t["base_pts"], t["multiplier"], t["final_pts"])
```

---

## 6. API ENDPOINTS

| Method | Endpoint | Notes |
|--------|----------|-------|
| GET | `/api/version` | Phase 5 — version handshake |
| GET | `/api/ping` | Uses `_srv.CURRENT_PUBLIC_URL` |
| GET | `/api/poll` | ETag check |
| GET | `/api/current-week` | `week_no` |
| GET/POST | `/api/state` | Full app state |
| GET | `/api/players` | `id, name, team, price, role, season_pts, points` |
| POST | `/api/resolve-player` | Fuzzy resolve |
| GET | `/api/leaderboard[?week=N]` | cap/vc-exact totals |
| GET | `/api/history/{n}` | Weekly history + `points_per_match` |
| GET | `/api/player-points/{n}` | Self-contained — players + weeks |
| GET | `/api/user-match-points/{n}` | Per-match pts |
| GET | `/api/debug-points/{n}` | Ghost/unscored check |
| POST | `/api/save-next-week/{n}` | Save draft |
| PUT | `/api/member/{n}` | Upsert member |
| POST | `/api/match` | Upsert match |
| POST | `/api/recalculate-points` | Rebuild all pts |
| GET | `/api/audit-scores/{n}` | Step audit via `logic.scoring_engine` |
| POST | `/api/clean-scores` | Wipe scoring tables |
| POST | `/api/rollover[?force=1]` | In-controller via rollover_engine |
| POST | `/api/seed-history` | Draft-preserving re-seed |
| GET | `/api/matches-status` | All match statuses |
| POST | `/api/update-match-url` | Set URL → `tasks.start_bg_scrape()` |
| GET | `/` `/static/` `/manifest.json` `/offline` | Static |

---

## 7. ERROR HANDLING PATTERNS

**Fuzzy match — unresolved must be logged:**
```python
fid = _fuzzy_fielder(name, pidx, bowl_code)
if fid:
    fc[fid]["catches"] += 1
else:
    dropped_fielding.append(f"catch: '{name}'")  # REQUIRED — never silent
```

**Background scrape isolation:** `tasks._scrape_bg()` wraps in try/except — scrape failure never kills Flask.

**Ghost audit:** `_audit_player_id_coverage()` runs on every server start.

---

## 8. LESSONS LEARNED — Internal Context

### 8.1 subprocess → Daemon Threads (Phase 3)
Old: `subprocess.run([sys.executable, "scraper.py"])` — new Python interpreter, DB race conditions, 400ms overhead.
New: `tasks.start_bg_scrape(match_id, BASE_DIR)` — in-process daemon thread, shared WAL pool, named `scrape-{match_id}`, raises `RuntimeError` not `sys.exit`.

### 8.2 Timezone Alignment
`DEADLINE_HOUR = 14` = **14:00 UTC = 16:00 SAST**. Both `rollover_engine.py` and `ipl_glue.js` (`ROLLOVER_HOUR_UTC=14`) agree. Fixed in Phase 6 — old comment incorrectly said 14:00 SAST.

### 8.3 routes.py Circular Import Safety (Phase 7)
`server.py` defines all shared state at module level, then `from routes import bp` at the end. `routes.py`'s `from server import db, ...` safely resolves from `sys.modules['server']` at that point.

### 8.4 W1-W10 Variable Aliasing
NEVER `_SAI_W3_TEAM = _SAI_W2_TEAM` — shared reference mutates both. Always own literal even if currently identical.

---

## 9. FULL-STACK POINTS ARCHITECTURE

### 9.1 Two Point Columns
| Column | Table | Meaning | Updated by |
|--------|-------|---------|------------|
| `season_pts` | `players` | Base pts, no cap/vc | `update_player_season_pts()` |
| `points` | `players` | Cap/VC-weighted | `update_player_points()` inside `update_week_points()` |

### 9.2 Atomic Per-Match Pipeline (FIX-014)
```
_upsert_match() → db.recalculate_points(match_id) → db.update_week_points()
← scraper moves to next match (user_selections fully current)
# End: db.update_player_season_pts()
```

### 9.3 `/api/player-points/<n>` — Self-Contained
```json
{"ok":true,"name":"Sai","total_pts":412,
 "players":[{"id":"k04","season_pts":187,"points":374,"total_pts":218,"is_cap":true,
             "matches":[{"base_pts":109,"multiplier":2.0,"final_pts":218}]}],
 "weeks":[{"week_no":1,"week_pts":412,"points_per_match":{"ipl26_m04":218}}]}
```

---

## 10. SCORING RULES

| Category | Rule |
|----------|------|
| Playing | +4 |
| Batting | +runs, +fours, +sixes×2; SR>125 +6, ≥110 +4, ≥100 +2, <70 -2, <60 -4 (≥10 balls) |
| Milestones | 30+ +4, 50+ +8, 100+ +16; duck (got_out, ≥1 ball, 0 runs) -2 |
| Bowling | wickets×25, lbw/bowled +8, maidens +12 |
| Wkt milestones | 2wkt +4, 3wkt +4, 4wkt +8, 5wkt +8 |
| Economy | ≥2 overs: <5 +6, <6 +4, <7 +2, >12 -6, >11 -4, >10 -2 |
| Fielding | catch +8 (3+ bonus +4), stumping +12, direct RO +12, assist +6 |
| **Multipliers** | **Captain ×2.0 (`CAP_MULT`), Vice-Captain ×1.5 (`VC_MULT`)** |

**Audit traces:**
```python
# Moe — Phil Salt CAP: 72r 48b 8×4 3×6
debug_calc_pts({"played":True,"runs":72,"balls":48,"fours":8,"sixes":3,"got_out":True},
               player_id="r03", cap_id="r03", vc_id="s04")
# base_pts=104, multiplier=2.0, final_pts=208

# Sai — Varun Chakravarthy CAP: 3wkt 1lbw 1maiden 4ov 24rc
debug_calc_pts({"played":True,"overs":4.0,"runs_conceded":24,"wickets":3,"lbw_bowled":1,"maidens":1},
               player_id="k04", cap_id="k04", vc_id="s05")
# base_pts=109, multiplier=2.0, final_pts=218
```

---

## 11. DB SCHEMA (db_manager.py v5.9)

```
players         (id, name, team, price, role, season_pts, points)
matches         (id, week_no, title, teams_json, date_label, status, scorecard_url, raw_json)
user_selections (display_name, week_no, tw_team_json, tw_cap_id, tw_vc_id,
                 nw_team_json, nw_cap_id, nw_vc_id, week_pts, points_per_match)
match_scores    (match_id, player_id, runs, balls, fours, sixes, got_out, duck, overs,
                 runs_conceded, wickets, maidens, lbw_bowled, catches, stumpings,
                 run_out_direct, run_out_assist, played, raw_score_json)
player_match_points (match_id, player_id, week_no, base_pts, multiplier, final_pts, calculated_at)
user_match_points   (display_name, week_no, match_id, pts)  PK:(display_name, match_id)
meta            (key, value)  — _seed_version, _last_rollover, _saved
```

---

## 12. PLAYER ID CONVENTION & CRITICAL IDs

`{team_prefix}{num:02d}` — `c`=CSK `d`=DC `g`=GT `k`=KKR `l`=LSG `m`=MI `p`=PBKS `r`=RCB `rr`=RR `s`=SRH

| ID | Player | Note |
|----|--------|------|
| `c09` | Sanju Samson | CSK (from RR) |
| `c12` | Noor Ahmad | CSK — name conflict with `g03` GT → team-resolved |
| `k04` | Varun Chakravarthy | KKR — Sai W1 CAP |
| `l01` | Rishabh Pant | LSG |
| `r03` | Phil Salt | RCB — Moe W1 CAP |
| `rr11` | Vaibhav **Sooryavanshi** | RR — double-o, Cricbuzz official |
| `s04` | Ishan Kishan | SRH — Moe W1 VC |
| `s05` | Abhishek Sharma | SRH — Sai W1 VC |

---

## 13. HISTORY SEED (init_db.py — `2026.v8.w3w4-defined`)

```
Sai W1: k04 k19 s04 s05 s07 r01 r03 r11 m04 m07 m12  cap=k04 vc=s05
Sai W2: d22 p10 c12 c02 g03 rr14 rr11 l11 c09 p03 s04  cap=c09 vc=rr11
Moe W1: k04 m04 m07 m17 r02 r03 r12 s01 s04 k07 r16   cap=r03 vc=s04
Moe W2: m03 r05 k09 r16 p07 c11 rr04 s05 m11 s04 l01  cap=l01 vc=s04
W3/W4: same XI as W2 — own variables, never alias
W5+: add own literal variables, extend _HISTORY_SEED, bump _SEED_VERSION
```

---

## 14. WEEK BOUNDARIES

| Week | Deadline (UTC) | Matches |
|------|----------------|---------|
| W1 | Mon Mar 31 14:00 | M1-M2 |
| W2 | Mon Apr 7 14:00 | M3-M11 |
| W3 | Mon Apr 14 14:00 | M12-M20 |
| W4 | Mon Apr 21 14:00 | M21-M29 |
| W5 | Mon Apr 28 14:00 | M30-M38 |
| W6–W10 | Weekly Mondays | M39-M74 |

---

## 15. SCRAPER FIX HISTORY

| Fix | Ver | Description |
|-----|-----|-------------|
| FIX-008 | v10.4 | Team-aware fuzzy — Noor Ahmad CSK/GT collision |
| FIX-009 | v10.5 | IPL team validation |
| FIX-012 | v10.7 | No-result/abandoned → 0 pts |
| FIX-013 | v10.7 | c and b dismissal |
| FIX-014 | v10.8 | Per-match atomic point update |
| Phase 3 | v10.9 | `run_full_scrape()` export |
| Phase 4 | v10.10 | Fuzzy → `logic/fuzzy_match.py` |

---

## 16. INTEGRITY GUARDRAILS

- **Ghost IDs:** `_audit_player_id_coverage()` on every start.
- **Week isolation:** each `user_selections` row has its own `points_per_match` blob.
- **Audit:** `GET /api/audit-scores/{n}` cross-checks stored vs computed.
- **No-result:** empty scores, 0 pts, status=completed.
- **Post-restart:** pmp cleared → run `python scraper.py` before trusting points.
- **Push order:** `config.py` → `logic/` engine → `db_manager.py`/`routes.py`/`server.py`. One file per commit.
