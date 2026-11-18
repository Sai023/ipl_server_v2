# score_competition.py — competition-aware scoring runner (v1.1.0)

**Purpose.** Scores any competition (SA20 2027, a future IPL) exactly the way IPL 2026 was scored: Cricbuzz
scorecard → `scraper.process_cricbuzz_scorecard` → `db.recalculate_points` / `update_week_points` /
`update_player_season_pts` (same `logic/scoring_engine.calc_pts`). It replaces the SQLite-era `scraper.py`
*runner* (`run_full_scrape`, still IPL/SQLite only and dormant); the parser inside `scraper.py` is reused.

**Where it runs.** On the admin's PC, not on Render (Cricbuzz blocks cloud hosts). It writes straight to the
production Postgres via `DATABASE_URL` (PowerShell: `$env:DATABASE_URL = (Get-Content -Raw <file holding the connection string>).Trim()`).

    py -3.14 score_competition.py sa20_2027 [--dry-run] [--no-discover] [--force <match_id>] [--series-id 12791 --series-slug sa20-2027]

**Flow per run.** (1) discovery: fill unfilled matches' Cricbuzz links from the series page. Only fixtures whose title
contains the series slug are considered (the page also lists other series). Two ways to pair one with our match:
(a) the title names two of `competitions.valid_teams` ("Sec Vs Pc 1St Match Sa20 2027"), paired Nth-to-Nth by ascending
Cricbuzz id; (b) the title is only a number ("1St Match Sa20 2027", how Cricbuzz lists SA20): the Nth match of the series
is our match N (the NN of `<slug>_mNN`, cross-checked with the ordinal in our stored title). A fixture matched by number
is stored only after Cricbuzz's own scorecard header (`matchHeader.team1/team2.shortName`, present before the match
starts) names the same two teams as our fixture: different teams → refused and reported; header without teams → stored
(the match-day check below still guards it); scorecard unreadable → left for the next run. Playoff matches (no teams in
our schedule yet) are never matched by number: paste those links in Admin → Match links. Two fixtures claiming one number
are never guessed. (2) for each match with a link, on/after match day, not already completed+scored: fetch scorecard,
`live` → mark live, `complete` → store stats (our title/teams/date/week kept), recalc points; (3) season points.

**Safety.** Scorecard teams not in the competition, or any team on the scorecard that is not one of the fixture's two →
SKIPPED and reported, link never wiped (the old IPL code reset the link). v1.1.0 tightened this: it used to need only
one team in common, which let a neighbouring match that shares a team through. Unknown players are auto-added to *that
competition's* roster (`ext_…` ids: they score but cannot be picked). Idempotent; `--dry-run` writes nothing.

**Cricbuzz facts (2026-11-13).** SA20 2027 series = id 12791, slug `sa20-2027` (`/cricket-series/12791/sa20-2027`); its
/matches page did not list fixtures yet (it returned other series' matches — hence the title filter).
**Seen 2026-11-17:** the series page lists 45–54 fixtures, only five are SA20, titled by number alone ("1St Match Sa20 2027" …
"5Th Match Sa20 2027", Cricbuzz ids 168411 / 168417 / 168427 / 168437 / 168451). Their scorecard header already names the
teams before the match: they are our m01 SEC v PC, m02 PR v JSK, m03 PC v DSG, m04 MICT v JSK, m05 DSG v SEC (all five
confirmed), and the six header short names are exactly our codes (DSG/JSK/MICT/PC/PR/SEC). The *innings-level* names that
the match-day check reads on a completed scorecard (`batTeamShortName`) are still UNVERIFIED until the first match; if a
real match is skipped for team codes, add an alias. Roster spelling variants (e.g. Lungi/Lungisani Ngidi) would be
auto-added as `ext_` duplicates and lose points — check the first scored match's "Auto-added" lines.

**Tests.** `tests/test_score_competition.py` (29 tests: pure helpers, pairing by team pair and by number, the
confirmation step with a fake Cricbuzz, the wrong-scorecard check, and `discover()` end to end with a fake database).
End-to-end (throwaway Postgres schema, real IPL scorecard #149618 as stand-in, incl. wrong-scorecard guard +
idempotency) passed 2026-11-13; re-run for v1.1.0 on 2026-11-17 against live Cricbuzz (5 of 5 SA20 fixtures matched by
number and confirmed, real write, second run a no-op, a wrong link refused with the link left unchanged).

## Status and what is still to do (2026-11-17)

**Deployed.** Shipped to `main` as app 2.14.0 (`SCRAPER_VER` 11.1, `FUZZY_MATCH_VER` 1.2.0, `CRICBUZZ_DISCOVERY_VER` 1.3.0). Render redeploys with the
code, but the runner itself is never run there: it lives in the repo, so any checkout of `main` on the admin's PC has it. The live app only imports
`fuzzy_match` for its `_SEMANTIC_MAP`; the edits to `scraper.py`, `fuzzy_match._index_players` and `resolve_series_id(slug=)` keep the old behaviour as
the default. The runner is scheduled on the owner's PC since 2026-11-18 (see "Scheduling" below). The links for SA20 matches 1-5 were stored in production on 2026-11-18 (see below); the other 29 matches have none yet.

**Match by number (v1.1.0, app 2.14.1).** The 2.14.0 rehearsal found that Cricbuzz lists SA20 fixtures with no team codes
("1St Match Sa20 2027"), so team-pair discovery linked none. Discovery now also matches the Nth Cricbuzz match to our
match N, confirmed against the teams in Cricbuzz's own scorecard header before the link is stored, and the match-day
check now needs every scorecard team to be one of the fixture's two (see Flow and Safety above). Rehearsed against live
Cricbuzz on the throwaway database: 5 of 5 fixtures matched and confirmed. What it does not do: playoff matches
(Qualifier 1, Eliminator, Qualifier 2, Final have no teams until the league ends) are linked by hand in Admin → Match
links.

**First production run (2026-11-18).** After a dry run on production in a session the database itself forced to read-only
(the five fixtures matched by number, each confirmed from Cricbuzz's header, then checked independently against our
schedule file: teams, match number, series, exact start time), the five links were stored: sa20_2027_m01..m05 ←
Cricbuzz #168411 / 168417 / 168427 / 168437 / 168451. Table fingerprints before and after differ only in `matches`, in
exactly those five rows. Undo: set those five rows' `scorecard_url` back to
`https://www.cricbuzz.com/live-cricket-scorecard/00000`. The production `sa20_2027` row has no `series_id` /
`series_slug`, so pass `--series-id 12791 --series-slug sa20-2027` on every run (or store them on the competition).
Cricbuzz's header field `year` is 0 on these fixtures: identify the series by `seriesId` 12791 / `seriesName` "SA20, 2027".

**Before and during the season (Jan 2027).**
1. Matches 1-5 are linked (2026-11-18). The scheduled full run (below) links further fixtures by itself as Cricbuzz lists them, teams-confirmed only; it logs what it linked, and a refused link raises the attention flag.
2. Check the team short names on the first real scorecards: the header names already equal our codes, the innings-level names are what the match-day check reads. A match skipped for team codes needs an alias; the link is never changed.
3. Scheduling is set up (2026-11-18, the owner's go-ahead): see "Scheduling" below. It does not alert anyone: look at `state\ATTENTION.txt` and the log after the first real match.
4. After the first scored match read the "Auto-added" lines: a roster spelling variant would be added as an `ext_` duplicate that scores but cannot be picked.
5. Every run without `--dry-run` writes to the production database. The scheduled runs do so under the owner's standing go-ahead for scheduling; any other production write (series id/slug on the competition row, the week-1 team copy, Activate) still needs a fresh yes.

## Scheduling (installed 2026-11-18)

Two per-user Windows tasks, installed by `scripts/scheduled_runner/install_tasks.ps1` (full details, check/pause/remove commands
and gotchas in `scripts/scheduled_runner/README.md`):

* `SA20 full run`: 09:00 and 13:15 daily until 1 Mar 2027 → `runner.py --mode full` (discovery + a scoring pass).
* `SA20 scoring pass`: every 30 minutes 14:00-23:00 from 17 Jan to 22 Feb 2027 → `runner.py --mode score` (scoring only).

`runner.py` is the unattended entry point (tests: `tests/test_scheduled_runner.py`): it runs `score_competition.score()` for
`sa20_2027` (series 12791 / `sa20-2027` come from its config because the production competition row has neither), without
the app's schema-init step, storing only teams-confirmed links, one run at a time, with a time limit and a redacted log. It runs
from a frozen copy of the live code in `C:\Users\<owner>\BCLFantasyRunner\app` (refresh it by re-running the installer after a
release), as the logged-on user (a standard Windows user cannot run a task whether logged on or not without a password), and
needs the PC on, awake and online. Verified 2026-11-18: both tasks started by the Task Scheduler end with result 0, and
production's table fingerprints are unchanged by them. **Two traps:** the runner folder must not be under `AppData` (the Claude
desktop app is a packaged app: its writes there are redirected to a private copy the Task Scheduler cannot see → `0x8007010B`)
nor inside OneDrive (sync/locks/dehydration); the installer refuses both.
