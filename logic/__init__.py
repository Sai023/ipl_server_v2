"""
IPL Fantasy 2026 — logic package
=================================
Pure-function business-logic modules. Submodules in this package import
only from stdlib (plus `requests` for cricbuzz_discovery) — they never
import from db_manager.py, base.py, routes.py, server.py, scraper.py, or
tasks.py, so they remain testable in isolation.

Submodules
----------
scoring_engine     — calc_pts(), debug_calc_pts(), _normalise_overs(),
                     _SEMANTIC_MAP-free; CAP_MULT=2.0, VC_MULT=1.5.
rollover_engine    — last_monday_deadline(), already_rolled(),
                     pick_active_team(). All times in UTC.
fuzzy_match        — _norm(), _build_player_index(), _fuzzy_match(),
                     _fuzzy_fielder(), _generate_dynamic_player()
                     (FIX-015 resilience fallback), plus _SEMANTIC_MAP
                     (single source of player nicknames; also imported
                     by base.py for the UI resolver).
cricbuzz_discovery — run_discovery(), resolve_series_id(),
                     fetch_series_matches(), merge_discoveries(),
                     load_schedule(), save_schedule(). Uses `requests`
                     (third-party) — only logic submodule that does I/O.
"""
