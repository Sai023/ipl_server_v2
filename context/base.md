# base.py — The League's Shared Workshop

> **Post-cleanup note (2026-05-15).** This document is the snapshot that
> drove the cleanup. The Dead Code Audit / Open Questions below describe
> what was **found**. The items listed there have been actioned across
> Waves A–E — see [dead_code_register.md](dead_code_register.md) for the
> current `fixed` / `kept` / `guarded` status of each.


## What it does (business view)

`base.py` is the **shared workshop** every other server-side script borrows
tools from. It sets up, once and only once:

- **The web app itself** — the Flask application that serves the league.
- **The database handle** — a single connection manager all routes share.
- **Logging** — one log channel that writes to screen and to `server.log`.
- **Rate limiting** — protection against any one user spamming the API.
- **The player-name resolver** — the "smart" lookup that turns a typed name
  like "VK" or "V Kohli" into the right player ID, even with typos.
- **Common safety nets** — automatic responses when the database hits a
  duplicate, a missing record, or an internal error.
- **The public URL** — a slot that's filled in at boot time when the tunnel
  (Cloudflare / ngrok / Pinggy) reports the address users will connect to.

This file exists **for one specific reason**: before it, `routes.py` had to
import from `server.py`, and `server.py` had to import from `routes.py`, which
deadlocked Python at startup. `base.py` was carved out to break that loop.

## Where it sits in the flow

Second from the bottom of the stack:

```
config.py  →  base.py  →  routes.py  →  server.py
```

It depends on `config.py` and `db_manager.py`, and is imported by `routes.py`
and `server.py`. It must never import from those upper layers.

## Inputs / Outputs

- **Inputs:**
  - `DB_PATH` from `config.py`
  - `DatabaseManager` class from `db_manager.py`
- **Outputs (the workshop tools):**
  - **Singletons:** `app` (Flask), `db` (DatabaseManager), `_write_limiter`
  - **Constants for the rules of fantasy:** `BUDGET_TOTAL = 100.0`,
    `XI_SIZE = 11`, **`MAX_WEEKS = 10`** (was 8 until 2026-05-18 —
    bumped to match `data/schedule.json` which has 10 weeks across 74
    matches; with 8, the W8→W9 Monday rollover silently no-op'd
    because `api_rollover` short-circuits on `current_week >= MAX_WEEKS`)
  - **Paths:** `BASE_DIR`, `DATA_DIR`, `STATIC_DIR`
  - **Helpers:** `_db_con`, `_log`, `_jloads`, `_check_rate`
  - **Player resolution:** `resolve_player_id`, `resolve_id_list`, `_ID_RE`
  - **Passcode + sessions (Phase 12):** `PASSCODE_RE` (4-digit regex),
    `hash_passcode(passcode, username)`, `verify_passcode(...)` (constant-time
    via `secrets.compare_digest`), `new_session_token()` (32-byte hex from
    `secrets.token_hex`), `get_bearer_token()` (parses `Authorization:
    Bearer <token>` from the current Flask request).
  - **Mutable global:** `CURRENT_PUBLIC_URL` — set by `server.py` when a
    tunnel comes up, read by `routes.py` for the `/api/ping` response.

## Key business rules it enforces

1. **Budget = 100.0 credits, XI = 11 players, Season = 8 weeks.** Hard-coded
   here as `BUDGET_TOTAL`, `XI_SIZE`, `MAX_WEEKS`. Used by `routes.py` in the
   `/api/ping` response and by `server.py` for the boot banner.
2. **Player IDs follow a strict pattern.** `_ID_RE` enforces 1–3 lowercase
   letters followed by 1–2 digits (e.g. `r01`, `k16`, `rr11`). Anything that
   doesn't match is treated as a name to be resolved, not a player ID.
3. **The 6-tier player resolver** (`resolve_player_id`). When the system sees
   a name (typed by an admin, written on a Cricbuzz scorecard), it tries in
   order:
   1. Exact player ID.
   2. Exact name **+ correct team** (resolves "Noor Ahmad" CSK vs GT).
   3. Exact name.
   4. **Nickname** (via `_SEMANTIC_MAP`: "vk" → Virat Kohli, "sky" → Suryakumar Yadav, etc.).
   5. **Fuzzy match** at ≥40% token-set ratio, with a 12-point bonus if teams agree.
   6. **Last-name match** as a last resort, preferring same-team hits.
4. **Auto-correction on save.** When a user's saved team has player *names*
   instead of *IDs*, `resolve_id_list` rewrites the saved JSON in place — so
   the next read finds clean IDs.
5. **Unresolved names are logged, never silently dropped.** Tier-(-1) entries
   produce a warning in `server.log`.
6. **Every database connection uses WAL mode.** `_db_con` sets
   `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=30000` — giving the
   scraper and the web server safe concurrent access.
7. **Write throttle = 30 requests / 60 seconds per IP.** `_RateLimiter` is
   applied to mutating endpoints via `_check_rate`.
8. **Security headers on every response.** `X-Content-Type-Options: nosniff`,
   `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, and
   `Cache-Control: no-store` on `/api/*` paths.
9. **404 falls back to the SPA.** Unknown paths re-render `index.html`, so
   client-side routes work on hard reload.
10. **Passcode hashing is salted-but-cheap (Phase 12).** `hash_passcode`
    returns `sha256("<username>:<passcode>")`. Username acts as the salt so
    two users picking `1234` produce different hashes. SHA-256 (not bcrypt)
    is deliberate — for a 4-digit space (10,000 combos) a slow hash buys
    almost nothing against an offline brute-force, and SHA-256 is fast
    enough that we don't have to think about login latency. See
    [user_capabilities.md](user_capabilities.md) §1.5 for the honest
    threat model.

## Called by / Calls into

- **Called by:** `routes.py` (for nearly everything), `server.py` (for `app`,
  `db`, `_log`, and the league constants).
- **Calls into:** `config.py`, `db_manager.py`. Plus stdlib (`sqlite3`,
  `re`, `unicodedata`, `threading`, `time`, `collections`) and `flask`.

## Supports which user capabilities

Cross-references to [user_capabilities.md](user_capabilities.md):

- **§1.1 Register / §1.2 Login / §1.3 Reset Passcode (Phase 12)** —
  `PASSCODE_RE`, `hash_passcode`, `verify_passcode`, `new_session_token`,
  and `get_bearer_token` are the primitives every `/api/passcode/*` and
  `/api/admin/*` handler in `routes.py` is built on.
- **§5.1 Build a draft XI** — `resolve_id_list` normalises names from
  the picker.
- **§5.3 Save the draft** — `resolve_id_list` auto-corrects typed names
  into player IDs when a draft is saved.
- **§9.1 Refresh / §10.2 Auto-rollover** — write endpoints behind these
  features are gated by `_write_limiter` (30/min/IP).
- **§1.4 Switch user** — 404 fallback re-renders `index.html` so the SPA
  routes still work on hard reload.
- **All tabs** — every page request gets the `_security_headers` middleware.

## Dead Code Audit

| Symbol | Where it's used | Verdict |
|--------|-----------------|---------|
| `_normalise` | Internal helper of the resolver | **Live** (private). |
| `_token_set_ratio` | Internal helper of the resolver | **Live** (private). |
| `_load_all_players` | Internal helper of the resolver | **Live** (private). |
| `resolve_player_id` | `routes.py`, internal in `resolve_id_list` | **Live.** |
| `resolve_id_list` | `routes.py` (save-next-week endpoint) | **Live.** |
| `_ID_RE` | `routes.py` save endpoint, `resolve_id_list` here | **Live.** |
| `_SEMANTIC_MAP` | Used by `resolve_player_id` tier 4 | **Live, but parallel to `logic/fuzzy_match.py`.** See Open Questions. |
| `_jloads` | `routes.py` | **Live.** |
| `_setup_logging` / `_log` | Whole project | **Live.** |
| `_db_con` | `routes.py`, `server.py` | **Live.** |
| `_RateLimiter`, `_write_limiter`, `_check_rate` | `routes.py` write endpoints | **Live.** |
| `db`, `app` | Everything | **Live singletons.** |
| `STATIC_DIR` | `routes.py` (`/static/<filename>`); Flask `static_folder` argument | **Live.** Capital-S **`"Static"`** since Phase 11 — Windows is case-insensitive so lowercase worked locally, but Linux (Render / Codespaces) treats `static` ≠ `Static` and 404s every `/static/*` asset, leaving the UI stuck on "Loading your league..." |
| `BASE_DIR`, `DATA_DIR` | Re-derived from `config.py` | **Duplicated** — not dead, just redundant. |
| `BUDGET_TOTAL`, `XI_SIZE`, `MAX_WEEKS` | `routes.py`, `server.py` | **Live.** |
| `CURRENT_PUBLIC_URL` | `server.py` writes, `routes.py` reads | **Live.** |
| Flask error handlers (`_handle_integrity`, `_handle_operational`, `_handle_500`, `_handle_404`) | Wired via `@app.errorhandler` decorators | **Live.** |
| `_security_headers` (after_request) | Wired via `@app.after_request` | **Live.** |
| `PASSCODE_RE`, `hash_passcode`, `verify_passcode`, `new_session_token`, `get_bearer_token` (Phase 12) | `routes.py` AUTH group | **Live.** |

**No outright dead code.** One real duplication concern below.

## Open Questions

1. **Two player resolvers exist.** `base.py` ships a full resolver
   (`resolve_player_id` + `_SEMANTIC_MAP`). `logic/fuzzy_match.py` also ships
   a resolver (`_fuzzy_match`, `_generate_dynamic_player`). The scraper uses
   the logic-package one; routes use the base.py one. Tracked as
   [docs_audit.md item G](docs_audit.md). The nickname map and fuzzy
   thresholds in the two files are *not* guaranteed to be in sync — meaning
   "VK" could resolve from the UI but not from the scorecard, or vice versa.
2. **`BASE_DIR` / `DATA_DIR` redefined.** `config.py` already exports both;
   `base.py` redoes the work using its own `__file__`. Tracked as
   [docs_audit.md item H](docs_audit.md). Same value today because both files
   sit in the same directory; fragile if either moves.
3. **Rate limit applies to write endpoints only.** Read endpoints have no
   limit — fine for a small private league, but worth flagging if this is
   ever exposed publicly.
4. **`CURRENT_PUBLIC_URL` is a mutable module global.** Works because there's
   only one server process. If the project ever moves behind a load balancer,
   this needs to become a config value or a database row.
