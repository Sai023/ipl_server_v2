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

### 9. Phase 11 — `force_full_rescrape` input + step 5b wipe

The HOSTED-mode Refresh button on the Render site calls
`cloud_sync.dispatch_workflow("daily_sync.yml",
inputs={"force_full_rescrape": "true"})`. To honour that, the workflow
declares a boolean input and a conditional step:

```yaml
on:
  workflow_dispatch:
    inputs:
      force_full_rescrape:
        type: boolean
        default: 'false'

# step 5b
if: ${{ github.event_name == 'workflow_dispatch'
        && inputs.force_full_rescrape == 'true' }}
run: rm -f data/matches/*.json
```

**Why this exists:** [scraper.py:615](../scraper.py:615) skips any
match whose `data/matches/match_NN.json` already exists. Without the
wipe, a workflow run after every match has been scraped at least once
produces zero changes — completed matches stay frozen at their first
scrape, even if Cricbuzz later updated the scorecard (late stats
corrections, etc.).

**Critical gotcha — string comparison, not boolean.** Type-`boolean`
workflow_dispatch inputs are serialised as the **strings** `"true"` /
`"false"` when passed via the REST API. Writing
`inputs.force_full_rescrape == true` (literal Boolean) returns
`false` → step skipped silently → workflow finishes green with no
data. Must be `== 'true'` with quotes.

**Scheduled runs skip the wipe** — the condition's `event_name`
clause excludes the cron paths. Daily scheduled scrapes are about
*new* matches; they don't need to redo old ones.

### 10. Phase 11 — pull-rebase + 3-retry push (SUPERSEDED — see §13)

Original Phase 11 design: `git pull --rebase --autostash` + 3-retry.
**Replaced by section 13's re-apply-on-conflict pattern** after a
production failure (workflow run #113 on 2026-05-17) where a host
`ui:passcode-change` landed during the scrape window and the rebase
hit a binary conflict on `fantasy.db` that git cannot resolve. The
old pattern aborted, retried twice with the same conflict, exited
1, and the scrape data was lost. See §13 for the working pattern.

### 11. Phase 11 — `git status` syntax fix

The previous version used `git status --porcelain --cached`. `--cached`
is **not** a valid `git status` flag — it exits 129 and `set -e`
killed the workflow before commit. The whole scrape silently produced
nothing. Now uses `git diff --cached --quiet`, which is the correct
primitive (exits 0 = nothing staged, exits 1 = something staged).

### 12. Next Week drafts and Phase 12 sessions are preserved across scrapes

The workflow writes to **score tables only** (`match_scores`,
`player_match_points`, `players.season_pts/points`,
`user_selections.week_pts`). It does **not** touch
`user_selections.nw_team_json` (Next Week drafts), `members`, or
`sessions` (Phase 12 auth state).

Three possible interleavings with a host write, and what happens to
each table:

| Sequence | Host write (draft / member / session) | Scrape data |
|---|---|---|
| Host pushes → workflow checkout → scrape → push (no contention) | preserved (workflow's checkout has it) | committed |
| Workflow checkout → scrape → host pushes → workflow tries push | preserved on remote | **lost** — workflow rebase-aborts on the binary `fantasy.db` conflict and exits non-zero; next cron run picks up |
| Workflow checkout → host pushes → workflow scrape → push | preserved on remote | **lost** — same rebase-abort path; workflow's pre-push pull-rebase from rule #10 hits the binary conflict, aborts, retries, then exits after 3 failures |

The third case is the one that matters for Phase 12 (it's now reachable
not only via Save Draft but also Register / Passcode Change / Admin
Reset). The rebase-abort is the **safety mechanism** — git refuses to
auto-resolve binary blob diffs, which protects host writes from being
overwritten by the workflow's stale-snapshot scrape.

**Cost:** one workflow cron run produces no commit. **Benefit:** no
host write is ever lost. The next 18:30 / 21:30 UTC run will pick up
the latest state on a fresh checkout and scrape cleanly.

For a friends league with bursty Sunday-night picks and 2 cron runs
per day, contention probability is low; sacrificing one scrape run
every few weeks is preferable to silently overwriting picks or
passcode changes.

### 13. Post-launch fix — re-apply scrape on binary conflict

§12's "acceptable trade-off" turned out to be unacceptable in
practice. After Phase 12 (passcodes) shipped, every legitimate user
action — Save Draft, passcode change, admin reset — became a
candidate to land during the workflow's scrape window and trigger
the conflict that §10 just exits 1 on. With 2 scheduled runs/day
+ manual triggers, the workflow was failing **more often than not**.

**Real failure** (run #113, 2026-05-17T16:53Z): host pushed
`ui:passcode-change:Sai` while the workflow was scraping. Workflow's
pre-push pull-rebase aborted on `data/fantasy.db` binary conflict.
Retried 3 times — same conflict. Exited 1. Scrape data dropped.

**Fix**: re-apply the scrape on top of the latest `origin/main` when
a conflict happens. Step 7 now:

```bash
# Back up scraped JSONs BEFORE first push attempt
mkdir -p /tmp/scrape-output
cp -r data/matches/*.json /tmp/scrape-output/
cp data/schedule.json /tmp/scrape-output/

push_attempt() {
  git add -f data/{schedule.json,matches/*.json,fantasy.db}
  git diff --cached --quiet && return 0  # nothing to commit
  git commit -m "data: scrape $(date)Z"
  git push origin main
}

for attempt in 1 2 3; do
  push_attempt && exit 0          # success
  [ $attempt -eq 3 ] && exit 1    # exhausted
  # Conflict path: reset to host's latest, restore our JSONs, rescrape
  git fetch origin main && git reset --hard origin/main
  cp -n /tmp/scrape-output/*.json data/matches/    # don't overwrite host's
  cp -n /tmp/scrape-output/schedule.json data/
  python Seed_Matches.py --no-live
  python scraper.py                                # uses cached JSONs, ~5s
done
```

**Why this works:**
- `cp -n` ("no-clobber") means host's newer JSONs win over ours
- `scraper.py` at [scraper.py:615](../scraper.py:615) short-circuits
  on cached `data/matches/match_NN.json` → re-run is ~5s, not 3 min
- After re-run, `fantasy.db` has host's writes (members, sessions,
  user_selections) AND our scrape data (match_scores,
  player_match_points, season_pts, week_pts)
- Both sides preserved at the application layer instead of trying
  to merge at the binary layer

**Cost:** ~5–10s extra per conflict (the re-scrape against cached
JSONs). For a friends league this is invisible.

**Edge case left documented but not fixed:** if Cricbuzz UPDATED an
already-cached match (late stats corrections), our pre-conflict
scrape would have new content for `match_NN.json` but the cached
JSON on origin/main has the old content. After reset, `cp -n` keeps
the OLD cached JSON (no-clobber), and scraper's line-615 check
skips re-fetching. The corrections are lost until someone triggers
a `force_full_rescrape` workflow run. Worth noting but rare.

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
