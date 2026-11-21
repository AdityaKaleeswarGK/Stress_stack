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
* `setup.py` whole, because it is code that cannot be read safely without
  running it; `tox.ini`'s install settings (`deps`, `extras`, `basepython`,
  ...) but not its commands or tool sections;
* when the repository has a test-specific requirement list, not its docs,
  typing, lint or build lists, which the test run never installs;
* Go's `go.mod` without comments, and `go.sum`;
* for C and C++, which have no single package manager: the dependency calls
  in the root `CMakeLists.txt` and `meson.build` (`find_package`,
  `FetchContent_Declare`, `dependency(...)`, the minimum CMake version and the
  language standard) rather than the whole file, whose source lists change
  with every new file; Conan and vcpkg manifests and Meson wraps whole; and
  every git submodule at its pinned commit.

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
from collections import Counter
import hashlib
import json
import re
import subprocess
import tomllib
from datetime import UTC, datetime
from pathlib import Path

LOCKFILES = frozenset({"uv.lock", "poetry.lock", "pdm.lock", "pipfile.lock", "pylock.toml",
                       # go.sum records the exact module set go.mod's minimum
                       # versions select; Go resolution is deterministic by design.
                       "go.sum", "conan.lock"})
_PYTHON_FILES = frozenset({"setup.py", "setup.cfg", "pyproject.toml", "tox.ini", "pipfile",
                           ".python-version", "environment.yml", "environment.yaml",
                           "constraints.txt", "uv.lock", "poetry.lock", "pdm.lock",
                           "pipfile.lock", "pylock.toml"})
_GO_FILES = frozenset({"go.mod", "go.sum", "go.work", "go.work.sum"})
_NATIVE_FILES = frozenset({"cmakelists.txt", "meson.build", "meson_options.txt", "meson.options",
                           "conanfile.txt", "conanfile.py", "conan.lock", "vcpkg.json",
                           "vcpkg-configuration.json", "configure.ac"})
_ROOT_FILES = _PYTHON_FILES | _GO_FILES | _NATIVE_FILES
_CMAKE_CALLS = re.compile(r"(?is)\b(cmake_minimum_required|find_package|fetchcontent_declare|"
                          r"externalproject_add|cpmaddpackage|pkg_check_modules|find_dependency)\s*\((.*?)\)")
_CMAKE_STANDARD = re.compile(r"(?i)\bset\s*\(\s*(CMAKE_(?:C|CXX)_STANDARD(?:_REQUIRED)?)\s+([^)\s]+)")
_MESON_CALLS = re.compile(r"(?s)\b(dependency|subproject)\s*\((.*?)\)")
_AUTOCONF_CALLS = re.compile(r"(?m)^\s*(PKG_CHECK_MODULES|AC_CHECK_LIB|AC_CHECK_HEADERS?|AX_\w+)\s*\(.*$")
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
# tox settings that change what gets installed. Commands, linters' own
# sections and the like change often and install nothing.
_TOX_KEYS = frozenset({"deps", "basepython", "envlist", "env_list", "extras", "requires", "min_version",
                       "minversion", "skip_install", "usedevelop", "package", "dependency_groups"})
# Requirement lists the test run does not install, when the repository also
# has a test-specific list. Flask's docs/typing/dev lists, bumped monthly,
# split 175 changes into 74 eras until these were set aside.
_SIDE_LISTS = re.compile(r"(?i)(^|[-_./])(docs?|typing|mypy|lint|style|build|dev|release|pre-commit)([-_.]|$)")
_TEST_LISTS = re.compile(r"(?i)(^|[-_./])tests?([-_.]|$)")
_SETUP_CFG_KEYS = (("options", "install_requires"), ("options", "python_requires"),
                   ("options", "setup_requires"), ("options", "tests_require"))


def _git(root: Path, *args: str, timeout: int = 60) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                            errors="replace", timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:300] or "git failed")
    return result.stdout


def dependency_files(root: Path, sha: str) -> dict[str, str]:
    """Dependency declaration paths at a commit, mapped to their object ids.

    Submodules appear as `submodule:<path>` mapped to the commit they pin,
    which is an exact dependency version in the same sense as a lockfile.
    """
    found = {}
    for line in _git(root, "ls-tree", "-r", sha).splitlines():
        meta, _, path = line.partition("\t")
        kind, blob = meta.split()[1:3]
        name = path.rsplit("/", 1)[-1].lower()
        if kind == "commit":
            found[f"submodule:{path}"] = blob
            continue
        if kind != "blob":
            continue
        top_level = "/" not in path
        first = path.split("/", 1)[0]
        if (top_level and (name in _ROOT_FILES or _REQUIREMENTS.match(name))) or \
                (not top_level and first in _REQUIREMENT_DIRS and name.endswith((".txt", ".in"))) or \
                (first == "subprojects" and path.count("/") == 1 and name.endswith(".wrap")):
            found[path] = blob
    lists = [p for p in found if p.endswith((".txt", ".in")) and (p.split("/", 1)[0] in _REQUIREMENT_DIRS
                                                                    or _REQUIREMENTS.match(p.lower()))]
    if any(_TEST_LISTS.search(p.rsplit("/", 1)[-1]) for p in lists):
        for path in lists:
            stem = path.rsplit("/", 1)[-1]
            if _SIDE_LISTS.search(stem) and not _TEST_LISTS.search(stem):
                del found[path]
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
    squash = lambda value: " ".join(value.split())
    if name == "go.mod":
        return "\n".join(line.split("//", 1)[0].rstrip() for line in text.splitlines() if line.split("//", 1)[0].strip())
    if name == "cmakelists.txt":
        uncommented = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
        calls = [f"{fn.lower()}({squash(args)})" for fn, args in _CMAKE_CALLS.findall(uncommented)]
        calls += [f"set({var.upper()} {value})" for var, value in _CMAKE_STANDARD.findall(uncommented)]
        return "\n".join(calls)
    if name == "meson.build":
        uncommented = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
        return "\n".join(f"{fn}({squash(args)})" for fn, args in _MESON_CALLS.findall(uncommented))
    if name == "tox.ini":
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read_string(text)
        except configparser.Error:
            return text
        kept = {f"{section}.{key}": " ".join(value.split())
                for section in parser.sections() if section == "tox" or section.startswith("testenv")
                for key, value in parser.items(section, raw=True) if key in _TOX_KEYS}
        return json.dumps(kept, sort_keys=True)
    if name == "configure.ac":
        return "\n".join(squash(m.group(0)) for m in _AUTOCONF_CALLS.finditer(text))
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


_NORMALISED = frozenset({"pyproject.toml", "setup.cfg", "tox.ini", "go.mod", "cmakelists.txt", "meson.build", "configure.ac"})


def runtime(root: Path, sha: str, manifests: list[str]) -> dict:
    """The language toolchain an era declares, read from its declarations.

    Python goes through `python_runtime`, which refuses to guess from a bare
    lower bound. Go's `go` and `toolchain` directives name the version
    exactly. C and C++ declare a minimum CMake and a language standard; the
    compiler itself comes from the base image.
    """
    names = {m.rsplit("/", 1)[-1].lower(): m for m in manifests}
    found: dict = {}
    if "go.mod" in names:
        text = _git(root, "show", f"{sha}:{names['go.mod']}")
        for directive in ("go", "toolchain"):
            match = re.search(rf"(?m)^{directive}\s+(\S+)", text)
            if match:
                found[f"{directive}_directive"] = match.group(1)
        found["language"] = "go"
    if "cmakelists.txt" in names:
        text = _git(root, "show", f"{sha}:{names['cmakelists.txt']}")
        minimum = re.search(r"(?i)cmake_minimum_required\s*\(\s*VERSION\s+([\d.]+(?:\.\.\.[\d.]+)?)", text)
        standard = re.search(r"(?i)set\s*\(\s*CMAKE_CXX_STANDARD\s+(\d+)", text)
        found.update(language=found.get("language", "c_cpp"),
                     cmake_minimum=minimum.group(1) if minimum else None,
                     cxx_standard=standard.group(1) if standard else None)
    if names.keys() & _PYTHON_FILES:
        from fleet.history.python_runtime import resolve_snapshot

        resolved = resolve_snapshot(root, sha)
        found.update(language=found.get("language", "python"), python=resolved.get("selected_python"),
                     python_status=resolved.get("status"))
    return found


def era_key(root: Path, sha: str, cache: dict | None = None) -> tuple[str, list[str]]:
    """The hash naming this commit's era, and the declaration files behind it."""
    cache = {} if cache is None else cache
    files = dependency_files(root, sha)
    parts = []
    for path, blob in sorted(files.items()):
        if path.rsplit("/", 1)[-1].lower() in _NORMALISED:
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
        lockfiles = [p for p in group["manifests"]
                     if p.rsplit("/", 1)[-1].lower() in LOCKFILES or p.startswith("submodule:")]
        declared_package_manager = any(
            p.rsplit("/", 1)[-1].lower() in _PYTHON_FILES | _GO_FILES | {"conanfile.txt", "conanfile.py", "vcpkg.json"}
            or _REQUIREMENTS.match(p.rsplit("/", 1)[-1].lower()) or p.split("/", 1)[0] in _REQUIREMENT_DIRS
            for p in group["manifests"])
        era = {
            "key": group["key"],
            "commits": [sha for _, sha in dated],
            "first_date": dated[0][0].astimezone(UTC).isoformat(),
            "last_date": dated[-1][0].astimezone(UTC).isoformat(),
            "representative": representative,
            "cutoff": dated[-1][0].astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "manifests": group["manifests"],
            "lockfiles": lockfiles,
            # A lockfile pins exact versions. Without one, a package manager
            # resolves what its index offered at `cutoff`, a guess the base
            # run checks. With no package manager at all (most C/C++), the
            # dependencies are system packages from the base image, which is
            # pinned by digest and can be dated through a distribution
            # snapshot archive.
            "resolution": "locked" if lockfiles else "date_pinned" if declared_package_manager else "system",
        }
        if resolve_python:
            era["runtime"] = runtime(root, representative, group["manifests"])
            era["python"] = era["runtime"].get("python")
            era["python_status"] = era["runtime"].get("python_status")
        for sha in era["commits"]:
            eras[sha] = era
    return eras


def summarise(eras: dict[str, dict]) -> dict:
    distinct = {era["key"]: era for era in eras.values()}
    sizes = sorted((len(era["commits"]) for era in distinct.values()), reverse=True)
    return {"commits": len(eras), "eras": len(distinct),
            "locked_eras": sum(era["resolution"] == "locked" for era in distinct.values()),
            "largest_era": sizes[0] if sizes else 0, "single_commit_eras": sizes.count(1),
            "pythons": sorted({str(era.get("python")) for era in distinct.values()}),
            "resolutions": dict(sorted(Counter(era["resolution"] for era in distinct.values()).items()))}
