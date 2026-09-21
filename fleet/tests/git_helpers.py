"""Build small real git repositories for the history tests.

Real repos rather than fixtures: the history layer's claims are claims about
git's own behaviour (what `--first-parent` skips, what a squash leaves
behind, what a rebase rewrites), and a fixture could only restate the
assumption being tested.

Commit dates are always explicit so window filtering is deterministic, and
`HOME` is pinned to the repo so a developer's own `~/.gitconfig` — a
`commit.gpgsign`, a default branch name, a template — can't change what a
test builds.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_IDENTITY = {
    "GIT_AUTHOR_NAME": "Fleet Test",
    "GIT_AUTHOR_EMAIL": "test@fleet.invalid",
    "GIT_COMMITTER_NAME": "Fleet Test",
    "GIT_COMMITTER_EMAIL": "test@fleet.invalid",
}


def git(repo: Path, *args: str, date: str | None = None) -> str:
    """Run one git command in `repo`, returning its stdout."""
    env = dict(_IDENTITY)
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env={**env, "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo)},
    )
    return result.stdout.strip()


def init_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "--quiet", "--initial-branch=main")
    return root


def commit(repo: Path, filename: str, content: str, message: str, *, date: str) -> str:
    """Write a file, commit it, and return the new commit's SHA.

    Creates parent directories, so a realistic path like `src/pkg/thing.py`
    works — which matters because the patch gates classify files by path,
    and a bare `a.txt` is a *doc*, not source.
    """
    target = repo / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    git(repo, "add", filename)
    git(repo, "commit", "-m", message, date=date)
    return git(repo, "rev-parse", "HEAD")


# A source change big enough to clear the default `min_source_loc` of 3.
# Named rather than inlined so the reason a fixture is this size is visible:
# a one-line fixture is rejected as `diff_too_small`, which is the gate
# working, not the fixture being wrong.
SOURCE_FIX = "def handle(value):\n    if value is None:\n        return 0\n    return value * 2\n"
SOURCE_PATH = "src/pkg/thing.py"


def commit_source_fix(repo: Path, message: str, *, date: str, path: str = SOURCE_PATH) -> str:
    """Commit a realistically-sized change to a real source path."""
    return commit(repo, path, SOURCE_FIX, message, date=date)
