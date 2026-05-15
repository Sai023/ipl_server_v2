"""
IPL Fantasy 2026 — Scoring Engine                  scoring_engine v1.1.0
===========================================================================
Phase 4 — Extracted verbatim from db_manager.py v5.7.
Phase 6 — debug_calc_pts() added for audit / validation.

Pure functions only — stdlib (math) only, zero project imports.
logic is bit-for-bit identical to db_manager.py v5.7; nothing changed.

Public API
----------
_normalise_overs(raw)
    Convert Cricbuzz decimal overs (e.g. 3.5 = 3 overs 5 balls) to a
    proper fractional value (e.g. 3.8333...).

calc_pts(s)
    Compute base fantasy points from a raw score dict.
    Returns int. The captain/VC multiplier is the caller's responsibility
    (apply via `pts * CAP_MULT` or use debug_calc_pts which does it inline).

debug_calc_pts(s, player_id, cap_id, vc_id)
    Audit utility — same maths as calc_pts() but returns a step-by-step
    breakdown dict (base_pts, multiplier, final_pts, per-component steps)
    for validation and console inspection. Calls calc_pts(s) internally so
    the totals always agree. Used by /api/audit-scores.

CAP_MULT = 2.0
VC_MULT  = 1.5
"""

import math

# ── Captain / VC multipliers ───────────────────────────────────────────────────────────────────
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


# ── Phase 6: Audit / Validation Utility ─────────────────────────────────────────────────────

def debug_calc_pts(
    s:          dict,
    player_id:  str | None = None,
    cap_id:     str | None = None,
    vc_id:      str | None = None,
) -> dict:
    """
    Phase 6 audit utility — step-by-step scoring trace.

    Mirrors the exact computation flow of calc_pts() and calls it
    internally to guarantee the totals always agree.  Returns a
    full breakdown dict suitable for console inspection or unit tests.

    Example (Moe audit — Phil Salt as CAP, KKR vs SRH W1 hypothetical)
    -------------------------------------------------------------------
    >>> from logic.scoring_engine import debug_calc_pts
    >>> score = {
    ...   "played": True, "runs": 72, "balls": 48,
    ...   "fours": 8, "sixes": 3, "got_out": True,
    ...   "overs": 0, "wickets": 0, "runs_conceded": 0,
    ... }
    >>> t = debug_calc_pts(score, player_id="r03", cap_id="r03", vc_id="s04")
    >>> t["base_pts"]   # 104 (4 + 72 + 8 + 6 + 8 + 6)
    104
    >>> t["multiplier"] # 2.0 (Captain)
    2.0
    >>> t["final_pts"]  # 208
    208

    Example (Sai audit — Varun Chakaravarthy as CAP)
    -------------------------------------------------
    >>> score = {
    ...   "played": True, "overs": 4.0, "runs_conceded": 24,
    ...   "wickets": 3, "lbw_bowled": 1, "maidens": 1,
    ...   "runs": 0, "balls": 0,
    ... }
    >>> t = debug_calc_pts(score, player_id="k04", cap_id="k04", vc_id="s05")
    >>> t["base_pts"]   # 109 (4 + 75 + 8 + 8 + 12 + 2)
    109
    >>> t["multiplier"] # 2.0
    2.0
    >>> t["final_pts"]  # 218
    218

    Parameters
    ----------
    s         : raw score dict (same format as calc_pts)
    player_id : the player whose score is being traced
    cap_id    : captain ID (2x multiplier if matches player_id)
    vc_id     : vice-captain ID (1.5x if matches player_id)

    Returns
    -------
    dict
        player_id, cap_id, vc_id  — echo of selector inputs
        inputs                    — normalised stat values fed to scorer
        steps                     — per-component point contributions
        base_pts                  — authoritative total from calc_pts(s)
        multiplier                — 1.0 / 1.5 / 2.0
        final_pts                 — round(base_pts * multiplier)
    """
    if not s or not s.get("played"):
        return {
            "player_id": player_id, "cap_id": cap_id, "vc_id": vc_id,
            "inputs": {}, "steps": {"played": False},
            "base_pts": 0, "multiplier": 1.0, "final_pts": 0,
        }

    # ── Normalise inputs (identical clamps to calc_pts) ───────────────────
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

    sr  = (runs / balls) * 100 if balls >= 10 else None
    eco = rc / overs           if overs >= 2  else None

    # ── Trace each component ─────────────────────────────────────────────────────────
    steps: dict = {}

    # Participation
    steps["participation"] = 4

    # Batting: runs + boundaries
    steps["runs"]        = runs
    steps["fours"]       = fours
    steps["sixes_bonus"] = sixes * 2

    bat_milestone = 0
    if   runs >= 100: bat_milestone = 16
    elif runs >= 50:  bat_milestone = 8
    elif runs >= 30:  bat_milestone = 4
    steps["bat_milestone"] = bat_milestone

    duck_pen = -2 if (duck and got_out and balls >= 1) else 0
    steps["duck_penalty"] = duck_pen

    sr_bonus = 0
    if sr is not None:
        if   sr >  125: sr_bonus =  6
        elif sr >= 110: sr_bonus =  4
        elif sr >= 100: sr_bonus =  2
        elif sr <   60: sr_bonus = -4
        elif sr <   70: sr_bonus = -2
    steps["strike_rate_bonus"] = sr_bonus
    steps["strike_rate"]       = round(sr, 2) if sr is not None else None

    # Bowling: wickets + bowling extras
    wkt_bonus = 0
    if wickets >= 2: wkt_bonus += 4
    if wickets >= 3: wkt_bonus += 4
    if wickets >= 4: wkt_bonus += 8
    if wickets >= 5: wkt_bonus += 8
    steps["wickets_base"] = wickets * 25
    steps["wicket_bonus"] = wkt_bonus
    steps["lbw_bowled"]   = lbwb * 8
    steps["maidens"]      = maidens * 12

    eco_bonus = 0
    if eco is not None:
        if   eco >  12: eco_bonus = -6
        elif eco >= 11: eco_bonus = -4
        elif eco >= 10: eco_bonus = -2
        elif eco <   5: eco_bonus =  6
        elif eco <   6: eco_bonus =  4
        elif eco <   7: eco_bonus =  2
    steps["economy_bonus"] = eco_bonus
    steps["economy"]       = round(eco, 3) if eco is not None else None

    # Fielding
    steps["catches"]        = catches * 8
    steps["catch_bonus"]    = 4 if catches >= 3 else 0
    steps["stumpings"]      = stump * 12
    steps["run_out_direct"] = rod * 12
    steps["run_out_assist"] = roa * 6

    # Authoritative total from calc_pts (guarantees parity)
    base_pts = calc_pts(s)
    steps["TOTAL_BASE"] = base_pts

    # Multiplier
    if   player_id and player_id == cap_id: mult = CAP_MULT
    elif player_id and player_id == vc_id:  mult = VC_MULT
    else:                                   mult = 1.0
    steps["multiplier_label"] = (
        f"CAP×2  ({player_id}={cap_id})" if mult == CAP_MULT
        else f"VC×1.5 ({player_id}={vc_id})" if mult == VC_MULT
        else "none×1"
    )

    final_pts = round(base_pts * mult)

    return {
        "player_id": player_id,
        "cap_id":    cap_id,
        "vc_id":     vc_id,
        "inputs": {
            "runs": runs, "balls": balls, "fours": fours, "sixes": sixes,
            "wickets": wickets, "overs": round(overs, 4), "runs_conceded": rc,
            "maidens": maidens, "lbw_bowled": lbwb, "catches": catches,
            "stumpings": stump, "run_out_direct": rod, "run_out_assist": roa,
            "duck": duck, "got_out": got_out,
        },
        "steps":      steps,
        "base_pts":   base_pts,
        "multiplier": mult,
        "final_pts":  final_pts,
    }
