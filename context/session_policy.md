# session_policy.py — How Long a Sign-In Lasts

## What it does (business view)

When a member signs in, the server gives their phone a random token (a "session"). `session_policy.py` decides two things about that token:

1. **How long it lasts:** **60 days** (two months), the length of a league. Sai's decision, 2026-11-19: "Make sessions last 2 months so it would cover an entire league."
2. **When using the app renews it:** every time a session is used while **fewer than 30 days** remain, it is extended to a full 60 days again.

Together: a member who opens the app at least once in any 60 days is never signed out, and the session row is written about once a month instead of on every request.

Before this the session was a fixed 30 days from sign-in and was never renewed. Someone who signed in on 19 Dec would have been signed out on Mon 18 Jan, the first Monday lock of SA20.

## Where it sits in the flow

`db_manager.create_session()` takes its default length from here when a member registers, signs in or changes their passcode. `db_manager.get_session()` — which every token-checked route (`_require_token`, so `/api/whoami` and every save) goes through — asks `renewed_expiry()` after it has found the session.

## Inputs / Outputs

### `new_expiry(now, ttl_days=60) → str`
- **Out:** the expiry as an ISO 8601 UTC string (`2027-03-19T14:00:00+00:00`), the format the `sessions.expires_at` text column already holds.

### `renewed_expiry(expires_at_iso, now, ttl_days=60, renew_below_days=30) → str | None`
- **Out:** the new expiry when the session should be extended, or `None` to leave it alone.
- **Never revives:** a session that is already over (expiry at or before now) gets `None`.
- **Never shortens:** it only ever returns a later expiry than the stored one.
- **Leaves alone** a session whose stored expiry is empty or unreadable.
- Naive timestamps (no zone) are read as UTC; other zones are converted.

## Key business rules it enforces

1. A session lasts 60 days. 2. It is renewed only when under 30 days remain, so an active member costs roughly one database write a month. 3. An existing session issued under the old 30-day rule is extended on its next use (it always has under 30 days left). 4. A finished session is never brought back.

## Called by / Calls into

- Called by `db_manager.py` (`create_session`, `get_session`).
- Calls nothing: stdlib `datetime` only, so it can be tested without a database ([tests/test_session_policy.py](../tests/test_session_policy.py): pure tests, file guards, and the real routes on a scratch schema).

## Known limits (until the identity phase of the SA20 readiness plan)

- "Switch user" in the app only forgets the token on that phone; the server session stays valid until it expires (now up to 60 days, or longer while it is used). Real sign-out and "Sign out other devices" arrive with Google sign-in.
- Changing a passcode or an admin reset still deletes every session of that member (unchanged).

## Dead Code Audit

None. Both public functions are used by `db_manager.py`; the constants are used by the defaults.

## Open Questions

None.
