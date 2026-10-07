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
