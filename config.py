"""
IPL Fantasy 2026 — Global Configuration                     config v1.0.0
===========================================================================
Phase 2 — Authoritative source for all system constants and version strings.
Phase 3 — Added TASKS_VER; bumped SCRAPER_VER to 10.9.
Phase 4 — Bumped DB_VER to 5.8, SCRAPER_VER to 10.10; added engine versions.

All scripts import from here; no hardcoded paths or versions elsewhere.

Rule: This file must have ZERO imports from other project modules.
      Only stdlib (pathlib) is permitted.
"""

from pathlib import Path

# ── Paths ───────────────────────────────────────────────────────────────────
# All scripts live in the same directory as this file so BASE_DIR is unambiguous.
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH  = DATA_DIR / "fantasy.db"

# ── Season ──────────────────────────────────────────────────────────────────
IPL_YEAR = 2026

# ── Deadline — Monday 14:00 SAST (12:00 UTC) ────────────────────────────────
DEADLINE_HOUR = 14
DEADLINE_MIN  = 0

# ── Versioning ──────────────────────────────────────────────────────────────
APP_VERSION = "4.0.0-rc1"

# Per-script version pins — mirrors the Golden File tag in each module header.
# Update here whenever a module's Golden File version bumps.
SERVER_VER  = "12.7"
DB_VER      = "5.8"    # Phase 4: imports from logic/scoring_engine + logic/rollover_engine
SCRAPER_VER = "10.10"  # Phase 4: imports from logic/fuzzy_match
INIT_DB_VER = "1.0.0"
TASKS_VER   = "1.0.0"

# logic/ engine versions (Phase 4)
SCORING_ENGINE_VER  = "1.0.0"  # logic/scoring_engine.py
ROLLOVER_ENGINE_VER = "1.0.0"  # logic/rollover_engine.py
FUZZY_MATCH_VER     = "1.0.0"  # logic/fuzzy_match.py

VERSION_MAP = {
    "1.0.0":     "Phase 1 — Relocated _SCHEMA + _auto_seed_* from server.py / db_manager.py",
    "2.0.0-rc1": "Phase 2 — Global Config & Versioning System",
    "3.0.0-rc1": "Phase 3 — tasks.py extracted; scraper.py exports run_full_scrape()",
    "4.0.0-rc1": "Phase 4 — logic/ package: scoring_engine, rollover_engine, fuzzy_match",
}
