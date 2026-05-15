# logic/ (package marker) — Pure Business Logic, No Project Imports

## What it does (business view)

`logic/__init__.py` doesn't do work — it **marks `logic/` as a Python package**
and documents the rule the package was created to enforce:

> Every module inside `logic/` describes a piece of the league's *rules*
> (scoring, weekly rollover, name matching) **in plain Python that knows
> nothing about the rest of the project**.

This means each engine can be reasoned about, tested, or replaced *without*
touching the database, the web server, or the scraper.

## Where it sits in the flow

A sideways box, called from anywhere that needs a business rule:

- `db_manager.py` calls `scoring_engine` to convert raw stats to points.
- `routes.py` calls `rollover_engine` to decide whether Monday's deadline has passed.
- `scraper.py` calls `fuzzy_match` to identify players on a Cricbuzz scorecard.
- `tasks.py` calls `cricbuzz_discovery` to find new match URLs each day.

## Inputs / Outputs

- **Inputs:** nothing — `__init__.py` is empty apart from the docstring.
- **Outputs:** makes `logic` importable, e.g.
  `from logic.scoring_engine import calc_pts`.

## Key business rules it enforces

The package's **structural** rule, repeated in the docstring:

> No module in `logic/` may import from any other project module — only the
> Python standard library is permitted.

This guarantees:

1. **Testability** — each engine can run in isolation.
2. **Reusability** — the same engine can power the live server, an audit
   script, or a future replay tool.
3. **No hidden side effects** — there is no way an engine can quietly write
   to the database or hit the network.

## Called by / Calls into

- **Called by:** `db_manager.py`, `routes.py`, `scraper.py`, `tasks.py`.
- **Calls into:** nothing.

## Dead Code Audit

The file is 13 lines and almost entirely docstring. One concern:

| Item | Status |
|------|--------|
| The submodule list in the docstring | **Stale.** It mentions `scoring_engine`, `rollover_engine`, `fuzzy_match`, but **omits `cricbuzz_discovery.py`** — which was added in Phase 9 (v2.2.0). Tracked as [docs_audit.md item E](docs_audit.md). |
| Function name list in the docstring | **Partially stale.** It lists `_norm`, `_build_player_index`, `_fuzzy_match`, `_fuzzy_fielder` for `fuzzy_match`, but doesn't mention `_generate_dynamic_player`, the resilience fix added in v1.1.0. Tracked as [docs_audit.md item F](docs_audit.md). |

**No code is dead, but the docstring is out of date.**

## Open Questions

1. **Update the docstring.** Add `cricbuzz_discovery` to the submodule list
   and mention `_generate_dynamic_player`. Cheapest possible fix; meaningful
   value for anyone reading `logic/` cold.
2. **Should `cricbuzz_discovery.py` live in `logic/`?** It does network I/O
   (HTTP requests to Cricbuzz), which arguably breaks the "no side effects /
   no external dependencies" promise of the package. Tracked as
   [docs_audit.md item E](docs_audit.md). To be revisited when Phase 2
   produces `cricbuzz_discovery.md`.
