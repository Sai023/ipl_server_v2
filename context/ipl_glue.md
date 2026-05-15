# Static/ipl_glue.js — The Frontend Integration Layer

> **Post-cleanup note (2026-05-15).** This document is the snapshot that
> drove the cleanup. The Dead Code Audit / Open Questions below describe
> what was **found**. The items listed there have been actioned across
> Waves A–E — see [dead_code_register.md](dead_code_register.md) for the
> current `fixed` / `kept` / `guarded` status of each.


## What it does (business view)

`ipl_glue.js` is the **API client + lifecycle controller** for the
browser-side app. Loaded after the inline script in
[index.html:822](../templates/index.html:822), it does six things:

1. **Defines `window.IplApi`** — the shim that wraps every `/api/*`
   call (`getState`, `getLeaderboard`, `saveNextWeek`, `rollover`,
   etc.) into a Promise-returning method.
2. **Polls `/api/poll` every 60 seconds**, ETag-aware. When the
   server's ETag changes, it re-fetches state + leaderboard and
   broadcasts `ipl:state-updated` / `ipl:leaderboard-updated` events.
3. **Schedules the auto-rollover** — a single `setTimeout` set for the
   next Monday at 14:00 UTC. When it fires, calls `IplApi.rollover(false)`.
4. **Injects CSS** for the maintenance overlay and the Match Centre
   look-and-feel.
5. **Provides the "Safe Boot" timeout** — if `/api/state` hasn't
   responded within 5 seconds, dismiss the loading spinner and show
   the error banner so the user isn't stuck behind a spinner forever.
6. **Overrides several inline renderers** (`_buildHistoryTab`,
   `_buildLeaderboardCard`, `_buildPointsTab`, `_buildMatchesTab`)
   defined in `index.html`. This is the "v7.4 UI OVERRIDES" section at
   line 352. The override pattern works because the inline script runs
   first and defines the function, then this file loads and
   reassigns it.

The file's docstring still claims version **v7.8** and mentions Phase
9.5 features (Match Centre + responsive tabs).

## Where it sits in the flow

```
   index.html (inline <script>)            ← runs first
        └── defines _build*, render(), _bootstrap()
        │
        ▼
   /static/ipl_glue.js  ← loads next
        ├── IIFE (lines 18–349):
        │     IplApi, polling, rollover, normaliseLeaderboard
        ├── Global UI overrides (lines 352+):
        │     _buildHistoryTab (DEAD), _buildLeaderboardCard,
        │     _buildPointsTab (DEAD), _buildMatchesTab (DEAD)
        └── _injectMCStyles, _injectStatsToPicker, _patchXiGrid-like stuff
        │
        ▼
   /static/mc_hub.js    ← loads last
        └── overrides _buildMatchCentreTab + opens box-score modal
```

## Inputs / Outputs

- **Inputs:**
  - `/api/*` HTTP responses.
  - Browser `localStorage`, `document.visibilityState`, DOM events.
- **Outputs:**
  - `window.IplApi`, `window.IplPolling`, `window.IplConfig`,
    `window.IplRollover`, `window.normaliseLeaderboard`.
  - Custom events on `window`: `ipl:ready`, `ipl:state-updated`,
    `ipl:leaderboard-updated`, `ipl:players-updated`,
    `ipl:rollover-triggered`, `ipl:week-changed`, `ipl:season-complete`,
    `ipl:saved`, `ipl:error`.
  - Side effects: polling loop, rollover timer, CSS injection,
    inline-renderer overrides.

## Key business rules it enforces

### 1. Polling cadence is 60 seconds, ETag-aware
- `POLL_INTERVAL_MS = 60000` ([line 21](../Static/ipl_glue.js:21)).
- `_pollCycle()` only re-fetches state when the server's `state_etag`
  changes — the typical poll is a tiny ETag-only call.
- Polling pauses when `document.hidden` and resumes immediately on
  visibility return.

### 2. The auto-rollover is local to each browser
- `ROLLOVER_HOUR_UTC = 14`, `ROLLOVER_MIN_UTC = 0` (line 23-24).
- `_msUntilNextRollover()` computes ms-until-next-Monday-14:00-UTC.
- `_executeRollover()` calls `IplApi.rollover(false)`. The server's
  `already_rolled` guard ensures multiple browsers firing
  simultaneously produce exactly one rollover.

### 3. ETag invalidation triggers
- On `ipl:saved` → `_lastStateEtag = null`.
- On `ipl:rollover-triggered` → `_lastStateEtag = null` + immediate
  `_pollCycle()`.
- Together: any save or rollover guarantees the *next* poll picks up
  the change without waiting for the 60s tick.

### 4. Safe boot timeout
- 5 seconds after `_init` runs, if the loading spinner is still
  visible, dismiss it and show an error banner.
- This handles the case where `/api/state` is completely unreachable
  and the user would otherwise be stuck.

### 5. `IplConfig` is populated from `/api/ping`
On boot, `IplApi.ping()` sets `IplConfig.budget`, `xi_size`,
`max_weeks`, and `current_week`. This is the "single source of truth"
the inline script *should* use — see Open Questions.

### 6. `normaliseLeaderboard` smooths API drift
The leaderboard response has historically returned either
`{rankings: [...]}` or `{standings: [...]}` plus the `league_avg` /
`top_score` / `member_count` either at the top level or inside a
`meta` object. `normaliseLeaderboard` produces a single shape so the
UI builders don't have to defensively check both keys.

## Called by / Calls into

- **Called by:** `index.html` (via `<script src="...">`), `mc_hub.js`
  (via the global `IplApi` and `IplConfig`).
- **Calls into:**
  - Every `/api/*` endpoint reachable through the methods on `IplApi`.
  - Browser APIs: `fetch`, `setInterval`, `setTimeout`,
    `addEventListener`, `MutationObserver`,
    `localStorage` (indirectly through index.html), `document.head`,
    `document.body`.

## Supports which user capabilities

From [user_capabilities.md](user_capabilities.md):

- **§10.1 60-second polling** — `_pollCycle` + `startPolling`.
- **§10.2 Auto-rollover** — `_executeRollover` + `_scheduleNextRollover`.
- **§9.1 Refresh** — `IplPolling.stop/start` called from `index.html`'s
  `_refreshData` wraps the discovery + scrape window.
- **§1.1 Register** — `IplApi.saveMember` from `_buildLoginCard` →
  `PUT /api/member/<n>`.
- **§5.3 Save Draft** — `IplApi.saveNextWeek`.
- **§4.2 Simulate rollover** — `IplApi.rollover(true)`.
- All read methods (`getState`, `getLeaderboard`, `getPlayers`,
  `getHistory`, `getCurrentWeek`, `getMatchCentre`, `getMatchDetails`).

## Dead Code Audit

### Dead overrides — the three dead tabs

| Item | Lines | Verdict |
|------|-------|---------|
| `_buildHistoryTab` override | 371–410 (~40 lines) | **DEAD.** History tab is not in the render switch in `index.html`. Already tracked as **D5**. |
| `_buildPointsTab` override | 462–565 (~104 lines) | **DEAD.** Points tab is not in the render switch. Already tracked as **D4**. |
| `_buildMatchesTab` + `_loadUserMatchPoints` + `_umpData` | 569–616 (~48 lines) | **DEAD.** Matches tab is not in the render switch. Already tracked as **D24**. |

### Dead IplApi client methods

| Item | Lines | Verdict |
|------|-------|---------|
| `IplApi.getPlayerPoints` | 211 | **DEAD.** Only consumer was the dead Points tab. (E1) |
| `IplApi.getUserMatchPoints` | 212 | **DEAD.** Only consumer was the dead Matches tab. (E2 / D21) |
| `IplApi.saveState` | 248 | **DEAD.** Real saves go through `saveNextWeek` / `saveMember`. (D17 / E6) |
| `IplApi.saveMatch` | 250 | **DEAD.** Superseded by `/api/update-match-url`. (D18) |
| `IplApi.seedHistory` | 274 | **DEAD.** Never called. (D19) |

### Dead scaffolds left over from earlier phases

| Item | Lines | Verdict |
|------|-------|---------|
| `_mcData`, `_mcLoading` globals + `_buildMatchCentreTab` stub | 766–775 (~10 lines) | **DEAD.** `mc_hub.js` has its own `_mcData` / `_mcLoading` inside its IIFE and overrides `window._buildMatchCentreTab` (line 369). The stub here is the v7.6 placeholder mc_hub.js was meant to replace. Added to register as **D25**. |
| `_injectStatsToPicker` + `_pickerObserver` + `_setupPickerObserver` | 619–676 (~58 lines) | **INERT.** Sets up a `MutationObserver` that scans for `[data-pid], .prow, .player-row, .pick-row` rows and injects `★ {points}` badges. **None of those selectors match anything in the live `index.html`** — the picker uses `<tr class="p-sel">` and the next-week squad uses `class="nw-player-row"`. Live `_buildNwSquad` renders `season_pts` *inline* ([index.html:343](../templates/index.html:343)), so this entire DOM-mutation path is doing nothing useful but still running on every state change. Added as **D26**. |
| `_playerMap` + `_loadPlayerMap` | 358–368 (~12 lines) | **Conditionally dead.** Consumed only by `_buildPointsTab` (D4) and `_injectStatsToPicker` (D26). If both are removed, this becomes orphaned too. |

### Live but worth-noting

| Item | Notes |
|------|-------|
| `_buildLeaderboardCard` override | 413–459. Adds per-week columns to the leaderboard table. **Live**, called by render(). The inline version in `index.html:532` is shorter and shadowed. |
| Maintenance overlay (`_showOverlay` etc.) | 76–109. **Live**, fires when polls fail with 5xx or `!navigator.onLine`. |
| `IplApi.ping`, `getCurrentWeek` | Both used by `_init`. **Live**. |
| Stale changelog header | 1–16 | Mentions v7.3 Matches tab (now dead) and v7.4 features still in use. Worth pruning to current state. |

**Total dead code in `Static/ipl_glue.js`:** ~275 lines once D3–D5,
D17–D21, D24–D26 are all executed — about **36% of the file**.

## Open Questions

1. **The IIFE / global-overrides split is fragile.** Lines 1–349
   are inside `(function (window) {...}(window))`; lines 350+ are
   global. Inside the IIFE, variables like `_mcData` are private;
   outside, the `_mcData` declared at line 766 is module-global (and
   different from the one mc_hub.js uses). This caused at least one
   real bug pattern in earlier phases. Worth unifying into a single
   IIFE so global state is impossible to clash.
2. **The override pattern relies on script load order.** If anyone
   ever moves `mc_hub.js` *before* `ipl_glue.js`, or moves the
   inline script to the bottom of the body, the overrides break
   silently. Worth documenting in the file's header.
3. **`MutationObserver` cost.** Even though `_injectStatsToPicker`
   does nothing useful, the observer fires on every DOM mutation
   inside `#app` — and `render()` rewrites that entire subtree on
   every state change. The cost is small (no matching selectors) but
   non-zero. D26 cleanup removes the overhead.
4. **`_safeBootTimer` doesn't fire if `/api/state` returns 200 but
   `ipl:state-updated` is never dispatched** (e.g. inline script
   crashes before `render()` runs). Worth adding a hard `setTimeout`
   that dismisses the loader regardless.
5. **`IplApi` is the contract**, but **five dead methods**
   (`getPlayerPoints`, `getUserMatchPoints`, `saveState`, `saveMatch`,
   `seedHistory`) inflate that contract. Worth grouping the
   delete-candidates in the cleanup pass — each one cascades to a
   route handler and a DAO method.
