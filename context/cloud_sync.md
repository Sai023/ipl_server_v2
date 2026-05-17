# cloud_sync.py — Git Plumbing for HOSTED Mode

> **Phase 11 addition (2026-05-15).** This module exists only when the
> server runs in HOSTED mode (Render / Codespaces / Fly.io). In local
> mode it's never imported.

## What it does (business view)

`cloud_sync.py` is the **only file allowed to shell out to `git`** in
HOSTED mode. Helpers:

| Function | What it does |
|---|---|
| `_repo_slug()` | Returns `owner/repo` from `GITHUB_REPOSITORY` env (preferred) or origin URL |
| `_authed_url(token)` | Builds `https://x-access-token:<token>@github.com/<slug>.git` — production auth path |
| `_git_env()` | Returns env dict with `GIT_TERMINAL_PROMPT=0` + `GIT_ASKPASS=/bin/echo` to prevent interactive prompts |
| `_sanitize(text, token)` | Strips token from any returned/logged string |
| `_git_auth_args(token)` | **DEPRECATED**: `-c http.extraHeader=...` (didn't work on Render — see rule #2) |
| `_ensure_identity()` | Sets bot `user.email`/`user.name` if unconfigured (Render container boots without git config) |

Public API — four high-level intentions:

1. **"Make sure `origin` is set to the canonical GitHub URL"** →
   `ensure_origin_remote()` reads `GITHUB_REPOSITORY` env and runs
   `git remote remove origin` + `git remote add origin
   https://github.com/<slug>.git`. Called at server boot because
   Render's container ships with a broken or missing origin remote.
2. **"Catch up to whatever's on remote"** → `pull_latest()` does
   `git fetch origin main` + `git reset --hard origin/main` and
   reports whether anything changed. Hard reset (not merge) is
   deliberate — see rule #7.
3. **"Persist this local DB write back to git"** →
   `commit_and_push(paths, message)` stages, commits, rebases on
   remote, and pushes — with a fine-grained PAT injected into the
   remote URL just for the push, then restored.
4. **"Ask GitHub Actions to scrape Cricbuzz on our behalf"** →
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

### 2. Auth: token embedded directly in the URL passed to fetch/push

**The auth approach in production is `_authed_url()` — token embedded
in the URL passed as the refspec argument**. Three approaches were
tried in production; only this one works on Render's free-tier
container:

| Attempt | Mechanism | Verdict |
|---|---|---|
| 1 | `_temporary_remote_with_token` (URL-rewrite of `origin` config) | Race-prone state mutation; failed silently — zero `ui:` commits in first 24h of HOSTED operation |
| 2 | `git -c http.extraHeader=Authorization: Bearer <token>` | Render's container has a default credential helper that intercepts auth BEFORE git applies the `-c` flag → silently fell back to credential helper which prompted for Username → `fatal: could not read Username for 'https://github.com'` |
| 3 (production) | **`git fetch https://x-access-token:<token>@github.com/<slug>.git main`** | Bypasses every credential helper. Same pattern `actions/checkout` uses. Verified working: passcode changes + Save Draft now produce `ui:*` commits reliably |

`_temporary_remote_with_token` is kept as DEPRECATED in the code for
git-history grep but no longer called. `_git_auth_args` (extraHeader)
is also kept as a legacy helper but production code uses `_authed_url`.

**Belt-and-suspenders hardening:**

- `GIT_TERMINAL_PROMPT=0` + `GIT_ASKPASS=/bin/echo` in subprocess env
  (`_git_env()`): any future auth issue dies immediately with a clear
  error instead of hanging or producing the misleading "could not read
  Username" message.
- `_sanitize(text, token)` strips the token from every returned error
  message and log line — defence in depth, since git itself already
  strips tokens from URLs in user-facing stderr.

### 3. Push retries with re-fetch on failure

The push sequence inside `commit_and_push`:

```
git add -f <paths>
git diff --cached --name-only  # bail if nothing staged
git commit -m "<message>"

for attempt in (1, 2, 3):
    git fetch <authed_url> main              # honors embedded auth
    git rebase FETCH_HEAD                    # local-only, no auth
    if rebase fails:                         # binary conflict on fantasy.db
        git rebase --abort; continue
    git push <authed_url> HEAD:main          # honors embedded auth
    if push succeeds: return (True, ...)
    # else: race with another writer; loop re-fetches and retries
return (False, "<step> failed after 3 tries: <sanitized stderr>")
```

The `pull --rebase` shortcut was rejected because git's internal
fetch inside pull does NOT always inherit `-c http.extraHeader` —
confirmed via production-failure stack trace. Explicit fetch + rebase
+ push lets each network step carry the token in its URL.

A binary conflict on `fantasy.db` aborts the rebase and retries on
the next loop iteration (with a fresh fetch). After 3 unresolved
attempts the function returns `(False, ...)`; the local commit is
kept and `_push_if_hosted` logs at WARN. The next successful write
picks up the local commit.

**For the symmetric problem on the workflow side** (where the workflow
holds new scrape data that diverges from a host-write that landed
during scraping), see
[daily_sync_workflow.md](daily_sync_workflow.md) §13 — the workflow
implements a complementary "re-apply on conflict" pattern that uses
the same `git reset --hard origin/main` recovery.

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

**Render-specific:** the container's `git remote get-url origin`
returned an unparseable internal URL during testing, so the env-var
path is the **only** reliable source in HOSTED mode. `GITHUB_REPOSITORY`
must be set; without it dispatch fails with `could not resolve repo slug`.

### 6. Render's container quirks and the workarounds

Render's free-tier Python service deploys a container that is *almost*
a normal git working tree but has three sharp edges. Discovered while
debugging the Refresh button:

| Quirk | Symptom | Fix in code |
|---|---|---|
| **Detached HEAD** at the build commit | `git pull --ff-only` → "You are not currently on a branch" | `pull_latest()` uses `git fetch origin main` + `git reset --hard origin/main` (no merge step). `commit_and_push()` uses explicit `origin main` / `HEAD:main` refspecs everywhere. Works in both attached and detached states |
| **`origin` remote missing or pointing at an unreachable internal URL** | `git fetch origin main` → "'origin' does not appear to be a git repository" | `ensure_origin_remote()` is called at server boot ([server.py](../server.py)). It `git remote remove origin` (ignores failure) then `git remote add origin https://github.com/<GITHUB_REPOSITORY>.git`. Idempotent, runs once per container start |
| **Private repo requires HTTPS auth for fetch, not just push** | `git fetch origin main` → "could not read Username for 'https://github.com'" | `_git_auth_args(token)` returns `["-c", "http.extraHeader=Authorization: Bearer <TOKEN>"]`. Spliced into every git command that hits the network (fetch, pull-rebase). Push still uses the URL-rewrite pattern from rule #2 — both authenticate the same PAT |

The push path uses URL rewrite (rule #2), the fetch and pull-rebase
paths use `http.extraHeader` (rule #6). Two different mechanisms in
one file is intentional: URL rewrite leaks in `git remote -v` output
but works reliably; `http.extraHeader` doesn't leak but git's HTTPS
helper sometimes ignores it for push (per GitHub Actions docs). Mixed
is empirically the most reliable combo.

### 7. `pull_latest()` is destructive by design

The function does `git fetch origin main` then `git reset --hard
origin/main` — not a merge. This wipes any local uncommitted changes.
Safe because:

- `commit_and_push()` is synchronous: every host write commits **and
  pushes** before its caller's HTTP response returns. By the time
  `pull_latest()` runs (called from `api_sync_now` or at boot), all
  in-flight host writes are on remote.
- The container's filesystem is ephemeral on Render anyway — the
  build-time `git checkout <sha>` is the only authoritative source of
  files until the next deploy, and `git reset --hard origin/main`
  rebuilds from the same source.

**Don't change this to a merge unless you also change `commit_and_push`
to use async push** — the current invariant breaks if a write is
pending push when `pull_latest` runs.

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
