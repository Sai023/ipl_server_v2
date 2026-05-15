# cloud_sync.py — Git Plumbing for HOSTED Mode

> **Phase 11 addition (2026-05-15).** This module exists only when the
> server runs in HOSTED mode (Render / Codespaces / Fly.io). In local
> mode it's never imported.

## What it does (business view)

`cloud_sync.py` is the **only file allowed to shell out to `git`** in
HOSTED mode. It turns three high-level intentions into safe git
plumbing with timeouts and retry:

1. **"Catch up to whatever's on remote"** → `pull_latest()` does
   `git pull --ff-only` and reports whether anything changed.
2. **"Persist this local DB write back to git"** →
   `commit_and_push(paths, message)` stages, commits, rebases on
   remote, and pushes — with a fine-grained PAT injected into the
   remote URL just for the push, then restored.
3. **"Ask GitHub Actions to scrape Cricbuzz on our behalf"** →
   `dispatch_workflow(filename, ref, inputs)` POSTs to the
   `workflow_dispatch` REST API. Used because Render (and any other
   Azure-hosted runner) can't reach Cricbuzz egress.

The module **never raises**. Every function returns a `(ok: bool,
msg: str)` tuple. Callers decide whether to surface failures to the
user — `routes._push_if_hosted` logs and swallows; `routes.api_sync_now`
forwards the message to the response body so DevTools can show it.

## Where it sits in the flow

```
                routes.py
                   │
                   │   _push_if_hosted()      api_sync_now()      api_update_match_url()
                   │      │                       │                     │
                   ▼      ▼                       ▼                     ▼
                cloud_sync.commit_and_push   .pull_latest             .dispatch_workflow
                                             .dispatch_workflow
                                                                          │
                                                                          ▼
                                                                  GitHub Actions REST API
                                                                  POST /repos/:slug/actions/
                                                                       workflows/:file/dispatches
```

Server.py also imports it lazily inside the `__main__` block to do a
boot-time `pull_latest()` so a fresh Render container starts on the
latest committed `fantasy.db` rather than the build-time snapshot.

## Inputs / Outputs

- **Inputs:**
  - The `GITHUB_TOKEN` env var (fine-grained PAT with `contents: write`
    + `actions: write` on the target repo). Also accepts `GH_TOKEN`
    as a fallback name.
  - Optional `GITHUB_REPOSITORY` env var (`owner/repo`) — if unset,
    the slug is parsed from `git remote get-url origin`.
  - The on-disk git working tree at the project root.
- **Outputs:**
  - Side effects: `git pull`, `git commit`, `git push`, and an HTTP
    POST to GitHub's REST API.
  - Returns: `(ok: bool, message: str)` from every public function.

## Key business rules it enforces

### 1. Never raise — always return `(ok, msg)`

Every public function is wrapped in try/except. A subprocess timeout,
network error, missing token, missing remote, or unexpected git
behaviour produces `(False, "<reason>")`, not an unhandled exception.
The caller decides whether to retry, log, or surface to the user.

This rule matters because these functions are called inline in
request handlers (`api_save_next_week`, `api_sync_now`, etc.). An
uncaught exception there returns a 500 to the user even though the
DB write itself succeeded.

### 2. Token never lives in the remote URL except during push

`commit_and_push` uses a context manager `_temporary_remote_with_token`
that:
1. Captures the original `origin` URL.
2. Rewrites it to `https://x-access-token:<TOKEN>@github.com/...`.
3. Runs `git push`.
4. Restores the original URL on exit, even on exception.

This keeps the token out of `git remote -v` for the bulk of the
container's lifetime. The PAT only appears in the FD's argument list
for the duration of one `git push` invocation.

### 3. Push retries on rebase failure

The push sequence inside `commit_and_push`:

```
git add -f <paths>
git diff --cached --name-only  # bail if nothing staged
git commit -m "<message>"
for attempt in (1, 2):
    git pull --rebase --autostash
    if rebase fails:
        git rebase --abort; continue
    git push
    if push succeeds: return (True, "pushed")
return (False, "push failed after retry")
```

A binary conflict on `fantasy.db` aborts the rebase and bails — git
can't merge SQLite blobs. The local commit is kept; the next
successful sync (workflow or future user write) picks it up.

### 4. `dispatch_workflow` passes inputs as strings

GitHub's `workflow_dispatch` REST API requires the body's `inputs`
values to be **strings** even when the workflow declares `type: boolean`.
Pass `{"force_full_rescrape": "true"}`, not `True`. The workflow's
own `if:` condition must also string-compare (`== 'true'`) — see
[daily_sync_workflow.md](daily_sync_workflow.md) §9.

### 5. Repo slug resolution prefers env var

`_repo_slug()` tries `GITHUB_REPOSITORY` first, falls back to parsing
`git remote get-url origin`. Both `https://github.com/<slug>.git` and
`git@github.com:<slug>.git` formats are supported. Returns `None` if
neither path works — the caller fails the operation with a clear
message.

## Called by / Calls into

- **Called by:**
  - `server.py` (boot-time `pull_latest()` when `IS_HOSTED`).
  - `routes._push_if_hosted` → `commit_and_push` (every write
    endpoint in HOSTED mode).
  - `routes.api_sync_now` → `pull_latest` + `dispatch_workflow`.
  - `routes.api_update_match_url` → `dispatch_workflow`.
- **Calls into:**
  - stdlib `subprocess`, `urllib.request`, `urllib.error`, `json`,
    `os`, `shutil`, `pathlib`.
  - External: the `git` binary on PATH; the GitHub REST API at
    `https://api.github.com/`.
  - **Not** the project's own modules — `cloud_sync` is leaf, zero
    project imports.

## Supports which user capabilities

From [user_capabilities.md](user_capabilities.md):

- **§5.3 Save the draft** — `_push_if_hosted("save-next-week:...")`
  via `commit_and_push`.
- **§4.2 Simulate Monday rollover** — `_push_if_hosted("rollover:...")`.
- **§8.2 Paste / fix a Cricbuzz scorecard URL** — both
  `_push_if_hosted("admin-url:...")` and
  `dispatch_workflow("daily_sync.yml")`.
- **§9.1 Refresh button** — `pull_latest()` then
  `dispatch_workflow("daily_sync.yml",
  inputs={"force_full_rescrape": "true"})`.

## Dead Code Audit

None. The module is new in Phase 11 and every function has at least
one live caller.

## Open Questions

1. **PAT rotation.** Fine-grained PATs expire (default 90 days). The
   module surfaces nothing about expiry; a stale token just returns
   401 from `dispatch_workflow` and silent push failures from
   `commit_and_push`. Worth a startup check that does a no-op
   authenticated GitHub API call and logs the token's `X-OAuth-Scopes`
   plus expiry headers.

2. **No exponential back-off on dispatch failure.** If GitHub's API
   is briefly unavailable, the request times out (~10s) and returns
   `(False, "network error: …")`. We don't retry. For Refresh
   button this is OK (user can click again); for boot-time pull it's
   also OK (next user action triggers another pull). Worth revisiting
   if reliability becomes an issue.

3. **`commit_and_push` is synchronous in request handlers.** A push
   that takes 5–10 seconds blocks the user's `POST` response. Async
   would be nicer UX but loses the durability guarantee (Render
   container can be torn down between the local DB write and the
   background push, losing the change).
