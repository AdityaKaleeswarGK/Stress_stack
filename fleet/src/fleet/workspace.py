"""Repository identity and persistent, current-directory mining workspaces."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse


def repository_name(value: str) -> str:
    value = value.strip().rstrip("/")
    if value.startswith("git@github.com:"):
        value = value.removeprefix("git@github.com:")
    elif "://" in value:
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or parsed.query or parsed.fragment:
            raise ValueError("Use https://github.com/owner/repo or owner/repo.")
        value = parsed.path.strip("/")
    value = value.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError("Use a repository URL, not an issue, PR or branch URL.")
    return value


def git(root: Path, *args: str, timeout: int = 120) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                            text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:600] or "Git command failed")
    return result.stdout.strip()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def check_identity(root: Path, expected: str) -> None:
    try:
        actual = repository_name(git(root, "remote", "get-url", "origin"))
    except (ValueError, RuntimeError) as exc:
        raise ValueError(f"Cannot verify origin for {root}; supply a clone of {expected}.") from exc
    if actual.lower() != expected.lower():
        raise ValueError(f"Requested {expected}, but {root} has origin {actual}.")


def prepare(repo: str, directory: Path, local: Path | None = None, *, refresh: bool = False) -> Path:
    """Never reuse an arbitrary current-directory Git repository."""
    directory.mkdir(parents=True, exist_ok=True)
    root = local.resolve() if local else directory.resolve() / "repo"
    if local or root.exists():
        check_identity(root, repo)
    else:
        result = subprocess.run(["git", "clone", "--quiet", f"https://github.com/{repo}.git", str(root)],
                                capture_output=True, text=True, timeout=900)
        if result.returncode:
            raise RuntimeError(result.stderr.strip()[:600])
    if refresh:
        git(root, "fetch", "--quiet", "origin", timeout=600)
    return root


def resolve_ref(root: Path, ref: str, *, refresh: bool = False) -> str:
    if ref.startswith("-"):
        raise ValueError("Invalid Git ref")
    if refresh and ref == "HEAD":
        ref = "refs/remotes/origin/HEAD"
    return git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
