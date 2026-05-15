# init_db.py — The Startup Seeder

> **Post-cleanup note (2026-05-15).** This document is the snapshot that
> drove the cleanup. The Dead Code Audit / Open Questions below describe
> what was **found**. The items listed there have been actioned across
> Waves A–E — see [dead_code_register.md](dead_code_register.md) for the
> current `fixed` / `kept` / `guarded` status of each.


## What it does (business view)

`init_db.py` runs **once per server boot**, before the web app accepts
its first request. Its job is to answer three questions in order:

1. **Are there any players in the roster?** If not, run
   `Seed_Players.py` to populate the 220-player IPL 2026 roster.
2. **Are there any matches in the schedule?** If not, run
   `Seed_Matches.py` to populate the 74-match fixture list.
3. **Is the seeded history (W1-W4 XIs for Sai and Moe) up to date?**
   If the stored seed version differs from the current one, **delete
   and reinsert** those weeks — but **preserve any next-week draft a
   user has already saved** and any extra weeks past W4 the rollover
   has already produced.

If all three answers are "yes, we're fine", the file does nothing and
the server boots in milliseconds.

## Where it sits in the flow

```
server.py __main__
   └── init_db.run_all_sync(db)             ← called once
         ├── _auto_seed_players_if_needed() ← spawns Seed_Players.py if empty
         ├── _auto_seed_if_needed()         ← spawns Seed_Matches.py if empty
         └── _auto_seed_history_if_needed() ← compares _seed_version meta
```

After this, `server.py` continues with `_rebuild_scores_and_points()`
and starts Flask.

## Inputs / Outputs

- **Input:** the on-disk SQLite file at `config.DB_PATH`, plus the
  presence of `Seed_Players.py` / `Seed_Matches.py` in the project root.
- **Output:**
  - First-boot: a fully-seeded roster + schedule.
  - Subsequent boots: a refreshed W1-W4 history if `_SEED_VERSION`
    changed; otherwise no change.

## Key business rules it enforces

### 1. Idempotency by counting rows
`_auto_seed_players_if_needed` and `_auto_seed_if_needed` each ask SQLite
*"how many rows are in this table?"* and skip if `n > 0`. There's no
attempt to merge new players into an existing roster — bulk re-seed is
delegated to operators running `Seed_Players.py --force` manually.

### 2. Versioned history seed
`_SEED_VERSION = "2026.v8.w3w4-defined"` is the discriminator. On boot,
the file reads `meta._seed_version` and compares:
- Same version → log "Season history up-to-date." and return.
- Different (or absent) → run the re-seed.

The re-seed is **draft-preserving**:
- Save each user's next-week draft (`nw_*` columns) for the max seed week.
- Save every user_selections row beyond the max seed week.
- Delete the seeded user_selections rows.
- Reinsert from `_HISTORY_SEED`, but for `week == max_seed_wk`, restore
  the saved `nw_*` draft instead of overwriting with the seed's draft.
- Re-insert the saved post-W4 rows.

This means: an operator can bump `_SEED_VERSION` to fix a typo in a W2
captain ID **without losing any user's W5 draft** that was already saved.

### 3. "Never alias W3=W2" — and the variant the rule allows
The docstring at [init_db.py:140](../init_db.py:140) declares:
> *Every week MUST have its own explicit variable — never alias W3=W2.*

The rule is about **avoiding shared references**. A literal
`_SAI_W3_TEAM = _SAI_W2_TEAM` would make both variables point to the
same list object; mutating one mutates both. The current code uses
**separate list literals with identical contents**
([init_db.py:153-159](../init_db.py:153)) — different objects, same
values — which complies with the rule even though they look
suspiciously identical.

### 4. Subprocess for player/match seeding, in-process for history
- Player and match seeding **spawn subprocesses** (`subprocess.run([sys.executable, seed])`)
  because those scripts have a `__main__` block that opens its own
  database connection.
- History seeding runs **in-process** using a local `_db_con()` because
  it needs to read existing state, decide what to preserve, and write
  back — all under one transaction.

## Called by / Calls into

- **Called by:**
  - `server.py:348` (`init_db.run_all_sync(db)`) — startup.
  - `routes.py:1000` (`init_db._auto_seed_history_if_needed()`) —
    the `POST /api/seed-history` admin endpoint.
- **Calls into:** `db_manager.DatabaseManager` (imported but not used —
  see Dead Code), `config` (constants), stdlib (`json`, `sqlite3`,
  `subprocess`, `sys`, `datetime`, `pathlib`).

## Supports which user capabilities

From [user_capabilities.md](user_capabilities.md):

- **First-time setup** — runs the seed scripts so the league has a
  roster and schedule on first boot. No user-visible button; this is
  pure infrastructure.
- **§7 Members** — `_auto_seed_history_if_needed` is what makes "Sai"
  and "Moe" appear in the Members list with their W1-W4 XIs filled in,
  even on a fresh DB. Without this, only manually-registered names
  would be visible.
- **§4 / §5 / §6** — by virtue of seeding W1-W4 history, the
  This Week / Next Week / Leaderboard tabs all have data on first boot
  rather than being empty.

## Dead Code Audit

| Symbol | Lines | Verdict | Notes |
|--------|-------|---------|-------|
| `_SCHEMA` | 42–137 (~95 lines) | **DEAD.** | Identical schema declaration as `db_manager.py:59-154`. **Never used in this file** — no `executescript`, no import. The docstring at line 10 says "db_manager.py retains its own copy ... full schema consolidation is deferred to a later migration phase". The opposite is true today: `db_manager.py` is the authoritative copy, and this one is unused. Added to register as D9. |
| `from db_manager import DatabaseManager  # noqa: F401` | 30 | **DEAD.** | Marked `# noqa` with the comment "used by run_all_sync caller", but `run_all_sync(db=None)` accepts `db` and **never uses it**. The import is not even providing type-hint coverage. Added as D10. |
| `INIT_DB_VER, VERSION_MAP` from config | 31 | **DEAD.** | Imported with `# noqa: F401`; not referenced anywhere in the file. Added as D11. |
| `run_all_sync(db=None)` parameter | 321 | **DEAD parameter.** | The `db` argument exists "for forward-compat" but is never read inside the function. Caller in `server.py:348` passes `db` anyway. Either start using it (rewrite the three `_auto_*` functions to take a `DatabaseManager` instance instead of opening their own connections), or drop the parameter. Added as D12. |
| `_SCHEMA`-only-related comment about "later migration phase" | 9–11, 38–40 | **STALE.** | The comments say `db_manager.py` is the "later" consolidation target. Today it's already the authoritative copy. Tracked as S7. |
| Identical W3/W4 literals to W2 | 153–159, 174–180 | **NOT dead** — by-design separate-object copies of the same content. Worth a comment so the redundancy isn't read as a bug. | — |

**Total dead code in `init_db.py`: ~100 lines** (mostly the redundant
`_SCHEMA` constant).

## Open Questions

1. **Schema duplication.** Pick one: either keep `_SCHEMA` here and
   delete `db_manager.py`'s copy, or vice versa. The cleanest move is
   to keep `db_manager.py` authoritative (it's the one actually running)
   and delete the dead copy here. Tracked as [D9](dead_code_register.md).
2. **`run_all_sync(db)` parameter shape.** The "forward-compat" comment
   ages badly when nothing has used it for 8 phases. Either:
   - Rewrite `_auto_seed_history_if_needed` to take a `DatabaseManager`
     so we stop opening parallel `sqlite3.connect()` calls, **or**
   - Drop the unused parameter.
3. **`_SEED_VERSION` string is opaque.** `"2026.v8.w3w4-defined"` could
   be a tuple `(2026, 8, "w3w4-defined")` or just a monotonic integer.
   The free-form string makes it harder to compare versions
   programmatically — but it's also rarely changed, so it doesn't
   matter much in practice.
4. **What happens if `Seed_Players.py` fails inside the subprocess?**
   The current code catches and prints `Could not run: {e}`. The server
   boots anyway with an empty roster — which is a worse failure mode
   than crashing, because subsequent endpoints will return "0 players"
   silently. Worth turning the catch into a hard fail at startup, with
   a clear log line telling the operator what to fix.
5. **`_HISTORY_SEED` only has W1-W4.** W5+ stubs are commented out at
   lines 161-164 and 182-185. When the W5 deadline passes, an operator
   needs to manually edit this file, define `_SAI_W5_TEAM` and
   `_MOE_W5_TEAM`, add them to `_HISTORY_SEED`, and bump `_SEED_VERSION`.
   Not a code smell — but the comment-driven extension point would
   benefit from a one-line example showing the *exact* edit needed.
