---
name: ipl-fantasy-sync
description: "End-to-end orchestrator for the IPL 2026 Fantasy system. Manages the lifecycle of player data from scraper.py ingestion to db_manager.py processing and server.py API delivery."
---

## Core Architecture & Dependency Map

**Primary Source of Truth:** `seed_players.py` (IDs) and `seed_matches.py` (match metadata).
Constraint: Never mutate these files; only read from them to validate external data.
Exception: scorecard URL IDs for Cricinfo are writable via the Admin tab.

**The Engine (`db_manager.py`):** Handles all heavy lifting — point aggregation, table joins, and draft-to-active selection migration logic.

**The Ingestor (`scraper.py`):** Must use `fuzz` (or similar) to map Cricinfo strings to `seed_players.py` keys.
Constraint: Use team name as second identifier to confirm the correct player.

**The Gatekeeper (`server.py`):** Enforces the Monday 14:00 SAST lock. No write operations to active user selections after this timestamp; last draft must be saved and permitted via API before this point.

---

## Frontend Layer — Decoupled Static Architecture (v7.5+)

### File Structure

| File | Served at | Purpose |
|---|---|---|
| `templates/index.html` | `/` | Lightweight HTML shell only. No inline `<style>` block. No `EMBEDDED_PLAYERS` array. |
| `Static/style.css` | `/static/style.css?v=X.X` | All CSS — reset, layout, cards, picker, pitch, points, admin, media queries. |
| `Static/ipl_glue.js` | `/static/ipl_glue.js?v=XX` | All JS logic — API layer, polling, rollover scheduler, UI tab builders, swap modal. |

### Versioning Rule (MANDATORY)
All `Static/` assets **must** use query-string versioning to prevent `304 Not Modified` cache locks:
```html
<link rel="stylesheet" href="/static/style.css?v=1.0">
<script src="/static/ipl_glue.js?v=75"></script>
```
Bump the version integer on every change to the respective file. Flask serves `Static/` → `/static/`.

### Source-of-Truth Rule (MANDATORY)
- **All future CSS changes** → edit `Static/style.css` only.
- **All future JS/logic changes** → edit `Static/ipl_glue.js` only.
- `templates/index.html` is edited **only** to bump asset version strings or alter the bare HTML shell structure.
- The root `ipl_glue.js` (repo root) is the authoring copy; mirror it into `Static/ipl_glue.js` on every release.

### `index.html` Contains (shell only)
- `<meta>` / `<link rel="manifest">` / `<title>`
- `<link rel="stylesheet" href="/static/style.css?v=X.X">`
- `#app-loading` spinner div
- `#error-banner` div
- `#app` mount point
- Inline `<script>` with global state, render functions, and bootstrap (no CSS, no player data)
- `<script src="/static/ipl_glue.js?v=XX">` — the decoupled glue layer

### `Static/ipl_glue.js` v7.5 Features
- **Safe Boot timeout** (5 s): if `#app-loading` still visible after 5 s, force-hides spinner and fires `#error-banner` with actionable message.
- **IplApi** — full fetch wrapper: `getState`, `getLeaderboard`, `getPlayers`, `getHistory`, `getPlayerPoints`, `getUserMatchPoints`, `saveNextWeek`, `rollover`, `ping`, `saveMember`, `seedHistory`.
- **60-second polling loop** with ETag-based change detection.
- **Monday 14:00 UTC auto-rollover scheduler**.
- **UI overrides** (tab builders replacing inline stubs): `_buildLeaderboardCard` (per-week columns + cap/vc note), `_buildPointsTab` (base pts + match-by-match team totals), `_buildHistoryTab` (weekly pts chip), `_buildMatchesTab` (My Pts column), `_injectStatsToPicker` (★ pts badge).
- **`_playerMap` cache** — hydrated from `/api/players` for fast picker stat injection.

---

## League-Specific Logic

**Scoring:** Standard IPL 2026 rules + Captain (2×) and Vice-Captain (1.5×) multipliers. `db_manager.py` manages all calculations.

**Rollover Mechanics:** Every Monday at 14:00 SAST, active selections are archived and draft selections are promoted. If no draft exists, the previous week's active team is cloned forward.

**Budget:** 100.0 CR total, XI_SIZE = 11 players.

---

## Skill Triggers & Advanced Workflow

Trigger on: "Run daily scrape," "Recalculate points for [user]," "Monday rollover," "Sync full stack," "Debug fuzzy matching," "CSS/UI change," "JS logic change," or "bump cache version."

**Backdated Testing Protocol:** When auditing a user:
1. Query `match_scores` for the specific historical `match_id`.
2. Cross-reference the completed week's snapshot against the user's selections.
3. Output a Calculation Trace: `Run pts + Boundary Bonus × Multiplier = Total`.

**Integrity Guardrail:** If `scraper.py` finds a player not in `seed_players.py`, halt and prompt to update the seed file before proceeding.

**Safety Protocol:** For any MCP file edit, provide a structural diff. For database writes, explain the `WHERE` clause logic clearly to prevent accidental table-wide updates.

---

## Instruction Style

| Domain | Freedom |
|---|---|
| Database schemas, point-calculation logic, API contract, rollover timing | **Strict / Low** |
| `Static/style.css` layout & design | **Creative / High** |
| `Static/ipl_glue.js` DOM manipulation & tab builders | **Creative / High** |
| Asset versioning (`?v=XX`) | **Strict — always bump on change** |
