"""
IPL Fantasy 2026 — Global Configuration                     config v1.1.0
===========================================================================
Phase 2 — Authoritative source for all system constants and version strings.
Phase 3 — Added TASKS_VER; bumped SCRAPER_VER to 10.9.
Phase 4 — Bumped DB_VER to 5.8, SCRAPER_VER to 10.10; added engine versions.
Phase 5 — Bumped SERVER_VER to 12.8; DB_VER to 5.9; added /api/version.
Phase 6 — SCORING_ENGINE_VER to 1.1.0; debug_calc_pts; UTC comment fixed.
Phase 7 — routes.py extracted from server.py; APP_VERSION 2.0.0-stable.
Phase 8 — ROUTES_VER 1.1.0: season_pts scouting badges + mobile UX fix.
           APP_VERSION 2.1.0.
Phase 9 — Daily auto-sync architecture (JSON-schedule refactor):
           • SERVER_VER  → 13.3   (start_daily_discovery_scheduler hook)
           • ROUTES_VER  → 1.4.0  (Match Centre v1.3.0 + /api/sync-now v1.4.0)
           • SCRAPER_VER → 11.0   (FIX-020/021/022/023, schedule.json consumer)
           • TASKS_VER   → 2.0.0  (APScheduler daily 23:55 IST job, sync pipeline)
           • New pins: SEED_MATCHES_VER, CRICBUZZ_DISCOVERY_VER
           • APP_VERSION 2.2.0.  See ROADMAP in VERSION_MAP below.

All scripts import from here; no hardcoded paths or versions elsewhere.

Rule: This file must have ZERO imports from other project modules.
      Only stdlib (pathlib) is permitted.
"""

import os
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH  = DATA_DIR / "fantasy.db"   # legacy (pre-INF-1); no longer the data source

# ── Database (INF-1): managed Postgres via DATABASE_URL ───────────────────────
# Set in the environment — Render env var in prod, exported locally for dev.
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ── Season ────────────────────────────────────────────────────────────────────
IPL_YEAR = 2026

# ── Deadline — Monday 14:00 UTC (= 16:00 SAST) ───────────────────────────────
# rollover_engine.py compares against datetime.now(timezone.utc).
# ipl_glue.js uses ROLLOVER_HOUR_UTC=14 with setUTCHours().
# Both sides agree: Monday 14:00 UTC.
DEADLINE_HOUR = 14
DEADLINE_MIN  = 0

# ── Versioning ────────────────────────────────────────────────────────────────
APP_VERSION = "2.21.0"   # 2.21.0 (2026-11-21): hardening release (HTTP errors answer as what they are, unexpected failures carry a request id and a fixed sentence, /api/health, the state tag is read before the data, a read-only one-competition player resolver, five unused diagnostic routes removed, HSTS); assets paper10.   2.20.0 (2026-11-21): one server-trouble banner, front-end only (Ui.connection; the blocking overlay and the red banner are gone); assets paper9

# Per-script version pins
SERVER_VER       = "13.5"   # INF-2: boot-time PAT-health check (cloud_sync.check_token).
                            # Phase 11: HOSTED mode (Render/Codespaces); boot-time git pull;
                            #           skip APScheduler + ephemeral wipe + tunnel in cloud
ROUTES_VER       = "1.14.0" # Hardening (2026-11-21): routes no longer catch Exception (base.py answers every unexpected failure with a request id and a fixed sentence, every HTTP error as JSON on /api/); GET /api/health; /api/state reads its tag first and answers 304 before any heavy read; the player resolver is read-only and scoped to one competition; removed /api/audit-scores, /api/clean-scores, /api/audit-player-ids, /api/audit-blobs, /api/snapshot. A save states the week its draft is FOR and answers 409 week_changed when a handover moved that on; POST /api/keep-xi/<n> ("Keep this XI": confirm the draft, nothing else changes); /api/history rows carry next_week_touched_at; both routes share one picks-lock rule (2026-11-20). POST /api/rollover applies every handover that is due from the stored lock schedule (one all-or-nothing transaction per week, recorded in the ledger; dry_run and forced+confirm modes; a competition with no schedule is never rolled); POST /api/admin/competition action "schedule" stores the schedule (dry-run by default); the save lock and /api/state "deadline" follow the schedule (managed, awaiting, season_over); /api/version lists rollover_plan (2026-11-20). Errors carry a machine `reason` (auth_required, session_ended, admin_only, not_your_team, name_taken, bad_credentials, locked, invalid_team, rate_limited, server_error ...); /api/poll and /api/state carry server_recv_utc + server_now_utc, the server's clock (2026-11-20). /api/version lists the session_policy module (2026-11-19). /api/rollover refuses a competition that is not active / not started and a forced rollover without an admin + confirmation (2026-11-19). UI redesign: /api/state carries "competition"; paper-themed /manifest.json + /offline. Admin import: POST /api/admin/competition action "import"
                            #           (roster + fixtures upload; dry-run by default).
                            # INF-2: GET /api/admin/health (PAT + freshness).
                            # BIZ-2: server-side deadline lock (selections_locked).
                            # SEC-1: token-gate all writes + admin-gate destructive ops;
                            #        BIZ-1: full server-side team validation (validate_selection).
                            # Phase 12 (Passcodes): /api/register, /api/login, /api/whoami,
                            #           /api/passcode/change, /api/admin/passcode/reset,
                            #           /api/admin/members. Bearer-token auth for the new
                            #           passcode + admin surface only; rest of API unchanged.
DB_VER           = "6.8"    # user_selections.nw_touched_at (the one additive ALTER at boot); save_next_week states and checks the draft week under a shared lock on the competition row; keep_next_week; get_history carries the touched time; the week-1 start resets it and undo restores it (2026-11-20). Lock schedule + weekly handovers: tables competition_weeks, rollover_log, ops_heartbeat (created at boot, additive) and reconcile_competition / set_schedule / undo_transition / get_lock_state / get_rollover_log / heartbeats (2026-11-20). Sessions last 60 days and are renewed while used (create_session default 60; get_session renews when under 30 days remain), 2026-11-19. get_meta_comp: the legacy un-suffixed meta keys belong to ipl_2026 only (2026-11-19). BIZ-1: validate_selection requires a captain AND a vice-captain on a full XI. get_state lists every registered member in every competition. Admin import: import_competition_data() (roster + fixtures upsert).
                            # Phase 12: members + sessions tables (passcode hash + bearer tokens)
SCRAPER_VER      = "11.1"   # SA20 scoring: persist_player hook so score_competition.py can add players in Postgres (legacy SQLite path unchanged). Phase 9: FIX-020/021/022/023 — schedule.json consumer,
                            #          single discovery code path, self-healing _reset_url
INIT_DB_VER      = "1.1.0"  # Phase 12: _auto_seed_members_if_needed backfill (1234 + must_change=1)
TASKS_VER        = "2.0.0"  # Phase 9: APScheduler daily 23:55 IST + run_discovery_and_scrape
SEED_MATCHES_VER = "4.1"    # Phase 10: populate teams_json from title; always refresh title in DB

# logic/ engine versions
SCORING_ENGINE_VER      = "1.1.0"  # Phase 6: debug_calc_pts() added       (unchanged)
ROLLOVER_ENGINE_VER     = "1.2.0"  # rollover_refusal() removed: the stored lock schedule (rollover_plan) now decides what may roll; pick_active_team and the old Monday rules stay (2026-11-20)
ROLLOVER_PLAN_VER       = "1.0.0"  # NEW: the lock schedule, what is due, and whether picking is locked (pure) (2026-11-20)
SESSION_POLICY_VER      = "1.0.0"  # 60-day sessions, renewed on use when under 30 days remain (2026-11-19)
FUZZY_MATCH_VER         = "1.2.0"  # SA20 scoring: _index_players() shared with the Postgres runner (behaviour unchanged). Resilience: _generate_dynamic_player()
CRICBUZZ_DISCOVERY_VER  = "1.3.0"  # SA20 scoring: resolve_series_id(slug=) for other competitions. Phase 9: title-keyed merge, multi-URL scrape,
                                   #          IPL 2026 series ID corrected to 9241

VERSION_MAP = {
    "1.0.0":        "Phase 1 — Relocated _SCHEMA + _auto_seed_* from server.py / db_manager.py",
    "2.0.0":        "Phase 2 — Global Config & Versioning System",
    "3.0.0":        "Phase 3 — tasks.py extracted; scraper.py exports run_full_scrape()",
    "4.0.0":        "Phase 4 — logic/ package: scoring_engine, rollover_engine, fuzzy_match",
    "5.0.0":        "Phase 5 — API Architect: DAO refactor, slim pass-throughs, /api/version",
    "6.0.0":        "Phase 6 — Full-Stack Verified: version handshake, Moe/Sai audit, UTC fix",
    "2.0.0-stable": "Phase 7 — Cleanup: routes.py Blueprint, SKILL.md consolidated",
    "2.1.0":        "Phase 8 — Scouting: season_pts badges in Next Week tab; mobile keyboard fix",
    "2.2.0":        "Phase 9 — Daily auto-sync: data/schedule.json source-of-truth, "
                    "logic/cricbuzz_discovery, APScheduler 23:55 IST in-server cron, "
                    "/api/sync-now, GH Actions workflow simplified to cloud safety-net. "
                    "Root-cause fix: IPL 2026 series_id 9237 → 9241.",
    "2.3.0":        "Phase 10 — Admin Tab overhaul: consistent 'M{n} · TEAM vs TEAM' titles, "
                    "teams_json populated by seed_to_db + _upsert_match, "
                    "duplicate Cricbuzz ID detection with red highlight, "
                    "clickable scorecard link in Admin Tab.",
    "2.4.0":        "Phase 11 — HOSTED mode: server runs on Render (or Codespaces) with "
                    "HOSTED=true env. Boot-time + on-demand git pull replaces Cricbuzz "
                    "scrape. _push_if_hosted wraps write endpoints (save-next-week, "
                    "member, rollover, recalc, update-match-url) so user actions persist "
                    "back to git. New monday_rollover.yml workflow cron-triggers rollover "
                    "at Mon 14:00 UTC. daily_sync.yml gains pull-rebase + retry to "
                    "coexist with host writes. render.yaml blueprint provided. "
                    "Local mode (HOSTED unset) unchanged.",
    "2.5.0":        "Phase 12 — Passcodes & Admin: 4-digit passcode gates login and "
                    "register. New `members` table (passcode_hash, must_change, is_admin) "
                    "and `sessions` table (bearer token, 30-day TTL). Existing users "
                    "backfilled with passcode '1234' + must_change=1 at first boot. "
                    "Sai seeded as the sole admin. Six new endpoints: /api/register, "
                    "/api/login, /api/whoami, /api/passcode/change, "
                    "/api/admin/passcode/reset, /api/admin/members. Admin tab and "
                    "Member Passcodes card visible only to admins. Reset Passcode "
                    "header button (no current-passcode verification by design — see "
                    "routes.py). All writes pass through _push_if_hosted so the new "
                    "tables are committed to git in HOSTED mode like every other write.",
}
