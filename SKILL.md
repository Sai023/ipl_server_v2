---
name: ipl-fantasy-sync
description: "End-to-end orchestrator for the IPL 2026 Fantasy system (v2.1.0-stable). Governs the layered architecture across config.py, logic/ engines, db_manager.py DAO, tasks.py, scraper.py, server.py, routes.py, and ipl_glue.js. Senior System Architect persona — prioritise modularity, one-file-at-a-time pushes, zero logic duplication."
---

# IPL Fantasy 2026 — ipl_server_v2  (`Sai023/ipl_server_v2`)
## Skill Version: v2.1.0-stable  |  APP_VERSION: 2.1.0-stable  |  Branch: `main`

---

## 1. ARCHITECTURE OVERVIEW — v2.1.0-stable

```
┌──────────────────────────────────────────────────────────────┐
│  ipl_glue.js  (v7.7)  — Browser / UI                        │
│  index.html + templates/  — Jinja2 rendering                 │
└───────────────────────┬──────────────────────────────────────┘
                        │ HTTP / JSON
┌───────────────────────▼──────────────────────────────────────┐
│  base.py  — Shared State (Flask app, db singleton, logging)  │
│  server.py  (v13.2)  — Thin Flask Initialiser                │
│  • Registers Blueprint:  from routes import bp               │
│  • Startup: ephemeral tables cleared, week_pts PRESERVED     │
│  • Tunnel, banner, __main__                                  │
├──────────────────────────────────────────────────────────────┤
│  routes.py  (v1.1.0) — API Router (Blueprint)                │
│  • All 24 @bp.route handlers in 8 labelled groups            │
│  • Imports shared state from base.py (no circular import)    │
└──────────┬─────────────────────┬────────────────────────────┘
           │                     │
┌──────────▼──────────┐  ┌───────▼────────────────────────────┐
│  db_manager.py      │  │  logic/  package                   │
│  (v5.9 — pure DAO)  │  │  ┌──────────────────────────────┐  │
│  SELECT/INSERT/     │  │  │ scoring_engine.py  (v1.1.0)  │  │
│  UPDATE only.       │  │  │ rollover_engine.py (v1.0.0)  │  │
│  No IPL rules.      │  │  │ fuzzy_match.py     (v1.1.0)  │  │
└──────────┬──────────┘  └──┴──────────────────────────────┴──┘
           │                     │
┌──────────▼──────────────────── ▼────────────────────────────┐
│  tasks.py (v1.0.0)  — Background Thread Orchestrator        │
│  scraper.py (v10.11) — Cricbuzz ingestion + resilience      │
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
    ├── base.py                     (imports config + db_manager; owns Flask app + db)
    ├── routes.py                   (imports base; never imports server)
    └── server.py                   (imports base + routes; no shared state defined here)
```

**Circular import fix (Phase 7 → stable):** `base.py` owns all shared state (Flask `app`, `db` singleton, logging, rate limiter). `routes.py` imports from `base.py` only. `server.py` imports from `base.py` + `routes.py`. No cycle exists.

---

## 3. FILE VERSIONS (2.1.0-stable)

| File | Version | Role |
|------|---------|------|
| `config.py` | 1.0.0 | Global constants + VERSION_MAP |
| `base.py` | 1.0.0 | Flask app, db singleton, logging, rate limiter, resolver |
| `logic/scoring_engine.py` | 1.1.0 | `calc_pts`, `apply_multiplier`, `debug_calc_pts`, `CAP_MULT=2.0`, `VC_MULT=1.5` |
| `logic/rollover_engine.py` | 1.0.0 | Monday 14:00 UTC deadline logic |
| `logic/fuzzy_match.py` | 1.1.0 | Player name resolution + `_generate_dynamic_player()` |
| `db_manager.py` | 5.9 | Pure DAO — fan-out SQL fixed |
| `routes.py` | 1.1.0 | 24 API handlers (Blueprint, 8 groups); ROUTES_VER in /api/version |
| `server.py` | 13.2 | Flask init + non-destructive startup + blueprint registration |
| `tasks.py` | 1.0.0 | Daemon thread orchestration |
| `scraper.py` | 10.11 | Cricbuzz ingestion + FIX-015/016/017/018 resilience |
| `init_db.py` | 1.0.0 | `_auto_seed_*`, `run_all_sync()` |
| `ipl_glue.js` | 7.7 | `_patchXiGrid` badge injection, mobile blur, version handshake |
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

**`fuzzy_match.py` v1.1.0**
```python
from logic.fuzzy_match import _norm, _build_player_index, _fuzzy_match, _fuzzy_fielder
from logic.fuzzy_match import _generate_dynamic_player
```
- `_generate_dynamic_player(name, team_code, cricbuzz_id)` — returns fully-keyed player dict for unknown players. ID = `ext_{cricbuzz_id}` (no collision with Seed IDs). role=`"AR"` (satisfies DB CHECK). price=7.0.
- Low-confidence matches return `None` — caller MUST log, never silently drop.

### 4.3 `db_manager.py` v5.9 — Pure DAO

❌ No `rollover_season()` | ❌ No `do_rollover()` | ❌ No local `calc_pts()`

**Points pipeline:**
```python
db.recalculate_points(match_id=None)   # match_scores → player_match_points
db.update_week_points()                 # pmp → user_selections.week_pts + user_match_points
db.update_player_season_pts()           # pmp → players.season_pts
db.update_player_points()               # cap/vc-weighted → players.points
```

**Rollover DAO (called by `routes.api_rollover`):**
```python
db.get_users_and_max_weeks()          # [{display_name, cur_wk}]
db.get_selection_row(name, week_no)   # dict | None
db.insert_rollover_week(name, new_wk, team_json, cap_id, vc_id)
db.set_last_rollover(iso)
```

**State includes `player_pts` (Phase 8):**
`get_state()` returns `player_pts: {id: season_pts}` from the players table. Used by `ipl_glue.js` Next Week badges — zero extra fetch.

### 4.4 `routes.py` v1.1.0 — API Router (Phase 7+)

```python
from routes import bp
app.register_blueprint(bp)  # in server.py
```

**8 route groups:**

| # | Group | Endpoints |
|---|-------|-----------|
| 1 | System | `/api/version` (includes ROUTES_VER), `/api/ping`, `/api/poll`, `/api/current-week` |
| 2 | State | `GET/POST /api/state` |
| 3 | Players | `/api/players` (season_pts DESC), `/api/resolve-player`, `/api/leaderboard` |
| 4 | History | `/api/history/<n>`, `/api/player-points/<n>`, `/api/user-match-points/<n>`, `/api/debug-points/<n>` |
| 5 | Save | `/api/save-next-week/<n>`, `/api/member/<n>`, `/api/match` |
| 6 | Scoring | `/api/recalculate-points`, `/api/audit-scores/<n>`, `/api/clean-scores` |
| 7 | Admin | `/api/rollover`, `/api/seed-history`, `/api/matches-status`, `/api/update-match-url` |
| 8 | Static | `/`, `/static/<filename>`, `/manifest.json`, `/offline` |

Route handlers: validate → `db.*` → `logic.*` → `jsonify()`.

### 4.5 `server.py` v13.2 — Thin Initialiser

Contains: imports from `base.py`, blueprint registration, startup functions, tunnel, banner, `__main__`.

Does NOT contain: any `@app.route` decorators (all in `routes.py`), shared state (all in `base.py`).

**Non-destructive startup (v13.2):** `_rebuild_scores_and_points()` clears ONLY ephemeral derived tables (`match_scores`, `player_match_points`, `user_match_points`, JSON cache). It does NOT touch `week_pts` or `season_pts`. See §8.6.

### 4.6 `tasks.py` v1.0.0 — Background Threads
```python
tasks.start_bg_scrape(match_id, BASE_DIR)  # named daemon thread
```
Calls `scraper.run_full_scrape(db)` in-process — no subprocess.

### 4.7 `ipl_glue.js` v7.7 — Frontend Integration Layer

- `_checkVersionHandshake()` on every page load → `/api/version` → console group.
- `ROLLOVER_HOUR_UTC = 14` (Monday 14:00 UTC = 16:00 SAST).
- `_patchXiGrid()` — wraps the inline `_buildXiGrid()` to inject season_pts badges at template render time. See §8.8.
- `_dismissKeyboard()` — `requestAnimationFrame(blur)` after pick/swap. See §8.9.

---

## 5. OPERATIONAL PROCEDURES

### 5.1 Version Handshake
```bash
curl http://localhost:5000/api/version
```
Browser: DevTools → Console → `🏏 IPL Fantasy 2.1.0 — Decoupled v2.0 Backend ✓`

### 5.2 Post-Restart Workflow
```powershell
git pull
python server.py --tunnel cloudflare
# week_pts and season_pts are preserved — leaderboard shows correct
# historical values immediately without re-scraping.
python scraper.py   # only needed to score NEW matches
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
| GET | `/api/version` | Includes `routes` module version |
| GET | `/api/ping` | Uses `_base.CURRENT_PUBLIC_URL` |
| GET | `/api/poll` | ETag check |
| GET | `/api/current-week` | `week_no` |
| GET/POST | `/api/state` | Full app state — includes `player_pts` dict |
| GET | `/api/players` | `id, name, team, price, role, season_pts, points` — sorted season_pts DESC |
| POST | `/api/resolve-player` | Fuzzy resolve |
| GET | `/api/leaderboard[?week=N]` | Pure SUM(week_pts) — no cross-join fan-out |
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

**Scraper resilience (FIX-015/016/017/018):**
- FIX-015: `_generate_dynamic_player()` — auto-adds unknown players with `ext_{cricbuzz_id}` IDs.
- FIX-016: All `b.get()` / `bw.get()` with safe defaults — `KeyError` eliminated.
- FIX-017: Per-player `try/except` → `NON_BLOCKING_ERROR` — one bad entry doesn't kill innings.
- FIX-018: Per-match `try/except` → `MATCH_FAILED` — one bad scorecard doesn't kill the run.

**Background scrape isolation:** `tasks._scrape_bg()` wraps in try/except — scrape failure never kills Flask.

**Ghost audit:** `_audit_player_id_coverage()` runs on every server start.

---

## 8. LESSONS LEARNED — Internal Context

### 8.1 subprocess → Daemon Threads (Phase 3)
Old: `subprocess.run([sys.executable, "scraper.py"])` — new Python interpreter, DB race conditions.
New: `tasks.start_bg_scrape(match_id, BASE_DIR)` — in-process daemon thread, shared WAL pool, named `scrape-{match_id}`.

### 8.2 Timezone Alignment
`DEADLINE_HOUR = 14` = **14:00 UTC = 16:00 SAST**. Both `rollover_engine.py` and `ipl_glue.js` (`ROLLOVER_HOUR_UTC=14`) agree.

### 8.3 base.py Circular Import Fix (Phase 7 → Stable)
`base.py` owns all shared state (Flask `app`, `db`, logging, limiter). `routes.py` imports from `base.py` only. `server.py` imports both. No cycle.

### 8.4 W1-W10 Variable Aliasing
NEVER `_SAI_W3_TEAM = _SAI_W2_TEAM` — shared reference mutates both. Always own literal even if currently identical.

### 8.5 SQLite WAL Mode + Concurrency

All connections use `PRAGMA journal_mode = WAL`. This enables:
- **Concurrent reads** — multiple threads/requests can read simultaneously.
- **Single writer** — `_write()` context manager holds `threading.Lock()` for exclusive writes.
- **Busy timeout** — `PRAGMA busy_timeout = 30000` prevents deadlock on contention.

The pattern across the codebase:
```python
@contextmanager
def _write(self):
    with self._wlock:          # threading.Lock — one writer at a time
        con = self._connect()
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.commit()
```

Thread-local connections (`threading.local()`) ensure each thread gets its own SQLite connection handle, preventing cursor conflicts in multi-request Flask scenarios.

### 8.6 Non-Destructive Startup (server.py v13.2)

**The regression:** `_rebuild_scores_and_points()` previously ran two statements on every server start:
```python
# REMOVED — these were the destructive lines:
con.execute("UPDATE user_selections SET week_pts = 0")       # ← wiped leaderboard
con.execute("UPDATE players SET season_pts = 0, points = 0") # ← wiped scouting badges
```

**Why it was catastrophic:**
- `week_pts` in `user_selections` is the **source of truth for the leaderboard**. Wiping it on restart meant W3=632 became W3=0 in the UI, even though the DB Browser showed the correct value from before startup.
- `season_pts` in `players` powers the scouting badges in `ipl_glue.js`. Wiping it made all `_state.player_pts` values zero → the badge guard `if (!pts) return` skipped every player.

**The fix:** Startup now clears ONLY the ephemeral derived tables:
```python
# EPHEMERAL — safe to clear (repopulated by scraper):
con.execute("DELETE FROM match_scores")
con.execute("DELETE FROM player_match_points")
con.execute("DELETE FROM user_match_points")
# + data/matches/*.json cache files

# SOURCE OF TRUTH — NEVER clear at startup:
# user_selections.week_pts      ← leaderboard totals
# user_selections.points_per_match ← history tab detail
# players.season_pts            ← scouting badges
# players.points                ← weighted display
```

**Rule:** `week_pts` and `season_pts`/`points` are written at scrape time and survive indefinitely. They are only reset by `rebuild_scores_and_points()` (explicit admin full-rebuild) or by `update_week_points()` / `update_player_season_pts()` after a fresh scrape.

### 8.7 Leaderboard Fan-Out Bug + Fix

**The regression:** `_LEADERBOARD_SQL` had a single `user_totals` CTE:
```sql
-- BROKEN: LEFT JOIN fans out user_selections to N match rows
SELECT display_name, SUM(us.week_pts) AS total_pts, COUNT(...) AS matches_counted
FROM user_selections us
LEFT JOIN user_match_points ump ON ump.display_name = us.display_name
                                AND ump.week_no = us.week_no
GROUP BY display_name
```

`user_match_points` has one row **per match**, so a W2 row (week_pts=900, 9 matches) fanned out to 9 rows. `SUM(us.week_pts)` counted 900 nine times (8100) + 490 twice (980) = **9080** instead of 1390.

**The fix:** Two independent CTEs — no cross-join between them:
```sql
-- FIXED: two independent CTEs, LEFT JOINed only in ranked
user_totals  → pure SUM(week_pts) FROM user_selections (no join)
match_counts → COUNT(DISTINCT match_id) FROM user_match_points (no join)
-- Both LEFT JOINed in ranked CTE — total_pts cannot fan-out
```

**Invariant:** `total_pts` (leaderboard Total column) == `SUM` of all weekly columns shown in the UI, because both read exclusively from `user_selections.week_pts`.

### 8.8 `_patchXiGrid()` — Template-Time Badge Injection

**The problem:** The v7.6 approach used a `MutationObserver` searching for `[data-pid]` elements. The inline script's `_buildXiGrid()` function generated card HTML **without `data-pid` attributes**, so `querySelectorAll('[data-pid]')` returned nothing and badges never rendered.

**The fix (`ipl_glue.js` v7.7):** Override `_buildXiGrid` at the source:

```js
var _orig = window._buildXiGrid;
window._buildXiGrid = function(team, cap, vc) {
  var html = _orig(team, cap, vc);      // call original
  var tmp = document.createElement('div');
  tmp.innerHTML = html;                  // parse to DOM
  var grid = (tmp.children.length === 1) ? tmp.firstElementChild : tmp;
  var cards = Array.from(grid.children); // one card per player

  cards.forEach(function(card, i) {
    var pid = team[i];                   // co-indexed — always safe
    card.setAttribute('data-pid', pid); // stamp for observer backup

    var pts = (window._state && window._state.player_pts)
              ? window._state.player_pts[pid] || 0
              : (_playerMap[pid] && _playerMap[pid].season_pts) || 0;
    if (!pts) return;

    var badge = document.createElement('span');
    badge.className = 'ipl-sp-badge';
    badge.style.cssText = 'background:rgba(0,212,170,0.13);color:#00D4AA;'
      + 'pointer-events:none;position:relative;z-index:1;' + /* ... */;
    badge.textContent = pts + ' Pts';

    var nameEl = card.querySelector('[class*="name"],[class*="title"]')
                 || card.firstElementChild;
    if (nameEl) nameEl.appendChild(badge);
    else card.appendChild(badge);
  });

  return tmp.innerHTML;                  // return modified HTML
};
```

**Co-indexing safety:** `_buildXiGrid(team, cap, vc)` renders exactly one card element per element in `team[]`, always in the same order. `grid.children[i]` maps 1:1 to `team[i]`. This is guaranteed by the inline script's render loop. Fallback filter (`.children.length >= 2`) handles grids with non-card header children.

**Data source:** `_state.player_pts[pid]` (embedded in `/api/state` response — zero extra HTTP fetch). Falls back to `_playerMap[pid].season_pts` from `/api/players` cache. Uses `season_pts` (base, unweighted) — the correct metric for scouting form guides.

**Visual safety:** `pointer-events:none` prevents the badge span from intercepting C/VC button tap events. `position:relative;z-index:1` ensures it renders above background layers without floating above the buttons.

### 8.9 Mobile Keyboard Dismiss

```js
function _dismissKeyboard() {
  requestAnimationFrame(function () {    // wait for DOM paint to complete
    document.activeElement.blur();       // then drop the keyboard
  });
}
// Hooks:
document.addEventListener('click', /* capture phase */, true);  // pick/swap buttons
window.addEventListener('ipl:saved', _dismissKeyboard);         // post-save ACK
```

`requestAnimationFrame` ensures blur fires **after** the DOM update that caused the keyboard to appear, not during it. Search text is not cleared. The input is not re-focused.

---

## 9. FULL-STACK POINTS ARCHITECTURE

### 9.1 Two Point Columns
| Column | Table | Meaning | Updated by | Used for |
|--------|-------|---------|------------|----------|
| `season_pts` | `players` | Base pts, no cap/vc | `update_player_season_pts()` | Scouting badges, /api/players sort |
| `points` | `players` | Cap/VC-weighted total | `update_player_points()` | Display in Points tab |
| `week_pts` | `user_selections` | Per-week user score | `update_week_points()` | Leaderboard source of truth |

### 9.2 Atomic Per-Match Pipeline (FIX-014)
```
_upsert_match() → db.recalculate_points(match_id) → db.update_week_points()
← scraper moves to next match (user_selections fully current)
# End of all matches: db.update_player_season_pts()
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

**Ephemeral tables** (cleared on restart): `match_scores`, `player_match_points`, `user_match_points`.
**Persistent tables** (never cleared on restart): `players.season_pts`, `players.points`, `user_selections.week_pts`, `user_selections.points_per_match`.

---

## 12. PLAYER ID CONVENTION & CRITICAL IDs

`{team_prefix}{num:02d}` — `c`=CSK `d`=DC `g`=GT `k`=KKR `l`=LSG `m`=MI `p`=PBKS `r`=RCB `rr`=RR `s`=SRH

Dynamic (unknown) players: `ext_{cricbuzz_id}` or `ext_{6-char-md5}` — generated by `_generate_dynamic_player()`.

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
| FIX-015 | v10.11 | `_generate_dynamic_player()` — auto-add unknown players with `ext_*` IDs |
| FIX-016 | v10.11 | Defensive `.get()` extraction — `KeyError` eliminated |
| FIX-017 | v10.11 | Per-player `try/except` → `NON_BLOCKING_ERROR` |
| FIX-018 | v10.11 | Per-match `try/except` → `MATCH_FAILED` + auto-advance |

---

## 16. INTEGRITY GUARDRAILS

- **Ghost IDs:** `_audit_player_id_coverage()` on every start — logs true ghosts (IDs in selections but not in players table).
- **Week isolation:** each `user_selections` row has its own `points_per_match` blob.
- **Audit:** `GET /api/audit-scores/{n}` cross-checks stored vs computed.
- **No-result:** empty scores, 0 pts, status=completed.
- **Fan-out prevention:** `_LEADERBOARD_SQL` uses two independent CTEs — `user_totals` and `match_counts` never joined to each other. `total_pts` == `SUM(week_pts)` always.
- **Non-destructive startup:** `week_pts` and `season_pts` are never cleared at server restart. Only ephemeral derived tables are wiped.
- **Push order:** `config.py` → `logic/` engine → `db_manager.py`/`routes.py`/`server.py`. One file per commit.
- **Dynamic player IDs:** `ext_{cricbuzz_id}` prefix — zero collision with `[a-z]{1,3}\d{1,2}` Seed IDs.
- **Variable aliasing:** NEVER `_SAI_W3_TEAM = _SAI_W2_TEAM` — always own literal.
