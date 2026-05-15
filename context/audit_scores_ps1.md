# Audit_Scores.ps1 — The Operator's "Are the Scores Right?" Tool

## What it does (business view)

`Audit_Scores.ps1` is the **operator-side validation tool**. Given a
running server on `localhost:5000`, it walks every named user's
week-by-week scoring and asks three questions for each week:

1. **Does the stored `week_pts` match what we get if we recompute from
   raw stats?** ("does the database agree with itself")
2. **Is any single match producing an unreasonably high score?**
   (`> 200 pts` is the threshold — a hint at a fuzzy-match bug or a
   duplicate scorecard).
3. **Do the totals across all weeks add up?**

If anything fails, the script prints a coloured report and an
"Recommended fix" block that suggests `Audit_Scores.ps1 -Clean
-DeleteJson`, then re-scrape.

It is **the only external consumer of `/api/audit-scores/<n>` and
`/api/clean-scores`** anywhere in the project — without this script,
both endpoints would be unreachable via the UI.

## Where it sits in the flow

```
Operator (Windows + PowerShell)
   │
   ▼
.\Audit_Scores.ps1 [-Users Sai,Moe] [-Port 5000] [-Clean] [-DeleteJson]
   │
   ├── for each user:
   │     GET http://localhost:5000/api/audit-scores/<user>
   │       └── routes.api_audit_scores (Phase 5)
   │            └── re-runs logic.scoring_engine.calc_pts per (player, match)
   │
   ├── compare stored_week_pts vs computed_week_pts per week
   ├── flag any single match with base_pts > 200
   └── if -Clean:
         POST http://localhost:5000/api/clean-scores[?delete_json=1]
            └── wipes match_scores, player_match_points, user_match_points,
                week_pts, season_pts, points; optionally deletes data/matches/*.json
```

## Inputs / Outputs

- **Inputs:**
  - `-Port <int>` — defaults to 5000.
  - `-Users <string[]>` — defaults to `Sai, Moe`.
  - `-Clean` — flag; sends `POST /api/clean-scores` after the audit.
  - `-DeleteJson` — flag; adds `?delete_json=1` to the clean call so
    cached scorecards are also wiped.
- **Outputs:**
  - Coloured console report.
  - Per-user, per-week status: `[OK]` (green) or
    `[MISMATCH stored=X computed=Y]` (red).
  - Per-player, per-match line with raw stats and the
    `base × multiplier = final` arithmetic.
  - A summary block at the end.

## Key business rules it enforces

### 1. The "match-suspicion" threshold is **200 pts**
Any single match producing `base_pts > 200` triggers a magenta
warning. The threshold is chosen because in the fantasy scoring
rules, even an unusually good performance (100 runs + 5 wickets + 4
catches) would land around 180-190 pts. 200+ usually indicates a
fuzzy-match bug, a duplicate scorecard, or a scoring rule applied
twice.

### 2. The audit is read-only by default
The default invocation (`.\Audit_Scores.ps1`) only **reads** —
calls `GET /api/audit-scores/<user>`. To clean, the operator must
explicitly pass `-Clean`.

### 3. Per-user TOTAL line is the headline check
For each user, the final line is
`{user} TOTAL: stored=X computed=Y [OK|MISMATCH]`. This is the
invariant the leaderboard depends on — if it's mismatched, the
leaderboard total is wrong.

### 4. The "Recommended fix" assumes server is running
The fix block at the bottom suggests:
1. `.\Audit_Scores.ps1 -Clean -DeleteJson`
2. `python scraper.py`
3. Restart `server.py`

That's a complete recovery sequence: wipe ephemeral data, rebuild from
scratch, restart for a clean state.

### 5. Server-must-be-running guard
Each `Invoke-RestMethod` call is wrapped in a `try/catch`. On error,
the script prints
*"Is the server running? Start with: python server.py"* — a clear
message, not a stack trace.

## Called by / Calls into

- **Called by:** an operator running it manually in PowerShell.
- **Calls into (HTTP):**
  - `GET http://localhost:{Port}/api/audit-scores/{user}` (one call
    per user).
  - `POST http://localhost:{Port}/api/clean-scores[?delete_json=1]`
    (one call total, only when `-Clean` is set).

## Supports which user capabilities

Not a user capability per se — this is an **operator** tool. But it
gates the **integrity** of every scoring-related capability:

- **§3 Match Centre / §6 Leaderboard / §4 This Week** — all rely on
  `week_pts` and the `points_per_match` blob. The audit verifies these
  are consistent with raw `match_scores` data.

## Dead Code Audit

The script is 128 lines and has no dead code:

- All parameters (`Port`, `Users`, `Clean`, `DeleteJson`) are used.
- Both `try/catch` blocks are exercised.
- The summary branch and the "issues detected" branch are both
  reachable.
- Both endpoint calls land on live routes (`/api/audit-scores` and
  `/api/clean-scores`).

**No dead code.**

The script is **also the entire justification** for keeping those two
backend routes alive after the Phase 5 cleanup. Without
`Audit_Scores.ps1`, both would have no consumer.

## Open Questions

1. **The `[OK]` vs `[MISMATCH]` check uses `-eq`.** PowerShell's `-eq`
   is fine for integers, but the audit JSON returns numbers that
   *could* be floats (e.g. `stored=632.0`, `computed=632`). Worth a
   sanity check that the server returns integers consistently — they
   should, since `week_pts` is `INTEGER` in the schema, but worth
   confirming.
2. **No `-NonInteractive` failsafe.** If the server returns a 500,
   `Invoke-RestMethod` raises; the `catch` clause prints a friendly
   message and `continue`s. If the operator runs this in a CI step,
   they get a clean failure — but a partial success (one user audited,
   another failed) currently exits with `$LASTEXITCODE = 0`. Worth an
   explicit `exit 1` when `$allGood = $false`.
3. **The threshold `200` is hardcoded.** Probably right for a fantasy
   league, but worth being a parameter (`-SuspiciousThreshold 200`).
4. **No way to audit a single week.** `-Users Sai` is supported, but
   not `-Week 3`. Adding `-Week <int>` would be a small change — the
   loop already iterates `audit.weeks`.
5. **The script's "Recommended fix" suggests destructive operations
   without confirmation.** A `.\Audit_Scores.ps1 -Clean -DeleteJson`
   wipes everything *and* deletes cached match JSON. Worth a `-Force`
   gate on `-Clean` so the operator can't muscle-memory their way
   into a data loss.
6. **No `-help` / inline usage banner** beyond the
   `<# .SYNOPSIS #>` comment block. A `--help` style print would be
   discoverable; today the operator has to `Get-Help
   .\Audit_Scores.ps1`.
