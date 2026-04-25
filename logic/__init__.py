"""
IPL Fantasy 2026 — logic package
=================================
Phase 4 — Pure-function business-logic modules extracted from db_manager.py
and scraper.py.  No module in this package may import from other project
modules (only stdlib is permitted), keeping them fully testable in isolation.

Submodules
----------
scoring_engine   — calc_pts(), _normalise_overs(), apply_multiplier()
rollover_engine  — last_monday_deadline(), already_rolled(), pick_active_team()
fuzzy_match      — _norm(), _build_player_index(), _fuzzy_match(), _fuzzy_fielder()
"""
