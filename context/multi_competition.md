# Multi-Competition Rebuild — Architecture & Changelog

> **Canonical doc** for turning the single-IPL app into a reusable
> *championship* platform (IPL, SA20, T20 World Cup, … year after year).
> This file is updated **every phase** — it is the single source of truth for
> the competition model. Per-file docs (`db_manager.md`, `routes.md`, …) are
> updated in the same commit as the code that changes them.

## Why

Re-use the app across competitions and seasons under **one site and one
login**. Each competition runs its own fantasy season; when it ends, the
winner is crowned and earns **1 championship point**. A running championship
tally (count of titles per member) is shown on the Leaderboard tab. This is
**rank-based** — never a sum of raw points across formats of different
lengths.

## The competition model

- A `competitions` table; every *game* table carries a `competition_id`.
- **Slug convention:** `<league>_<year>` → `ipl_2026`, `sa20_2026`,
  `t20wc_2026`. New competitions mint match IDs `<slug>_m<NN>`
  (e.g. `sa20_2026_m01`).
- Exactly **one** competition is normally `status='active'`. APIs and DAO
  methods default to it when no competition is named (back-compat).
- **Global** (shared across competitions): `members`, `sessions`, passcodes.
  One login everywhere; members auto-participate (rows created lazily on
  first save — no join flow).
- **Per competition:** players, teams, prices, matches, selections, scores,
  points. `players` PK is `(competition_id, id)`, so `c09` in IPL ≠ `c09`
  in SA20, each with its own team/price.
- **Championship:** an admin "Close & crown" action sets
  `champion = current leaderboard #1` (admin resolves ties), marks the
  competition `completed`, and the tally =
  `COUNT(*) of completed competitions GROUP BY champion`.
- **All T20** for now → one shared scoring profile (`scoring_engine.py`
  unchanged). A `format` column is reserved for a future ODI but unused.

## Competition lifecycle (every competition)

Every league we run — IPL each year, SA20, a T20 World Cup — follows the same
repeatable flow. **Nothing here is SA20-specific; SA20 is just the first one
through it.**

1. **Create** the competition (Admin → Competitions, or a migration row).
2. **Seed its data, well before match 1** — add `data/<slug>/players.json` +
   `schedule.json`, then run `Seed_Players --comp <slug>` and
   `Seed_Matches --comp <slug> --no-live`. `max_weeks` and each match's week
   fall out of the schedule + `week1_anchor_utc` (no hand-set week count).
3. **Activate** it — it becomes the default competition. (Members can already
   build a week-1 XI as soon as the players are seeded, even before activation:
   `save-next-week` works for `upcoming` competitions and the switcher lists
   them.)
4. **Members select before the first match.** The first rollover at
   `week1_anchor_utc` locks week-1 selections into scoring.
5. **Weekly play** — a rollover each `deadline_hour`; the scraper (Phase 3b-4)
   updates scores for the active competition.
6. **Close & crown** at the end — the winner earns 1 championship point.

So onboarding IPL 2027 or a T20 World Cup later is the same six steps as SA20 —
mostly dropping in two data files and clicking Activate.

## Schema (target shape)

`competitions(slug PK, name, format, status, budget_total, xi_size,
max_weeks, week1_anchor_utc, deadline_hour, deadline_min, series_id,
series_slug, year, valid_teams_json, champion, closed_at, created_at)`

`competition_id` added to: `players`, `matches`, `user_selections`,
`match_scores`, `player_match_points`, `user_match_points`.

PK changes (done in Phase 0): `players → (competition_id, id)`,
`user_selections → (competition_id, display_name, week_no)`. The match-child
tables keep their PKs because `match_id` is already prefix-unique.

`meta` rollover/timestamp keys are namespaced per competition —
`_saved:<slug>`, `_last_rollover:<slug>` — with the legacy bare key read as a
fallback (true until the first namespaced write for that competition).

## Conventions / gotchas (the nitty-gritty)

- **Legacy IPL26 keeps its `ipl26_m*` match-ID strings** (no PK/FK rewrite —
  needless risk across four tables); only its `competition_id` is set to
  `ipl_2026`. New competitions use `<slug>_m<NN>`. The match-number parser
  only reads the trailing digits, so the cosmetic mismatch is harmless.
- **`week1_anchor_utc` is stored in UTC**, fixing the old IST/UTC week-anchor
  mismatch ("S9") rather than reproducing it per league.
- **Per-competition data dirs** (Phase 3): `data/<slug>/schedule.json` +
  `data/<slug>/matches/`. Bare `match_NN.json` filenames would otherwise
  collide across competitions.
- The Phase-12 trust model is unchanged: passcode/admin endpoints are
  token-gated; other writes still trust `?user=`.

## Working setup

- Branch **`multi-comp`** lives in a git worktree at
  `../ipl_server_v2_multicomp` (off `origin/main`). The finished IPL26 site
  stays on **`main`** (the Render deploy) until we deliberately cut over.
- `migrate_to_competitions.py` is the one-time Phase 0 migration (idempotent
  guard: refuses to run if `competitions` already exists). Keep it — it is
  also how the production DB gets migrated at cutover.
- Pre-migration DB backup: `../fantasy.db.premigration-*` (git on
  `origin/main` is the real backup).

## Phase changelog

- **[DONE] Phase 0** (`cca806f`): `competitions` table + `competition_id`
  backfill; IPL26 frozen as competition #1 (`status='active'` until crowned
  in Phase 5). Parity verified (counts, points sums, real `_LEADERBOARD_SQL`
  output identical pre/post). IPL26 champion-to-be: **Sai** (10367).
- **[DONE] Phase 1** (`5c80eb8`): `competition_id` threaded through the whole
  `db_manager` DAO, defaulting to the active competition; `_LEADERBOARD_SQL`
  scoped by a `:comp` param (fan-out-proof structure preserved); fixed the
  `ON CONFLICT` targets broken by the new PKs; new competition DAO. Verified:
  parity holds, a 2nd competition is fully isolated, import chain healthy.
- **[DONE] Phase 2** (`5f04746`, `9d182bc`, `+writes`) — `?comp=<slug>`
  (default active) threaded through all read **and** write endpoints; new
  `GET /api/competitions`; per-competition `budget/xi/max_weeks/deadline` come
  from the row via `_comp_cfg()` (config/base constants demoted to fallbacks);
  the rollover handler uses the competition's own `deadline_hour`/`min` and
  namespaced `_last_rollover:<slug>`. Verified via Flask test client incl. a
  full points-pipeline recalc (leaderboard unchanged: Sai 10367 / Moe 9262 /
  Buddy 1941) and a season-complete rollover. `routes.md` updated.
  **Deferred to Phase 5:** `POST /api/admin/competition`
  (create/activate/close&crown) — built alongside the crown logic.
- **[WIP] Phase 3** — **3a DONE**: IPL26 data moved to
  `data/ipl_2026/{schedule.json,matches/}`; `server.py` boot (cold-hydrate +
  rebuild) and `db_manager` hydrate/rebuild defaults are now per-competition;
  `_rebuild_scores_and_points` wipes only **active** competitions (completed
  seasons keep their data + JSON archive, viewable without Cricbuzz); routes'
  cache-wipe paths per-competition. Verified: per-comp hydrate re-ingests from
  `data/ipl_2026/matches/`. **3b-1 DONE:** removed the one-time history-seed
  bootstrap from `init_db.py`. **3b-2 DONE:** `Seed_Players` is data-driven —
  roster in `data/<slug>/players.json`, `--comp`, scoped by `competition_id`.
  **3b-3 DONE:** `Seed_Matches` per-competition — syncs `data/<slug>/
  schedule.json`, mints `<slug>_m<NN>`, week-numbers from the competition row's
  `week1_anchor_utc`. Together these complete the **data-onboarding** plumbing
  (add a competition's players + matches as data files). **3b-4 TODO:**
  `scraper.py` + `tasks.py` + `cricbuzz_discovery.py` for **live** per-
  competition scraping — the fragile Cricbuzz engine, only fully verifiable
  against a LIVE competition, so best built as SA20 approaches.
  **Design note for 3b-4:** the frozen `ipl_2026` keeps legacy `ipl26_m*` match
  ids while its slug is `ipl_2026`. Before any re-seed/scrape of it, add a
  `match_prefix` to the competitions row (`ipl_2026 -> "ipl26"`, new comps ->
  slug) so minting `<prefix>_m<NN>` never creates duplicate `ipl_2026_m*` rows.
  *Per-file docs (`server.md`, `scraper.md`, `seed_*.md`, `tasks.md`,
  `init_db.md`, `cricbuzz_discovery.md`) refreshed at Phase 3 completion.*
  Note: `data/ipl_2026/matches/match_12.json` is absent — a **pre-existing**
  archive gap (the DB has all 74 matches; HOSTED boot trusts the DB, not the
  JSON, so the live site is unaffected).
- **[DONE] Phase 4** (`0eb56f0`, `5ac241b`, `6d9903b`) — frontend: IplComp
  store + `?comp=` injection at `_fetchJson` (and on the raw bootstrap/refresh
  fetches); header competition `<select>` (shown with ≥2 competitions) wired to
  `_switchComp` (re-bootstraps); dynamic page title = active competition name;
  🏆 Championship tally card above the leaderboard. **Verified live** against a
  throwaway DB copy (switcher IPL 2026 / SA20 demo, championship "Sai: 1",
  switching re-scopes the leaderboard; inline script passes `node --check`).
  *Per-file docs `index_html.md` / `ipl_glue.md` / `user_capabilities.md`
  batched with Phase 5's UI.*
- **[DONE] Phase 5** (`4373946`, `b299abc`) — crown applied: user-confirmed
  `ipl_2026` champion = Sai (status=completed), the first championship point
  ("Sai: 1"). Admin lifecycle shipped: `POST /api/admin/competition`
  (create / activate / reopen / close — admin-gated; close crowns the
  leaderboard #1 or an explicit champion, returns 409 on a tie) + an Admin
  "Competitions" card (Activate / Close & crown buttons + a create form) so
  future competitions are managed from the app. Verified via test client +
  offline Node render of the card + `node --check`. *Per-file docs pending the
  consolidation pass.*
- **[TODO] Phase 6 (data-only)** — onboard `sa20_2026` **well before match 1**
  so members can pick: create it (Admin → Competitions, or a migration), add
  `data/sa20_2026/players.json` + `schedule.json`, run
  `Seed_Players --comp sa20_2026` + `Seed_Matches --comp sa20_2026 --no-live`,
  then **Activate** it (becomes default; picks open). The first rollover at its
  `week1_anchor_utc` locks the week-1 selections. Cutover: point the live deploy
  at `multi-comp`; IPL26 stays viewable as a `completed` competition.
  *Refresh `README.md` + the ipl-fantasy-sync skill.*

> **Onboarding/selection invariant:** `save-next-week` works regardless of
> status and the switcher lists `upcoming` competitions, so members can build
> their week-1 XI as soon as a competition's players are seeded — before it is
> activated or its first match is played.
