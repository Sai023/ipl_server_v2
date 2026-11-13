# competition_import.py — The Roster & Fixture Checker

## What it does (business view)

Every new competition (SA20, IPL 2027, a World Cup …) needs two things before
members can pick a team: its **players with prices** and its **fixture list**.
`competition_import.py` is the checker and translator for those two files.
It takes an uploaded `players.json` and `schedule.json`, rejects anything that
would break the game — saying exactly which player or match and why — and turns
the rest into rows ready to save.

Before this module, onboarding meant running the SQLite-only `Seed_Players.py` /
`Seed_Matches.py` scripts on a laptop and committing the result to git. Since
INF-1 (the database moved to Postgres) those scripts no longer reach the live
data, and the import replaces them: an admin uploads the two files in the
Admin tab. No script, no git commit, no deploy.

Pure functions, ~380 lines, stdlib only, no I/O, no project imports.

## Where it sits in the flow

```
Admin tab → Competitions → "Import data"   (index_html: _impPick / _impCall)
   │  browser reads the chosen files, POSTs {action:"import", slug, players?, schedule?, dry_run}
   ▼
routes._admin_import                       (admin token + rate limit via api_admin_competition)
   │  parse_players / parse_schedule / cross_check   ← THIS MODULE (validation, row building)
   ▼
db.import_competition_data                 (ONE transaction; dry_run=True rolls it back)
   │  returns inserted / updated / unchanged / locked counts
   ▼
JSON change report → the panel shows it; "Import now" repeats the call with dry_run:false
```

The default is a **dry run**: the server runs exactly the statements a real
import would run and then rolls back, so the preview is the result, not a guess.

## Inputs / Outputs

### `parse_players(raw, *, budget_total=100.0, xi_size=11, valid_teams=None)`
- **In:** a list of `{id,name,team,price,role}` (legacy `[id,name,team,price,role]`
  tuples and a `{"players":[…]}` wrapper are also accepted); the competition's
  budget, XI size and team codes (if it has any).
- **Out:** `{"rows": [(id,name,team,price,role)], "errors", "warnings", "stats"}`.
  `stats` = total, per-team and per-role counts, price min/max/mean/sum and the
  cost of the XI-size priciest players.

### `parse_schedule(raw, *, slug, anchor, valid_teams=None)`
- **In:** `{"matches":[…]}` as in `data/<slug>/schedule.json` (or a bare list);
  the competition's week-1 anchor as a tz-aware datetime.
- **Out:** `{"rows": [dict], "errors", "warnings", "stats", "meta"}`. Each row has
  the minted id, week number, title, teams, date label and scorecard URL. `stats`
  has the per-week match counts, first/last match, how many have a Cricbuzz id and
  how many have teams still to be decided. `meta` carries `series_id` /
  `series_slug` / `year` from the file.

### `cross_check(player_rows, match_rows)` → warnings when the roster and the
fixtures disagree about team codes. `parse_anchor(value)` and
`week_no_for_match(date, time, anchor)` are the two helpers behind the week rule.

## Key business rules it enforces

1. **Player id `^[a-z]{1,3}\d{1,2}$`** — the same rule as `base._ID_RE`. Captain and
   vice-captain ids are validated against it on every save, so a roster id that fails
   it (e.g. `mict01`) could never be picked as captain. That is why Mumbai Indians
   Cape Town is `mct01`.
2. **Role is BAT / BOWL / AR / WK** (`players.role` CHECK). Lower case is accepted
   and normalised.
3. **Price is a number from 0 to the budget.** The roster must be able to field a
   team: at least `xi_size` players, and the `xi_size` cheapest must fit the budget
   (errors). If even the priciest XI is under budget, the budget never binds (warning).
4. **Teams belong to the competition.** If the competition already lists its teams
   (`valid_teams_json`) every player and fixture team must be one of them.
5. **Duplicate id = error; duplicate name = warning** (the id is what the game uses,
   but duplicate names confuse player matching; the IPL roster has one such quirk).
6. **Week numbers reproduce the legacy seeder exactly:** week 1 is everything
   before the competition's first rollover (`week1_anchor_utc`); each following
   7-day block is the next week (`2 + floor(days since anchor / 7)`); times are read
   as IST (the legacy field name is `time_ist`). Verified against production: all 74
   IPL 2026 matches reproduce the stored week, title, teams and date label, and the
   derived `max_week` (10) equals `competitions.max_weeks`.
7. **Teams may be unknown for some fixtures** (playoffs, "Qualifier 1"). They are
   stored with `teams_json = "[]"` exactly like the legacy seeder, with a warning.
8. **A week-1 anchor is required for a schedule** — without it week numbers are
   meaningless (the 1970 "never set" default counts as missing).
9. **Match ids are `<slug>_m<NN>`**; a real Cricbuzz id builds the scorecard URL,
   otherwise the `/00000` placeholder is used. The DAO never replaces a real URL
   with the placeholder.
10. **Size caps:** 1000 players, 400 matches, 40 listed messages (the rest are
    summarised). The HTTP body is capped app-wide at 1 MB (`MAX_CONTENT_LENGTH`).

What the DAO adds on top (`db_manager.import_competition_data`): never deletes;
updates in place and leaves `season_pts` / `points` alone; leaves live or completed
matches untouched; re-derives `competitions.max_weeks` from the match weeks; fills
`valid_teams_json`, `series_id`, `series_slug`, `year` (and `week1_anchor_utc`, only
while it is still the never-set default) when the files imply them and the
competition has none; bumps the state ETag so open browsers refresh; refuses
completed competitions.

## Called by / Calls into

- **Called by:** `routes._admin_import`, `tests/test_competition_import.py`.
- **Calls into:** nothing project-side (stdlib only).

## Supports which user capabilities

- Admin **Import data** on the Competitions card ([user_capabilities.md §8.7](user_capabilities.md)).

## Dead Code Audit

None — new module, every function is used.

## Open Questions

1. **`time_ist` is a legacy field name.** A league in another timezone must convert
   kick-off times to IST when preparing the file. A `start_utc` field would be cleaner
   once the scraper (Phase 3b-4) is made competition-aware.
2. **No pruning.** Players dropped from a roster mid-season are kept and reported
   (`not_in_file`); there is no way to delete one from the UI.
3. **Cricbuzz ids** are filled later by discovery (3b-4, still TODO); an import of a
   file without ids never wipes ids that discovery already found.
4. **`Seed_Players.py` / `Seed_Matches.py`** are superseded for onboarding (and
   SQLite-only, dormant since INF-1). Remove them once the scraper no longer imports
   from `Seed_Matches`.
5. **Tests:** `tests/test_competition_import.py` covers this module (pure logic,
   no database). The DAO and endpoint were verified against a real Postgres in a
   throwaway schema; that harness is not in the repo.
