"""
BCL Fantasy - Session policy                          session_policy v1.0.0
===========================================================================
How long a sign-in lasts, and when using the app renews it.

Pure functions only: stdlib (datetime) only, zero project imports.

2026-11-19 (Sai: "Make sessions last 2 months so it would cover an entire league"):
a session lasts 60 days and is renewed whenever it is used while fewer than 30 days
remain. So a member who opens the app at least once in any 60 days is never signed
out, and a session row is written about once a month, not on every request.

Before this, a session was a fixed 30 days from sign-in and was never renewed: someone
who signed in on 19 Dec would have been signed out on Mon 18 Jan, a lock day.

Public API
----------
SESSION_TTL_DAYS = 60, SESSION_RENEW_BELOW_DAYS = 30

new_expiry(now, ttl_days=SESSION_TTL_DAYS) -> str
    The expiry (ISO 8601, UTC) of a session created at `now`.

renewed_expiry(expires_at_iso, now, ttl_days=..., renew_below_days=...) -> str | None
    The new expiry a session should get when it is used at `now`, or None to leave it
    alone. It never revives a session that is already over, never shortens one, and
    leaves a session alone when its stored expiry cannot be read.
"""

from datetime import datetime, timedelta, timezone

SESSION_TTL_DAYS = 60            # the user's decision: two months, enough for a whole league
SESSION_RENEW_BELOW_DAYS = 30    # renew only when fewer days than this remain (about one write a month per session)


def _utc(d):
    """A datetime as an aware UTC datetime (a naive one is read as UTC)."""
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def _parse_utc(iso):
    """An ISO 8601 string as an aware UTC datetime, or None when it is empty or unreadable."""
    if not iso:
        return None
    try:
        return _utc(datetime.fromisoformat(str(iso).strip()))
    except (ValueError, TypeError):
        return None


def new_expiry(now, ttl_days=SESSION_TTL_DAYS):
    """The expiry (ISO 8601, UTC) of a session created at `now`."""
    return (_utc(now) + timedelta(days=ttl_days)).isoformat()


def renewed_expiry(expires_at_iso, now, ttl_days=SESSION_TTL_DAYS, renew_below_days=SESSION_RENEW_BELOW_DAYS):
    """The new expiry for a session used at `now`, or None when it should be left as it is."""
    now = _utc(now)
    exp = _parse_utc(expires_at_iso)
    if exp is None or exp <= now:
        return None                                    # unreadable, or already over: a finished session is never revived
    if exp - now >= timedelta(days=renew_below_days):
        return None                                    # plenty of time left: no write
    newer = now + timedelta(days=ttl_days)
    return newer.isoformat() if newer > exp else None  # renewing only ever extends
