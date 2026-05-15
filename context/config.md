# config.py — The League's Rulebook of Constants

## What it does (business view)

`config.py` is the **single rulebook** the whole system reads from. It declares,
in one place, the answers to questions like:

- Which IPL season are we running? (`IPL_YEAR = 2026`)
- When do weekly picks lock? (Monday, 14:00 UTC — i.e. 16:00 South Africa time)
- Where is the league's data stored? (`data/fantasy.db`)
- What version of the app and each of its pieces is currently in production?

If any of these answers needs to change, **this is the only file that should be edited**.
No other script is allowed to invent its own copy of these values.

## Where it sits in the flow

At the very bottom of the dependency stack. Every other script imports *from*
`config.py`; `config.py` imports *from nothing in the project*. It only uses
Python's standard library (`pathlib`).

## Inputs / Outputs

- **Inputs:** none — pure declarations.
- **Outputs:** named constants imported by every other module:
  - **Paths:** `BASE_DIR`, `DATA_DIR`, `DB_PATH`
  - **Season:** `IPL_YEAR`
  - **Deadline:** `DEADLINE_HOUR`, `DEADLINE_MIN`
  - **Versions:** `APP_VERSION` plus a version pin per script
    (`SERVER_VER`, `ROUTES_VER`, `DB_VER`, `SCRAPER_VER`, `TASKS_VER`,
    `INIT_DB_VER`, `SEED_MATCHES_VER`, `SCORING_ENGINE_VER`,
    `ROLLOVER_ENGINE_VER`, `FUZZY_MATCH_VER`, `CRICBUZZ_DISCOVERY_VER`)
  - **`VERSION_MAP`:** a human-readable changelog of every phase the app has been through.

## Key business rules it enforces

1. **One deadline for the whole league.** Monday 14:00 UTC is hard-coded here.
   Both the server's rollover engine and the browser's countdown clock read this
   same value, so they cannot drift apart.
2. **One database for the whole league.** `DB_PATH` is the only allowed location.
3. **Versioning discipline.** When a script changes, its version pin here must be
   bumped — the `/api/version` endpoint surfaces these pins so the front-end can
   detect mismatches.
4. **Zero project imports.** A hard rule, documented in the file's own docstring:
   if `config.py` ever imports from another project file, the dependency graph
   breaks.

## Called by / Calls into

- **Called by:** every script that needs a constant or version pin —
  `base.py`, `db_manager.py`, `init_db.py`, `scraper.py`, `tasks.py`,
  `routes.py`, `server.py`, and the `logic/` engines.
- **Calls into:** nothing project-local. Only `pathlib.Path` from stdlib.

## Supports which user capabilities

`config.py` underpins everything but isn't tied to a single feature. Two
indirect links worth noting (see [user_capabilities.md](user_capabilities.md)):

- **§4.2 / §10.2 Rollover** — the `DEADLINE_HOUR = 14` constant is what both
  the server-side rollover engine and the in-browser auto-rollover timer
  agree on.
- **§9.1 Refresh / §10.1 Polling** — `APP_VERSION` and the per-script `_VER`
  pins surface via `/api/version`, used by the frontend version-handshake
  check.

## Dead Code Audit

| Symbol | Status | Notes |
|--------|--------|-------|
| `BASE_DIR` | **Defined here, but never imported as `config.BASE_DIR`.** | `base.py` redefines `BASE_DIR` from its own `__file__` rather than importing it. Not dead — value still useful as the canonical anchor — but the duplication is worth knowing about. |
| `DATA_DIR` | Used in `tasks.py`, `init_db.py`. Re-defined in `base.py`. | Same duplication pattern as `BASE_DIR`. |
| `DEADLINE_MIN` | Used in `routes.py` only. | Live. |
| `IPL_YEAR` | Used in `scraper.py`, `tasks.py`. | Live. |
| All `_VER` pins | All currently imported by at least one consumer. | Live. |
| `VERSION_MAP` | Imported by `routes.py` for `/api/version` response. | Live. |

**No dead code found.** The only smell is the duplicated path constants
(`BASE_DIR`, `DATA_DIR`) that `base.py` re-derives instead of importing —
a tiny redundancy, not a bug.

## Open Questions

1. **Header version drift.** The docstring header says "config v1.1.0" but
   the file has no `CONFIG_VER` constant exposing this. The header is the
   only source for that number — easy to miss when bumping versions.
2. **DEADLINE_MIN is always 0.** If the team is sure it will never move off
   the top of the hour, this could be folded into `DEADLINE_HOUR`. Keep it
   for now: explicit minutes match how humans read a deadline.
3. **`VERSION_MAP` is changelog data, not code.** Consider moving older
   entries (Phase 1–7) to a `CHANGELOG.md` so this dict stays focused on
   what's currently relevant.
4. **APP_VERSION drift in the docs.** `config.py` says `2.3.0`, but
   README says `2.1.0-stable` and SKILL.md says `2.2.0-match-centre`. See
   [docs_audit.md](docs_audit.md) item K. The fix is on the docs side,
   not here.
