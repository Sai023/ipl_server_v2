# .github/workflows/daily_sync.yml — The Cloud Safety-Net

## What it does (business view)

`daily_sync.yml` is the **cloud safety-net** for the league. Twice a
day on GitHub Actions, it does the **scrape-and-commit half** of the
daily refresh cycle, so the league's data stays current even when the
operator's Windows machine is genuinely off.

What it *does not* do is **discover new Cricbuzz IDs** — that has to
run on the operator's local box because Cricbuzz blocks GitHub Actions
egress IPs. The two halves are deliberately split:

| Half | Where it runs | When |
|------|----------------|------|
| **Discovery** (find new match IDs on Cricbuzz, write to `schedule.json`) | Operator's box only — `tasks.start_daily_discovery_scheduler` (APScheduler in `server.py`) | Daily at 23:55 IST |
| **Scrape + commit** (fetch scorecards for known IDs, recalc points, push data) | **This workflow** + locally via the same `tasks.run_discovery_and_scrape` | Daily at 18:30 UTC and 21:30 UTC |

The cloud half can only fetch scorecards for matches whose
`cricbuzz_id` is **already** in the committed `schedule.json`. If the
operator's machine has been offline since before the latest match's
discovery window, the cloud workflow simply skips those matches (logs
"SKIP" cleanly) — the discovery half catches them tomorrow once the
local box wakes up.

## Where it sits in the flow

```
   Operator's Windows box (must be ON for discovery)
       │
       ├── 23:55 IST   tasks.run_discovery_and_scrape()
       │                  ├── logic.cricbuzz_discovery.run_discovery()  ← updates data/schedule.json
       │                  └── scraper.run_full_scrape()                   ← fetches new scorecards
       │
       └── git push schedule.json + data/matches/*.json + fantasy.db
                                  │
                                  ▼
                          GitHub repository
                                  │
                                  ▼   (cron: 18:30 UTC and 21:30 UTC)
   GitHub Actions runner — daily_sync.yml
       ├── Step 1: checkout
       ├── Step 2: setup-python 3.11 (cached)
       ├── Step 3: pip install -r requirements.txt
       ├── Step 4: python Seed_Players.py     (idempotent — no-op if seeded)
       ├── Step 5: python Seed_Matches.py --no-live   (sync JSON→DB, skip Cricbuzz)
       ├── Step 6: python scraper.py          (pure consumer of schedule.json)
       └── Step 7: git add + commit + push    (data/schedule.json, data/matches/*.json, data/fantasy.db)
```

## Inputs / Outputs

- **Inputs:**
  - The repository's current state — `data/schedule.json`,
    `data/matches/*.json`, `data/fantasy.db`, the seed scripts.
  - GitHub's free Ubuntu runner.
- **Outputs:**
  - A commit pushed to `main` containing whatever changed during the
    run (new match JSON files, updated `schedule.json`, updated
    `fantasy.db`).
  - Console logs accessible via the GitHub Actions UI.

## Key business rules it enforces

### 1. Discovery is **never** attempted in the cloud
Step 5 (`Seed_Matches.py --no-live`) explicitly skips Cricbuzz
discovery. Step 6 (`scraper.py`) is a pure consumer of `schedule.json`
— it does not hit `/cricket-series/...` pages. Matches whose
`scorecard_url` ends in `/00000` are logged as "SKIP" and left alone.

### 2. Two cron times, post-local-discovery
- `30 18 * * *` — 18:30 UTC = midnight IST.
- `30 21 * * *` — 21:30 UTC = 03:00 IST.

Both fire **after** the local 23:55 IST = 18:25 UTC discovery. The
ordering matters: if the operator pushed an updated `schedule.json` at
~18:30 UTC, the cloud workflow picks it up at the next cron tick.

### 3. Concurrency guard prevents overlapping runs
`concurrency.group: ipl-sync` plus `cancel-in-progress: false` ensures
two scheduled fires can't run simultaneously (e.g. if the 18:30 run
overruns into the 21:30 window) — they queue.

### 4. Commit only when something changed
The push step uses `if [ -n "$(git status --porcelain)" ]` — if the
scrape produced no new files, no commit is created. Avoids noisy empty
commits.

### 5. Schedule.json is committed too
Step 7 includes `data/schedule.json` in the commit. This is FIX-022's
half of the contract: if the scraper's `_reset_url` cleared a wrong
`cricbuzz_id` during this run, the cleanup propagates back to the
repository so the next local discovery refills the slot from a clean
slate.

### 6. APScheduler installed but never started
`requirements.txt` includes APScheduler so the import in `tasks.py`
doesn't fail when `Seed_Matches.py` or `scraper.py` triggers a
transitive import chain. The workflow never **starts** the scheduler —
no `server.py` boot here.

### 7. Players seeding is fault-tolerant
Step 4 uses `python Seed_Players.py || echo "..."` so a non-zero exit
doesn't fail the whole workflow. The seeder is idempotent (wipes +
reseeds), so re-running every day is fine.

### 8. `manual: workflow_dispatch` is allowed
An operator can trigger the workflow manually from the GitHub Actions
UI without waiting for a cron — useful for catching up after a long
local offline period.

## Called by / Calls into

- **Called by:**
  - GitHub Actions cron at 18:30 UTC and 21:30 UTC.
  - GitHub Actions manual trigger (`workflow_dispatch`).
- **Calls into:**
  - The seeders, the scraper, the schedule loader, and (transitively)
    every piece of project code those entail.

## Supports which user capabilities

From [user_capabilities.md](user_capabilities.md):

- **§3 Match Centre / §6 Leaderboard / §4 This Week** — all read from
  scoring data that this workflow can refresh independently of the
  operator's box. If the box is off for a week, the cloud workflow
  still produces updated scores for any matches whose IDs were already
  in `schedule.json` when the box went down.
- **§10.2 Daily auto-sync (cloud half)** — the workflow is the
  silent partner to the local APScheduler job.

## Dead Code Audit

The workflow file is 107 lines and was rewritten in Phase 9 to match
the JSON-schedule architecture. Nothing in it is dead:

- All 7 steps are required and executed.
- The two `cron` triggers are both used.
- The `concurrency.group` is necessary to prevent overlap.
- The `git add -f` flags are needed because `data/fantasy.db` and the
  matches JSON files are not ignored but might be touched by other
  workflows.

**No dead code.**

## Open Questions

1. **`Seed_Matches.py --no-live` still triggers
   `IPL_2026_SCHEDULE = _load_schedule_tuples()`** at module import
   time. D15 in the register flags this as "dead but with cost"; in a
   CI run, the cost is one extra disk read — negligible. Worth noting
   that D15 cleanup doesn't break this workflow.
2. **The workflow commits `data/fantasy.db`.** This is intentional
   (the DB is the league's authoritative state, and the cloud runner
   needs to push updated `season_pts`/`week_pts`), but it means the
   repository's commit history bloats every day with a binary diff.
   Worth considering whether the database should instead be rebuilt
   from `data/matches/*.json` + the seed scripts on the operator's
   box — and only the JSON files committed. Trade-off: a longer cold
   start vs a leaner repo.
3. **No notification on failure.** A silently failing cron means the
   operator finds out about a Cricbuzz outage when they next check
   the league. Worth adding a Slack/email webhook on
   `if: failure()`.
4. **APScheduler in `requirements.txt` is a harmless dependency in
   CI but installs unnecessary indirect transitive dependencies.**
   Worth either splitting into `requirements.txt` (runtime) and
   `requirements-ci.txt` (subset), or accepting the bloat.
5. **The workflow runs on every push too?** No — only `cron` and
   `workflow_dispatch` triggers are declared. A push to `main` would
   not retrigger; an operator wanting a re-run after fixing
   `schedule.json` has to use the manual dispatch button.
6. **`fetch-depth: 0`** ([line 49](../.github/workflows/daily_sync.yml:49))
   fetches the full git history. Not needed for the workflow's
   actions; could be `fetch-depth: 1` to save runner time. Tiny
   optimization.
