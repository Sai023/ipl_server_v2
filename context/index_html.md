# templates/index.html — The Single-Page App Shell

> **Post-cleanup note (2026-05-15).** This document is the snapshot that
> drove the cleanup. The Dead Code Audit / Open Questions below describe
> what was **found**. The items listed there have been actioned across
> Waves A–E — see [dead_code_register.md](dead_code_register.md) for the
> current `fixed` / `kept` / `guarded` status of each.


> **Multi-competition update (Phases 4-5).** New: a header competition
> `<select>` switcher (shown with >=2 competitions) wired to `_switchComp`; a
> dynamic page title = active competition name; a 🏆 Championship tally card
> above the leaderboard; and an Admin "Competitions" card (Activate /
> Close & crown + create form + **Import data** panel). `_bootstrap` / `_doStateRefresh` thread
> `?comp=` onto their raw fetches. See
> [multi_competition.md](multi_competition.md).

## What it does (business view)

`index.html` is **the only HTML page** the user ever loads. It boots
once, then everything they see (tab switches, modals, draft edits,
leaderboard rankings) is rendered client-side from the JSON returned
by the `/api/*` endpoints.

The file is **825 lines, mostly inline JavaScript**:

- `<head>` is 11 lines — meta, manifest, stylesheet.
- The HTML body is essentially `<div id="app"></div>` plus the boot
  screen (`#app-loading`). (The red `#error-banner` is gone since
  2026-11-21; trouble reaching the server is a slim banner that
  `Ui.connection` in `ui.js` creates on demand as the first child of
  `<body>`.)
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
    `switchTab()`, `_nwSave()`,
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

**In and out from inside the picker (2026-11-18).** `_pickerRowClick` on a player who is already in the draft now takes him OUT
(`_nwRemove`: clears a captain / vice-captain flag, shows the 6-second Undo toast) instead of the old "already in your XI" notice, so nobody
has to go back to the squad screen to remove someone. On phones the header line "9/11 picked · 12.5 CR left" is a button
(`_pickerToggleXi`, state `_pickerXiOpen`) that opens **Your XI** (`_pickerXiHtml`): the draft as a two-column grid, role / price / C-VC on each
cell and a cross that takes the player out, for a player the current filters hide. It is hidden at >= 1000 px, where the squad is on screen anyway.

**Team filter = several teams at once.** `_pickerTeams` (array, empty = every team) replaced the single `_pickerTeam` dropdown. The Filters panel
draws one toggle chip per team with `Ui.chips` (team colour dot; the leading "All" chip clears); `_pickerToggleTeam` flips one team; the
results bar shows a removable chip per team. Reset on a competition switch (`_resetCompScope`). Tests: `tests/js/picker_team_chips.test.js`.

**Edit-team UX pass (2026-11-18)** - the Pick tab, found by testing it task by task on a 375 px phone and at 1280 px. Tests: `tests/js/team_edit_ux.test.js`.
- *Picks are never lost.* Every change to the draft goes through `_nwChanged()` (keep + redraw). `_draftStore()` writes the unsaved draft to
  `localStorage` as `ipl_draft:<competition>:<member>` = `{wk, base, team, cap, vc}`; `_nwInit()` -> `_draftRestore()` brings it back after a reload / closed
  app / dropped connection, but only for the same week (`wk`) and the same saved XI (`base` = signature of what the server held when the edit was made),
  so a draft made before the server moved on (saved from another phone, a rollover) is dropped. The competition comes from `IplComp.get()` first (as the API
  calls do): `_activeComp` is only set once `/api/competitions` has answered. `_nwInit(null)` still restores (a new member / before the first week has no
  history row). Storage that throws (private mode) is swallowed: the draft just stays in memory. `_nwHasUnsaved()` (non-empty AND different from saved)
  drives the dot on the Pick tab (`_navDot`), the hint "Restored your unsaved picks", the action banner ("not saved yet" + a one-tap **Save XI**) and
  `Discard changes` (`_nwDiscard`, with Undo). `_nwNewWeek()` restarts the draft when the rollover ran while the app was open (`_doStateRefresh`).
- *A locked week is read-only.* `_deadlineLocked()` = the SERVER's clock (`_nowMs()`, see `IplClock` in ipl_glue.md) past `next_lock_utc` (for a competition on a stored lock schedule this is the lock that is waiting for its handover, so it reads locked until the handover has run) OR `deadline.season_over` (the final week of a scheduled competition: "Final week under way — picks are closed") OR season over OR the server answered 423 for this week
  (`_lockedWeek === _currentWeek`, set by `_nwTooLate()`; the server is the authority, the device clock can be wrong). The 1-second tick flips the screen
  when the countdown runs out (`_lockSeen`) and, while locked, asks `/api/current-week` every 20 s (`_checkWeek`) so the draft reopens by itself after the
  rollover. Locked view: `_nwShown()` (the XI the server holds, never an unsaved edit), `.nw-lockbar` with the reason, rows as static `div`s, no budget bar,
  no Add players, no picker (`.nw-locked`, one column). The unsaved draft is NOT thrown away (a wrong clock must not eat it). The countdown is a pill
  (`.nw-countdown`) that turns red in the last hour (`_lockUrgent`).
- *Save.* `_nwSave()`: one request at a time (`_nwSaving`; the button shows `Saving...`, `aria-busy`), after 4 s the hint says the free server may have been
  asleep (`_nwSlow`); 423 -> locked view; no HTTP status (offline) -> the toast says "Your picks are kept on this phone".
- *Captain and vice-captain are MANDATORY (2026-11-19, the league's call).* The Save button is off until both are chosen (`canSave` in `_buildBudgetBar`), the hint
  says "Choose your captain (x2 points) and vice-captain (x1.5 points) to save." with a **Choose** button, and `_nwSave()` itself refuses (warn + opens the right
  list) as a safety net: there is no "save anyway". The banner on the other tabs never calls an XI "ready" without both roles AND a save: it says "Your XI needs a
  captain (and a vice-captain)" with a **Choose captain / Choose vice-captain** button (`_nwPickRole`), then "complete but not saved yet" with **Save XI**.
  The server enforces the same rule (`db_manager.validate_selection`, DB_VER 6.4): a full XI without a captain and/or vice-captain gets a 422 with its own message
  ("Choose a captain and a vice-captain before saving" / "Choose a captain before saving" / "Choose a vice-captain before saving"), decided before the database is
  read; an empty team still clears a draft. Tests: `tests/test_selection_rules.py` (no database needed; one case runs against a scratch schema when
  `TEST_DATABASE_URL` is set) and the cases named "mandatory" / "banner" in `tests/js/team_edit_ux.test.js`.
- *Hint line* (`_buildBudgetBar`): says what is left and the one button that does it: "Pick 4 more players. About 8.6 CR each keeps you in budget", a dead-end
  warning when the CR left cannot fill the spots (`rem < k * cheapest`), "Choose" (required to save) -> `_nwPickRole('cap'|'vc')` (the whole XI in one list, best scorers
  first; the vice-captain list follows the captain's when none is set), over budget, unsaved, restored, saved.
- *Layout.* The empty "+ pick a player" slots are gone (11 identical buttons pushed the real Add players button below the fold on a phone); one stable
  `Add players (n/11)` button sits right under the hint, above the list. The over-budget notice in the picker is now a toast (`Ui.warn`), not a banner
  that re-rendered the page after 2.8 s. A second tap on the same picker row within 400 ms is ignored (`_lastTap`): on a slow phone it is a re-tap, and it
  used to undo the add. While `/api/players` has not arrived the list says "Loading the players" instead of "No players match these filters".
**Floating bars + the strip (2026-11-19, from phone screenshots).** Three things the league saw on a real phone, each with a number from the preview app:
- *The strip under the app bar* (`_pageStripHtml`) was four chips that wrapped onto a second line at 393 px (67 px tall + margin). It is now ONE row: the status chip plus one
  capsule `.chip.stats` ("3 members · 34 matches · 100 CR", " budget" kept for screen readers), 27 px tall, `flex-wrap: nowrap` with sideways scrolling as the fallback
  on a very narrow phone. Saves ~45 px on every tab.
- *The Pick tab's floating bar touched the app bar.* It was `position: sticky; top: 64px` on the bar itself while the app bar is 69 px: it slid 5 px UNDER it, with no step and no
  backdrop. Now the bar sits in a sticky wrapper `.nw-stick` (`top: var(--app-bar-h)`, 8 px of card colour above the bar so rows never show in the gap, a 6 px fade under it,
  a soft pencil shadow on the bar). `--app-bar-h` is the app bar's REAL height, measured and published by `_syncBarH()` (after every render, on resize, when the web fonts are
  ready; 69 px on a phone, 113 px with the desktop nav tabs): it grows with the phone's font size, so a hard-coded number would drift. At >= 760 px the wrapper is static (as
  before); on desktop the side panel keeps the same step (`.nw-left { top: calc(var(--app-bar-h) + 8px) }`). The old 64 px rule in `screens.css` is gone.
- *"Add players" had no context while scrolling* (how many left to pick, the budget, the role balance). The cause was a latent bug: `.picker-shell { overflow: hidden }`
  (style.css) makes the shell the scroll container for sticky children, and it never scrolls, so the layer header and the toolbar (both `position: sticky`) never stuck.
  Fixed (`overflow: visible` for the shell inside the layer) and the page now has a floating summary `.pb-wrap` (`_pickerBarHtml`, built from the same `_barParts()` as the Pick
  tab's bar): "BUDGET 95.0 / 100CR", the progress track, "5 / 11", the role balance and "6 to pick" / "XI full". Header (fixed `--lh-h` = 64 px: back, title, Done) and summary stick;
  the search/filter toolbar scrolls with the list (header + summary are 98 + 64 px = ~19% of a phone screen; a sticky toolbar would make it ~30%). The right-hand
  "6 to pick" button opens "Your XI" as a DRAWER inside the sticky wrapper (it used to open at the top of the list, off-screen once you had scrolled). Hidden at >= 1000 px
  where the squad is on screen. Tests: the section "the strip under the app bar, and the two floating bars" in `tests/js/team_edit_ux.test.js` (markup, numbers, drawer
  placement, `_syncBarH`, and the stylesheet rules that make them stick: the shell must not clip, nothing may position the bar by a hard-coded 64 px).
- *Keyboard and screen readers.* `render()` rebuilds the page, which used to drop focus to the top after every tap. Controls that matter carry `data-fk`
  (`pk:<id>` picker row, `sq:<id>` squad row, `xx:<id>` Your XI cross, `nw-save`, `nw-cta`, `pk-done` ...); `_focusKey()` reads it before the rebuild and
  `_focusFind()` restores it after, falling back to the nearest working control (a removed player's row -> Add players; a Save that just became disabled ->
  the hint button / Add players; closing the picker -> the button that opened it). The picker moves focus in on open; the swap sheet and Scoring Rules give
  focus back to what opened them. Tap targets on this screen are 44 px (`Done`, team chips, `Your XI` toggle, filter chip crosses via a pseudo-element hit area).

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

### 7b. Session handling: checked at launch, and what happens when it ends (2026-11-20)
- **Launch.** A saved name + token is checked with `GET /api/whoami` (`_checkSavedSession`), and the loader stays up meanwhile (no 8 s give-up). Only a real
  401 signs the member out, and then straight to the passcode prompt for them ("Your session ended. Enter your 4-digit passcode to continue."). No network, a
  slow server (25 s) or a failing one is NOT an answer: the member stays signed in on trust (`_unverified`; the Admin tab follows the last known `ipl_is_admin`),
  sees the league with a slim "Can't confirm your sign-in" banner, and `_recheckSession` retries quietly (3 s, 8 s, 20 s, then every minute; also on "Retry now"
  and when the phone comes back online).
- **Mid-session.** `IplApi` fires `ipl:session-ended` when a signed-in member's token is refused; `_sessionEnded` shows `Ui.sheetBlocking` ("You've been signed
  out. Your picks on this phone are safe." / Sign in). It cannot be dismissed (Escape and a tap outside do nothing). Sign in opens the passcode prompt for the
  same member; closing that prompt brings the sheet back; signing in (`_reauthDone`) carries on exactly where they were: same tab, the unsaved draft untouched.
- Flags: `_authPending`, `_unverified`, `_sessionLost`, `_reauth`, `_sessionSheet`; all are cleared by `_establishSession` and `logout`.
- Tests: `tests/js/session_flow.test.js`.

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

Admin → Competitions → **Import data** (competition import; see
[competition_import.md](competition_import.md), [user_capabilities.md §8.7](user_capabilities.md)):

| Function | Purpose |
|----------|---------|
| `_imp` / `_impReset(slug)` | Panel state (slug, parsed files and their names, last report, busy / done flags, week-1 date). Kept in a variable, **not** in the DOM, because `render()` rebuilds the whole page and a background refresh must not lose a half-filled import. |
| `_impOpen(slug)` | Shows / hides the panel for a competition (the table button toggles "Import data" / "Hide import"). |
| `_impPick(kind, input)` | Reads the chosen file with `FileReader`, parses the JSON (a parse error is shown in the panel, no request sent) and immediately runs a dry-run check. |
| `_impCall(dry)` | `IplApi.adminCompetition({action:"import", slug, players?, schedule?, dry_run, week1_anchor_utc?})`. A 400 arrives as a thrown error whose `serverData` carries `errors[]`, `warnings[]` and `needs_anchor`. A successful real import clears `_players` and calls `_bootstrap()`. |
| `_impRun()` | "Import now": confirms with the counts, then `_impCall(false)`. Disabled unless the last check passed. |
| `_buildImportPanel()` / `_buildImportReport(r)` | Render the file pickers, the optional week-1 date picker (shown only when the server answers `needs_anchor`) and the green / amber / red report. |

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
  `normaliseLeaderboard` (all from `ipl_glue.js`; `IplRollover` was removed 2026-11-19);
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
| §8.5 Weekly rollover (Admin) | `_buildDevTools` (an explanation only since 2026-11-19; the Simulate button and `_simulateRollover` were removed) |
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
| `_pickerNoticeTimer` global / `_pickerShowNotice` | - | **Removed (2026-11-18).** The over-budget notice is a `Ui.warn` toast now. |
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

## The weekly pick, Phase 1.6 (2026-11-20, app 2.19.0)

- **Which week a draft is for** (`_nwTargetWeek`): on a lock schedule the first week not yet handed over (`deadline.applied_to + 1`, or the waiting `awaiting.to_week`); otherwise the week after the current one (week 1 before it starts). The Pick card is labelled from it ("Pick your team" before the season, "Week 2 draft" right after the first week starts, which the old `_currentWeek + 1` got wrong), and every save and keep states it.
- **Keep this XI** (`_nwCarried`, `_nwKeep`): a draft that equals the XI playing this week and has not been saved or kept since the row was created is "carried over". The sticky bar's button reads **Keep this XI** (enabled) with an **Edit** button in the hint row ("Week 3 draft: using last week's XI. It carries over unless you change it."); pressing it calls `/api/keep-xi`; afterwards **Kept ✓** and "Kept. We will use this XI for Week 3.". The banner on the other tabs offers the same one-tap Keep. (Before, a carried draft showed a disabled "Saved" button.)
- **A draft from a week that moved on is offered back, never deleted** (`_nwOfferStale`, `_nwAdopt`, `_nwLetGo`): in three places - the app opens with a kept copy on the phone from an earlier week (`_draftRestore`; the copy stays until answered), a handover runs while the app is open with unsaved picks (`_nwNewWeek`), and a save is refused with 409 `week_changed` (`_nwWeekMoved`: "Week 3 just started. Your draft was for the Week 3 lineup, but Week 3 is already playing. Save it as your Week 4 draft?" - Save as Week 4 draft / Discard; a refused Keep only says the week moved on). All use `Ui.sheetBlocking`: it must be answered, Escape and a tap outside do nothing.
- **State sync** (`_nwSyncTarget`): a handover moves the week a draft is for even when the current week keeps its number (the first week starts), so the page compares the target in every new state and refetches the member's rows when it changes (`_doStateRefresh` then calls `_nwNewWeek`).
- **Lock card wording** (`_lockCardText`, `_deadlineText`): "Picks locked at 16:00. Setting up Week 3. This takes a few seconds." (the chip reads "Locked - setting up Week 3"); after two minutes "Still setting up Week 3 - This is taking longer than usual. Your locked XI is safe." (the admin-has-been-told sentence waits for the Phase 2 alert); the last week "Picks are closed"; competitions without a schedule keep the old text.
- **Notices** (`_nwNotices`, managed competitions only): "No XI for Week N. You'll score 0 until Week N+1. You can still draft Week N+1." for a member with no XI for the week that is playing; "Week N is open - Your XI carried over" once (dismissed per week and phone, `ipl_seen_week:<comp>:<name>`), only when last week's draft was never confirmed.
- **Local time** (`_whenLocal`, `_hhmm`): "Locks Mon 18 Jan, 16:00" under the label, in the phone's own time zone (the countdown keeps following the server clock).
- Tests: `tests/js/team_edit_ux.test.js` (97 cases, the new ones at the end), `tests/test_pick_flow.py` for the server half.

## Server trouble, Phase 1.7 (2026-11-21, app 2.20.0)

**Before:** two unrelated mechanisms and a blocking one. A full-screen "Server Unavailable" overlay (in `ipl_glue.js`) hid the whole app, an XI being edited included, after any 5xx on the 60 s poll; a bare red `#error-banner` said something else, a redraw wiped it, and it never cleared when the data had not changed.

**Now:** one controller, `Ui.connection` in `Static/ui.js`. Every same-origin `/api/` call reports its outcome (a one-time wrapper around `window.fetch`; the caller gets the very same promise): an answer below 500 means the server is up (a 401/409/422/429 is the server talking), a 5xx or no answer is trouble (an aborted request is neither).
- **When it shows:** one failure is silent. A quiet probe of `/api/ping` follows after ~3 s and the SECOND consecutive failure raises the banner, so a single blip never flashes. The probe backs off 3, 5, 10, 20, 40, then every 60 s (each +-20 % jitter), never runs while the tab is hidden, and runs at once when the tab is shown again. The first good answer clears the banner, toasts "Connected again." (only if the banner had shown) and fires `ipl:recovered` (the glue refetches at once).
- **What it says** (priority offline > server > sign-in): "You're offline. Showing the last data we have. (updated 3 min ago)" (before the first screen: "You're offline. We'll load your league as soon as you're back."); "The server is having a moment. Retrying..."; after 90 s "Still can't reach the server. Showing the last data we have. Your picks are kept on this phone." (red); "Can't confirm your sign-in - retrying..." (the page's own `_signinTrouble`, fed by `_unverified`). Server and sign-in messages wait while the boot screen is up (`hideLoading` calls `Ui.connection.refresh()`); offline shows through (the banner sits above the boot screen).
- **Where:** an element created on demand as the FIRST child of `<body>`, in the normal flow (`position: relative`, never fixed): it pushes the page down and never covers the sticky app bar. Polite live region (`role=status`); the "updated ... ago" text is `aria-hidden`; a changed message is announced, an unchanged repaint is not, and a banner that returns after clearing is rebuilt so it is announced again. "Retry now" (44 px hit area) probes at once and fires `ipl:retry`, which the page answers by re-running `_bootstrap()`.
- **Stale offline flag:** a good answer from the server proves the phone is online, so a missed `online` event can never leave "You're offline" stuck.
- **Failed save:** `_nwSave` says "Could not save your team: <reason> Your picks are kept on this phone." for a network failure and for any 5xx (other failures keep `Ui.fail`'s wording).
- **Gone:** `#error-banner` (element, CSS in `style.css`/`ui.css`), `showErrorBanner`/`hideErrorBanner`, the `ipl:error` listener, the overlay, Safe Boot, `ipl:rollover-triggered`. `tests/js/connection.test.js` fails if any comes back.
- Tests: `tests/js/connection.test.js` (29 cases, all 39 mutants of the new code killed), `tests/js/session_flow.test.js` (the sign-in message goes through `_signinTrouble`). Checked in a real browser against a rig whose `/api/` can be switched to 503.
