"""Dependency eras: runs of commits that can share one environment image.

Building an image per commit is the most expensive way to get an environment
and the one that does not scale: SWE-Gym spent about 6 TB on 2,438 per-task
images, while SWE-smith fitted 50,000 tasks into 295 GB by sharing images. Two
commits need different environments only when what they *depend on* differs,
so the image is keyed by the dependency declarations, not by the commit. The
commit's own code is mounted at run time (see `validation.source_roots`).

An era's key is a hash of every dependency declaration at a commit:

* lockfiles and requirement lists, byte for byte;
* only the dependency sections of `pyproject.toml` and `setup.cfg`, because
  both also hold tool settings (ruff, coverage, mypy) that change far more
  often than dependencies and would otherwise start a new era each time;
* `setup.py` and `tox.ini` whole, because `setup.py` is code that cannot be
  read safely without running it, and `tox.ini` decides test dependencies.

Nothing here executes repository code: files are read from git objects and
parsed as TOML or INI only.

Commits with the same key share an era even when they are not adjacent (a
dependency change that was later reverted returns to an earlier era's image).
The era's representative is its newest commit, and its resolution date is
that commit's date: the newest packages every commit in the era could have
installed. When an era has no lockfile this is an approximation. Whether it
holds for an older commit is measured by that commit's base run, not assumed.
"""
from __future__ import annotations

import configparser
import hashlib
import json
import re
import subprocess
import tomllib
from datetime import UTC, datetime
from pathlib import Path

LOCKFILES = frozenset({"uv.lock", "poetry.lock", "pdm.lock", "pipfile.lock", "pylock.toml"})
_ROOT_FILES = frozenset({"setup.py", "setup.cfg", "pyproject.toml", "tox.ini", "pipfile",
                         ".python-version", "environment.yml", "environment.yaml",
                         "constraints.txt", *LOCKFILES})
_REQUIREMENTS = re.compile(r"^requirements(?:[-_.][\w.-]+)?\.(?:txt|in)$")
# Folders conventionally holding the project's own requirement lists. Deeper
# lists (pytest's testing/plugins_integration/requirements.txt, bumped weekly
# by dependabot) describe side environments, not the main suite.
_REQUIREMENT_DIRS = ("requirements", "reqs")
_PYPROJECT_KEYS = (("build-system",), ("project", "dependencies"), ("project", "optional-dependencies"),
                   ("project", "requires-python"), ("dependency-groups",),
                   ("tool", "poetry", "dependencies"), ("tool", "poetry", "group"),
                   ("tool", "poetry", "dev-dependencies"), ("tool", "pdm", "dev-dependencies"),
                   ("tool", "uv", "dev-dependencies"), ("tool", "uv", "sources"))
_SETUP_CFG_KEYS = (("options", "install_requires"), ("options", "python_requires"),
                   ("options", "setup_requires"), ("options", "tests_require"))


def _git(root: Path, *args: str, timeout: int = 60) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                            errors="replace", timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:300] or "git failed")
    return result.stdout


def dependency_files(root: Path, sha: str) -> dict[str, str]:
    """Dependency declaration paths at a commit, mapped to their blob ids."""
    found = {}
    listing = _git(root, "ls-tree", sha)
    listing += _git(root, "ls-tree", "-r", sha, "--", *_REQUIREMENT_DIRS)
    for line in listing.splitlines():
        meta, _, path = line.partition("\t")
        kind, blob = meta.split()[1:3]
        name = path.rsplit("/", 1)[-1].lower()
        if kind != "blob":
            continue
        top_level = "/" not in path
        if (top_level and (name in _ROOT_FILES or _REQUIREMENTS.match(name))) or \
                (not top_level and path.split("/", 1)[0] in _REQUIREMENT_DIRS and name.endswith((".txt", ".in"))):
            found[path] = blob
    return found


def _pick(data: dict, keys: tuple[str, ...]):
    for key in keys:
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data


def normalise(path: str, text: str) -> str:
    """The part of a declaration file that can change the environment."""
    name = path.rsplit("/", 1)[-1].lower()
    try:
        if name == "pyproject.toml":
            data = tomllib.loads(text)
            dynamic = _pick(data, ("project", "dynamic")) or []
            kept = {".".join(keys): _pick(data, keys) for keys in _PYPROJECT_KEYS if _pick(data, keys) is not None}
            # Dynamic dependencies are computed by a build backend from other
            # files; those files are tracked separately, but say it was dynamic.
            if "dependencies" in dynamic or "optional-dependencies" in dynamic:
                kept["project.dynamic"] = sorted(dynamic)
            return json.dumps(kept, sort_keys=True)
        if name == "setup.cfg":
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string(text)
            kept = {f"{s}.{k}": parser.get(s, k) for s, k in _SETUP_CFG_KEYS if parser.has_option(s, k)}
            if parser.has_section("options.extras_require"):
                kept["options.extras_require"] = dict(parser.items("options.extras_require"))
            return json.dumps(kept, sort_keys=True)
    except (tomllib.TOMLDecodeError, configparser.Error, UnicodeError, ValueError):
        pass  # Unparseable: fall back to the whole file, which can only over-split.
    return text


def era_key(root: Path, sha: str, cache: dict | None = None) -> tuple[str, list[str]]:
    """The hash naming this commit's era, and the declaration files behind it."""
    cache = {} if cache is None else cache
    files = dependency_files(root, sha)
    parts = []
    for path, blob in sorted(files.items()):
        if path.rsplit("/", 1)[-1].lower() in {"pyproject.toml", "setup.cfg"}:
            if blob not in cache:
                cache[blob] = normalise(path, _git(root, "cat-file", "-p", blob))
            parts.append(f"{path}\0{hashlib.sha256(cache[blob].encode()).hexdigest()}")
        else:
            parts.append(f"{path}\0{blob}")  # a blob id is already a content hash
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:24], sorted(files)


def _date(root: Path, sha: str) -> datetime:
    return datetime.fromisoformat(_git(root, "show", "-s", "--format=%cI", sha).strip())


def assign(root: Path, shas: list[str], *, resolve_python: bool = True) -> dict[str, dict]:
    """Every commit's era. Commits sharing dependency declarations share one."""
    cache: dict = {}
    groups: dict[str, dict] = {}
    for sha in dict.fromkeys(shas):
        key, manifests = era_key(root, sha, cache)
        group = groups.setdefault(key, {"key": key, "manifests": manifests, "commits": []})
        group["commits"].append((_date(root, sha), sha))
    eras: dict[str, dict] = {}
    for group in groups.values():
        dated = sorted(group["commits"])
        representative = dated[-1][1]
        lockfiles = [p for p in group["manifests"] if p.rsplit("/", 1)[-1].lower() in LOCKFILES]
        era = {
            "key": group["key"],
            "commits": [sha for _, sha in dated],
            "first_date": dated[0][0].astimezone(UTC).isoformat(),
            "last_date": dated[-1][0].astimezone(UTC).isoformat(),
            "representative": representative,
            "cutoff": dated[-1][0].astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "manifests": group["manifests"],
            "lockfiles": lockfiles,
            # A lockfile pins exact versions; without one the environment is
            # what the package index offered at `cutoff`, which is a guess the
            # base run checks.
            "resolution": "locked" if lockfiles else "date_pinned",
        }
        if resolve_python:
            from fleet.history.python_runtime import resolve_snapshot

            runtime = resolve_snapshot(root, representative)
            era["python"] = runtime.get("selected_python")
            era["python_status"] = runtime.get("status")
        for sha in era["commits"]:
            eras[sha] = era
    return eras


def summarise(eras: dict[str, dict]) -> dict:
    distinct = {era["key"]: era for era in eras.values()}
    sizes = sorted((len(era["commits"]) for era in distinct.values()), reverse=True)
    return {"commits": len(eras), "eras": len(distinct),
            "locked_eras": sum(era["resolution"] == "locked" for era in distinct.values()),
            "largest_era": sizes[0] if sizes else 0, "single_commit_eras": sizes.count(1),
            "pythons": sorted({str(era.get("python")) for era in distinct.values()})}
