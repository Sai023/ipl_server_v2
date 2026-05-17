# templates/index.html — The Single-Page App Shell

> **Post-cleanup note (2026-05-15).** This document is the snapshot that
> drove the cleanup. The Dead Code Audit / Open Questions below describe
> what was **found**. The items listed there have been actioned across
> Waves A–E — see [dead_code_register.md](dead_code_register.md) for the
> current `fixed` / `kept` / `guarded` status of each.


## What it does (business view)

`index.html` is **the only HTML page** the user ever loads. It boots
once, then everything they see (tab switches, modals, draft edits,
leaderboard rankings) is rendered client-side from the JSON returned
by the `/api/*` endpoints.

The file is **825 lines, mostly inline JavaScript**:

- `<head>` is 11 lines — meta, manifest, stylesheet.
- The HTML body is essentially `<div id="app"></div>` plus a loading
  overlay and an error banner.
- The remaining ~810 lines are the inline `<script>` that defines all
  the renderers (`_buildLoginCard`, `_buildThisWeekCard`,
  `_buildNextWeekCard`, `_buildPicker`, `_buildLeaderboardCard`,
  `_buildMembersCard`, `_buildMatchesCard`, `_buildAdminTab`, etc.)
  plus the central `render(state)` function that switches on
  `_activeTab` and assembles the page.

The two external scripts at the bottom of `<body>` (`/static/ipl_glue.js`,
`/static/mc_hub.js`) override and extend several of these inline
functions — that's the architecture's "glue" layer.

## Where it sits in the flow

```
Browser  ──GET / ──>  routes.api index()  ──render_template──>  index.html
                                                                    │
                                                                    ▼
                                                          inline <script>:
                                                          - defines builders
                                                          - defines render()
                                                          - calls _bootstrap()
                                                                    │
                                                                    ▼
                                                          /static/ipl_glue.js   ← overrides _buildLeaderboardCard, etc.
                                                          /static/mc_hub.js     ← overrides _buildMatchCentreTab
                                                                    │
                                                                    ▼
                                                          render(state)  ← fires on every state change
```

`index.html` is loaded **once**; from that point on the SPA mutates
the DOM in-place. The Flask 404 handler in `base.py` also re-renders
`index.html` so client-side routes work on hard reload.

## Inputs / Outputs

- **Inputs:**
  - The raw HTML payload (no template variables — the file is
    rendered as a static string).
  - Browser-side: `localStorage` (`ipl_username`), `_state` (from
    `/api/state`), `_leaderboard`, `_players`, `_historyData`.
- **Outputs:**
  - The DOM under `<div id="app">`.
  - Side effects via global functions: `login()`, `logout()`,
    `switchTab()`, `_nwSave()`, `_simulateRollover()` (Admin-only),
    `_showRulesModal()` / `_closeRulesModal()`, etc.

## The 6 visible tabs

Per the tab list at [index.html:744-754](../templates/index.html:744) —
this is the authoritative count, not SKILL.md's "9 tabs" claim:

| # | Tab ID | Renderer | Default? |
|---|--------|----------|----------|
| 1 | `match-centre` | `_buildMatchCentreTab` (mc_hub.js) | **Yes** |
| 2 | `team` | `_buildThisWeekCard` (Scoring Rules button → `_showRulesModal`) | — |
| 3 | `next` | `_buildNextWeekCard` (includes `_buildPicker`; Scoring Rules button → `_showRulesModal`) | — |
| 4 | `leaderboard` | `_buildLeaderboardCard` (overridden in ipl_glue.js) | — |
| 5 | `members` | `_buildMembersCard` | — |
| 6 | `admin` | `_buildAdminTab` (which now opens with `_buildAdminPasscodesCard`, Phase 12) + `_buildDevTools`. **Admin-only**: tab button rendered only when `_isAdmin === true`. | — |

The Points, History, and Matches tab renderers exist in this file
and/or `ipl_glue.js` but **no tab button references them and no
render-switch branch dispatches to them** — see Dead Code.

## Key business rules it enforces

### 1. Identity is a localStorage string + a bearer token (Phase 12)
- `localStorage["ipl_username"]` is set on register / successful login.
- `localStorage["ipl_session_token"]` is set on `/api/register` /
  `/api/login` / `/api/passcode/change` (managed by `IplAuth` in
  `ipl_glue.js`). Token cleared on `logout()` or any 401 from
  `/api/passcode/*` or `/api/admin/*`.
- The bootstrap auto-logs the user back in **only if** both keys are
  present **and** `/api/whoami` validates. Invalid token → both keys
  cleared and login card shown. The pre-Phase-12 "trust the name in
  state.members" path is gone.

### 2. Hard constraints in the draft picker
- Squad size = `XI_SIZE` (= 11), hardcoded on line 17.
- Budget = `BUDGET_TOTAL` (= 100.0 CR), hardcoded on line 17.
- Server enforces both again in `/api/save-next-week`.

When the XI is full and the user clicks a new player, `_pickerRowClick`
opens the **swap modal** ([index.html:686](../templates/index.html:686))
asking which existing player to remove.

### 3. The Save Draft button is gated client-side
[index.html:368](../templates/index.html:368):
`canSave = count === XI_SIZE && rem >= -0.001`. The 0.001 epsilon
absorbs floating-point rounding so 50.0 + 50.0 doesn't fail.

### 4. The "Refresh" button is a three-step ritual
The header **Refresh** button (`_refreshData()` at line 259):
1. `POST /api/sync-now` — kicks off discovery + scrape in the background.
2. **Immediately** re-fetches `/api/state`, `/api/leaderboard`, `/api/history/<n>`.
3. **After 75 seconds**, re-fetches state again to capture any
   late-arriving scraped points.

While running, polling is paused via `IplPolling.stop()` and restarted
at the end. The 75 s figure is tuned to the typical discovery+scrape
cycle (the docstring in `tasks.py` agrees).

### 5. The Members tab "log in as anyone" hole is closed (Phase 12)
Clicking another member's pill / xi-name now calls `attemptLogin(otherName)`,
which opens the passcode prompt modal — same flow as a login-card chip
click. You can still see everyone's XIs (read-only `_buildMembersCard`),
but you can't *become* them without their passcode. Pre-Phase 12 this was
a one-click impersonation by design.

### 6. Captain ≠ Vice-Captain enforced on toggle
`_nwSetCap(id)` and `_nwSetVc(id)` each clear the *other* role if the
user assigns both to the same player ([index.html:187-188](../templates/index.html:187)).

### 7. Mobile keyboard dismiss after pick/save
`document.activeElement.blur()` fires inside `requestAnimationFrame`
after pick/swap so the keyboard dismisses before the next paint —
mentioned in the README's frontend section.

### 8. 404 falls back to this file
`base.py`'s 404 handler re-renders `index.html`, so a hard refresh of
e.g. `/leaderboard` (if anyone ever bookmarked one) still loads the SPA.

### 9. Passcode modals + admin gate (Phase 12)

Three new modal builders + one global flag drive the entire auth UX:

| Function | Purpose |
|----------|---------|
| `_showPasscodePromptModal(name)` | Slides up after a member chip click. Auto-submits on the 4th digit (`input` listener strips non-digits then calls `_submitPasscode`). On success → `_establishSession`; if `must_change` → immediately opens forced reset. |
| `_showResetPasscodeModal(forced)` | Two flows in one modal: `forced=true` hides the × and the cancel button; `forced=false` keeps both. Calls `IplApi.changePasscode(new)` which rotates the token. |
| `_buildAdminPasscodesCard()` | Top of the Admin tab. Renders each member as a row with a status pill (⚠ Default / 🔒 Custom) and a "Reset to 1234" button. Button disabled on the admin's own row (they use the header self-reset). |

Globals added:
- `_isAdmin` (default `false`) — set by `_establishSession` from
  `/api/login` or `/api/whoami`. The Admin tab button is conditionally
  pushed into `_tabs` only when this is true; if the user lands on
  `_activeTab="admin"` without being admin (e.g. after a passcode-reset
  forced demotion), the render switches them to `match-centre`.
- `_mustChange` — drives the auto-open of the forced reset modal.
- `_adminMembersList` — cache for `/api/admin/members`, invalidated and
  reloaded after every admin reset.

## Called by / Calls into

- **Called by:** the user's browser (initial GET).
- **Calls into (HTTP):** every `/api/*` endpoint reachable via the UI.
  Detailed mapping in [user_capabilities.md §12](user_capabilities.md).
- **Calls into (JS):** `IplApi`, `IplPolling`, `IplConfig`,
  `IplRollover`, `normaliseLeaderboard` (all from `ipl_glue.js`);
  `_buildMatchCentreTab`, `_openMatchModal`, `_closeMatchModal` (from
  `mc_hub.js`).

## Supports which user capabilities

Direct 1:1 with [user_capabilities.md](user_capabilities.md):

| Capability | Renderer / handler |
|------------|---------------------|
| §1.1 Register (Phase 12) | `_buildLoginCard` + `_doRegister` |
| §1.2 Login (Phase 12) | `attemptLogin` → `_showPasscodePromptModal` → `_submitPasscode` → `_establishSession` |
| §1.3 Reset Passcode (Phase 12) | header "🔑 Reset Passcode" → `_showResetPasscodeModal(false)`; forced variant auto-opened from `_establishSession` when `must_change=1` |
| §1.4 Switch user | `logout()` + Switch user button (also clears `ipl_session_token`) |
| §8.6 Member Passcodes (Admin, Phase 12) | `_buildAdminPasscodesCard` + `_adminResetPasscode` |
| §3 Match Centre | delegated to `mc_hub.js` |
| §4.1 This Week XI | `_buildThisWeekCard` + `_buildPitchView` |
| §4.2 Scoring Rules popup | `_showRulesModal` / `_closeRulesModal` (button injected by `_buildThisWeekCard` and `_buildNextWeekCard`) |
| §8.5 Simulate rollover (Admin) | `_buildDevTools` + `_simulateRollover` (rendered under the `admin` tab branch) |
| §5.1 Picker | `_buildPicker` |
| §5.2 Captain/VC | `_buildNwSquad` (C/V/× buttons), `_nwSetCap`, `_nwSetVc` |
| §5.3 Save Draft | `_buildBudgetBar` + `_nwSave` |
| §5.4 Season badges | inline in `_buildNwSquad` (line 343) |
| §6 Leaderboard | `_buildLeaderboardCard` (override) |
| §7 Members | `_buildMembersCard` |
| §8 Admin tab | `_buildAdminTab` + `_adminSaveUrl` |
| §9.1 Refresh | `_refreshData` (lines 259-303) |

## Dead Code Audit

| Item | Lines | Verdict |
|------|-------|---------|
| `_buildHistoryTab()` | 498–515 (~18 lines) | **DEAD UI tab.** Not in tab list, not in render switch. Also redefined in `Static/ipl_glue.js:371` (overridden). Already tracked as **D5**. |
| `_buildPointsTab()` | 569–612 (~44 lines) | **DEAD UI tab.** Same pattern as D5. Also redefined in `Static/ipl_glue.js:462`. Already tracked as **D3** (this file) and **D4** (ipl_glue.js). |
| Points-tab supporting state and loader: `_ptsData`, `_ptsLoading`, `_loadPoints()`, plus `switchTab("points")` reference at line 101 | 25, 122–133 (~12 lines) | **DEAD.** Only consumer is `_buildPointsTab`. Already tracked. |
| `_buildMatchesCard(matchList)` | 552–560 (~9 lines) | **DEAD.** This is a *different* renderer from `_buildMatchesTab` (D24, in ipl_glue.js) — it's a simpler list view that's also not called from anywhere. Added to register as **D28**. |
| Inline `BUDGET_TOTAL=100.0, XI_SIZE=11` at line 17 | 17 | **Duplicate constants.** `/api/ping` returns the server's values via `IplConfig.budget` / `xi_size` and the rest of the app already uses those. The inline hardcoded values are a fallback in case the bootstrap fails. Worth keeping — the failure mode would otherwise be a silently mis-sized picker. Not dead. |
| `_pickerNoticeTimer` global | 24 | **Live.** Used in `_pickerShowNotice`. |
| `_buildXiGrid()` | 323–334 | **Live.** Used by `_buildHistoryTab` (dead), `_buildMembersCard` (live, line 526), and `_buildLeaderboardCard` (live). Live because of the members card. |
| The inline `<script>` block 15–820 | — | **Mostly live.** The bulk of renderers, mutators, and the bootstrap. Dead items are the three tabs above. |
| **UI bug — not dead code, but a defect** | 491 | The Next Week panel says `"Locks Mon 14:00 IST"`. The actual deadline is 14:00 UTC (= 19:30 IST). Tracked as **S3**. |

**Total dead UI in `index.html`:** ~83 lines across three orphaned tab
renderers and their state (D3, D5, D28).

## Open Questions

1. **The inline script is 800+ lines in a single `<script>` tag.**
   Hard to test, hard to refactor, hard to source-map in DevTools.
   Worth either splitting renderers into `static/builders.js`, or
   moving the whole inline script into `static/app.js`. Functionally
   equivalent, much easier to maintain.
2. **`IplConfig` is populated by `/api/ping` but the inline script
   uses the hardcoded `BUDGET_TOTAL` / `XI_SIZE` constants** instead
   of `IplConfig.budget` / `IplConfig.xi_size`. Today they always
   match (the server returns 100 / 11), but if the server constants
   ever change, the UI will silently disagree.
3. **The "points" / "history" / "matches" tab IDs are referenced
   in `switchTab()` ([line 99-104](../templates/index.html:99))**
   despite no UI ever triggering them. `switchTab("points")` would
   call `_loadPoints()`, mutate `_activeTab="points"`, but the render
   switch has no branch for it — the page would render an
   empty `<div class="wrap">`. Dead state path; cleanup is part of
   the D3/D5/D24 cascade.
4. **`_buildLeaderboardCard` defined twice — once inline (line 532),
   once in `ipl_glue.js` (line 413).** The `ipl_glue.js` override wins
   because it loads after the inline script runs. Worth either
   deleting the inline version (it's a strict subset of the override)
   or moving the override into this file and deleting the
   ipl_glue.js entry.
5. **`_buildXiGrid` doesn't stamp `data-pid` attributes** — the v7.7
   `_patchXiGrid()` patch existed to add them, but that patch lives
   only in the orphan root `ipl_glue.js` (O1) and not in the live
   Static version. The live `_buildNwSquad` renders `season_pts` inline
   (line 343), so the data-pid stamping is no longer required.
   Tracked as a documentation drift — README still mentions
   `_patchXiGrid` as the fix.
