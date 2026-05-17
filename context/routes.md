# routes.py — The League's HTTP API Surface

> **Post-cleanup note (2026-05-15).** This document is the snapshot that
> drove the cleanup. The Dead Code Audit / Open Questions below describe
> what was **found**. The items listed there have been actioned across
> Waves A–E — see [dead_code_register.md](dead_code_register.md) for the
> current `fixed` / `kept` / `guarded` status of each.


## What it does (business view)

`routes.py` is the **whole HTTP API** of the league — every URL the
browser, the cloud cron, or an operator-side script can hit.
It's a Flask Blueprint registered onto the app at startup, with **35
route handlers** organised into 10 named groups (Phase 12 added the
six AUTH endpoints — see §1.5 below).

Every handler follows the same shape:
1. Authenticate (read `?user=` or `<n>` from the URL — there is no
   real authentication, names are trusted).
2. Rate-limit if it's a write endpoint (`_check_rate(_write_limiter)`).
3. Validate the input.
4. Delegate to `db.*` (Phase 3) or `logic.*` (Phase 2) for the
   actual work.
5. Wrap the result in `jsonify()` with `ok: True/False` and a
   sensible HTTP status code.

The handlers themselves contain almost no business logic. The two
exceptions are the **rollover handler** (which orchestrates the
`logic.rollover_engine` + `db_manager` rollover DAO into a single
transaction) and the **audit-scores handler** (which re-computes
points from raw stats using `logic.scoring_engine.calc_pts` and
compares against the stored totals).

## Where it sits in the flow

```
   browser / cron / curl
            │
            ▼
   base.app  (Flask)
            │
   bp = Blueprint("api", …)   ← defined here
            │
   @bp.route("/api/…")        ← 29 handlers, 9 groups
            │
            ▼
   db.*   (Phase 3 DAO)        logic.*  (Phase 2 engines)
            │
            ▼
       SQLite + side effects (background scrapes, schedule writes)
```

`routes.py` imports **everything from `base.py`** and from
**`config.py`** (versions, paths). It **never** imports from
`server.py` — that's the rule that broke the circular import in
Phase 7.

## Inputs / Outputs

- **Inputs:**
  - HTTP requests on `/api/*` and the static routes (`/`,
    `/static/<filename>`, `/manifest.json`, `/offline`).
  - JSON request bodies and query strings.
  - State read from `db` (the singleton from `base.py`).
- **Outputs:**
  - JSON responses (`jsonify(...)`).
  - Side effects via `db.*` (writes), `tasks.start_bg_*` (background
    threads), `init_db._auto_seed_history_if_needed` (admin re-seed).

## The 29 routes — by group

| # | Group | Route | Method | Purpose |
|---|-------|-------|--------|---------|
| 1 | System | `/api/version` | GET | Returns `APP_VERSION` + every module's version pin + `VERSION_MAP` changelog. |
| 1 | System | `/api/ping` | GET | Liveness check + `public_url` + budget constants. |
| 1 | System | `/api/poll` | GET | Returns just the state ETag — the polling endpoint. |
| 1 | System | `/api/current-week` | GET | Just the integer week number. |
| 1.5 | Auth | `/api/register` | POST | Phase 12 — create a new member with display name + 4-digit passcode. Returns `{ok, name, token, must_change:false, is_admin:false}`. |
| 1.5 | Auth | `/api/login` | POST | Phase 12 — verify passcode, issue 30-day bearer token. Returns `{ok, name, token, must_change, is_admin}`. Generic 401 hides unknown-user vs wrong-passcode. |
| 1.5 | Auth | `/api/whoami` | GET | Phase 12 — validate the bearer token. Used by the bootstrap to decide whether to auto-login or drop to the login card. |
| 1.5 | Auth | `/api/passcode/change` | POST | Phase 12 — set a new passcode (no current verification, see rule #8). Rotates every session for this user. |
| 1.5 | Auth | `/api/admin/passcode/reset` | POST | Phase 12 — admin-only. Resets target user's passcode to `1234` and `must_change=1`, deletes their sessions. |
| 1.5 | Auth | `/api/admin/members` | GET | Phase 12 — admin-only. Lists every member with `must_change` + `is_admin` flags for the Member Passcodes card. |
| 2 | State | `/api/state` | GET | The biggest read — members, matches, scores, `player_pts`. ETag-aware (304 on match). |
| 2 | State | `/api/state` | POST | **DEAD** — see Dead Code. Was a generic "save anything" endpoint. |
| 3 | Players | `/api/players` | GET | All players sorted by `season_pts DESC`. |
| 3 | Players | `/api/resolve-player` | POST | Fuzzy-resolve a single name into a player ID. |
| 3 | Players | `/api/leaderboard` | GET | The 73-line CTE from [db_manager.py:178](../db_manager.py:178). Supports `?week=N`. |
| 4 | History | `/api/history/<n>` | GET | Week-by-week XI history for one user. |
| 4 | History | `/api/player-points/<n>` | GET | **DEAD** (E1) — consumer was the dead Points tab. |
| 4 | History | `/api/user-match-points/<n>` | GET | **DEAD** (E2 below) — consumer was the dead Matches tab. |
| 4 | History | `/api/debug-points/<n>` | GET | **DEAD** (E3 below) — no consumer anywhere. |
| 5 | Save | `/api/save-next-week/<n>` | POST | Save a user's next-week draft. Auto-resolves typed names → IDs. Enforces budget = 100 CR and XI = 11. |
| 5 | Save | `/api/member/<n>` | PUT | Create or update a member's `this_week` / `next_week` selections. |
| 5 | Save | `/api/match` | POST | **DEAD** (E4 below) — generic match upsert; no UI calls it. |
| 6 | Scoring | `/api/recalculate-points` | POST | Force-rebuild every points table. Wired in IplApi but admin-only manual use. |
| 6 | Scoring | `/api/audit-scores/<n>` | GET | Re-derives every week's pts from `match_scores` + `calc_pts` and compares to stored. Consumed by `Audit_Scores.ps1`. |
| 6 | Scoring | `/api/clean-scores` | POST | Wipe scoring tables. `?delete_json=1` also deletes `data/matches/*.json`. Used by `Audit_Scores.ps1` reset flow. |
| 6b | Audit | `/api/audit-player-ids` | GET | Sweep `user_selections` for player IDs not in `players` table. Returns ghosts with prefix-based suggestions. |
| 6b | Audit | `/api/audit-blobs` | GET | Verify `sum(points_per_match.values()) == week_pts` for every row. |
| 6b | Audit | `/api/snapshot` | POST | Run both audits + leaderboard, write to `data/snapshot_<ts>.json`. Receipt for change reviews. |
| 6c | Match Centre | `/api/match-centre` | GET | Hub endpoint — all matches grouped by week + per-match user pts + season summary. (§3.1) |
| 6c | Match Centre | `/api/match-details/<id>` | GET | Box Score — historical XI for one match. Reads `tw_team_json` from the week the match belongs to. (§3.2) |
| 7 | Admin | `/api/rollover` | POST | The weekly promotion. Three callers: browser cron, "Simulate" button, manual curl. (§4.2, §10.2) |
| 7 | Admin | `/api/seed-history` | POST | **DEAD** (E5 below) — wired in IplApi but never called. Runs the W1-W4 re-seed. |
| 7 | Admin | `/api/matches-status` | GET | All match rows + a `duplicate_url` flag computed server-side. Drives the Admin tab. (§8.1) |
| 7 | Admin | `/api/update-match-url` | POST | Save a Cricbuzz URL + fire a background scrape. (§8.2) |
| 7 | Admin | `/api/sync-now` | POST | Manual trigger for the daily discovery+scrape pipeline. (§9.1) |
| 8 | Static | `/` | GET | Renders `index.html`. |
| 8 | Static | `/static/<path>` | GET | Serves files from `STATIC_DIR`. |
| 8 | Static | `/manifest.json` | GET | PWA manifest with inline SVG icon. |
| 8 | Static | `/offline` | GET | Hard-coded offline page. |

## Key business rules it enforces

### 1. Display names are the identity
- Capped at **30 characters** everywhere (`if not n or len(n)>30: return 400`).
- **Phase 12 update:** login + passcode endpoints now require the 4-digit
  passcode and issue a bearer token. The token gates ONLY `/api/passcode/*`
  and `/api/admin/*`. Every other endpoint (`/api/save-next-week`,
  `/api/member`, `/api/rollover`, `/api/update-match-url`, etc.) still trusts
  `?user=<n>` exactly as before — see Open Question 1.

### 2. Writes are rate-limited
Every `POST` / `PUT` handler starts with `_check_rate(_write_limiter)`
(30 calls / 60 s / IP from [base.py:209](../base.py:209)). Reads are
unlimited.

### 3. Budget and squad-size are enforced server-side too
`/api/save-next-week` re-runs the same checks the UI does
([routes.py:322-329](../routes.py:322)): `len(team) == XI_SIZE` (=11)
and `db.validate_budget(team, BUDGET_TOTAL=100)`. The UI's checks
catch most cases; the server is the final gate.

### 4. Player name → ID auto-correction on save
If the saved team contains *names* instead of *IDs* (an old client,
a manual JSON post), `resolve_id_list` rewrites them in place and
returns a `resolution_log` showing what changed. Same for `cap`/`vc`.

### 5. Rollover idempotency
`/api/rollover` always:
1. Computes `lmd = last_monday_deadline(now, 14, 0)`.
2. If `force=False` and `already_rolled(_last_rollover, lmd)` → no-op.
3. If current_week ≥ MAX_WEEKS=8 → season complete, no-op.
4. Otherwise: per-user, pick the active team (`pick_active_team`),
   resolve IDs, insert a new week row, then update points.
5. Set `_last_rollover` (unless `force=True`).

The `force=1` query parameter skips the idempotency check but still
updates state — used by the Dev Tools "Simulate" button.

### 6. Wrong-scorecard recovery in Admin
`/api/update-match-url` only validates that the URL contains a 5+
digit Cricbuzz ID. The scraper does the deeper validation
(team-pair match, IPL-team check) and resets the URL if needed via
`_reset_url` ([scraper.py:448](../scraper.py:448)).

### 7. Snapshots are receipts, not backups
`/api/snapshot` writes a JSON file with the current leaderboard and
both audit results to `data/snapshot_<ts>.json`. The intended use:
take a snapshot **before** a risky change, then take another after,
and diff them. Not for full DB backup — see Open Questions.

### 8. Passcode change requires no current passcode (Phase 12)
Both flows that mutate a member's passcode — the header **Reset Passcode**
button (self-service) and the auto-opened **Forced Reset** modal (after an
admin reset) — call `/api/passcode/change` with only `{new}` in the body
plus the bearer token in the header. The endpoint never asks the user to
type their old passcode.

This is **intentional**, not an oversight:
- Bearer token already proves the user is authenticated.
- Forced-reset users *cannot* type their current passcode because the admin
  just rewrote the hash without telling them.
- Friction without security gain hurts adoption in a casual league.

The trade-off: someone with physical access to a logged-in browser can
change the passcode and lock the user out. Documented in
[user_capabilities.md](user_capabilities.md) §1.5.

### 9. Admin gate is DB-driven, not hardcoded (Phase 12)
`_require_admin()` reads `members.is_admin` for the token's user. `Sai` is
seeded as the sole admin on first boot ([init_db.md](init_db.md)
§_auto_seed_members_if_needed), but adding a second admin later is a
single `UPDATE members SET is_admin=1 WHERE username=...` — no code
change, no redeploy.

### 10. Sessions are opportunistically cleaned (Phase 12)
Every call to `db.get_session(token)` runs
`DELETE FROM sessions WHERE expires_at < now` first, then SELECTs the
token. No cron job needed; the table self-prunes on read.

## HOSTED mode (Phase 11) — behaviour deltas

`_IS_HOSTED` is read once at module scope (line ~47) from the
`HOSTED` env var. When true, five write paths and one read path
behave differently. **No URL changes**; only the body of the handler
forks. Local mode (`HOSTED` unset) keeps every behaviour from above.

### A. `_push_if_hosted(reason)` wraps every write

After a successful DB write, the handler calls
`_push_if_hosted("<event>:<args>")` which delegates to
`cloud_sync.commit_and_push(paths=["data/fantasy.db"], message=...)`.
This is **synchronous** — the user's `POST` doesn't return until the
push finishes (or fails). Trade-off: ~2–5s extra latency per write,
in exchange for durability against Render container restarts (which
wipe the filesystem).

Wrapped endpoints (in route order):

| Route | reason string |
|---|---|
| `POST /api/save-next-week/<n>` | `save-next-week:<user>:w<N>` |
| `PUT /api/member/<n>` | `member:<user>` |
| `POST /api/recalculate-points` | `recalc:rows=<n>` |
| `POST /api/rollover` | `rollover:w<N>` |
| `POST /api/update-match-url` | `admin-url:<match_id>=<cb_id>` |
| `POST /api/register` | `register:<user>` (Phase 12) |
| `POST /api/passcode/change` | `passcode-change:<user>` (Phase 12) |
| `POST /api/admin/passcode/reset` | `admin-passcode-reset:<target>` (Phase 12) |

Failures are logged at WARN level and the response still returns 200
— the local DB has the change and the next successful write catches
it up.

### B. `/api/sync-now` forks completely

Local mode keeps the existing behaviour: spawns
`tasks.start_bg_sync()` which discovers + scrapes Cricbuzz.

HOSTED mode replaces that with two cloud operations:

1. `cloud_sync.pull_latest()` → `git pull --ff-only`. On any new
   commits, `db.reload_from_disk()` invalidates every thread-local
   SQLite handle so the next read sees the fresh `fantasy.db`.
2. `cloud_sync.dispatch_workflow("daily_sync.yml", ref="main",
   inputs={"force_full_rescrape": "true"})` — asks GitHub Actions to
   run the scrape on our behalf (Render can't reach Cricbuzz). The
   `force_full_rescrape` input wipes the match-JSON cache so the
   scraper's per-match short-circuit at
   [scraper.py:615](../scraper.py:615) doesn't make the workflow a
   no-op.

Response includes `mode`, `pulled`, `pull_msg`, `dispatched`,
`dispatch_msg`, plus a user-facing `message`. The Refresh button
returns immediately; the user re-Refreshes ~60–90s later once the
workflow finishes and pushes new data.

### C. `/api/rollover` accepts an optional bearer token

If `ROLLOVER_TOKEN` is set in the host env, requests carrying an
`Authorization: Bearer <token>` header MUST match it. The
`monday_rollover.yml` GitHub Actions workflow sends that header so
it can trigger rollover from the cloud.

Requests **without** any Authorization header still pass — that's
the in-browser auto-rollover (`setTimeout` in
[Static/ipl_glue.js](../Static/ipl_glue.js)) and the Dev Tools
"Simulate Monday 2:00 PM Rollover" button. Tightening would break
both. Wrong tokens are rejected hard (401).

### D. `/api/update-match-url` adds a per-match cache wipe

Before the `_push_if_hosted` call, the handler deletes
`data/matches/match_NN.json` for the affected match. This is the
*targeted* counterpart of `/api/sync-now`'s global `force_full_rescrape`:
the workflow checks out a tree where only that one match's cached
JSON is missing, so when scraper.py runs, only that one match
re-scrapes from Cricbuzz. Avoids the ~90s cost of a full rescrape
when the operator only changed one match's URL.

Also: in HOSTED mode the handler does NOT call
`tasks.start_bg_scrape` (no Cricbuzz egress). The work goes to the
workflow via `cloud_sync.dispatch_workflow("daily_sync.yml")`. The
response message says so.

### E. Read endpoints are unchanged

`/api/state`, `/api/leaderboard`, `/api/match-centre`,
`/api/match-details`, etc. are read-only — they hit the local
`fantasy.db` and return data. They never push back to git, and they
benefit transparently from any `git pull` that happened on the most
recent `/api/sync-now`.

### Auth / config surface for HOSTED mode

Two env vars must be set on the host (Render dashboard):

| Var | Purpose | Used by |
|---|---|---|
| `GITHUB_TOKEN` | Fine-grained PAT, `contents:write` + `actions:write` on this one repo | `cloud_sync.commit_and_push` (push), `cloud_sync.dispatch_workflow` (REST API call to `/repos/:owner/:repo/actions/workflows/:file/dispatches`) |
| `ROLLOVER_TOKEN` | Random string; **same value also added as a GitHub Actions repo secret** so `monday_rollover.yml` can present it on the bearer header | `api_rollover` auth check |

Without `GITHUB_TOKEN`, writes return 200 but silently fail to push
(local DB has the change; remote doesn't). Without `ROLLOVER_TOKEN`,
the rollover handler is wide-open — fine for local, not for cloud.

## Called by / Calls into

- **Called by:**
  - The browser (via `ipl_glue.js` / `mc_hub.js` / `index.html`).
  - `Audit_Scores.ps1` (operator-side, hits `/api/audit-scores` and
    `/api/clean-scores`).
  - Manual `curl` from the README's troubleshooting examples.
- **Calls into:**
  - `base.*` (`db`, `_db_con`, `_log`, `_write_limiter`,
    `_check_rate`, `resolve_player_id`, `resolve_id_list`, …).
  - `logic.rollover_engine.*` (3 functions).
  - `logic.scoring_engine.calc_pts`, `CAP_MULT`, `VC_MULT`.
  - `init_db._auto_seed_history_if_needed` (dead — see E5).
  - `tasks.start_bg_scrape`, `tasks.start_bg_sync`.
  - Flask: `Blueprint`, `request`, `jsonify`, `render_template`,
    `send_from_directory`.

## Supports which user capabilities

Direct mapping from [user_capabilities.md](user_capabilities.md):

| Capability | Endpoint(s) |
|------------|-------------|
| §1.1 Register (Phase 12) | `POST /api/register` |
| §1.2 Login (Phase 12) | `POST /api/login`, `GET /api/whoami` (bootstrap auto-login) |
| §1.3 Reset Passcode (Phase 12) | `POST /api/passcode/change` |
| §1.4 Switch user | (frontend-only — clears `localStorage`) |
| §8.6 Member Passcodes (Admin, Phase 12) | `GET /api/admin/members`, `POST /api/admin/passcode/reset` |
| §3.1 Match Centre hub | `/api/match-centre` |
| §3.2 Box Score | `/api/match-details/<id>` |
| §4.1 This Week locked XI | `/api/state`, `/api/history/<n>` |
| §4.2 Simulate rollover | `/api/rollover?force=1` |
| §5.1-5.3 Build / save next week | `/api/players`, `/api/save-next-week/<n>` |
| §6 Leaderboard | `/api/leaderboard` |
| §7 Members | `/api/state` |
| §8.1-8.3 Admin tab | `/api/matches-status`, `/api/update-match-url` |
| §9.1 Refresh | `/api/sync-now`, `/api/state`, `/api/leaderboard`, `/api/history/<n>` |
| §10.1 Polling | `/api/poll` |
| §10.2 Auto-rollover | `/api/rollover` |
| (no UI) | `/api/recalculate-points`, `/api/audit-scores/<n>`, `/api/clean-scores`, `/api/audit-player-ids`, `/api/audit-blobs`, `/api/snapshot` — all reachable only via `Audit_Scores.ps1` or `curl` |

## Dead Code Audit

| ID | Item | Lines | Verdict |
|----|------|-------|---------|
| **E1** | `GET /api/player-points/<n>` + the `api_player_points` handler | 190–251 (~62 lines) | **DEAD.** Confirmed in Phase 4: only consumed by the dead Points tab (D3/D4) and the dead root `ipl_glue.js` (O1). `Audit_Scores.ps1` does **not** consume it. **Safe to delete with D3/D4.** |
| **E2** | `GET /api/user-match-points/<n>` + the `api_user_match_points` handler | 253–261 | **DEAD.** Only consumer is `_loadUserMatchPoints()` in `Static/ipl_glue.js:571`, which is only called by `_buildMatchesTab()` — and `_buildMatchesTab` is **a third dead tab** (no entry in the tab list, no entry in the render switch in `index.html`). Added to register as **D21**. |
| **E3** | `GET /api/debug-points/<n>` + the `api_debug_points` handler | 263–294 (~32 lines) | **DEAD.** Zero consumers anywhere — no UI client method, no `Audit_Scores.ps1` reference, no `curl` examples in README or SKILL.md. Pure orphan. Added as **D20**. |
| **E4** | `POST /api/match` + the `api_match` handler | 351–361 | **DEAD.** Exposed in `IplApi.saveMatch` but **never called** anywhere in the frontend. Generic "upsert one match" — superseded by `/api/update-match-url`. Added as **D18**. |
| **E5** | `POST /api/seed-history` + the `api_seed_history` handler | 996–1001 | **DEAD.** Exposed in `IplApi.seedHistory` but never called. `_auto_seed_history_if_needed` runs at startup; nothing needs to re-trigger it via HTTP. Added as **D19**. |
| **E6** | `POST /api/state` + the `api_save_state` handler | 127–136 | **DEAD.** Exposed in `IplApi.saveState` but never called. The actual save paths are `/api/save-next-week/<n>` and `PUT /api/member/<n>`. The handler delegates to `db.save_state`, which would also become a delete-candidate. Added as **D17**. |
| `_match_ordinal`, `_fmt_date_label`, `_IST` | 624–643 | **Live** (internal helpers for Match Centre routes). |
| Imports `TASKS_VER, INIT_DB_VER, SEED_MATCHES_VER, CRICBUZZ_DISCOVERY_VER` | 49–56 | **Live.** All four show up in the `/api/version` response. |

**Total dead code in `routes.py`:** ~140 lines of endpoint handlers
(E1–E6). Five separate "exposed-in-IplApi-but-never-called" patterns
suggest the API client was speculatively over-built.

## Open Questions

1. **Partial authentication after Phase 12.** Login and passcode endpoints
   now require a bearer token, but every other write endpoint
   (`/api/save-next-week`, `PUT /api/member`, `/api/rollover`,
   `/api/update-match-url`, `/api/recalculate-points`, `/api/clean-scores`)
   still trusts `?user=<n>`. A user who knows another member's name can
   still act as them on those endpoints. To close the gap, extend
   `_require_token()` to every `_check_rate(_write_limiter)` site and check
   `sess["username"] == n`. The 4-digit passcode would then be doing actual
   authentication, not just login-screen friction.
2. **The IplApi client is over-broad.** Five endpoints (E2–E6) are
   defined on the client *and* the server but called nowhere. Worth
   either trimming the client or restoring a use for the endpoints.
3. **`/api/clean-scores` is destructive and rate-limited but
   unauthenticated.** Anyone who knows the URL can wipe `match_scores`,
   `player_match_points`, `user_match_points`, `season_pts`, and
   optionally every `*.json` cache file. The rate limiter doesn't help
   when one request is enough to do damage. Consider an explicit
   `confirm=YES_REALLY` body field.
4. **`/api/snapshot` writes to a permission-loose path.** Saves to
   `data/snapshot_<ts>.json`. Filename uses `now_iso.replace(":", "-")`
   which is fine; but `data/` is the same directory `fantasy.db` lives
   in. Worth a `data/snapshots/` subdirectory.
5. **`/api/version` includes the full `VERSION_MAP` dict.** ~12 entries
   of free text. Useful for the front-end version-handshake check, but
   nothing in the project actually parses past `app_version`. Could be
   trimmed to save bytes on every poll.
6. **The Match Centre handler reads `points_per_match` and falls back
   to `user_match_points` if missing** — the comment at
   [line 701](../routes.py:701) notes this handles mixed state where
   some weeks are scraped and others are history-seeded. Worth
   documenting in [seed_matches.md](seed_matches.md) that the seed-only
   path produces `points_per_match` blob entries with no matching
   `user_match_points` rows, and the API handles both.
7. **`audit-scores` reimplements the trace** rather than calling
   `logic.scoring_engine.debug_calc_pts`. Tracked as D2 in the
   register. If the cleanup pass restores `debug_calc_pts`, this is
   the single caller to refactor.
