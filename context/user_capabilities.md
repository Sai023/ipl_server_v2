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

### 1.1 Pick a profile to play as
- **What:** "Who are you?" card on first load. The user clicks one of the
  existing member chips, or types a new display name and clicks **Join League**.
- **How it's remembered:** name is stored in `localStorage` under key
  `ipl_username` ([index.html:53-56](../templates/index.html:53)). On reload,
  the saved name is automatically logged back in *if* it still exists in the
  member list returned by `/api/state`.
- **What backend it touches:**
  - Existing member → no write. Just sets `_username`.
  - New member → `IplApi.saveMember(name, …)` → `PUT /api/member/<n>`
    ([index.html:478](../templates/index.html:478)).

### 1.2 Switch user / log out
- **What:** "Switch user" button in the header bar
  ([index.html:743](../templates/index.html:743)).
- **Effect:** clears `_username`, drops the cached `localStorage` entry, and
  re-renders the login screen.

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
| 6 | `admin` | Admin ⚙️ | Per-match Cricbuzz scorecard URL editor with duplicate-URL detection. |

There is **no Points tab** in the rendered tab list. See the Dead Capabilities
section at the bottom of this document.

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

### 4.2 Dev Tools — Simulate Monday rollover
- Big red button: "▶ Simulate Monday 2:00 PM Rollover"
  ([index.html:561-566](../templates/index.html:561)).
- Calls `IplApi.rollover(true)` → `POST /api/rollover?force=1`.
- On success, refreshes history and drops the next-week draft.

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

---

## 9. Header Bar (shared on every tab)

### 9.1 Refresh button
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
| §1.1 Pick / Register | `/api/state`, `PUT /api/member/<n>` | `upsert_member`, `get_state` | — | `init_db` (auto-seed members) |
| §3 Match Centre | `/api/match-centre`, `/api/match-details/<id>` | `get_match_centre`, `get_match_details` | — | scraper (fills `match_scores`) |
| §4 This Week | `/api/state`, `/api/history/<n>` | `get_state`, `get_history` | `rollover_engine` (active-team pick) | — |
| §4.2 Simulate rollover | `/api/rollover` | `roll_week` | `rollover_engine` | — |
| §5 Next Week draft | `/api/save-next-week/<n>`, `/api/players` | `save_next_week_draft`, `get_players` | `fuzzy_match` (auto-correct typed names) | — |
| §6 Leaderboard | `/api/leaderboard` | `get_leaderboard` | — | — |
| §7 Members | `/api/state` | `get_state` | — | — |
| §8 Admin | `/api/matches-status`, `/api/update-match-url` | `get_matches`, `upsert_match` | — | `tasks.start_bg_scrape` |
| §9.1 Refresh | `/api/sync-now` | — | `cricbuzz_discovery` (find new IDs) | `tasks`, `scraper` |
| §10.1 Polling | `/api/poll` | `get_state_etag` | — | — |
| §10.2 Auto-rollover | `/api/rollover` | `roll_week` | `rollover_engine` | — |

This table will be expanded and corrected per-file in the subsequent phases.
