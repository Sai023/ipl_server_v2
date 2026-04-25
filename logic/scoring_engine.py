"""
IPL Fantasy 2026 — Scoring Engine                  scoring_engine v1.0.0
===========================================================================
Phase 4 — Extracted verbatim from db_manager.py v5.7.

Pure functions only — stdlib (math) only, zero project imports.
Logic is bit-for-bit identical to db_manager.py v5.7; nothing has been
optimised or changed.

Public API
----------
_normalise_overs(raw)
    Convert Cricbuzz decimal overs (e.g. 3.5 = 3 overs 5 balls) to a
    proper fractional value (e.g. 3.8333...).

calc_pts(s)
    Compute base fantasy points from a raw score dict.
    Returns int — multiplier NOT applied (see apply_multiplier).

apply_multiplier(base_pts, player_id, cap_id, vc_id)
    Apply the captain (2x) or vice-captain (1.5x) multiplier.
    Extracted from inline expressions in db_manager.py / server.py.

CAP_MULT = 2.0
VC_MULT  = 1.5
"""

import math

# ── Captain / VC multipliers ─────────────────────────────────────────────────
CAP_MULT = 2.0
VC_MULT  = 1.5


def _normalise_overs(raw: float) -> float:
    """
    Convert Cricbuzz decimal overs to a proper fraction of an over.
    Identical to db_manager.py v5.7 _normalise_overs().

    Examples
    --------
    3.5  → 3 + 5/6 ≈ 3.833  (3 overs, 5 balls)
    4.0  → 4.0               (4 complete overs)
    0.0  → 0.0
    """
    if raw <= 0: return 0.0
    full_overs = math.floor(raw)
    ball_digit = min(5, max(0, round((raw - full_overs) * 10)))
    return full_overs + ball_digit / 6


def calc_pts(s: dict) -> int:
    """
    Compute base fantasy points for one player's match scorecard entry.

    Identical to db_manager.py v5.7 calc_pts() — no changes whatsoever.
    The cap/vc multiplier is NOT applied here; use apply_multiplier() for that.

    Parameters
    ----------
    s : dict
        Raw score dict.  Recognised keys (camelCase OR snake_case accepted):
          played, runs, balls, fours, sixes, wickets, overs,
          runsConceded / runs_conceded, maidens,
          catches, stumpings,
          runOutDirect / run_out_direct,
          runOutAssist  / run_out_assist,
          lbwBowled     / lbw_bowled,
          duck, gotOut  / got_out.

    Returns
    -------
    int — base fantasy points (before captain/VC multiplier).
    """
    if not s or not s.get("played"): return 0
    runs    = max(0, int(s.get("runs",  0)))
    balls   = max(0, int(s.get("balls", 0)))
    fours   = max(0, min(runs, int(s.get("fours",  0))))
    sixes   = max(0, int(s.get("sixes",   0)))
    wickets = max(0, min(10,  int(s.get("wickets", 0))))
    overs   = _normalise_overs(max(0.0, float(s.get("overs", 0))))
    rc      = max(0, int(s.get("runsConceded",  s.get("runs_conceded",  0))))
    maidens = max(0, int(s.get("maidens", 0)))
    catches = max(0, min(10,  int(s.get("catches",  0))))
    stump   = max(0, int(s.get("stumpings",     0)))
    rod     = max(0, int(s.get("runOutDirect",  s.get("run_out_direct", 0))))
    roa     = max(0, int(s.get("runOutAssist",  s.get("run_out_assist", 0))))
    lbwb    = max(0, min(wickets, int(s.get("lbwBowled", s.get("lbw_bowled", 0)))))
    duck    = bool(s.get("duck", False))
    got_out = bool(s.get("gotOut", s.get("got_out", False)))
    pts = 4
    pts += runs + fours + sixes * 2
    if   runs >= 100: pts += 16
    elif runs >= 50:  pts += 8
    elif runs >= 30:  pts += 4
    if duck and got_out and balls >= 1: pts -= 2
    if balls >= 10:
        sr = (runs / balls) * 100
        if   sr >  125: pts += 6
        elif sr >= 110: pts += 4
        elif sr >= 100: pts += 2
        elif sr <   60: pts -= 4
        elif sr <   70: pts -= 2
    pts += wickets * 25 + lbwb * 8 + maidens * 12
    if wickets >= 2: pts += 4
    if wickets >= 3: pts += 4
    if wickets >= 4: pts += 8
    if wickets >= 5: pts += 8
    if overs >= 2:
        eco = rc / overs
        if   eco >  12: pts -= 6
        elif eco >= 11: pts -= 4
        elif eco >= 10: pts -= 2
        elif eco <   5: pts += 6
        elif eco <   6: pts += 4
        elif eco <   7: pts += 2
    pts += catches * 8
    if catches >= 3: pts += 4
    pts += stump * 12 + rod * 12 + roa * 6
    return round(pts)


def apply_multiplier(
    base_pts: int,
    player_id: str,
    cap_id: str | None,
    vc_id:  str | None,
) -> float:
    """
    Apply captain (2x) or vice-captain (1.5x) multiplier to base points.

    Extracted from the inline expressions that appeared in multiple places
    in db_manager.py, server.py, and scraper.py:
        mult = 2.0 if pid == cap else (1.5 if pid == vc else 1.0)

    Returns a float; callers should round() as appropriate.
    """
    if player_id == cap_id:
        return base_pts * CAP_MULT
    if player_id == vc_id:
        return base_pts * VC_MULT
    return float(base_pts)
