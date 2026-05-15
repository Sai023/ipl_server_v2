# db_manager.py — The League's Sole Database Clerk

> **Post-cleanup note (2026-05-15).** This document is the snapshot that
> drove the cleanup. The Dead Code Audit / Open Questions below describe
> what was **found**. The items listed there have been actioned across
> Waves A–E — see [dead_code_register.md](dead_code_register.md) for the
> current `fixed` / `kept` / `guarded` status of each.


## What it does (business view)

`db_manager.py` is the **only file in the project allowed to talk to the
SQLite database**. Every other module that needs to read or write league
data calls into the `DatabaseManager` class here.

By design, this file is a **pure clerk**, not a brain:

- It knows how to **read**, **write**, **update**, and **aggregate** the
  seven league tables.
- It does **not** know any IPL scoring rules — those live in
  `logic/scoring_engine.py`.
- It does **not** know what a "Monday rollover" is — that's
  `logic/rollover_engine.py`.
- It does **not** make HTTP requests, parse Cricbuzz pages, or schedule
  jobs.

The pre-Phase-5 refactor removed the last bits of business logic from
this file. Today, the most "intelligent" SQL it runs is the leaderboard
aggregation — and even that just executes a pre-prepared statement.

## Where it sits in the flow

```
   logic/scoring_engine.calc_pts   ← only logic-engine import
                ↑
        db_manager.DatabaseManager (this file)
              ↑
              │ every read/write goes through here
              │
     ┌────────┴────────┐
     │                 │
  routes.py        scraper.py / tasks.py
```

`base.py` holds **the singleton instance** of `DatabaseManager`, called
`db`. Every callsite uses `db.xxx()` — never instantiates a second
`DatabaseManager`.

## The seven tables (business view)

| Table | What it stores | Persistence | Owned by |
|-------|----------------|-------------|----------|
| `players` | The 220-row IPL roster: name, team, role, price, plus computed `season_pts` (raw) and `points` (cap/vc-weighted) | **Persistent** — survives restarts | `Seed_Players.py` writes initial rows; `scraper.py` can append dynamic `ext_*` players. |
| `matches` | The 74-match schedule + status + Cricbuzz URL | **Persistent** | `Seed_Matches.py` initial rows; `scraper.py` updates status. |
| `user_selections` | One row per `(member, week)`: this-week XI, next-week draft, **`week_pts`** (leaderboard input), and a `points_per_match` blob | **Persistent** — the leaderboard's source of truth | `init_db.py` seeds W1-W4; `routes.py` saves drafts and rollovers. |
| `match_scores` | Raw per-player per-match stat lines from Cricbuzz | **Ephemeral** — wiped at every server startup, rebuilt by `scraper.py` | `scraper.py` only. |
| `player_match_points` | The **calculated base points** for each player in each match, plus the cap/vc multiplier flag | **Ephemeral** | `recalculate_points()` rebuilds it from `match_scores`. |
| `user_match_points` | One row per `(member, match)`: that member's total points for that match | **Ephemeral** | `update_week_points()` writes it. |
| `meta` | A key/value store for timestamps (`_saved`, `_last_rollover`) and the history-seed version | **Persistent** | Read/written by many methods. |

**The ephemeral-vs-persistent rule is critical:** server startup wipes
the three ephemeral tables and rebuilds them from `data/matches/*.json`,
but it **never** wipes `week_pts`, `season_pts`, or `points`. The
leaderboard is therefore correct *before* the scraper runs again.

## Key business rules it enforces

### 1. The points pipeline
The four methods that turn raw stats into ranked points, in order:
```
db.recalculate_points(match_id=None)   # match_scores → player_match_points (base only)
db.update_week_points()                # → user_selections.week_pts + user_match_points
                                       # ALSO: internally calls update_player_points()
db.update_player_season_pts()          # → players.season_pts (raw sum across season)
```
- `recalculate_points` is the only method here that calls
  `logic.scoring_engine.calc_pts()`.
- `update_week_points` is the only place the **cap×2 / vc×1.5
  multipliers are applied at write time** — using an inline ternary, not
  `apply_multiplier()` (see [dead_code_register.md](dead_code_register.md) D1).
- The atomic per-match pipeline lives in `scraper.py`:
  `_upsert_match → recalculate_points(match_id) → update_week_points()`.

### 2. The leaderboard fan-out fix (`_LEADERBOARD_SQL`)
The single biggest SQL statement in the file
([db_manager.py:178-250](../db_manager.py:178)). Two independent CTEs:
- `user_totals` reads **only** from `user_selections`, summing `week_pts`.
  No join — cannot fan-out.
- `match_counts` reads **only** from `user_match_points`. No join to
  selections — cannot inflate totals.

A previous version JOINed these, multiplying `week_pts` by match count
(W2's 900 became 8100 etc.). The fix is the central guarantee that the
"Total Pts" column on the leaderboard always equals the sum of the
weekly columns shown beside it.

### 3. The MVP column
The same SQL also computes each member's **highest-scoring player for
the week** (the MVP shown in the leaderboard table). Ties are broken by
`MIN(player_id)` — deterministic, but arbitrary. If two players tie on
points, the alphabetically-lower ID wins.

### 4. Schema migrations
`_init_schema()` runs idempotent `ALTER TABLE ... ADD COLUMN` statements
([db_manager.py:356-366](../db_manager.py:356)) wrapped in `try/except`.
The columns were added across Phases 5-8: `week_pts`, `season_pts`,
`points`, `points_per_match`. Re-running on an already-migrated DB is a
no-op.

### 5. Concurrency
- Every connection uses `journal_mode=WAL`, `foreign_keys=ON`,
  `busy_timeout=30000`.
- One connection **per thread** (`threading.local`). Web requests, the
  scraper daemon, and the APScheduler job each get their own.
- Writes go through `_write()`, which holds a single `threading.Lock`
  (`self._wlock`) and uses `BEGIN IMMEDIATE` — only one writer at a time
  across the whole process.

### 6. Player ID auto-correction on save
Doesn't live here directly — but `save_next_week()` and `upsert_member()`
write whatever IDs the caller passes. `base.resolve_id_list` is what
normalises typed names into IDs *before* calling the DAO; the DAO trusts
its input.

## Inputs / Outputs

The public surface is the methods on `DatabaseManager`. Grouped by purpose:

| Group | Methods |
|-------|---------|
| **State / read** | `get_state`, `get_history`, `get_leaderboard`, `get_players`, `get_current_week`, `get_user_match_points`, `ping_stats`, `get_etags`, `get_meta` |
| **Member / match writes** | `upsert_member`, `upsert_match`, `save_state`, `save_next_week`, `validate_budget`, `set_meta` |
| **Points pipeline** | `recalculate_points`, `update_week_points`, `update_player_season_pts`, `update_player_points` |
| **Rollover DAO** | `get_users_and_max_weeks`, `get_selection_row`, `insert_rollover_week`, `set_last_rollover` |
| **Bulk rebuild / hydrate** | `rebuild_scores_and_points`, `hydrate_from_json` |
| **Dangerous** | `reset` |

## Called by / Calls into

- **Called by:** `routes.py` (the majority — every API handler), `scraper.py`
  (the points-pipeline methods after each match), `server.py`
  (`hydrate_from_json` and `rebuild_scores_and_points` during startup).
- **Calls into:** `logic.scoring_engine.calc_pts` (the only project
  import). Plus stdlib (`json`, `re`, `sqlite3`, `threading`, `datetime`,
  `pathlib`, `contextlib`).

## Supports which user capabilities

From [user_capabilities.md](user_capabilities.md), the DAO underpins
*every* capability that reads or writes league data:

| Capability | Method(s) |
|------------|-----------|
| **§1.1 Pick / Register** | `upsert_member`, `get_state` |
| **§3.1 Match Centre hub** | `get_state`, `get_user_match_points` |
| **§3.2 Box Score modal** | `get_state` (for `tw_team_json` from `user_selections`) + `recalculate_points` outputs |
| **§4.1 This Week locked XI** | `get_state`, `get_history` |
| **§4.2 Simulate rollover** | `get_users_and_max_weeks`, `get_selection_row`, `insert_rollover_week`, `set_last_rollover`, `update_week_points` |
| **§5.1-5.3 Build / save draft** | `get_players`, `validate_budget`, `save_next_week` |
| **§5.4 Season badges** | `get_state` returns `player_pts` |
| **§6 Leaderboard** | `get_leaderboard` (the fan-out-proof CTE) |
| **§7 Members** | `get_state` (the `members` dict) |
| **§8 Admin** | `upsert_match` (via `routes.api_update_match_url`) |
| **§9.1 Refresh** | All read methods plus the points pipeline triggered downstream |
| **§10.1 Polling** | `get_etags` (the `_saved` timestamp) |

## Dead Code Audit

| Symbol | Lines | Verdict | Notes |
|--------|-------|---------|-------|
| `GoldenDB = DatabaseManager` | 860 | **DEAD.** | Module-level alias never imported or referenced anywhere in the project. Likely a leftover from a renaming pass. Added to [dead_code_register.md](dead_code_register.md) as D6. |
| `reset()` | 853–857 | **DEAD.** | Never called from anywhere. Wipes 6 tables — dangerous to keep without callers; either expose via an admin endpoint or delete. Added as D7. |
| `update_player_points()` | 550–572 | **Live but indirectly.** | Called only from `update_week_points()` (line 627). No external callers. SKILL.md says `players.points` is "Display in Points tab" — but the Points tab is dead (D3). So the method computes a value nothing displays. Added as D8 (conditional — delete after Points tab decision). |
| `get_etags()` | 787–788 | **Live.** | Used by `/api/poll` for ETag check. Returns a one-key dict; could be inlined as `db.get_meta("_saved")`, but works. |
| `save_state()` | 440–468 | **Live.** | Single caller in `routes.py:128` (`POST /api/state`). |
| `hydrate_from_json()` | 717–736 | **Live.** | Called by `server.py:167` during startup rebuild. |
| `rebuild_scores_and_points()` | 687–715 | **Live.** | Called by `server.py:349` during startup. |
| `get_history()` | 805–821 | **Live.** | Endpoint `/api/history/<n>` still consumed by frontend (login draft pre-fill, Members tab) even though the **dedicated History tab is dead** (D5). Endpoint keeps. |

**Total dead code in `db_manager.py`: ~30 lines** (`GoldenDB` alias +
`reset` method). Plus one **functional-but-purposeless** method
(`update_player_points`) pending the Points-tab decision.

## Open Questions

1. **Duplicate schema in `init_db.py`.** `_SCHEMA` is defined in **both**
   `db_manager.py:59-154` and `init_db.py:42-137`. The `init_db.py` copy
   is dead (see [init_db.md](init_db.md)). The docstring there
   acknowledges the duplication and says consolidation is "deferred". A
   ~95-line cleanup once we agree.
2. **`update_player_points` writes a column nobody reads.** SKILL.md's
   "Display in Points tab" claim is wrong — the Points tab is dead. The
   column `players.points` is therefore set on every match scrape but
   read by zero consumers. Possibilities:
   - Restore the Points tab → keeps the column meaningful.
   - Delete the column, the method, and the SKILL.md claim → ~30 lines
     of saved work per scrape.
3. **`set_meta` is a public method called from one place only**
   ([routes.py:988](../routes.py:988) — the rollover handler) and it's
   only ever passed `_saved`. The dozen internal `set_meta` calls go
   through inline `INSERT OR REPLACE` SQL — inconsistent style. Worth
   converging on one or the other.
4. **`_LEADERBOARD_SQL` is 73 lines.** It's correct, well-commented, and
   the most performance-critical query in the system — but it's a wall
   of SQL in the middle of a Python file. Worth either moving to a
   `.sql` resource file, or splitting into smaller named CTEs as
   constants. Cosmetic; no behaviour change needed.
5. **No transactions across method calls.** Each method opens its own
   `_write()` context. A rollover that touches `user_selections`,
   `meta`, and (indirectly) the points tables does so in **multiple
   committed transactions**. If the process dies mid-rollover, the
   database can be left half-rolled. Worth flagging — the consequence
   is rare but real.
6. **MVP tie-breaker is alphabetical-by-ID.** `MIN(sp.player_id)`
   ([db_manager.py:217](../db_manager.py:217)) is deterministic but
   arbitrary. If `k04` and `r03` tie on a member's MVP-week, the K
   always wins. Not a bug; worth knowing.
