"""Commit state changes (knowledge, learned rules) so any change is one `git revert` away."""

from __future__ import annotations

import subprocess
from pathlib import Path


def commit(directory: Path, message: str) -> bool:
    """Commit everything under `directory`. False if it isn't in a git repo or nothing changed."""
    d = str(directory)
    try:
        subprocess.run(["git", "-C", d, "rev-parse", "--is-inside-work-tree"], check=True, capture_output=True)
        subprocess.run(["git", "-C", d, "add", "."], check=True, capture_output=True)
        return subprocess.run(["git", "-C", d, "commit", "-m", message, "--", "."],
                              capture_output=True).returncode == 0
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def open_pr(directory: Path, branch: str, message: str, title: str, body: str) -> str | None:
    """Move pending changes under `directory` onto a new branch, commit, push and open
    a PR with `gh`. Returns the PR URL, or None on any failure. The caller writes the
    file changes first; this branches off the current HEAD (uncommitted changes carry
    over), then switches back. On failure the branch is left for manual repair."""
    d = str(directory)

    def g(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", d, *args], capture_output=True, text=True)

    try:
        g("rev-parse", "--is-inside-work-tree")
        previous = g("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if not previous or g("checkout", "-b", branch).returncode != 0:
            return None
        try:
            g("add", "-A", "--", ".")
            if g("commit", "-m", message).returncode != 0:
                return None
            if g("push", "-u", "origin", branch).returncode != 0:
                return None
            r = subprocess.run(["gh", "pr", "create", "--title", title, "--body", body],
                               cwd=d, capture_output=True, text=True)
            return r.stdout.strip() or None if r.returncode == 0 else None
        finally:
            g("checkout", previous)
    except FileNotFoundError:
        return None
