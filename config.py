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

# ── Joining ───────────────────────────────────────────────────────────────────
# A new member joins with a Google account (Google sign-in step 3, 2026-11-21). The league stops taking new members at this many (an admin can also close joining at any
# time: meta key joining_open). A safety net, not a rule of the game: three friends today.
MAX_MEMBERS = 30

# ── Versioning ────────────────────────────────────────────────────────────────
APP_VERSION = "2.28.0"   # 2.28.0 (2026-11-22): OPS CORE (Phase 2 of the SA20 readiness plan; no new screen, no schema or data change): POST /api/ops/tick (a clock calls it with a token, or an admin does) applies whatever weekly handover is due for any competition, leaves the clock's heartbeat, judges how things look and says how soon to call again (every minute within 48 hours of a lock, every 30 minutes otherwise); GET /api/ops/status gives the Ops card its data (health, the clocks and their ages, every competition's state and last handover); the server runs its own clock (a thread: a tick soon after boot, so a visit that wakes a sleeping service catches a missed handover up at once, then at the rhythm); admin alerts go to Telegram when a bot token is set (a late handover, a competition that is locked but cannot start, a clock that went quiet, a week that went live; one message per incident, a reminder every half hour, a message when it clears) and POST /api/ops/test-alert sends a test. Everything is dormant until its secret is set; nothing changes for members; assets paper15 (unchanged).   2.27.0 (2026-11-22): ORIGIN DISCIPLINE (plan item 1.10; no new screen, no schema or data change): the app's own address is ONE setting (PUBLIC_BASE_URL; else the address Render gave the service; else the one the request came to) read by one pure module, logic/origin.py, and a test fails if any source file names the live address; GET /api/version says what the server believes its address is; and an OLD address, redeployed with MOVED_TO_URL set, shows every link a "We've moved" page and answers every other API call with 410 (reason moved) while /api/ping, /api/health and /api/version keep working. Nothing changes while neither setting is set (the normal case); assets paper15 (unchanged).   2.26.0 (2026-11-22): SIGN-IN UX PASS (a walk through the new-member journey and the existing-member sign-in): the Match Centre no longer spins for ever after the app comes back from the browser (the page redrew first and the Match Centre then threw away its own request; it now asks again at once, and says so, with Try again, if it fails or is slow); the sign-in card offers a passcode chip only to members who still have a passcode (GET /api/auth/config lists their names), the new-member hint sits right under the Google button, and a name with no passcode opens Google instead of a passcode prompt that can never work; a wrong passcode says so in plain words and no connection / too many tries / a failing server say what they are instead of "wrong passcode"; the claim sheet cannot be dismissed while it works, starts on a neutral Continue and warns that a chosen name cannot be changed; My team and Pick say so, with Try again, when the saved XI did not load (it was a spinner, or an empty editor that looked like a lost team); a finished competition points to the next one; a member who joined after the lock is told so; the menu item that signs out says Sign out; assets paper15.   2.25.0 (2026-11-21): SIGN IN FROM THE APP (the Android app cannot sign in with Google itself, Google refuses a WebView: the app opens the phone's browser for the Google step, the browser hands the member back through a link and a one-time code, and the app proves it started the sign-in with a secret that never left it; needs APK 1.5; the old app keeps showing the open-in-Chrome notice); assets paper14.   2.24.0 (2026-11-21): Google sign-in, step 3: JOIN WITH GOOGLE (a new member taps Continue with Google, picks the Gmail, chooses "I'm new here" and types a display name: no passcode, no PIN to choose; the PIN registration form is gone while Google is on and the server refuses it); a member cap and an admin switch that closes joining; assets paper13.   2.23.0 (2026-11-21): Google sign-in, step 2: a Google account that belongs to nobody yet is claimed in ONE visit (Continue with Google, pick your name from the members who are still free, enter your passcode once: no second Google tap); a member who already has a Google account can never be picked, and the server refuses it too; an admin who unlinks their own account stays signed in; assets paper12.   2.22.0 (2026-11-21): Google sign-in, step 1 (an identities table; the server checks the ID token Google hands the page; a member switches from the passcode to Google and the passcode is retired; the admin can unlink; dormant until GOOGLE_CLIENT_ID is set; the Referrer-Policy becomes strict-origin-when-cross-origin, the value Google documents for a page that loads its sign-in library); assets paper11.   2.21.0 (2026-11-21): hardening release (HTTP errors answer as what they are, unexpected failures carry a request id and a fixed sentence, /api/health, the state tag is read before the data, a read-only one-competition player resolver, five unused diagnostic routes removed, HSTS); assets paper10.   2.20.0 (2026-11-21): one server-trouble banner, front-end only (Ui.connection; the blocking overlay and the red banner are gone); assets paper9

# Per-script version pins
SERVER_VER       = "13.7"   # 13.7: the server's own ops clock thread (a tick soon after boot, then at the rhythm; on by default on a hosted service, OPS_APP_CLOCK=1/0 overrides) (Phase 2).   13.6: the boot log says which public address the server believes it has (and where that came from), whether the moved page is on, and any unusable address setting (plan item 1.10).   13.5 / INF-2: boot-time PAT-health check (cloud_sync.check_token).
                            # Phase 11: HOSTED mode (Render/Codespaces); boot-time git pull;
                            #           skip APScheduler + ephemeral wipe + tunnel in cloud
ROUTES_VER       = "1.21.0" # Ops core (2026-11-22): NEW POST /api/ops/tick (the shared clock token OPS_TOKEN or ROLLOVER_TOKEN, or an admin; ?source= names the clock), GET /api/ops/status, POST /api/ops/test-alert (admin); /api/version lists ops_plan and ops_notify; ops_clock_loop is the server's own clock.   1.20.0: Origin discipline (2026-11-22): GET /api/version also carries public_base_url, public_base_source, moved_to and origin_problems and lists the origin module; an old address switched to the moved page (MOVED_TO_URL) answers every link with a "We've moved" page and every other API call except /api/ping, /api/health and /api/version with 410 (reason moved), decided in base.py before the routes run.   1.19.0: Sign-in UX pass (2026-11-22): GET /api/auth/config also carries pin_members, the names of the members who still have a passcode, so the sign-in card offers a chip only to them (names only, nothing else).   1.18.0: Sign in from the app (2026-11-21): NEW POST /api/auth/handoff {challenge} (a signed-in browser asks for a single-use two-minute code for the app's challenge) and POST /api/auth/handoff/redeem {code, verifier} (the app proves it holds the verifier and gets its OWN new session); new reason handoff_expired; /api/version lists app_handoff. Join with Google (2026-11-21): NEW POST /api/auth/join {ticket, name} creates a member with a Google account and no passcode (409 name_taken, 403 joining_closed, 409 members_full, 400 name_invalid); the claim payload carries join:{allowed, why: ""|closed|full}; POST /api/register answers 403 register_closed while Google sign-in is on; NEW POST /api/admin/joining {open} (admin only) and GET /api/admin/members carries joining_open, member_count, member_cap. Google claim (2026-11-21): POST /api/auth/google answers a good but unlinked Google account with a claim {ticket, members:[{name, status free|linked|temp}]} instead of a dead end; NEW POST /api/auth/claim {ticket, name, passcode} links that account to that member (and retires their passcode) and signs them in; wrong passcodes are counted per Google account (reason claim_locked after five); reasons claim_expired, claim_locked, member_taken, temp_passcode; POST /api/admin/auth/unlink keeps the caller's own session when an admin unlinks themselves. Google sign-in (2026-11-21): GET /api/auth/config, GET /api/auth/nonce, POST /api/auth/google, POST /api/auth/link, POST /api/auth/logout, POST /api/admin/auth/unlink; /api/login answers a switched member with reason pin_retired; /api/whoami and /api/admin/members carry linked + a masked email; the admin passcode reset refuses a member who signs in with Google. Hardening (2026-11-21): routes no longer catch Exception (base.py answers every unexpected failure with a request id and a fixed sentence, every HTTP error as JSON on /api/); GET /api/health; /api/state reads its tag first and answers 304 before any heavy read; the player resolver is read-only and scoped to one competition; removed /api/audit-scores, /api/clean-scores, /api/audit-player-ids, /api/audit-blobs, /api/snapshot. A save states the week its draft is FOR and answers 409 week_changed when a handover moved that on; POST /api/keep-xi/<n> ("Keep this XI": confirm the draft, nothing else changes); /api/history rows carry next_week_touched_at; both routes share one picks-lock rule (2026-11-20). POST /api/rollover applies every handover that is due from the stored lock schedule (one all-or-nothing transaction per week, recorded in the ledger; dry_run and forced+confirm modes; a competition with no schedule is never rolled); POST /api/admin/competition action "schedule" stores the schedule (dry-run by default); the save lock and /api/state "deadline" follow the schedule (managed, awaiting, season_over); /api/version lists rollover_plan (2026-11-20). Errors carry a machine `reason` (auth_required, session_ended, admin_only, not_your_team, name_taken, bad_credentials, locked, invalid_team, rate_limited, server_error ...); /api/poll and /api/state carry server_recv_utc + server_now_utc, the server's clock (2026-11-20). /api/version lists the session_policy module (2026-11-19). /api/rollover refuses a competition that is not active / not started and a forced rollover without an admin + confirmation (2026-11-19). UI redesign: /api/state carries "competition"; paper-themed /manifest.json + /offline. Admin import: POST /api/admin/competition action "import"
                            #           (roster + fixtures upload; dry-run by default).
                            # INF-2: GET /api/admin/health (PAT + freshness).
                            # BIZ-2: server-side deadline lock (selections_locked).
                            # SEC-1: token-gate all writes + admin-gate destructive ops;
                            #        BIZ-1: full server-side team validation (validate_selection).
                            # Phase 12 (Passcodes): /api/register, /api/login, /api/whoami,
                            #           /api/passcode/change, /api/admin/passcode/reset,
                            #           /api/admin/members. Bearer-token auth for the new
                            #           passcode + admin surface only; rest of API unchanged.
DB_VER           = "6.13"   # ops_competitions, recent_applied, ops_alert_state and ops_alert_step (Phase 2: what the clocks, the Ops card and the alerts read; the alert state is one atomic row under a lock); touch_heartbeat takes `now`.   6.12: list_pin_members (the names that still have a passcode, for the sign-in card).   6.11: count_members; create_member_with_identity (a NEW member and their Google identity in ONE transaction, serialised by an advisory lock: name unique case-insensitively, one Google account one member, a member cap) (2026-11-21). list_members_for_claim (names + whether each is linked / has a passcode / has a temporary one: no hash, no address); unlink_identity can keep one session (an admin unlinking themselves stays signed in) (2026-11-21). identities (provider, subject = Google's sub, member, email, timestamps; additive, created at boot) with get_identity / touch_identity / link_identity (links AND retires the passcode in one transaction) / unlink_identity / count_pin_members / delete_session; get_member_auth and list_members_admin_view also return the linked Google email; NO_PIN marks a member without a passcode (2026-11-21). user_selections.nw_touched_at (the one additive ALTER at boot); save_next_week states and checks the draft week under a shared lock on the competition row; keep_next_week; get_history carries the touched time; the week-1 start resets it and undo restores it (2026-11-20). Lock schedule + weekly handovers: tables competition_weeks, rollover_log, ops_heartbeat (created at boot, additive) and reconcile_competition / set_schedule / undo_transition / get_lock_state / get_rollover_log / heartbeats (2026-11-20). Sessions last 60 days and are renewed while used (create_session default 60; get_session renews when under 30 days remain), 2026-11-19. get_meta_comp: the legacy un-suffixed meta keys belong to ipl_2026 only (2026-11-19). BIZ-1: validate_selection requires a captain AND a vice-captain on a full XI. get_state lists every registered member in every competition. Admin import: import_competition_data() (roster + fixtures upsert).
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
GOOGLE_IDENTITY_VER     = "1.1.0"  # 1.1.0: ClaimStore (a verified Google account that belongs to nobody yet is remembered under a single-use ten-minute ticket; wrong passcodes counted per Google account). 1.0.0: checks the signed ID token Google hands the page (RS256 pinned, audience, issuer, expiry, verified email, nonce); single-use nonces (2026-11-21)
HANDOFF_VER             = "1.0.0"  # NEW: one-time codes that carry a signed-in member from the phone's browser into the installed app, with a PKCE-style proof (2026-11-21)
ORIGIN_VER              = "1.0.0"  # NEW: the app's public address (PUBLIC_BASE_URL) and the "We've moved" switch for an old address (pure) (2026-11-22)
OPS_PLAN_VER            = "1.0.0"  # NEW: what the clocks, the Ops card and the admin alerts need to know: state, rhythm, health, incidents (pure) (2026-11-22)
OPS_NOTIFY_VER          = "1.0.0"  # NEW: admin alerts to Telegram; dormant until TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set (2026-11-22)
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
