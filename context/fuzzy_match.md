# fuzzy_match.py — The "Who Is This Player on the Scorecard?" Engine

## What it does (business view)

Cricbuzz scorecards do not use the same player IDs as the league's
roster (`c09`, `rr11`, etc.). They use raw display names like
`"V Kohli"`, `"Phil Salt"`, or `"Noor Ahmad"` — and worse, some names
appear in **two squads at once** (Noor Ahmad plays for CSK *and* GT).

`fuzzy_match.py` is the engine that turns a Cricbuzz name into the
correct league player ID, **using the team code as a tie-breaker** when
the name alone is ambiguous. When the name is genuinely unknown (a fresh
overseas signing, a replacement player), it generates a **dynamic
fallback player** so the scraper can still record their stats without
crashing.

This is the only part of the scraper pipeline that can "fail soft" — a
match with an unknown wicketkeeper still gets scored; the unknown
player's points just attach to an `ext_{cricbuzz_id}` row instead of a
seeded one.

## Where it sits in the flow

Called only by `scraper.py`, deep inside the per-innings loop:

```
scraper.py
  └── _build_player_index(con)   ← read all 220 roster rows, build hashes
  └── for each batter/bowler/fielder name on the scorecard:
        ├── _fuzzy_match(name, idx, team_hint)   ← batters/bowlers
        ├── _fuzzy_fielder(name, idx, bowl_team) ← catchers/stumpers
        └── if no match → _generate_dynamic_player(name, team, cb_id)
```

## Inputs / Outputs

### `_norm(s) → str`
Normalises a name for comparison: lowercase, NFD-decompose accents, strip
combining marks, replace `.-'/` with spaces, collapse runs of whitespace.
`"Á. Sharma" → "a sharma"`.

### `_build_player_index(con) → dict`
Reads `SELECT id, name, team, role FROM players` and builds **five**
lookup tables in one pass:
- `by_name` → `{normalised_name: player}` (last writer wins on conflict).
- `by_surname` → `{surname: [player, ...]}` (one-to-many).
- `all` → flat list of player dicts (for fuzzy iteration).
- `by_name_team` → `{(normalised_name, TEAM_UPPER): player}` — the
  team-aware disambiguator.
- `name_conflicts` → set of normalised names shared by ≥2 players.

### `_fuzzy_match(name, idx, team_hint=None) → str | None`
The batter/bowler resolver. Four-tier strategy:
1. **Team-aware exact** — if the name appears in `name_conflicts` and a
   team hint is given, `by_name_team[(n, team)]` wins.
2. **Exact normalised name** — direct hash hit.
3. **Single-match surname** — if only one player has that surname.
4. **Token-set fuzzy ratio ≥ 0.45** — one-letter tokens (initials) are
   expanded to any matching token in the target name before scoring.
   Anything below 0.45 returns `None`.

### `_fuzzy_fielder(name, idx, bowling_team=None) → str | None`
A separate, more permissive resolver for **catchers and stumpers** (where
the team context is the bowling side, not the bat side). Three tiers:
1. Exact normalised name.
2. Single-match surname (optionally team-filtered if multiple).
3. Surname-of-last-word in a multi-word name (also team-filterable).

### `_generate_dynamic_player(name, team_code, cricbuzz_id=None) → dict`
The **resilience fallback**. When every fuzzy tier returns `None`,
the scraper synthesises a complete player row so the stats can still be
written. Decisions baked in:
- **ID strategy:** `ext_{cricbuzz_id}` when a Cricbuzz numeric player
  ID is available (zero collision risk with seeded IDs); otherwise
  `ext_{first-6-hex-of-md5-of-name}`.
- **`role = "AR"`** — chosen because the database has a
  `CHECK (role IN ('BAT','BOWL','AR','WK'))` constraint. `UNCAPPED` would
  fail; `AR` is the safest fall-through.
- **`price = 7.0`** — midrange, so adding the player doesn't break the
  100 CR budget for anyone who later drafts them.

## Key business rules it enforces

1. **Team is a tiebreaker, not a filter.** Players whose names *aren't*
   in `name_conflicts` are matched without the team hint — so "Virat
   Kohli" still resolves cleanly even if Cricbuzz puts him on the bowling
   team for an over of part-time spin.
2. **0.45 fuzzy threshold is the floor.** Below that, return `None` and
   let the caller decide (the scraper appends to `dropped_*` lists and
   logs).
3. **An unknown player is never a crash.** `_generate_dynamic_player`
   guarantees a writeable row — only a database-constraint violation
   could break the pipeline, and the schema is satisfied by construction.
4. **Dynamic IDs are clearly non-canonical.** The `ext_` prefix means
   any audit script (e.g. `Audit_Scores.ps1`) can grep dynamically-added
   players and flag them for manual reconciliation.

## Called by / Calls into

- **Called by:** `scraper.py` only ([scraper.py:44-45, 116-373, 604](../scraper.py:44)).
- **Calls into:** `hashlib`, `re`, `sqlite3` (type hint only),
  `unicodedata` from stdlib. **Zero project imports** — the package
  promise holds.

## Supports which user capabilities

From [user_capabilities.md](user_capabilities.md):

- **§3 Match Centre / §4 This Week / §6 Leaderboard** — these capabilities
  show a player's points. If `fuzzy_match` mis-attributes a name, the
  wrong player's `season_pts` increments and the wrong leaderboard row
  changes. So this file is the **invisible quality gate** for every
  scoring-related capability.
- **§8 Admin Tab** — when an admin pastes a fresh scorecard URL, the
  background scrape calls into this file. Any "couldn't find player"
  warnings end up in the server log.

## Dead Code Audit

| Symbol | Verdict |
|--------|---------|
| `_norm` | **Live.** Used by every other function in this file and imported by `scraper.py`. |
| `_generate_dynamic_player` | **Live.** Imported by `scraper.py`; called from two places in the scraper. |
| `_build_player_index` | **Live.** One caller in `scraper.py`. |
| `_fuzzy_match` | **Live.** Two callers in `scraper.py` (batter and bowler resolution). |
| `_fuzzy_fielder` | **Live.** Four callers in `scraper.py` (catches, stumpings, run-outs). |

**No dead code.** Every symbol has a real caller.

## Open Questions

1. **Two parallel resolvers exist in the codebase.** `base.resolve_player_id`
   and this file's `_fuzzy_match` solve the same problem with different
   thresholds (0.40 vs 0.45), different nickname dictionaries
   (`base._SEMANTIC_MAP` vs none here), and different tier orders.
   Tracked as [docs_audit.md item G](docs_audit.md). The right fix is
   probably to fold the nickname map into a shared module and have both
   callers import it.
2. **No nicknames here at all.** A scorecard line like `"VK"` would never
   resolve via `_fuzzy_match` (no semantic map). If Cricbuzz ever
   abbreviates a name in a scorecard JSON, the player will be auto-added
   as `ext_…` instead. Worth checking the scraper's
   `NON_BLOCKING_ERROR`/`dropped_*` logs to see if this has ever
   actually happened.
3. **Stale tag in `_norm`.** [fuzzy_match.py:36](../logic/fuzzy_match.py:36)
   includes `†` (†) in its punctuation strip-set. No idea where that
   came from; harmless but unexplained. Leave it unless you remember.
4. **Tier 4 fuzzy uses set ratio, not edit distance.** A typo like
   "Vrat Khli" (missing letters) won't be caught by token-set since the
   tokens themselves are wrong. This is by design — false positives are
   worse than false negatives here, because every false positive
   misattributes points. The current behaviour (return `None` → fallback
   to dynamic player) is the conservative choice. Worth documenting in
   the docstring.
