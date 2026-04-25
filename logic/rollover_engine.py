"""
IPL Fantasy 2026 — Rollover Engine                rollover_engine v1.0.0
===========================================================================
Phase 4 — Extracted verbatim from db_manager.py v5.7.

Pure functions only — stdlib (datetime, json) only, zero project imports.
Logic is identical to the inline code in db_manager.py v5.7
rollover_season() and do_rollover(); nothing has been changed.

Public API
----------
last_monday_deadline(now, deadline_hour, deadline_min) -> datetime
    Return the most recent Monday at deadline_hour:deadline_min (UTC)
    that is ≤ now.  Handles the rollover across week boundaries.

already_rolled(last_rollover_iso, lmd) -> bool
    Return True if the stored _last_rollover meta timestamp indicates a
    rollover has already been applied for the deadline `lmd`.

pick_active_team(nw_team_json, nw_cap_id, nw_vc_id,
                 tw_team_json, tw_cap_id, tw_vc_id, jloads)
    -> (team_json: str, cap_id: str|None, vc_id: str|None)
    Decide which team to promote from draft → active for the new week.
    Rule: use next-week draft if non-empty, else fall back to this-week.
"""

from datetime import datetime, timezone, timedelta


def last_monday_deadline(
    now: datetime,
    deadline_hour: int,
    deadline_min: int,
) -> datetime:
    """
    Return the most recent Monday at deadline_hour:deadline_min UTC
    that is equal to or before `now`.

    Identical to the inline calculation in db_manager.py v5.7
    rollover_season() and do_rollover():

        days_since_mon = now.weekday()
        lmd = (now - timedelta(days=days_since_mon)).replace(
            hour=deadline_hour, minute=deadline_min,
            second=0, microsecond=0, tzinfo=timezone.utc)
        if lmd > now: lmd -= timedelta(days=7)

    Parameters
    ----------
    now           : datetime — current UTC datetime (timezone-aware).
    deadline_hour : int      — hour of the Monday lock (14 for 14:00 SAST).
    deadline_min  : int      — minute of the Monday lock (0).

    Returns
    -------
    datetime — the most recent Monday deadline (UTC, timezone-aware).
    """
    days_since_mon = now.weekday()
    lmd = (now - timedelta(days=days_since_mon)).replace(
        hour=deadline_hour, minute=deadline_min,
        second=0, microsecond=0, tzinfo=timezone.utc,
    )
    if lmd > now:
        lmd -= timedelta(days=7)
    return lmd


def already_rolled(last_rollover_iso: str, lmd: datetime) -> bool:
    """
    Return True if the stored _last_rollover meta value indicates the
    season has already been rolled for the current deadline.

    Identical to the guard logic in db_manager.py v5.7 rollover_season()
    and do_rollover():

        if last_raw:
            try:
                last_dt = datetime.fromisoformat(last_raw)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                if lmd <= last_dt: ...
            except ValueError: pass

    Parameters
    ----------
    last_rollover_iso : str      — ISO datetime from meta table, or "".
    lmd               : datetime — deadline from last_monday_deadline().

    Returns
    -------
    bool — True if this deadline has already been processed.
    """
    if not last_rollover_iso:
        return False
    try:
        last_dt = datetime.fromisoformat(last_rollover_iso)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        return lmd <= last_dt
    except ValueError:
        return False


def pick_active_team(
    nw_team_json: str,
    nw_cap_id:    str | None,
    nw_vc_id:     str | None,
    tw_team_json: str,
    tw_cap_id:    str | None,
    tw_vc_id:     str | None,
    jloads,
) -> tuple:
    """
    Decide which team to promote to active for the new week.

    Rule (identical to db_manager.py v5.7 rollover_season()):
      If the next-week draft is non-empty → promote the draft.
      Otherwise → carry the current this-week active team forward unchanged.

    Parameters
    ----------
    nw_team_json : str      — JSON player-ID list for next-week draft.
    nw_cap_id    : str|None — next-week captain ID.
    nw_vc_id     : str|None — next-week VC ID.
    tw_team_json : str      — JSON player-ID list for this-week active team.
    tw_cap_id    : str|None — this-week captain ID.
    tw_vc_id     : str|None — this-week VC ID.
    jloads       : callable — safe JSON loader, e.g. the module-level _jloads.

    Returns
    -------
    tuple(team_json: str, cap_id: str|None, vc_id: str|None)
        The values to write as both tw_ and nw_ columns for the new week.
    """
    nw_team = nw_team_json or "[]"
    cap     = nw_cap_id
    vc      = nw_vc_id
    if jloads(nw_team, []) == []:
        nw_team = tw_team_json or "[]"
        cap     = tw_cap_id
        vc      = tw_vc_id
    return nw_team, cap, vc
