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
                     selections_locked() (the old Monday rules, only for a
                     competition with no stored lock schedule) and
                     pick_active_team() (draft -> XI). All times in UTC.
rollover_plan      — build_schedule(), validate_schedule(), next_due(),
                     lock_state(): the stored lock schedule (week 1 = opener
                     - 60 min, later weeks Monday 14:00 UTC), which weekly
                     handover is due, and whether picking is locked now
                     (see context/rollover_plan.md).
fuzzy_match        — _norm(), _build_player_index(), _fuzzy_match(),
                     _fuzzy_fielder(), _generate_dynamic_player()
                     (FIX-015 resilience fallback), plus _SEMANTIC_MAP
                     (single source of player nicknames; also imported
                     by base.py for the UI resolver).
cricbuzz_discovery — run_discovery(), resolve_series_id(),
                     fetch_series_matches(), merge_discoveries(),
                     load_schedule(), save_schedule(). Uses `requests`
                     (third-party) — only logic submodule that does I/O.
competition_import — parse_players(), parse_schedule(), cross_check(),
                     parse_anchor(), week_no_for_match(). Validates an
                     uploaded roster / fixture list and builds the rows the
                     admin import writes (see context/competition_import.md).
session_policy     — new_expiry(), renewed_expiry(); SESSION_TTL_DAYS = 60,
                     SESSION_RENEW_BELOW_DAYS = 30. How long a sign-in lasts
                     and when using the app renews it (see
                     context/session_policy.md).
"""
