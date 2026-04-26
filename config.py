"""
IPL Fantasy 2026 — Global Configuration                     config v1.0.0
===========================================================================
Phase 2 — Authoritative source for all system constants and version strings.
Phase 3 — Added TASKS_VER; bumped SCRAPER_VER to 10.9.
Phase 4 — Bumped DB_VER to 5.8, SCRAPER_VER to 10.10; added engine versions.
Phase 5 — Bumped SERVER_VER to 12.8; DB_VER to 5.9; added /api/version.
Phase 6 — SCORING_ENGINE_VER to 1.1.0; debug_calc_pts; UTC comment fixed.
Phase 7 — routes.py extracted from server.py; APP_VERSION 2.0.0-stable.
Phase 8 — ROUTES_VER 1.1.0: season_pts scouting badges + mobile UX fix.
           APP_VERSION 2.1.0.

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
APP_VERSION = "2.1.0"

# Per-script version pins
SERVER_VER  = "13.0"   # Phase 7: routes extracted to routes.py (Blueprint)
ROUTES_VER  = "1.1.0"  # Phase 8: season_pts in /api/players; mobile UX fix
DB_VER      = "5.9"    # Phase 5: pure DAO
SCRAPER_VER = "10.11"  # Resilience: FIX-015/016/017/018
INIT_DB_VER = "1.0.0"
TASKS_VER   = "1.0.0"

# logic/ engine versions
SCORING_ENGINE_VER  = "1.1.0"  # Phase 6: debug_calc_pts() added
ROLLOVER_ENGINE_VER = "1.0.0"
FUZZY_MATCH_VER     = "1.1.0"  # Resilience: _generate_dynamic_player()

VERSION_MAP = {
    "1.0.0":        "Phase 1 — Relocated _SCHEMA + _auto_seed_* from server.py / db_manager.py",
    "2.0.0":        "Phase 2 — Global Config & Versioning System",
    "3.0.0":        "Phase 3 — tasks.py extracted; scraper.py exports run_full_scrape()",
    "4.0.0":        "Phase 4 — logic/ package: scoring_engine, rollover_engine, fuzzy_match",
    "5.0.0":        "Phase 5 — API Architect: DAO refactor, slim pass-throughs, /api/version",
    "6.0.0":        "Phase 6 — Full-Stack Verified: version handshake, Moe/Sai audit, UTC fix",
    "2.0.0-stable": "Phase 7 — Cleanup: routes.py Blueprint, SKILL.md consolidated",
    "2.1.0":        "Phase 8 — Scouting: season_pts badges in Next Week tab; mobile keyboard fix",
}
