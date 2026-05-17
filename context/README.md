# Context Files — IPL Fantasy 2026

These documents describe each script in **business language**, not in code.
Read these to understand *what the system does for the league* without having
to read Python first.

## ⚡ Cleanup completed 2026-05-15

These per-file context docs are **the analysis snapshot that drove the
cleanup**. They list every dead symbol, drift, and open question as
**found at the time of writing** — many of those items have since been
fixed across Waves A, B, C, D, and E.

**For the current state**, read **[dead_code_register.md](dead_code_register.md)** —
that's the live workbook with every item's status (`fixed` / `kept` /
`guarded` / `open`).

### Cleanup outcome

| Wave | Net Δ | What |
|------|-------|------|
| Pre-A | −438 lines | Deleted stale root `SKILL.md`; replaced `.claude/skills/...` with thin pointer. |
| A | −80 lines | Fixed user-visible IST/UTC label bug, added latent-bug guard for week-number drift, unified nickname map and `_normalise_overs`. |
| C | −250 lines | Deleted duplicate `_SCHEMA` in `init_db.py`, dead imports, `GoldenDB` alias, `reset()`, inert MutationObserver; deleted 2 orphan files. |
| B | −462 lines | Removed three dead UI tabs (Points / History / Matches), six dead HTTP endpoints, and their DAO/IplApi cascades. |
| Hotfix | +feature | M51/M52/M53 schedule drift fixed; M53 wrong CB ID cleared; Preview-state auto-recovery added to scraper; UI display number regex fixed. |
| D | −70 lines | `apply_multiplier` deleted; `api_audit_scores` rewritten to use `debug_calc_pts`; path constants unified; UTF-8 stdout at boot. |
| E | doc-only | Docstring drift in `logic/__init__.py`, `rollover_engine.py`, `mc_hub.js`; `Seed_Matches.py --force` flag removed; README "Daily Sync" section + troubleshooting refresh. |
| **Cleanup total** | **~−1,300 lines** | No behavioural regressions; live API surface preserved; **`Sai W1-W2` audit total computed via refactored path = stored value, bit-identical**. |
| **Phase 11** | **+~500 lines, new infra** | HOSTED mode for Render/Codespaces deploy: env-aware boot, `cloud_sync.py` (git pull / push / workflow_dispatch), per-write `_push_if_hosted`, `monday_rollover.yml` cron, `daily_sync.yml` `force_full_rescrape` + pull-rebase retry, `render.yaml` blueprint. Local mode unchanged. |
| **Phase 11.1 (post-launch hardening)** | **+~180 lines, 6 fixes** | Three iterations to find an auth approach that survives Render's container quirks — final: `_authed_url()` embeds the token directly in the URL passed to `git fetch`/`push`, bypassing every credential helper (same as `actions/checkout`). Plus: `GIT_TERMINAL_PROMPT=0`, `db.checkpoint()` (WAL flush before `git add` — fixed the silent passcode-loss bug), `/api/_test-push` diagnostic endpoint, workflow `re-apply scrape on binary conflict` (preserves both host writes and scrape data when they race), and `git add -Af` + `_push_if_hosted(extra_paths=...)` so per-match Save & Scrape actually stages the cached-JSON deletion. After this wave: Refresh button, Save Draft, header Reset Passcode, Admin Reset to 1234, per-match Save & Scrape, twice-daily scrape commits, and Monday rollover all land reliably as `ui:*` / `data:*` commits on `origin/main`. End-to-end verified 2026-05-17. |
| **Phase 12** | **+~650 lines** | Passcode login + admin role. New `members` + `sessions` tables, six AUTH endpoints (`/api/register`, `/api/login`, `/api/whoami`, `/api/passcode/change`, `/api/admin/passcode/reset`, `/api/admin/members`), idempotent backfill seeds `1234`+`must_change=1` for existing users and Sai as sole admin. Bearer-token auth gates only the new passcode + admin surface — rest of API stays trust-based per project's existing stance. Admin tab + Member Passcodes card visible only to admins; "Reset Passcode" button in every user's header. All writes pass through `_push_if_hosted` so new tables travel with the HOSTED git pipeline (no `render.yaml` change). |

### What's still open

Only **S9 full UTC migration** remains — a multi-file change to align
`Seed_Matches.SEASON_WEEK1_END` with the rollover engine's UTC anchor.
A startup audit (`_audit_monday_match_schedule` in `server.py`) is
already in place that fires a clear warning if a future schedule update
ever lands a Monday match in the affected window. **Latent, not active.**

## How to use

- One `.md` per source file, named after the file (e.g. `config.md` documents `config.py`).
- Each file follows the same template:
  1. **What it does (business view)** — plain English, no jargon.
  2. **Where it sits in the flow** — a sentence locating it in the data pipeline.
  3. **Inputs / Outputs** — what comes in, what goes out, in league terms.
  4. **Key business rules it enforces** — the decisions baked into the file.
  5. **Called by / Calls into** — neighbours in the dependency graph.
  6. **Dead Code Audit** — symbols / imports / blocks that no one uses.
  7. **Open Questions** — things that look wrong, stale, or worth a follow-up.

## Phases

Documents are produced from the **user-facing capabilities inward**, then
along the dependency chain:

| Phase | Theme | Files |
|-------|-------|-------|
| 0 | User capabilities & docs audit | `user_capabilities.md`, `docs_audit.md` |
| 1 | Foundation | `config.md`, `base.md`, `logic_package.md`, **`cloud_sync.md`** (Phase 11) |
| 2 | Logic Engines | `scoring_engine.md`, `rollover_engine.md`, `fuzzy_match.md`, `cricbuzz_discovery.md` |
| 3 | Data Layer | `db_manager.md`, `init_db.md` |
| 4 | Ingestion | `scraper.md`, `tasks.md`, `seed_players.md`, `seed_matches.md` |
| 5 | API & Server | `routes.md`, `server.md` |
| 6 | Frontend | `index_html.md`, `ipl_glue.md`, `mc_hub.md` |
| 7 | Operations | `daily_sync_workflow.md`, **`monday_rollover_workflow.md`** (Phase 11), `audit_scores_ps1.md`, `setup_cloudflare_ps1.md`, `cloudflared_exe.md`, **`render_yaml.md`** (Phase 11) |

**Read these first:**

1. [user_capabilities.md](user_capabilities.md) — every feature the user can
   actually reach, with line-anchored evidence. The single source of truth
   for "what the system does".
2. [docs_audit.md](docs_audit.md) — every drift between SKILL.md / README
   and reality. Reference this when reading the in-repo docs.
3. [dead_code_register.md](dead_code_register.md) — the running catalog
   of dead symbols, duplicate logic, and orphan files. Nothing is deleted
   until the final cleanup phase, which works exclusively from this register.

Every backend context file in Phases 2-5 cites a capability from
`user_capabilities.md` so a reader can trace any feature →
routes → DAO → logic → ingestion.

## Glossary (used throughout)

- **Week** — one of up to 10 fantasy weeks in the season. A user has one XI per week.
- **Rollover** — the Monday 14:00 UTC event when "next week's draft" becomes "this week's locked team".
- **XI** — a user's 11 players selected for a given week, including a Captain and Vice-Captain.
- **Captain / Vice-Captain** — multipliers applied to a player's points (Captain ×2.0, VC ×1.5).
- **`season_pts`** — a player's raw fantasy points across the whole season, no multipliers.
- **`points`** — a player's points with Captain/VC weighting baked in.
- **`week_pts`** — a user's total score for one week. **This is the leaderboard.**
- **Ephemeral tables** — wiped at every server restart, rebuilt by the scraper.
- **Persistent tables** — survive restarts; never wiped.
- **Scorecard** — the per-match dataset pulled from Cricbuzz.
