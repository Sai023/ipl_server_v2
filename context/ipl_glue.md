> **2026-11-19 - read this first.** The browser no longer schedules or sends a weekly rollover (the Monday 14:00 UTC `setTimeout` in `Static/ipl_glue.js`, `IplRollover` and `IplApi.rollover` are gone) and the red admin button "Simulate Monday rollover" is gone. They could roll a competition that had not started (reproduced on scratch data) and only ever worked for an admin who had the page open. Since 2026-11-20 `POST /api/rollover` applies every overdue week of the stored lock schedule (see [rollover_plan.md](rollover_plan.md)); a forced rollover needs a signed-in admin plus `confirm=<slug>:<week>` (`tests/test_rollover_guards.py`). The redundant automatic triggers come in Phase 2 of the SA20 readiness plan. Sections below that describe the browser timer or the button are historical.

> **2026-11-21 (Phase 1.7) - server trouble is ONE slim banner now.** The full-screen "Server Unavailable" overlay (`_showOverlay` and friends), the "Safe Boot" block (it wrote into an element that no longer existed) and the dead `ipl:rollover-triggered` / `ipl:error` plumbing are gone from this file. A failed poll is silent here; `Ui.connection` in `Static/ui.js` watches every `/api/` call and owns the banner, the probe and the backoff, and this file only refetches at once when it announces `ipl:recovered`. See "Phase 1.7 additions" at the end; sections below that describe the overlay or Safe Boot are historical. Test: `tests/js/connection.test.js`.

# Static/ipl_glue.js — The Frontend Integration Layer

> **Post-cleanup note (2026-05-15).** This document is the snapshot that
> drove the cleanup. The Dead Code Audit / Open Questions below describe
> what was **found**. The items listed there have been actioned across
> Waves A–E — see [dead_code_register.md](dead_code_register.md) for the
> current `fixed` / `kept` / `guarded` status of each.


> **Multi-competition update (Phases 4-5).** `_fetchJson` attaches
> `?comp=<slug>` (from the `IplComp` localStorage store) to every `/api/*`
> call, mirroring the bearer-token attach. New `IplApi` methods:
> `getCompetitions`, `adminCompetition`. `window.IplComp` is exposed. See
> [multi_competition.md](multi_competition.md).

## What it does (business view)

`ipl_glue.js` is the **API client + lifecycle controller** for the
browser-side app. Loaded after the inline script in
[index.html:822](../templates/index.html:822), it does seven things:

1. **Defines `window.IplApi`** — the shim that wraps every `/api/*`
   call (`getState`, `getLeaderboard`, `saveNextWeek`, `rollover`,
   plus the Phase 12 AUTH methods — see below) into a
   Promise-returning method.
2. **Defines `window.IplAuth` (Phase 12)** — bearer-token store backed
   by `localStorage["ipl_session_token"]`. Three methods: `getToken()`,
   `setToken(t)`, `clearToken()`. The token is auto-attached as
   `Authorization: Bearer <t>` on any `_fetchJson` call whose URL
   starts with `/api/passcode`, `/api/admin`, or `/api/whoami`. A 401
   from any of those endpoints auto-calls `clearToken()` so the next
   `_bootstrap()` drops to the login card.
3. **Polls `/api/poll` every 60 seconds**, ETag-aware. When the
   server's ETag changes, it re-fetches state + leaderboard and
   broadcasts `ipl:state-updated` / `ipl:leaderboard-updated` events.
4. **Schedules the auto-rollover** — a single `setTimeout` set for the
   next Monday at 14:00 UTC. When it fires, calls `IplApi.rollover(false)`.
5. **Injects CSS** for the Match Centre look-and-feel. (The maintenance
   overlay it used to inject is gone: 2026-11-21, Phase 1.7.)
6. ~~**The "Safe Boot" timeout**~~ — removed 2026-11-21. A slow first load
   is explained by `Ui.boot` in `ui.js` (stages at 0 / 6 / 25 s, a "Try
   again" screen at 75 s).
7. **Overrides several inline renderers** (`_buildHistoryTab`,
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
  - `window.IplApi`, `window.IplAuth` (Phase 12), `window.IplPolling`,
    `window.IplConfig`, `window.IplRollover`, `window.normaliseLeaderboard`.
  - Custom events on `window`: `ipl:ready`, `ipl:state-updated`,
    `ipl:leaderboard-updated`, `ipl:players-updated`,
    `ipl:week-changed`, `ipl:season-complete`, `ipl:saved`,
    `ipl:session-ended`. It listens to `ipl:saved` and `ipl:recovered`.
    (`ipl:rollover-triggered` and `ipl:error` were removed 2026-11-21.)
  - Side effects: polling loop, rollover timer, CSS injection,
    inline-renderer overrides.

## Key business rules it enforces

### 1. Polling cadence is 60 seconds, ETag-aware
- `POLL_INTERVAL_MS = 60000` ([line 21](../Static/ipl_glue.js:21)).
- `_pollCycle()` only re-fetches state when the server's `state_etag`
  changes — the typical poll is a tiny ETag-only call.
- Polling pauses when `document.hidden` and resumes immediately on
  visibility return.
- **On ETag change, refreshes three endpoints** (post-launch fix
  2026-05-18): `/api/state`, `/api/leaderboard`, AND `/api/players`.
  Players had originally been fetched only once at bootstrap, which
  meant `ext_*` dynamic players added by the scraper after page-load
  would appear in selections as their raw ID (e.g. `ext_10945` with
  no name/team/role) until the user full-reloaded. Same fix mirrored
  in `_doStateRefresh` (index.html) for the Refresh-button path.

### 2. The auto-rollover is local to each browser
- `ROLLOVER_HOUR_UTC = 14`, `ROLLOVER_MIN_UTC = 0` (line 23-24).
- `_msUntilNextRollover()` computes ms-until-next-Monday-14:00-UTC.
- `_executeRollover()` calls `IplApi.rollover(false)`. The server's
  `already_rolled` guard ensures multiple browsers firing
  simultaneously produce exactly one rollover.

### 2b. `IplClock`: lock countdowns follow the SERVER's clock (2026-11-20)
A lock is decided by the server, so a countdown on a phone whose clock is wrong used to show the wrong time left and a wrong locked / open state.
`/api/poll` (every minute) and `/api/state` now carry two server stamps: `server_recv_utc` (when the request arrived) and `server_now_utc` (when the reply
leaves). `IplClock.sync(serverNow, sentAt, receivedAt, serverRecv)` works the offset out as NTP does, so the time the server spent on the request cancels
out (a heavy `/api/state` can take seconds); a trip whose NETWORK part is over 5 s, such as a sleeping server waking up, is ignored. The page loads several
things at once and the browser can hold a request back before it is sent, which makes that reading up to a second or two off, so the clock keeps the reading
from the quickest trip (any trip under 300 ms always replaces the previous one; a slower one only replaces an older-than-10-minutes reading).
`IplClock.now()` applies the offset; until the server's time is known it is the phone's clock. The page's lock functions (`_deadlineLocked`, `_lockUrgent`,
`_deadlineText`, `_lockIn`) read it through `_nowMs()` in `templates/index.html`. Tests: `tests/js/server_clock.test.js`, `tests/test_server_clock.py`.

### 2c. A refused token ends the session in the open (2026-11-20; the rule changed 2026-11-21)
`_fetchJson` reads a 401's `reason` first. A 401 with no reason, `session_ended` or `auth_required` means the session is gone: the token is forgotten and, when a token
WAS sent (a signed-in member) and the call was not a sign-in attempt, `ipl:session-ended` is dispatched; the page answers with the blocking "You've been signed out" sheet
(see index_html.md, "Session handling"). A sign-in attempt (`/api/login`, `/api/register`, `/api/auth/google`, `/api/auth/claim`) never ends a session, but a 401 there still clears a
stale token, as before. **Any other 401 reason is not a session ending**: a Google token that did not check out at `/api/auth/link` (`token_invalid`, `nonce_invalid` ...)
is a refusal for a member who IS signed in; before 2026-11-21 every 401 was taken for a session ending. Tests: `tests/js/session_flow.test.js`, `tests/js/google_signin.test.js`.

### 3. Bearer-token attach is URL-prefix-scoped (Phase 12)

`_fetchJson` inspects the URL and only attaches `Authorization: Bearer
<token>` when the path starts with `/api/passcode`, `/api/admin`, or
`/api/whoami`. This keeps the token off **every other** request —
including reads (`/api/state`, `/api/leaderboard`) and writes
(`/api/save-next-week`, etc.) — matching the server's deliberate scope
in [routes.md](routes.md) Rule #1. If the token surface is ever widened
to gate writes (Open Question 1 in routes.md), this is the one place to
update on the client.

Phase 12 also adds six methods to `IplApi`:

| Method | Endpoint | Stores token? |
|--------|----------|----------------|
| `register(name, passcode)` | `POST /api/register` | Yes, on success |
| `login(name, passcode)` | `POST /api/login` | Yes, on success |
| `whoami()` | `GET /api/whoami` | — (validates an existing one) |
| `changePasscode(newPasscode)` | `POST /api/passcode/change` | Yes (token rotates) |
| `adminListMembers()` | `GET /api/admin/members` | — |
| `adminResetPasscode(targetUsername)` | `POST /api/admin/passcode/reset` | — |
| `logoutClearToken()` | — (local-only) | Clears |

### 3. ETag invalidation triggers
- On `ipl:saved` → `_lastStateEtag = null`.
- On `ipl:recovered` (the server answered again after trouble;
  2026-11-21) → `_lastStateEtag = null` + immediate `_pollCycle()`.
- Together: any save or recovery guarantees the *next* poll picks up
  the change without waiting for the 60s tick.

### 4. Safe boot timeout (REMOVED 2026-11-21)
- It used to give up after 5 s and write into `#error-banner`, an
  element nobody had rendered for a long time. Gone with the red
  banner. A slow or failed first load is handled by `Ui.boot` in
  `ui.js`: a patient message that explains the free server may be
  waking up, then a "Try again" screen at 75 s.

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
| Maintenance overlay (`_showOverlay` etc.) | **REMOVED 2026-11-21 (Phase 1.7).** It hid the whole app (an XI being edited included) after any 5xx on the 60 s poll. Replaced by the slim `Ui.connection` banner. |
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
4. ~~**`_safeBootTimer` doesn't fire if `/api/state` returns 200 but
   `ipl:state-updated` is never dispatched**~~ (e.g. inline script
   crashes before `render()` runs). Removed with the Safe Boot block
   (2026-11-21); `Ui.boot` shows its own "Try again" screen at 75 s
   whatever the cause.
5. **`IplApi` is the contract**, but **five dead methods**
   (`getPlayerPoints`, `getUserMatchPoints`, `saveState`, `saveMatch`,
   `seedHistory`) inflate that contract. Worth grouping the
   delete-candidates in the cleanup pass — each one cascades to a
   route handler and a DAO method.

## Phase 1.6 additions (2026-11-20)

`IplApi.saveNextWeek(name, picks, week)` now states the week the draft is FOR (optional; the server answers 409 `week_changed` when a handover moved that on) and `IplApi.keepNextWeek(name, week)` is new (`POST /api/keep-xi/<n>`: "Keep this XI"). See [routes.md](routes.md) and [index_html.md](index_html.md).

## Phase 1.7 additions (2026-11-21): one "server trouble" pattern

What changed in this file: the overlay section, `MAINTENANCE_DELAY`, `SAFE_BOOT_MS`, the overlay/safe-boot timers and the `ipl:rollover-triggered` listener are deleted; `_pollCycle`'s `.catch` is silent (the failed request already told `Ui.connection`); `_init` adds `window.addEventListener("ipl:recovered", ...)` which clears `_lastStateEtag` and polls at once. "Retry now" (`ipl:retry`) is the PAGE's to handle (`templates/index.html` re-runs `_bootstrap()`), so the glue deliberately does not also poll on it (that would fetch everything twice).

## Phase 3 additions (2026-11-21): Google sign-in

New `IplApi` methods: `authConfig`, `authNonce`, `authGoogle(credential, nonce)` (stores the session token like `login`), `authLink(credential, nonce)`, `authClaim(ticket, name, passcode)` (a Google account nobody has linked is claimed with a member's name and passcode; stores the session token like `login`), `authLogout()`
(ends THIS device's session on the server; the page calls it before it forgets the token), `adminUnlinkGoogle(name)`. The 401 rule changed (2c). The page's half is
`Static/auth_google.js`, see [google_signin.md](google_signin.md).

The owner of the pattern is `Ui.connection` in `Static/ui.js` (see [index_html.md](index_html.md), "Server trouble, Phase 1.7"). `tests/js/connection.test.js` loads this file too and pins the three glue behaviours above.
