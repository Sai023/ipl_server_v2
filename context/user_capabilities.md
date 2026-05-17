# User Capabilities — IPL Fantasy 2026

> **Source of truth:** this document is built **only** from
> [templates/index.html](../templates/index.html), [Static/ipl_glue.js](../Static/ipl_glue.js),
> and [Static/mc_hub.js](../Static/mc_hub.js) — not from SKILL.md or README.md,
> both of which are partially stale. Every claim below is anchored to a file
> and line.

This is the canonical list of **what a user can do in the system**. Every
backend context file should map its work back to one of these capabilities.

---

## 1. Identity

> **Phase 12 — Passcodes.** Identity now requires a 4-digit passcode. The
> "trust-based" stance from earlier phases is replaced by a friction barrier:
> a sibling can't impersonate another member by typing their name. The barrier
> stops short of being real auth — see §1.5 for the honest threat model.

### 1.1 Register a new profile
- **What:** "Who are you?" card on first load shows a **Register here** link
  that expands into two fields: display name + 4-digit passcode.
- **Backend:** `IplApi.register(name, passcode)` →
  `POST /api/register` returns `{token, must_change:false, is_admin:false}`.
  Server creates the `members` row + an empty `user_selections` row.
- **`Sai` is the only admin** — seeded by [init_db.py](../init_db.py) on first
  boot. New registrations get `is_admin=0`.

### 1.2 Log in as an existing member
- **What:** Click a member chip → 4-box passcode prompt slides up; auto-submits
  on the 4th digit.
- **Backend:** `IplApi.login(name, passcode)` → `POST /api/login` returns
  `{token, must_change, is_admin}` on success, 401 with a generic "wrong name
  or passcode" otherwise (intentionally doesn't distinguish unknown-user from
  wrong-passcode).
- **How sessions persist:** the token is stored in `localStorage` under
  `ipl_session_token`. Together with `ipl_username`, the bootstrap calls
  `/api/whoami` to validate; valid → auto-login, invalid → token cleared and
  login card shown. 30-day TTL on the server side.

### 1.3 Reset your passcode (header button)
- **What:** **🔑 Reset Passcode** button next to ⟳ Refresh, visible whenever
  a user is logged in.
- **Effect:** opens a modal that asks for **new + confirm** (no current
  passcode — the user is already authenticated via bearer token, so requiring
  it would be friction without security gain). On save, all of that user's
  other sessions are invalidated and a fresh token is issued.
- **Forced variant:** when `must_change=1` (after an admin reset), the same
  modal opens automatically right after login, has no × close button, and
  blocks the rest of the UI until a new passcode is set.

### 1.4 Switch user / log out
- **What:** "Switch user" button in the header bar.
- **Effect:** clears `_username`, drops `ipl_session_token` from
  `localStorage`, calls `IplApi.logoutClearToken()`, and re-renders the
  login screen. The bearer token stays valid server-side until the next
  passcode change for that user — log-out is client-only.

### 1.5 Honest threat model
- 4-digit passcode = **friction, not security**. 10,000 combinations is
  trivially brute-forceable, and only `/api/passcode/*` + `/api/admin/*`
  require the token — *every other* write endpoint (`/api/save-next-week`,
  `PUT /api/member`, `/api/rollover`, `/api/update-match-url`) still trusts
  `?user=<n>` exactly as before. A determined attacker who can hand-craft an
  HTTP request can still act as anyone.
- Hashed passcodes (sha256 of `username:passcode`) are committed to git in
  HOSTED mode along with `fantasy.db` — anyone with repo read access can
  brute-force them offline.
- For a private friends league this is the intended trade-off.
  Tracked in [routes.md](routes.md) Open Question 1 as the follow-up if real
  auth ever matters.

---

## 2. Six Tabs (logged-in view)

The full tab list rendered for a logged-in user
([index.html:744-755](../templates/index.html:744)):

| # | Tab ID | Label | Purpose |
|---|--------|-------|---------|
| 1 | `match-centre` | Match Centre 🎯 | **Default tab.** Hub of all matches with per-user per-match points; click a card to open the Box Score modal. Owned by `mc_hub.js`. |
| 2 | `team` | This Week 🔒 | Read-only view of the user's locked XI for the current week, with budget bar, role pills, C/VC. Also shows Dev Tools rollover simulator. |
| 3 | `next` | Next Week ✏️ | Editable draft for next week — picker, budget tracker, swap modal, Save Draft button. |
| 4 | `leaderboard` | Leaderboard | Ranking table with rank, name, total pts, MVP per user. |
| 5 | `members` | Members | All members listed with their current XIs side-by-side. |
| 6 | `admin` | Admin ⚙️ | **Admin-only** (Phase 12) — visible only to members with `is_admin=1`. Hosts the Member Passcodes card (§8.6), Match URL editor, and Dev Tools. |

There is **no Points tab** in the rendered tab list. See the Dead Capabilities
section at the bottom of this document.

The Admin tab is gated client-side on `_isAdmin` (set from
`/api/login`/`/api/whoami`) AND server-side on `members.is_admin=1` for every
`/api/admin/*` endpoint — defence-in-depth, neither side trusts the other.

---

## 3. Match Centre 🎯 (default tab)

### 3.1 Browse all matches grouped by week
- Cards show fixture, status, and the **user's points from that match**.
- Cached client-side in `_mcData` ([mc_hub.js:26](../Static/mc_hub.js:26)).
- Cache is invalidated whenever the state polls a change
  (`ipl:state-updated` event), so a fresh scraper run auto-refreshes the hub.

### 3.2 Open a match box score
- Clicking a match card calls `_openMatchModal(match_id)` (lazy fetch from
  `/api/match-details/<id>?user=<name>`).
- Modal shows the **XI that was actually playing in that week**, not the
  latest squad — historical accuracy is preserved.
- Each row shows: role badge (BAT/BOWL/AR/WK colour pill), C×2 / VC×1.5
  annotation when applicable, top scorer highlighted with a 2px gold
  left-border, and a **MATCH TOTAL** footer computed client-side as
  `sum(p.final_pts)` — an independent integrity check against the
  server-reported total.

---

## 4. This Week 🔒 (`team` tab)

### 4.1 See your locked XI
- Renders via `_buildPitchView()`
  ([index.html:445](../templates/index.html:445)) — players grouped by role
  (Bowlers → All-Rounders → Batsmen → Wicket-Keepers).
- Budget remaining bar, role count pills, "✅ Valid" or "⚠ N players" status.
- Captain / Vice-Captain shown with their multipliers (2× / 1.5×).
- **Read-only.** The current week's selection cannot be edited.

### 4.2 Scoring Rules popup (This Week + Next Week)
- "📖 Scoring Rules" ghost button sits in the week-label row on both the
  This Week and Next Week tabs.
- Opens `_showRulesModal()` — a slide-up sheet built in `templates/index.html`
  with the full point breakdown (participation, batting, bowling, fielding,
  captain/VC multipliers).
- The numbers in the modal must stay in sync with `logic/scoring_engine.py`
  (`calc_pts`). There is no API behind the popup — content is static HTML.
- Closes via the × icon, the "Got it" button, or by tapping the dim overlay.

---

## 5. Next Week ✏️ (`next` tab)

### 5.1 Build a draft XI
- Player picker table — sort by Season pts / Price / Name; filter by team and
  role; live search box.
- Click a row to add. If the XI is already full, the **swap modal** opens
  asking who to remove ([index.html:686](../templates/index.html:686)).
- Hard constraints enforced client-side:
  - **Squad size = 11** (`XI_SIZE`)
  - **Budget = 100.0 CR** (`BUDGET_TOTAL`)
  - Over-budget rows are dimmed; over-budget adds are blocked with a notice.

### 5.2 Assign Captain / Vice-Captain
- Each draft row has C / V / × buttons.
- Picking C clears any existing C; same player as both C and VC is prevented.

### 5.3 Save the draft
- "Save Draft" button is disabled until `team.length === 11` and `remaining ≥ 0`.
- Calls `IplApi.saveNextWeek(_username, _nwDraft)` → `POST /api/save-next-week/<n>`.
- On success, fires `ipl:saved` which re-bootstraps the whole app state.

### 5.4 Season-points scouting badges
- Each draft row shows the player's `season_pts` next to the price
  ([index.html:343, 348](../templates/index.html:343)) — sourced from
  `_state.player_pts`, no extra HTTP call.

---

## 6. Leaderboard

- Table: rank, name, **total pts**, **MVP** (top-scoring player for that user).
- Header summary: league average, top score, member count.
- Current user's row is highlighted.
- Built from `/api/leaderboard` (no `?week=` parameter → cumulative).

---

## 7. Members

- Pill list of every registered display name.
- Below: a side-by-side grid of every member's current XI (their
  this-week if set, else next-week draft).
- Clicking another member's pill **logs in as them**
  ([index.html:518, 525](../templates/index.html:518)).
  - Side effect: there is **no login authentication** at all — this is a
    private trust-based league.

---

## 8. Admin ⚙️

### 8.1 Filter the match list
- Four filter buttons: **All**, **🟢 Completed**, **🕑 Upcoming**, **⚠ Missing IDs**.
- "Missing IDs" matches rows whose Cricbuzz URL ends in `/00000` or otherwise
  doesn't have a numeric suffix.

### 8.2 Paste / fix a Cricbuzz scorecard URL
- Each match has an editable URL input, a status pill, "Has ID / No ID" badge,
  and a **Save & Scrape** button.
- Saving calls `POST /api/update-match-url`, which triggers a background
  scrape of that match.
- Display title is normalised to `M{n} · TEAM vs TEAM` client-side
  ([index.html:656](../templates/index.html:656)).

### 8.3 Duplicate-URL detection
- The same Cricbuzz numeric ID appearing on two different `match_id`s is
  flagged in red with "⚠ Duplicate URL — also on M{x}"
  ([index.html:636-666](../templates/index.html:636)).

### 8.4 Click a scorecard link
- When a URL is saved, the row exposes a "View scorecard" link that opens the
  Cricbuzz page in a new tab.

### 8.5 Dev Tools — Simulate Monday rollover
- Card titled "🛠 Dev Tools" appears at the bottom of the Admin tab
  (built by `_buildDevTools()` in `templates/index.html`).
- Big red button: "▶ Simulate Monday 14:00 UTC Rollover".
- Calls `IplApi.rollover(true)` → `POST /api/rollover?force=1`.
- On success, refreshes history and drops the next-week draft.
- Operator escape hatch — use only if the scheduled Monday rollover did not
  fire. Previously sat under §4 (This Week tab) — moved to Admin so it isn't
  exposed to everyday members.

### 8.6 Member Passcodes (Phase 12)
- Card titled "🔐 Member Passcodes" sits at the **top** of the Admin tab.
- Lists every registered member with:
  - **Status pill:** ⚠ **Default (1234)** if `must_change=1`, 🔒 **Custom** otherwise.
  - **ADMIN** badge if `is_admin=1`.
  - **Reset to 1234** button (disabled on the admin's own row — they self-reset
    via the header button in §1.3).
- Clicking Reset → `confirm("Reset passcode for X back to 1234?")` →
  `IplApi.adminResetPasscode(name)` → `POST /api/admin/passcode/reset`. Server
  rewrites the hash to `sha256("X:1234")`, sets `must_change=1`, and **deletes
  every session for that user** so they're forced through §1.3's forced-reset
  flow on their next page load.
- The card auto-refreshes after every reset.

---

## 9. Header Bar (shared on every tab)

Buttons in left-to-right order: **🔑 Reset Passcode** · **⟳ Refresh** · **Switch user**.
Admins also see an inline **ADMIN** chip next to their display name.

### 9.1 Reset Passcode button (Phase 12)
- See §1.3 for the modal flow. Always visible to a logged-in user.

### 9.2 Refresh button
- `_refreshData()` ([index.html:259-303](../templates/index.html:259)) does
  three things in sequence:
  1. `POST /api/sync-now` → triggers a background **discovery + scrape**.
  2. Immediately re-fetches `/api/state`, `/api/leaderboard`, and the user's
     history so the UI shows the latest cached data.
  3. After **75 seconds**, re-fetches state again to pick up any scoring
     that completed during the background scrape.
- Polling is paused for the duration and restarted after.

---

## 10. Behind-the-scenes (no UI element, but user-visible effects)

### 10.1 Background polling every 60 seconds
- `ipl_glue.js` polls `/api/poll` every 60 s with ETag
  ([Static/ipl_glue.js:21, 298](../Static/ipl_glue.js:21)).
- On ETag change, fires `ipl:state-updated`, which refreshes all open tabs
  and invalidates Match Centre cache.

### 10.2 Auto-rollover at Monday 14:00 UTC
- A browser `setTimeout` schedules `_executeRollover()` for the next Monday
  14:00 UTC ([Static/ipl_glue.js:23, 158-179](../Static/ipl_glue.js:23)).
- This means **any client that's open at the moment can trigger** the
  rollover by hitting `/api/rollover`. The server's rollover is idempotent —
  `already_rolled()` prevents a second roll in the same week.

### 10.3 Member-aware auto-fill of next-week draft
- On login, if the user has no next-week draft saved, the picker is
  pre-populated with **their current this-week XI** as a starting point
  ([index.html:114-117](../templates/index.html:114)). Convenient, but means
  the "Save Draft" button is essentially "lock these same 11 again".

---

## 11. Dead Capabilities — present in code, not reachable via UI

These are tracked here so Phase 6 can confidently propose deletion.

### 11.1 Points tab — orphaned
- `_buildPointsTab()` exists at [index.html:569-612](../templates/index.html:569)
  (~44 lines) and again at
  [Static/ipl_glue.js:462](../Static/ipl_glue.js:462).
- Supporting state (`_ptsData`, `_ptsLoading`, `_loadPoints()`,
  `_ptsData=null` resets) exists at multiple sites in
  [index.html:25, 122-133](../templates/index.html:25).
- The render switch ([index.html:756-761](../templates/index.html:756)) has
  no `points` branch. The tab button array
  ([index.html:744-754](../templates/index.html:744)) has no `points` entry.
- `switchTab("points")` is never called from anywhere (`grep` confirms zero
  hits across the project).
- **Conclusion:** users cannot reach this tab. The code is genuinely dead.
- **Downstream consequence:** `/api/player-points/<n>` is still served from
  `routes.py` and the response is still computed by `db_manager.py`. With
  the Points tab dead, that endpoint has no UI consumer. Worth confirming in
  Phase 5 whether any external script depends on it (e.g. `Audit_Scores.ps1`).

### 11.2 History tab — orphaned
- `_buildHistoryTab()` exists at
  [index.html:498-515](../templates/index.html:498) and computes the week
  selector UI from `_historyData`.
- It is **not in the tab list** and **not in the render switch**.
- However, `_loadHistory()` is still called on login
  ([index.html:90](../templates/index.html:90)) and `_historyData` is
  consumed by several other places (`_buildMembersCard`, `_buildNextWeekCard`
  for draft pre-fill) — so the *data* is alive, just the *tab* is dead.
- The relevant `/api/history/<n>` endpoint is therefore still in use, even
  though the dedicated History tab is not.

### 11.3 Root-level `ipl_glue.js`
- The project root contains an **outdated copy** of `ipl_glue.js`
  (v7.7, 875 lines).
- [index.html:822](../templates/index.html:822) loads `/static/ipl_glue.js`
  — i.e. the [Static/ipl_glue.js](../Static/ipl_glue.js) copy (v7.8, 775
  lines). The root copy is **not served**, **not imported**, and **not
  referenced**.
- **Conclusion:** delete-candidate.

### 11.4 `A _ Sticky budget _ table picker.html`
- A draft HTML file in the project root. Not referenced by `index.html`, not
  served by Flask, no Python file imports it.
- Looks like a discarded experiment for the picker UI.
- **Conclusion:** delete-candidate.

---

## 12. Capability → Backend Map (preview for Phases 2-5)

Each backend file's context document should reference the capabilities here
by section number. Indicative wiring:

| Capability | Routes (Phase 5) | DAO (Phase 3) | Logic (Phase 2) | Ingestion (Phase 4) |
|------------|------------------|----------------|------------------|----------------------|
| §1.1 Register | `POST /api/register` | `upsert_member`, `upsert_member_auth`, `create_session` | `hash_passcode`, `new_session_token` (base.py) | — |
| §1.2 Login | `POST /api/login` | `get_member_auth`, `create_session` | `verify_passcode` (base.py) | — |
| §1.3 Reset Passcode | `POST /api/passcode/change` | `set_passcode`, `delete_sessions_for_user`, `create_session` | `hash_passcode`, `_require_token` | — |
| §1.4 Switch user | (frontend-only — clears localStorage) | — | — | — |
| §3 Match Centre | `/api/match-centre`, `/api/match-details/<id>` | `get_match_centre`, `get_match_details` | — | scraper (fills `match_scores`) |
| §4 This Week | `/api/state`, `/api/history/<n>` | `get_state`, `get_history` | `rollover_engine` (active-team pick) | — |
| §4.2 Scoring Rules popup | — (static client-side) | — | mirrors `scoring_engine.calc_pts` | — |
| §8.1 Simulate rollover (Admin) | `/api/rollover` | `roll_week` | `rollover_engine` | — |
| §5 Next Week draft | `/api/save-next-week/<n>`, `/api/players` | `save_next_week_draft`, `get_players` | `fuzzy_match` (auto-correct typed names) | — |
| §6 Leaderboard | `/api/leaderboard` | `get_leaderboard` | — | — |
| §7 Members | `/api/state` | `get_state` | — | — |
| §8 Admin | `/api/matches-status`, `/api/update-match-url` | `get_matches`, `upsert_match` | — | `tasks.start_bg_scrape` |
| §8.6 Member Passcodes (Admin) | `GET /api/admin/members`, `POST /api/admin/passcode/reset` | `list_members_admin_view`, `set_passcode`, `delete_sessions_for_user` | `_require_admin` | — |
| §9.1 Refresh | `/api/sync-now` | — | `cricbuzz_discovery` (find new IDs) | `tasks`, `scraper` |
| §10.1 Polling | `/api/poll` | `get_state_etag` | — | — |
| §10.2 Auto-rollover | `/api/rollover` | `roll_week` | `rollover_engine` | — |
| (bootstrap auto-login) | `GET /api/whoami` | `get_session`, `get_member_auth` | `_require_token` | — |

This table will be expanded and corrected per-file in the subsequent phases.
