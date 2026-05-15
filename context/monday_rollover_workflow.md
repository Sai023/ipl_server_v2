# .github/workflows/monday_rollover.yml — Weekly Rollover Trigger

> **Phase 11 addition (2026-05-15).** Cron-driven GitHub Actions
> workflow that fires the league's weekly rollover on time, regardless
> of whether the operator's local box is online or any user has the
> site open in a browser.

## What it does (business view)

Every **Monday at 14:00 UTC** (= 16:00 SAST, 19:30 IST — the league's
deadline), this workflow POSTs to the hosted server's
`/api/rollover` endpoint with a bearer token. The hosted server
performs the rollover (`next_week` selections → `this_week`,
new week row inserted, points updated) and `_push_if_hosted` commits
the new `fantasy.db` back to git.

Before this workflow existed, rollover was triggered by a
`setTimeout` in the browser's `ipl_glue.js` ([Static/ipl_glue.js:158](../Static/ipl_glue.js:158))
that fired only when a user had the site open at 14:00 UTC. If no
one was looking, rollover happened whenever the next user logged in
— which could be hours late. The workflow makes Monday rollover
**punctual**.

The in-browser fallback is **kept** as a safety net for the case
where the workflow fails (host down, GitHub Actions outage,
Cricbuzz nuclear war). `roll_week` is idempotent (`already_rolled`
guard checks the `_last_rollover` meta key), so firing the workflow
and the browser-side timeout both is safe — only the first one does
work.

## Where it sits in the flow

```
   Monday 14:00 UTC
       │
       ├── workflow_dispatch (manual, anytime) ──┐
       │                                          │
       ├── cron '0 14 * * 1'  ────────────────────┤
       │                                          │
       │   for attempt in 1..5:                   │
       │     curl -X POST -H "Authorization: …"  │
       │       https://<HOSTED_URL>/api/rollover │
       │     if 200: exit 0                       │
       │     if 401: exit 1 (bad token)           │
       │     else: sleep 30 (wake host)           │
       │                                          │
       ▼                                          ▼
   Render service /api/rollover               In-browser setTimeout
       │                                       (fallback — same endpoint,
       │   logic.rollover_engine                no auth header)
       │   db.insert_rollover_week, set_last_rollover
       │   _push_if_hosted("rollover:wN")
       │
       └── new commit on main:  "ui: rollover:w3"
```

`concurrency: group: ipl-sync` shares the slot with
`daily_sync.yml` — a rollover and a scrape never run simultaneously,
which would otherwise race on git push.

## Inputs / Outputs

- **Inputs:**
  - Cron schedule (`'0 14 * * 1'`).
  - Manual trigger via Actions UI (`workflow_dispatch`).
  - Two GitHub Actions repo secrets (Settings → Secrets and variables
    → Actions):
    - `HOSTED_URL` — the Render service's public URL, no trailing
      slash, e.g. `https://ipl-fantasy-2026-h0m9.onrender.com`.
    - `ROLLOVER_TOKEN` — same string set as the host's env var of
      the same name.
- **Outputs:**
  - Side effect: a `POST /api/rollover` to the host. If the host
    rolls, the result is a new commit on `main` named
    `ui: rollover:w<N>` (from `_push_if_hosted`).
  - Workflow log lines (`::notice::` on success, `::error::` on
    final failure after retries).

## Key business rules it enforces

### 1. Five attempts, 30-second gaps — cold-start tolerant

Render's free tier sleeps after 15 minutes of inactivity. At 14:00
UTC Monday, the host is likely cold. The first attempt may time out
or 503; subsequent attempts after 30-second sleeps give the
container time to wake and respond. Total budget: up to ~3 minutes
of retries.

```bash
for attempt in 1 2 3 4 5; do
  HTTP_CODE=$(curl -sS -o /tmp/resp.json -w "%{http_code}" \
              --max-time 60 \
              -X POST \
              -H "Authorization: Bearer ${ROLLOVER_TOKEN}" \
              -H "Content-Type: application/json" \
              "$URL" || echo "000")
  [ "$HTTP_CODE" = "200" ] && exit 0
  [ "$HTTP_CODE" = "401" ] && { echo "::error::Token rejected"; exit 1; }
  sleep 30
done
exit 1
```

### 2. 401 bails immediately

A 401 response means `ROLLOVER_TOKEN` mismatches between the GitHub
secret and the host's env var. Retrying won't fix that — the
workflow exits 1 with a clear log line.

### 3. Other failures fall through to the in-browser fallback

If all 5 attempts fail (host genuinely down, network issue), the
workflow exits non-zero and the operator gets a red Actions run.
The next user to load the site at any point fires their browser's
own rollover timeout. `roll_week`'s idempotency guard means no
double-rollover ever occurs.

### 4. `concurrency: ipl-sync`

Shared with `daily_sync.yml`. GitHub Actions queues the second
arrival until the first finishes. Prevents two parallel pushes to
`main` racing on `fantasy.db`.

### 5. `permissions: contents: write` — wait, why?

The workflow doesn't push directly — it curls the host. But it's
defensive: if a future iteration ever needs to commit (e.g. a fallback
that runs rollover locally inside Actions when the host is down),
the permission is already there. Drop it if you decide that path
isn't worth the contingency.

## Called by / Calls into

- **Called by:**
  - GitHub Actions cron at Monday 14:00 UTC.
  - GitHub Actions manual `workflow_dispatch` trigger (operator can
    fire it on demand for testing or after a missed Monday).
- **Calls into:**
  - The hosted server's `POST /api/rollover` endpoint.
  - No project-side Python imports; the workflow is shell + curl.

## Supports which user capabilities

From [user_capabilities.md](user_capabilities.md):

- **§10.2 Auto-rollover at Monday 14:00 UTC.** Previously implemented
  client-side only; this workflow makes it server-side and
  punctuality-guaranteed. The client-side timeout remains as a
  fallback for the rare case where the workflow fails.
- **§4.2 Simulate Monday rollover.** The Dev Tools button uses the
  same `/api/rollover` endpoint. It sends `force=1` (which bypasses
  the `already_rolled` guard) but no Authorization header, so the
  host's optional-bearer logic lets it through.

## Dead Code Audit

None. The file is new in Phase 11; every step is consumed.

## Open Questions

1. **No retry on transient HTTP 5xx.** A 502/503 from Render during
   wake-up does cause a retry (because we only short-circuit on
   200 and 401). But anything else — 500, 504, even a curl exit
   code — gets the 30s sleep and retry. That's right for the
   cold-start case but means a buggy host that returns 500 forever
   wastes the full retry budget. Acceptable trade-off given how rare
   a steady-state 500 is.

2. **No notification on final failure.** If all 5 attempts fail,
   the Actions run goes red but nobody is paged. For a friends
   league this is fine — the next site visit catches it. Worth
   revisiting if the league grows.

3. **`HOSTED_URL` is environment-baked, not derived from the repo.**
   If the operator's Render URL ever changes (recreated service,
   custom domain added), they need to update the GitHub secret
   manually. Could be auto-discovered via Render's API but the
   complexity isn't worth it.
