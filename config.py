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

from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH  = DATA_DIR / "fantasy.db"

# ── Season ────────────────────────────────────────────────────────────────────
IPL_YEAR = 2026

# ── Deadline — Monday 14:00 UTC (= 16:00 SAST) ───────────────────────────────
# rollover_engine.py compares against datetime.now(timezone.utc).
# ipl_glue.js uses ROLLOVER_HOUR_UTC=14 with setUTCHours().
# Both sides agree: Monday 14:00 UTC.
DEADLINE_HOUR = 14
DEADLINE_MIN  = 0

# ── Versioning ────────────────────────────────────────────────────────────────
APP_VERSION = "2.4.0"

# Per-script version pins
SERVER_VER       = "13.4"   # Phase 11: HOSTED mode (Render/Codespaces); boot-time git pull;
                            #           skip APScheduler + ephemeral wipe + tunnel in cloud
ROUTES_VER       = "1.6.0"  # Phase 11: /api/sync-now branches to git-pull in HOSTED,
                            #           _push_if_hosted wrapper on write endpoints,
                            #           ROLLOVER_TOKEN bearer auth on /api/rollover
DB_VER           = "6.0"    # Phase 10: _upsert_match enriches title with teams
SCRAPER_VER      = "11.0"   # Phase 9: FIX-020/021/022/023 — schedule.json consumer,
                            #          single discovery code path, self-healing _reset_url
INIT_DB_VER      = "1.0.0"  # (unchanged)
TASKS_VER        = "2.0.0"  # Phase 9: APScheduler daily 23:55 IST + run_discovery_and_scrape
SEED_MATCHES_VER = "4.1"    # Phase 10: populate teams_json from title; always refresh title in DB

# logic/ engine versions
SCORING_ENGINE_VER      = "1.1.0"  # Phase 6: debug_calc_pts() added       (unchanged)
ROLLOVER_ENGINE_VER     = "1.0.0"  # (unchanged)
FUZZY_MATCH_VER         = "1.1.0"  # Resilience: _generate_dynamic_player()  (unchanged)
CRICBUZZ_DISCOVERY_VER  = "1.2.0"  # Phase 9: title-keyed merge, multi-URL scrape,
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
}
