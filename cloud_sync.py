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

import os
import shutil
import subprocess
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


def pull_latest(log=print) -> tuple[bool, str]:
    """
    Fast-forward pull. Used by /api/sync-now and the background poll loop.

    Returns (changed, message). `changed` is True iff new commits landed.
    Never raises. On any error, returns (False, "<reason>") and lets the
    caller decide what to log.
    """
    if not _is_git_repo():
        return False, "not a git repo (skipping pull)"
    try:
        r = subprocess.run(
            ["git", "pull", "--ff-only", "--quiet"],
            cwd=_BASE_DIR, capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return False, f"pull failed: {r.stderr.strip() or r.stdout.strip()}"
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

        # Push with one retry: rebase on top of remote first to absorb
        # concurrent scrape / rollover commits.
        for attempt in (1, 2):
            r = subprocess.run(
                ["git", "pull", "--rebase", "--autostash", "--quiet"],
                cwd=_BASE_DIR, capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                # Rebase conflict on a binary blob (fantasy.db) — abort and
                # keep local commit. Next loop iteration will retry.
                subprocess.run(
                    ["git", "rebase", "--abort"],
                    cwd=_BASE_DIR, capture_output=True, text=True, timeout=10,
                )
                if attempt == 2:
                    return False, f"rebase failed: {r.stderr.strip()}"
                continue

            with _temporary_remote_with_token(token) as remote_ok:
                if not remote_ok:
                    return False, "could not rewrite remote URL with token"
                p = subprocess.run(
                    ["git", "push", "--quiet"],
                    cwd=_BASE_DIR, capture_output=True, text=True, timeout=30,
                )
                if p.returncode == 0:
                    return True, "pushed"
                if attempt == 2:
                    return False, f"push failed: {p.stderr.strip()}"
                # else: loop and re-rebase

        return False, "push failed after retry"
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
