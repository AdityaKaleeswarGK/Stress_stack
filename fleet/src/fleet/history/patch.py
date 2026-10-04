"""What a candidate's fix actually changed — read from the clone, not the API.

`git diff <base_sha> <merge_sha>` is the oracle patch: for a merge commit
that is precisely the net change the branch brought onto the mainline. We
read it locally with `--numstat`, which costs no API budget at all and takes
about a second on a clone that has its blobs. The API's
`additions`/`deletions` fields would cost one request
per candidate for strictly less information — no per-file breakdown, so no
way to tell a one-line fix with a 400-line test from a 400-line refactor.

Path-level classification has a real limit worth stating: a file is
classified as a whole, so a language that puts tests *inside* the source
file — Rust's inline `#[cfg(test)] mod tests`, Python doctests — reports
those lines as source. Separating them needs hunk- or entity-level
analysis, which the hidden-test-patch stage will need anyway.

The per-file split is the load-bearing part. Measured on `pallets/click`
PR #3739, the diff is:

    src/click/_termui_impl.py    1+  1-   <- the fix
    tests/test_termui.py        71+  0-   <- the test that proves it
    CHANGES.md                   5+  0-   <- changelog noise

Judging that by its total (78 lines) would call it a medium-sized change.
It is a *one-line* fix with a thorough test, which is the most valuable
shape a mined task can have — and only the breakdown shows it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

# `100  12  src/click/core.py`, or `-  -  logo.png` for a binary file, or
# `3  1  src/{old => new}/file.py` when a rename is detected.
_NUMSTAT_RE = re.compile(r"^(\d+|-)\t(\d+|-)\t(.*)$")
_RENAME_BRACE_RE = re.compile(r"^(.*)\{(.*) => (.*)\}(.*)$")


class PathKind(StrEnum):
    """What role a changed file plays. Drives the shape gates."""

    SOURCE = "source"  # application code — the thing an agent must fix
    TEST = "test"  # proves the fix; the oracle for a runtime task
    DOC = "doc"  # prose, changelogs, examples
    CONFIG = "config"  # CI, lockfiles, packaging — outside the source-focused pilot


# Directory names that mark a test tree. Matched on whole path *components*,
# never as a substring: substring matching (which SWE-bench uses, and
# Repo2RLEnv inherits) files `src/click/testing.py` and `docs/testing.md` as
# tests, because both contain "testing".
_TEST_DIRS = frozenset({"test", "tests", "testing", "spec", "specs", "e2e", "__tests__", "it"})
_DOC_DIRS = frozenset({"doc", "docs", "documentation", "example", "examples", "sample", "samples"})
_DOC_SUFFIXES = (".md", ".rst", ".txt", ".adoc")
_CONFIG_DIRS = frozenset({".github", ".circleci", ".gitlab", "ci", ".devcontainer"})
_CONFIG_NAMES = frozenset(
    {
        "setup.py", "setup.cfg", "pyproject.toml", "requirements.txt", "uv.lock",
        "poetry.lock", "package.json", "package-lock.json", "yarn.lock",
        "go.mod", "go.sum", "cargo.toml", "cargo.lock", "makefile", "dockerfile",
        "tox.ini", ".pre-commit-config.yaml", ".gitignore", ".editorconfig",
        "codecov.yml", ".codecov.yml", "codecov.yaml", "pytest.ini", "requirements.in",
        "manifest.in", ".coveragerc", ".tox-coveragerc", ".readthedocs.yml", ".readthedocs.yaml",
        ".travis.yml", ".travis.yaml",
    }
)
_REQUIREMENTS_NAME_RE = re.compile(r"^requirements(?:[-_.][\w.-]+)?\.(?:txt|in)$")
_TEST_BASENAME_RE = re.compile(
    r"""(?x)
    ^test_.*\.(?:py|js|ts|tsx|rb)$      # test_foo.py
    | .*_test\.(?:py|go|rb|ts|js)$      # foo_test.go
    | .*\.test\.(?:ts|tsx|js|jsx)$      # foo.test.ts
    | .*\.spec\.(?:ts|tsx|js|jsx)$      # foo.spec.ts
    | ^conftest\.py$                    # pytest fixtures
    """
)


def classify_path(path: str) -> PathKind:
    """Which bucket a repository-relative path belongs to.

    Order matters. Documentation wins over tests, so `docs/testing.md` is a
    doc; a test directory wins over source; and a changelog anywhere is a
    doc even when it sits at the repo root.
    """
    if not path:
        return PathKind.SOURCE
    parts = [part.lower() for part in path.split("/") if part]
    if not parts:
        return PathKind.SOURCE
    basename = parts[-1]
    directories = parts[:-1]
    if basename in {"license", "licence", "copying", "notice", "authors", "readme", "changelog"}:
        return PathKind.DOC

    # Docs first: a file under docs/ is never a test, whatever it is called.
    if any(part in _DOC_DIRS for part in directories):
        # Dependency lists remain configuration even when used to build docs.
        if _REQUIREMENTS_NAME_RE.fullmatch(basename):
            return PathKind.CONFIG
        return PathKind.DOC
    # Agent permissions are development configuration, not application source.
    if ".claude" in directories and basename in {"settings.json", "settings.local.json"}:
        return PathKind.CONFIG
    if any(part in _CONFIG_DIRS for part in directories):
        return PathKind.CONFIG
    if basename in _CONFIG_NAMES or _REQUIREMENTS_NAME_RE.fullmatch(basename):
        return PathKind.CONFIG
    if basename.endswith(_DOC_SUFFIXES):
        return PathKind.DOC
    if any(part in _TEST_DIRS for part in directories):
        return PathKind.TEST
    if _TEST_BASENAME_RE.match(basename):
        return PathKind.TEST
    return PathKind.SOURCE


@dataclass(frozen=True, slots=True)
class FileChange:
    path: str
    added: int
    removed: int
    kind: PathKind
    binary: bool = False

    @property
    def loc(self) -> int:
        return self.added + self.removed


@dataclass(frozen=True, slots=True)
class PatchStats:
    """Per-file shape of one candidate's fix."""

    files: tuple[FileChange, ...] = ()

    # --- totals ---

    @property
    def loc_changed(self) -> int:
        """Lines added plus removed, across every file."""
        return sum(change.loc for change in self.files)

    @property
    def file_count(self) -> int:
        return len(self.files)

    # --- by role ---

    def of_kind(self, kind: PathKind) -> tuple[FileChange, ...]:
        return tuple(change for change in self.files if change.kind is kind)

    @property
    def source_files(self) -> tuple[FileChange, ...]:
        return self.of_kind(PathKind.SOURCE)

    @property
    def test_files(self) -> tuple[FileChange, ...]:
        return self.of_kind(PathKind.TEST)

    @property
    def source_loc(self) -> int:
        """The size of the *fix* — the number the size gates should judge.

        Not the diff total: a one-line fix shipped with a 71-line test is a
        one-line fix, and a `min_loc` gate reading the total would wave it
        through while a `max_loc` gate reading the total would throw it out.
        """
        return sum(change.loc for change in self.source_files)

    @property
    def test_loc(self) -> int:
        return sum(change.loc for change in self.test_files)

    @property
    def has_source_change(self) -> bool:
        return bool(self.source_files)

    @property
    def has_test_change(self) -> bool:
        return bool(self.test_files)

    @property
    def touches_binary(self) -> bool:
        return any(change.binary for change in self.files)

    def counts_by_kind(self) -> dict[str, dict[str, int]]:
        """Keep source, test, documentation and configuration costs separate.

        Binary files count as files; their line counts are unknown, not measured
        zero. Consumers can see that in each group's binary_file_count.
        """
        return {
            str(kind): {
                "file_count": len(files),
                "added": sum(f.added for f in files),
                "deleted": sum(f.removed for f in files),
                "loc_changed": sum(f.loc for f in files),
                "binary_file_count": sum(f.binary for f in files),
            }
            for kind in PathKind
            for files in (self.of_kind(kind),)
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "loc_changed": self.loc_changed,
            "source_loc": self.source_loc,
            "test_loc": self.test_loc,
            "file_count": self.file_count,
            "source_file_count": len(self.source_files),
            "test_file_count": len(self.test_files),
            "has_test_change": self.has_test_change,
            "by_kind": self.counts_by_kind(),
            "files": [
                {
                    "path": change.path,
                    "added": change.added,
                    "removed": change.removed,
                    "kind": str(change.kind),
                    **({"binary": True} if change.binary else {}),
                }
                for change in self.files
            ],
        }


def _unwrap_rename(path: str) -> str:
    """`src/{old => new}/file.py` -> `src/new/file.py`.

    Git compresses renames in `--numstat` output. We keep the destination,
    which is the path the change lands on.
    """
    match = _RENAME_BRACE_RE.match(path)
    if match:
        prefix, _old, new, suffix = match.groups()
        return f"{prefix}{new}{suffix}".replace("//", "/")
    if " => " in path:
        return path.split(" => ", 1)[1].strip()
    return path


def parse_numstat(output: str) -> PatchStats:
    """Parse `git diff --numstat [-z]` into per-file changes.

    Binary files report `-` for both counts; they are recorded with zero
    lines and a `binary` flag rather than dropped, so a candidate that is
    mostly a new image doesn't look like an empty diff. Live scans use -z
    so quoted, Unicode, tab and newline-containing paths stay classifiable.
    Plain output remains accepted for existing fixtures and saved evidence.
    """
    rows = []
    if "\0" in output:
        fields = iter(output.split("\0"))
        for field in fields:
            parts = field.split("\t", 2)
            if len(parts) != 3:
                continue
            added, removed, path = parts
            if not path:
                # With -z, a rename is counts + NUL + old path + NUL + new path.
                next(fields, "")
                path = next(fields, "")
            rows.append((added, removed, path))
    else:
        for line in output.splitlines():
            match = _NUMSTAT_RE.match(line)
            if match:
                added, removed, path = match.groups()
                rows.append((added, removed, _unwrap_rename(path.strip())))

    changes: list[FileChange] = []
    for added_raw, removed_raw, path in rows:
        if not path or any(v != "-" and not v.isdecimal() for v in (added_raw, removed_raw)):
            continue
        binary = added_raw == "-" or removed_raw == "-"
        changes.append(
            FileChange(
                path=path,
                added=0 if binary else int(added_raw),
                removed=0 if binary else int(removed_raw),
                kind=classify_path(path),
                binary=binary,
            )
        )
    return PatchStats(files=tuple(changes))


def difficulty_bucket(stats: PatchStats) -> str:
    """A coarse size label, judged on the fix rather than the whole diff.

    Buckets exist so a consumer can slice train/eval sets by hardness
    without re-deriving anything.
    """
    loc = stats.source_loc
    if loc <= 5:
        return "trivial"
    if loc <= 20:
        return "small"
    if loc <= 80:
        return "medium"
    return "large"
