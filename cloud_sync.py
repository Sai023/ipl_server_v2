"""
IPL Fantasy 2026 — Cloud Sync Helper                        cloud_sync v1.0.0
===========================================================================
Phase 2: git operations for HOSTED mode (Render / Fly.io / Codespaces).

In HOSTED mode the cloud host cannot scrape Cricbuzz (Azure egress is
blocked). The host's job is to serve the latest data that was committed
to the repo by:
  - the operator's local box (discovery + scrape via APScheduler)
  - the daily_sync.yml workflow (cloud scrape safety-net)
  - the monday_rollover.yml workflow (Phase 3)

This module wraps the git plumbing — pull, push, commit — with the
retry / autostash / conflict handling that the rest of the app needs.

Design rules:
  - All functions must return quickly (timeouts on every subprocess call).
  - Never raise — always return a status tuple (ok: bool, message: str).
    Callers decide whether to surface the failure to the user.
  - Never destructive — `git pull --ff-only` only; conflicts are reported,
    not auto-merged. Phase 4's write helper uses --rebase --autostash but
    falls back to local-only write on failure.
  - Skipped gracefully if git is not installed or the working dir is not
    a git repo. The same code path must boot cleanly in dev/test setups.
"""

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

# Imported lazily to avoid a circular dep with base.py
_BASE_DIR = Path(__file__).resolve().parent


def _git_available() -> bool:
    return shutil.which("git") is not None


def _is_git_repo() -> bool:
    if not _git_available():
        return False
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=_BASE_DIR, capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0 and r.stdout.strip() == "true"
    except Exception:
        return False


def _git_auth_args(token: str | None) -> list[str]:
    """
    Return `-c http.extraHeader=Authorization: Bearer <token>` args.

    Use case: private repos require auth for `git fetch` AND `git push`.
    Embedding the token in the remote URL works but leaks in `git remote -v`
    output. The `http.extraHeader` config option is per-command, doesn't
    persist in any config file, and is the standard way GitHub's own
    Actions runner injects credentials.

    Returns [] if no token — caller can splat without conditionals.
    """
    if not token:
        return []
    return ["-c", f"http.extraHeader=Authorization: Bearer {token}"]


def ensure_origin_remote() -> tuple[bool, str]:
    """
    Render's runtime container has a working .git directory (the build
    clones the repo) but the `origin` remote is sometimes missing or
    points at an internal Render URL we can't push to. Result: every
    `git fetch` / `git push` fails with "'origin' does not appear to be
    a git repository".

    Idempotently set `origin` to the canonical GitHub HTTPS URL based on
    GITHUB_REPOSITORY (preferred) or the existing remote if we can find
    one. Called at server boot before any cloud_sync operations.

    Returns (ok, message). Never raises.
    """
    if not _git_available():
        return False, "git binary not on PATH"

    slug = _repo_slug()
    if not slug:
        return False, "no GITHUB_REPOSITORY env var; cannot infer origin URL"

    url = f"https://github.com/{slug}.git"

    # remove + add is the most reliable idempotent path. If origin doesn't
    # exist, the remove silently fails (returncode 128, we ignore). If it
    # does exist, this resets it to the canonical URL.
    try:
        subprocess.run(
            ["git", "remote", "remove", "origin"],
            cwd=_BASE_DIR, capture_output=True, text=True, timeout=5,
        )
        r = subprocess.run(
            ["git", "remote", "add", "origin", url],
            cwd=_BASE_DIR, capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return False, f"remote add failed: {r.stderr.strip()}"
        return True, f"origin set to {url}"
    except Exception as e:
        return False, f"ensure_origin error: {e}"


def pull_latest(log=print) -> tuple[bool, str]:
    """
    Fast-forward pull. Used by /api/sync-now and the background poll loop.

    Returns (changed, message). `changed` is True iff new commits landed.
    Never raises. On any error, returns (False, "<reason>") and lets the
    caller decide what to log.

    NOTE — explicit `origin main` refspec is required. Render checks out
    the build SHA in detached HEAD state, so a bare `git pull` fails
    with "You are not currently on a branch". Naming the remote and
    branch sidesteps the attached-branch requirement.
    """
    if not _is_git_repo():
        return False, "not a git repo (skipping pull)"
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    auth = _git_auth_args(token)
    try:
        r = subprocess.run(
            ["git", *auth, "fetch", "origin", "main", "--quiet"],
            cwd=_BASE_DIR, capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return False, f"fetch failed: {r.stderr.strip() or r.stdout.strip()}"
        # Reset working tree to origin/main. Safe at runtime because the
        # container has no local file changes — fantasy.db writes go via
        # commit_and_push which commits + pushes synchronously before
        # returning, so by the time we get here all work is on remote.
        r = subprocess.run(
            ["git", "reset", "--hard", "origin/main", "--quiet"],
            cwd=_BASE_DIR, capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return False, f"reset failed: {r.stderr.strip() or r.stdout.strip()}"
        # Detect whether any files changed. If `git pull` was a no-op, stdout
        # is empty. If it fast-forwarded, stdout contains the range.
        msg = (r.stdout + r.stderr).strip()
        changed = bool(msg)  # any output means new commits landed
        return changed, msg or "already up to date"
    except subprocess.TimeoutExpired:
        return False, "pull timed out after 30s"
    except Exception as e:
        return False, f"pull error: {e}"


def commit_and_push(paths: list[str], message: str, log=print) -> tuple[bool, str]:
    """
    Phase 4 write-back: stage `paths`, commit, push. Used by the host
    after user writes (save_next_week, save_member) and by the rollover
    endpoint.

    Strategy:
      1. git add <paths>
      2. If nothing staged, return (True, "nothing to commit")
      3. git commit -m <message>
      4. git pull --rebase --autostash  (catch up to remote)
      5. git push  (retry once on failure after another rebase)

    A fine-grained PAT must be in the GITHUB_TOKEN env var with `contents:
    write` on the target repo. The remote URL is rewritten in-process to
    include the token so the push authenticates without prompting.

    Returns (ok, message). On failure the local commit is kept — the next
    successful sync will push it.
    """
    if not _is_git_repo():
        return False, "not a git repo (skipping push)"

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        return False, "GITHUB_TOKEN not set — cannot push from host"

    try:
        # Stage. `git add -f` so .gitignore'd files (fantasy.db) are included.
        subprocess.run(
            ["git", "add", "-f", *paths],
            cwd=_BASE_DIR, capture_output=True, text=True, timeout=15,
        )

        # Anything staged?
        st = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=_BASE_DIR, capture_output=True, text=True, timeout=10,
        )
        if not st.stdout.strip():
            return True, "nothing to commit"

        # Identity (Render containers boot without git config)
        _ensure_identity()

        # Commit
        c = subprocess.run(
            ["git", "commit", "-m", message, "--quiet"],
            cwd=_BASE_DIR, capture_output=True, text=True, timeout=15,
        )
        if c.returncode != 0:
            return False, f"commit failed: {c.stderr.strip() or c.stdout.strip()}"

        # Push with retry. NEVER use `git pull --rebase` here — git's pull
        # runs `fetch` as an internal subprocess in a way that does NOT
        # always inherit the `-c http.extraHeader=...` auth flag, which
        # caused every push to fail with "fatal: could not read Username
        # for 'https://github.com'" on Render's container.
        #
        # Working pattern (mirrors pull_latest, which is verified):
        #   1. git fetch origin main         (single HTTP request, honors -c)
        #   2. git rebase FETCH_HEAD         (local-only, no network/auth)
        #   3. git push origin HEAD:main     (single HTTP request, honors -c)
        #
        # Explicit `origin main` / `HEAD:main` refspecs are required
        # because Render checks out the build SHA in detached HEAD.
        auth = _git_auth_args(token)
        last_err = ""
        for attempt in (1, 2, 3):
            # 1. Fetch the latest remote main with auth.
            r = subprocess.run(
                ["git", *auth, "fetch", "origin", "main", "--quiet"],
                cwd=_BASE_DIR, capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                last_err = (r.stderr.strip() or r.stdout.strip()
                            or f"fetch exit {r.returncode}")
                if attempt == 3:
                    return False, f"fetch failed after 3 tries: {last_err}"
                continue

            # 2. Rebase our local commit on top of FETCH_HEAD. Pure local;
            # no network, no auth. On binary conflict (fantasy.db diverged
            # in both directions), abort and retry the loop.
            r = subprocess.run(
                ["git", "rebase", "FETCH_HEAD", "--quiet"],
                cwd=_BASE_DIR, capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                subprocess.run(
                    ["git", "rebase", "--abort"],
                    cwd=_BASE_DIR, capture_output=True, text=True, timeout=10,
                )
                last_err = (r.stderr.strip() or r.stdout.strip()
                            or f"rebase exit {r.returncode}")
                if attempt == 3:
                    return False, f"rebase failed after 3 tries: {last_err}"
                continue

            # 3. Push with auth.
            p = subprocess.run(
                ["git", *auth, "push", "--quiet", "origin", "HEAD:main"],
                cwd=_BASE_DIR, capture_output=True, text=True, timeout=30,
            )
            if p.returncode == 0:
                return True, f"pushed on attempt {attempt}"
            last_err = (p.stderr.strip() or p.stdout.strip()
                        or f"push exit {p.returncode}")
            if attempt == 3:
                return False, f"push failed after 3 tries: {last_err}"
            # else: someone pushed between our fetch and our push — loop
            # and re-fetch on next attempt to absorb their commit.

        return False, f"push exhausted retries; last error: {last_err}"
    except subprocess.TimeoutExpired:
        return False, "commit/push timed out"
    except Exception as e:
        return False, f"commit/push error: {e}"


def _ensure_identity() -> None:
    """Set a bot identity if none configured — required on fresh containers."""
    try:
        r = subprocess.run(
            ["git", "config", "user.email"],
            cwd=_BASE_DIR, capture_output=True, text=True, timeout=5,
        )
        if not r.stdout.strip():
            subprocess.run(
                ["git", "config", "user.email", "ipl-fantasy-host@example.local"],
                cwd=_BASE_DIR, capture_output=True, text=True, timeout=5,
            )
            subprocess.run(
                ["git", "config", "user.name", "IPL Fantasy Host"],
                cwd=_BASE_DIR, capture_output=True, text=True, timeout=5,
            )
    except Exception:
        pass


def _repo_slug() -> str | None:
    """
    Resolve `owner/repo` for the GitHub API. Tries (in order):
      1. GITHUB_REPOSITORY env var (set automatically in GitHub Actions; can
         also be set manually in Render env if origin-parsing ever drifts)
      2. Parse from `git remote get-url origin`
    Returns None if neither path works.
    """
    slug = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if slug and "/" in slug:
        return slug
    try:
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=_BASE_DIR, capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        url = r.stdout.strip()
        # https://github.com/Sai023/ipl_server_v2.git → Sai023/ipl_server_v2
        # git@github.com:Sai023/ipl_server_v2.git    → Sai023/ipl_server_v2
        for sep in ("github.com/", "github.com:"):
            if sep in url:
                tail = url.split(sep, 1)[1]
                return tail.removesuffix(".git").strip("/")
    except Exception:
        pass
    return None


def dispatch_workflow(workflow_filename: str, ref: str = "main",
                      inputs: dict | None = None,
                      log=print) -> tuple[bool, str]:
    """
    POST to GitHub's workflow_dispatch endpoint to trigger a workflow run.

    Used by /api/sync-now and /api/update-match-url in HOSTED mode so the
    user-facing buttons actually cause fresh scrape data to be produced.
    Render can't reach Cricbuzz directly, but the GitHub Actions runner
    can fetch scorecards for known cricbuzz_ids — so we ask Actions to do
    it on our behalf.

    `inputs` maps the workflow's declared `inputs:` to runtime values. Pass
    {"force_full_rescrape": "true"} to make daily_sync.yml wipe the
    match-JSON cache and fully re-scrape every completed match. Strings,
    not booleans — GitHub's dispatch API requires string-typed inputs even
    when the workflow declares `type: boolean`.

    Requires GITHUB_TOKEN with **actions: write** scope (in addition to
    contents: write for the push path). If the token lacks the scope,
    GitHub returns 403 and we surface that to the caller.

    Returns (ok, message). Never raises.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        return False, "GITHUB_TOKEN not set"
    slug = _repo_slug()
    if not slug:
        return False, "could not resolve repo slug (set GITHUB_REPOSITORY env)"

    url = f"https://api.github.com/repos/{slug}/actions/workflows/{workflow_filename}/dispatches"
    payload: dict = {"ref": ref}
    if inputs:
        payload["inputs"] = inputs
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept":        "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type":  "application/json",
            "User-Agent":    "ipl-fantasy-host/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            # 204 No Content = success
            if 200 <= resp.status < 300:
                return True, f"dispatched {workflow_filename}"
            return False, f"unexpected status {resp.status}"
    except urllib.error.HTTPError as e:
        # 403 = PAT missing actions:write. 404 = workflow filename wrong
        # or token can't see the repo. 422 = workflow doesn't have
        # workflow_dispatch trigger.
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            body = ""
        return False, f"HTTP {e.code}: {body}"
    except urllib.error.URLError as e:
        return False, f"network error: {e.reason}"
    except Exception as e:
        return False, f"dispatch error: {e}"


# DEPRECATED — kept only for git history grepping. No live callers since
# the post-launch refactor of `commit_and_push` to use `http.extraHeader`
# for push (same as fetch). URL-rewrite auth proved to fail silently in
# Render's container; the header-based path is the proven working one.
# Safe to delete in a future cleanup.
class _temporary_remote_with_token:
    """
    Context manager that swaps the `origin` URL to include the PAT, then
    restores the original. Keeps the token out of `git remote -v` for the
    bulk of the container's lifetime.
    """
    def __init__(self, token: str):
        self.token = token
        self.original = None

    def __enter__(self) -> bool:
        try:
            r = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=_BASE_DIR, capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0:
                return False
            self.original = r.stdout.strip()
            if self.original.startswith("https://") and "@" not in self.original:
                # https://github.com/Sai023/ipl_server_v2.git
                # -> https://x-access-token:<TOKEN>@github.com/Sai023/ipl_server_v2.git
                authed = self.original.replace(
                    "https://",
                    f"https://x-access-token:{self.token}@",
                    1,
                )
                subprocess.run(
                    ["git", "remote", "set-url", "origin", authed],
                    cwd=_BASE_DIR, capture_output=True, text=True, timeout=5,
                )
            return True
        except Exception:
            return False

    def __exit__(self, exc_type, exc, tb):
        if self.original:
            try:
                subprocess.run(
                    ["git", "remote", "set-url", "origin", self.original],
                    cwd=_BASE_DIR, capture_output=True, text=True, timeout=5,
                )
            except Exception:
                pass
