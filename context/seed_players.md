# Seed_Players.py — The Roster Loader

## What it does (business view)

`Seed_Players.py` is the **roster loader**. It wipes the `players`
table and rewrites it with the IPL 2026 squad lists — about 220 rows
covering every player on every IPL franchise's roster.

Each row carries:

- **A short league-internal ID** following the convention
  `{team_prefix}{number:02d}` — `c09` is Chennai's player №9, `rr11`
  is Rajasthan's player №11, etc.
- **The player's display name** — used by `logic.fuzzy_match` to
  resolve scorecard names back to this ID.
- **The IPL team code** (`CSK`, `DC`, `GT`, `KKR`, `LSG`, `MI`,
  `PBKS`, `RCB`, `RR`, `SRH`).
- **The price** in fantasy credits (used by §5 budget enforcement).
- **The role** — `BAT`, `BOWL`, `AR` (all-rounder), or `WK`
  (wicketkeeper).

The roster lives **as a Python list literal** inside this file, not in
a database or external CSV. Updating a player's price or moving
someone between teams means editing this file and re-running the
seeder.

## Where it sits in the flow

Pure CLI bootstrap, run **once per IPL season** (or whenever the
roster needs to be patched):

```
python Seed_Players.py            ← manual run, wipes + reseeds
python Seed_Players.py --reset    ← also wipes match_scores / pmp
   │
   ▼
   players table  ←  authoritative for IDs, names, teams, prices, roles
   │
   └── consumed by:
       • logic.fuzzy_match._build_player_index (every scrape)
       • base.resolve_player_id (every API resolve call)
       • routes.api_players (the /api/players response)
       • the picker UI (§5.1) and budget bar (§5.2)
```

Triggered automatically by `init_db._auto_seed_players_if_needed()` on
**first server boot** (when the players table is empty).

## Inputs / Outputs

- **Input:** `PLAYERS` — a hardcoded list of `(id, name, team, price,
  role)` tuples ([Seed_Players.py:29-247](../Seed_Players.py:29)).
- **Output:** the `players` table is wiped, then every tuple is
  inserted. Returns nothing (CLI script); prints a summary line:
  *"Players seeded: 217 inserted, 0 skipped"*.

## Key business rules it enforces

### 1. ID convention is enforced by construction, not by schema
The schema's only constraint is `id TEXT PRIMARY KEY`. The
`{team_prefix}{nn}` pattern lives **only** in this file's data and is
honored by every other consumer (the scraper, the resolver, the
auto-add fallback's `ext_` prefix is deliberately *outside* this
pattern so it cannot collide).

### 2. Always wipe then reseed
The seeder always runs `DELETE FROM players` before inserting. There
is no "merge" mode — partial roster edits require a full re-run. This
avoids the class of bugs where stale rows linger after a player
transfer.

### 3. `--reset` clears scoring tables too
Adding `--reset` also wipes `match_scores` and `player_match_points`.
Useful when re-seeding mid-season for testing; never needed in
production.

### 4. Roles are constrained
The `players.role` column has a DB-level check
`CHECK (role IN ('BAT','BOWL','AR','WK'))`. The seeder's tuples are
hand-curated to match. The dynamic-player fallback in
`logic.fuzzy_match._generate_dynamic_player` deliberately uses `AR` to
satisfy the same constraint.

### 5. Trade and transfer corrections live here
The v2 docstring notes:
- `rr11`: name corrected from "Vaibhav Suryavanshi" to
  "Vaibhav Sooryavanshi" (the Cricbuzz spelling).
- `c11`: price corrected 2.2 → 8.0 (Dewald Brevis).
Anyone editing the roster needs to know they'll be running this file
again to apply changes.

## Called by / Calls into

- **Called by:**
  - Operator: `python Seed_Players.py` manually.
  - Operator: `python Seed_Players.py --reset` after `--reset`.
  - `init_db._auto_seed_players_if_needed()` automatically on first
    boot (via `subprocess.run`).
  - `daily_sync.yml` GitHub Actions workflow step 4 (idempotent — no-op
    if already seeded).
- **Calls into:** `sqlite3` (stdlib), `argparse`, `pathlib`. Nothing
  project-local — this file is intentionally standalone.

## Supports which user capabilities

From [user_capabilities.md](user_capabilities.md):

- **§5.1 Build a draft XI** — the picker is populated from
  `/api/players`, which reads what this file wrote.
- **§5.4 Season-points badges** — the badge values are
  `players.season_pts`, but the player rows themselves came from here.
- **§1.1 Register / pick a player** — every player ID a user can
  select originated in this file's `PLAYERS` list.
- **All scoring-related capabilities (§3, §4, §6)** — the scraper
  cannot resolve a name to an ID unless the player exists in this
  table.

## Dead Code Audit

| Symbol | Verdict |
|--------|---------|
| `PLAYERS` list | **Live.** Sole input to `seed()`. |
| `seed(reset=False)` | **Live.** Sole worker function. |
| `__main__` argparse block | **Live.** CLI entry. |
| `BASE_DIR`, `DB_PATH` | **Live.** |

**No dead code.** This is the smallest "live" file in the project —
nothing in it is unused.

The team comments (`# Captain`, `# Retained (Uncapped)`,
`# Key Overseas Signing`) are **not** dead code; they're roster notes
that help an operator double-check the list against an IPL auction
report.

## Open Questions

1. **The roster is a hand-maintained Python literal.** A typo (wrong
   team prefix, two players with the same numeric suffix) would only
   surface as a `sqlite3.IntegrityError` during seeding — *and would
   be reported as a "Skip" line that's easy to miss*. Worth a
   pre-seed sanity check: assert all IDs match the
   `^[a-z]{1,3}\d{2}$` pattern, all prefixes map to a known team, no
   duplicates.
2. **Player `k07 — Rachin Ravindra` has `team="PBKS"`** despite the
   `k*` prefix convention saying `k` = KKR
   ([Seed_Players.py:106](../Seed_Players.py:106)). Either the ID
   should be `p*` (consistent with the prefix rule) or the team field
   should be `KKR`. Today both consumers (the scraper's fuzzy match,
   the budget bar) read the `team` field rather than the prefix, so
   nothing visibly breaks — but this is the *exact* kind of split
   that ambiguous-name resolution falls over on. Worth fixing.
3. **No versioning of the roster.** Players change between seasons;
   prices change mid-auction. There's no `players._roster_version`
   meta row that says "currently seeded: v2". Compared to
   `init_db._SEED_VERSION`, the players seed is fire-and-forget. If
   the league wanted to "force a re-seed when the roster file
   changes", we'd need that.
4. **All-or-nothing semantics on `--reset`.** `--reset` wipes both
   `match_scores` and `player_match_points` *but not*
   `user_match_points` or `user_selections.week_pts`. So a `--reset`
   followed by a scraper run will produce per-player pmp rows but
   leave stale `week_pts` until `update_week_points` runs. The
   pipeline does run from the scraper, so this self-corrects, but
   worth noting if anyone debugs a partial-reset state.
5. **No way to add a single player without wiping.** A mid-season
   signing today requires either editing the file + full re-seed
   (destroying everyone's `season_pts`!) or… nothing — the scraper's
   FIX-015 dynamic-add safety net catches it as an `ext_*` player.
   That works, but a `Seed_Players.py --add <id> <name> <team>
   <price> <role>` mode would be a kinder operator experience.
