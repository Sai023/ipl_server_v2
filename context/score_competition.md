# score_competition.py — competition-aware scoring runner (v1.0.0)

**Purpose.** Scores any competition (SA20 2027, a future IPL) exactly the way IPL 2026 was scored: Cricbuzz
scorecard → `scraper.process_cricbuzz_scorecard` → `db.recalculate_points` / `update_week_points` /
`update_player_season_pts` (same `logic/scoring_engine.calc_pts`). It replaces the SQLite-era `scraper.py`
*runner* (`run_full_scrape`, still IPL/SQLite only and dormant); the parser inside `scraper.py` is reused.

**Where it runs.** On the admin's PC, not on Render (Cricbuzz blocks cloud hosts). It writes straight to the
production Postgres via `DATABASE_URL` (PowerShell: `$env:DATABASE_URL = (Get-Content -Raw <file holding the connection string>).Trim()`).

    py -3.14 score_competition.py sa20_2027 [--dry-run] [--no-discover] [--force <match_id>] [--series-id 12791 --series-slug sa20-2027]

**Flow per run.** (1) discovery: fill unfilled matches' Cricbuzz links from the series page — accepts only fixtures
whose title contains the series slug AND whose two teams are both in `competitions.valid_teams`, paired Nth-to-Nth by
ascending Cricbuzz id; (2) for each match with a link, on/after match day, not already completed+scored: fetch scorecard,
`live` → mark live, `complete` → store stats (our title/teams/date/week kept), recalc points; (3) season points.

**Safety.** Scorecard teams not in the competition / not the fixture's teams → SKIPPED and reported, link never wiped
(the old IPL code reset the link). Unknown players are auto-added to *that competition's* roster (`ext_…` ids: they score
but cannot be picked). Idempotent; `--dry-run` writes nothing.

**Cricbuzz facts (2026-11-13).** SA20 2027 series = id 12791, slug `sa20-2027` (`/cricket-series/12791/sa20-2027`); its
/matches page did not list fixtures yet (it returned other series' matches — hence the title filter). Team short names on
SA20 scorecards are UNVERIFIED (expected DSG/JSK/MICT/PC/PR/SEC); if a real match is skipped for team codes, add an alias.
Roster spelling variants (e.g. Lungi/Lungisani Ngidi) would be auto-added as `ext_` duplicates and lose points — check the
first scored match's "Auto-added" lines.

**Tests.** `tests/test_score_competition.py` (pure helpers); end-to-end (throwaway Postgres schema, real IPL scorecard
#149618 as stand-in, incl. wrong-scorecard guard + idempotency) passed 2026-11-13.
