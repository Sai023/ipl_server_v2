# render.yaml — Render Blueprint for Cloud Deploy

> **Phase 11 addition (2026-05-15).** This file is read by Render's
> Blueprint feature to provision a free-tier web service that hosts
> the league publicly. Local-only operators can ignore it entirely.

## What it does (business view)

`render.yaml` is the **declarative deploy spec** for hosting the
league on Render. Connect the GitHub repo as a Blueprint, Render
reads this file, and a Python 3.11 web service comes up running
`python server.py --host 0.0.0.0 --port $PORT` with `HOSTED=true`
in the env.

The choice of Render (vs Fly.io, Heroku, etc.) was driven by:
- **Free tier** — 750h/month, enough for a friends-only league.
- **Auto-wake on incoming HTTP** — the URL stays reachable to friends
  even after the 15-minute idle sleep; cold-start adds ~20–30s to
  the first request which the project's spec deemed acceptable.
- **GitHub auto-deploy** — `autoDeploy: true` means every push to
  `main` triggers a new build. No "deploy" step in the operator's
  workflow.
- **Declarative Blueprint** — render.yaml in the repo means the
  service config is versioned, not click-trapped in a dashboard.

## Where it sits in the flow

```
   Operator pushes to main (locally or via host write-back)
              │
              ▼
   GitHub repo  (Sai023/ipl_server_v2)
              │
              │  Render watches main, autoDeploy=true
              ▼
   Render build:   pip install -r requirements.txt
              │
              ▼
   Render run:     python server.py --host 0.0.0.0 --port $PORT
                                                with HOSTED=true
              │
              ▼
   Public URL (e.g. https://ipl-fantasy-2026-h0m9.onrender.com)
```

The host platform terminates TLS, routes the public URL to the
container's `$PORT`, and handles the auto-sleep / auto-wake cycle.
The container itself just runs Flask.

## Inputs / Outputs

- **Inputs:**
  - This file (read by Render at Blueprint sync time).
  - Three env vars set manually in the Render dashboard (the first
    is a plain string, the other two are secret — marked `sync: false`
    here so the secrets aren't committed):
    - `GITHUB_REPOSITORY` = `Sai023/ipl_server_v2`. **Required** —
      `cloud_sync._repo_slug()` reads this to know which repo to
      target for `git push`, `git fetch`, and the
      `workflow_dispatch` REST call. The fallback (parse `git remote
      get-url origin`) does not work in Render's container.
    - `GITHUB_TOKEN` — fine-grained PAT with **both** `Contents:
      Read and write` AND `Actions: Read and write` on
      `Sai023/ipl_server_v2`. Contents alone is enough for
      `commit_and_push`, but `dispatch_workflow` returns 403 without
      Actions.
    - `ROLLOVER_TOKEN` — random string; same value also added as
      a GitHub Actions repo secret (`monday_rollover.yml` sends it).
- **Outputs:**
  - A running web service named `ipl-fantasy-2026` in the operator's
    Render workspace.
  - A public HTTPS URL (the suffix is randomised on first deploy and
    sticky after).

## Key business rules it enforces

### 1. `runtime: python` + `PYTHON_VERSION` env var

Render dropped the top-level `pythonVersion` field — that version of
the schema rejects it (`field pythonVersion not found in type
file.Service`). The supported way to pin the runtime version is the
`PYTHON_VERSION` env var with a full patch version (`3.11.9`, not
just `3.11`).

### 2. `healthCheckPath: /api/ping`

Render polls `/api/ping` to decide whether the container is live.
The endpoint returns a small JSON object including the public URL
and budget constants. If it 500s for more than ~90s after startup,
Render kills the container and reports the deploy as failed.

### 3. `autoDeploy: true` — every code push redeploys

This is mostly a feature but has one cost: when the host writes
back data commits via `_push_if_hosted` or when the
`monday_rollover.yml` workflow commits, those pushes ALSO trigger a
redeploy. The container restarts; the next user hits a cold start.

Worth knowing but not worth disabling for a friends league —
activity is bursty and the 30s cold-start is acceptable per the
project's stated tolerance.

### 4. `sync: false` on secrets

`GITHUB_TOKEN` and `ROLLOVER_TOKEN` are listed in `envVars:` with
`sync: false`, meaning Render expects them to be set **manually in
the dashboard**, not from this file. Two reasons:

- Secrets must not live in the repo.
- Setting them manually lets the operator rotate without touching
  source.

### 5. Render container quirks worked around in code

Three Render-specific behaviours that broke HOSTED mode until we
worked around them in `cloud_sync.py`. Listed here so a future
operator who hits the same symptoms knows where the fixes live:

| Symptom | Cause | Where fixed |
|---|---|---|
| `git pull` → "You are not currently on a branch" | Render checks out the build commit SHA in **detached HEAD** | `pull_latest()` uses explicit `origin main` / `HEAD:main` refspecs; see [cloud_sync.md](cloud_sync.md) rule #6 |
| `git fetch` → "'origin' does not appear to be a git repository" | Origin remote missing or pointing at internal Render URL | `ensure_origin_remote()` called at server boot; see [cloud_sync.md](cloud_sync.md) rule #6 |
| `git fetch` → "could not read Username for 'https://github.com'" | Private repo requires HTTPS auth for fetch too, not just push | `_git_auth_args()` injects `http.extraHeader: Authorization: Bearer <PAT>`; see [cloud_sync.md](cloud_sync.md) rule #6 |

None of these are documented by Render — they were found through
debugging the Refresh button. The Shell tab on Render's paid Starter
plan would have made them trivial to diagnose; on free tier the only
visible signal was the `pull_msg` field in `/api/sync-now` responses.

### 6. `region: oregon` — change for latency

Oregon is the default. For a league played mostly out of IST /
SAST, `singapore` would have lower latency. Free tier supports
region selection at create time; changing region later requires a
new service.

## The actual config (annotated)

```yaml
services:
  - type: web                    # vs `worker`, `cron`, `static`
    name: ipl-fantasy-2026
    runtime: python
    plan: free                   # 750h/month, sleeps after 15min idle
    region: oregon
    branch: main
    autoDeploy: true

    buildCommand: pip install --upgrade pip && pip install -r requirements.txt

    # 0.0.0.0 = Render LB reachable; $PORT injected by Render
    startCommand: python server.py --host 0.0.0.0 --port $PORT

    healthCheckPath: /api/ping

    envVars:
      - key: PYTHON_VERSION
        value: 3.11.9          # MUST be full patch version
      - key: HOSTED
        value: "true"          # the magic flag (server.py + routes.py)
      - key: PYTHONIOENCODING
        value: utf-8
      - key: PYTHONUNBUFFERED
        value: "1"             # so print() shows up in Logs immediately
      - key: GITHUB_TOKEN
        sync: false            # set in dashboard
      - key: ROLLOVER_TOKEN
        sync: false            # set in dashboard
```

## Called by / Calls into

- **Called by:** Render's Blueprint sync when the operator connects
  the repo via `New + → Blueprint`. Re-read on every `git push` if
  the file changed.
- **Calls into:** nothing project-side; everything happens at
  Render's infrastructure layer.

## Supports which user capabilities

All of them, indirectly — `render.yaml` is what makes any HOSTED
behaviour reachable to friends without the operator's local box
being on.

The local-only operators can ignore this file entirely. It does not
affect `python server.py` on Windows.

## Dead Code Audit

None. The file is new in Phase 11; every key is consumed by Render
or by the running container.

## Open Questions

1. **Region.** Oregon is far from IST. Worth re-deploying in
   `singapore` for ~200ms latency improvement on every request.

2. **Free tier sleep.** 15-min idle sleep means the first hit after
   inactivity pays a cold-start tax. If the league grows enough to
   warrant it, Render's `starter` plan (~$7/mo) removes the sleep.

3. **Disk persistence.** Render's free web services have ephemeral
   filesystems — the container is wiped on every redeploy. `fantasy.db`
   survives only because we git-push it back. If the host died and
   Render redeployed before a push made it through, the in-flight
   write is lost. Documented as a known constraint in
   [routes.md](routes.md) Phase 11 section.

4. **No Render disk add-on configured.** Could attach a persistent
   disk to `/opt/render/project/src/data/` to eliminate the
   git-as-DB pattern, but that costs money and ties the operator to
   Render specifically. Current git-based persistence is portable.
