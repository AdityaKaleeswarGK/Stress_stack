"""Cheap, deterministic signals about what kind of change a commit is.

None of these reject anything. They label and bin, so the expensive stages
spend their budget on likely tasks first, and so an audit can check each
signal against what the change really was.

The signals are ordered by how far they can be trusted, which decides how the
kind is chosen:

1. **A changelog fragment the project wrote** (`changelog/123.bugfix.rst`,
   towncrier style). The maintainers classified the change themselves, at
   review time, for their users. Strongest, and absent from most repos.
2. **A revert relationship.** `This reverts commit X` is machine-written by
   git; a reverted change was judged broken by the project.
3. **The commit message.** Weakest: about 44% of commit messages lack the
   *what* or *why* (Tian et al., ICSE 2022) and about a third of issue labels
   are wrong (Herzig, 2013). Used only when nothing better exists.
4. **The diff's shape** — new public definitions, a test added beside a small
   change, the same edit repeated across many files. Never decides the kind
   alone; recorded as evidence.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import re
import subprocess
from pathlib import PurePosixPath

BUG_FIX, FEATURE, REFACTOR, OTHER, UNKNOWN = "bug_fix", "feature", "refactor", "other", "unknown"

# towncrier and its relatives: changelog/1234.bugfix.rst, newsfragments/12.feature.md, ...
_FRAGMENT_DIRS = {"changelog", "changelog.d", "changes", "newsfragments", "news", "changelog_entries"}
_FRAGMENT_TYPE = re.compile(r"^[\w+.-]*?\.(?P<type>[a-z_]+)(?:\.\d+)?\.(?:rst|md|txt)$")
_FRAGMENT_KINDS = {
    "bugfix": BUG_FIX, "bug": BUG_FIX, "fix": BUG_FIX, "fixed": BUG_FIX, "regression": BUG_FIX,
    "security": BUG_FIX,
    "feature": FEATURE, "added": FEATURE, "improvement": FEATURE, "enhancement": FEATURE,
    "breaking": FEATURE, "deprecation": FEATURE, "removal": FEATURE, "change": FEATURE, "changed": FEATURE,
    "performance": REFACTOR, "perf": REFACTOR,
    "doc": OTHER, "docs": OTHER, "trivial": OTHER, "misc": OTHER, "contrib": OTHER,
    "packaging": OTHER, "vendor": OTHER, "internal": OTHER,
}
_LOG_FILES = {"changelog", "changes", "history", "news", "release-notes", "releasenotes"}

_CONVENTIONAL = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]*\))?!?:\s")
_CONVENTIONAL_KINDS = {"fix": BUG_FIX, "bugfix": BUG_FIX, "hotfix": BUG_FIX,
                       "feat": FEATURE, "feature": FEATURE,
                       "refactor": REFACTOR, "perf": REFACTOR, "style": REFACTOR,
                       "docs": OTHER, "test": OTHER, "tests": OTHER, "chore": OTHER,
                       "build": OTHER, "ci": OTHER, "deps": OTHER}
_WORDS = (
    (BUG_FIX, re.compile(r"\b(fix(e[sd])?|bug|regression|crash(es|ed)?|incorrect(ly)?|wrong|broken|"
                         r"error|exception|traceback|handle[sd]?|issue)\b", re.I)),
    (REFACTOR, re.compile(r"\b(refactor\w*|clean ?up|tidy|simplif\w+|renam\w+|reorgani[sz]e|"
                          r"typing|type hints?|lint\w*|format\w*)\b", re.I)),
    (FEATURE, re.compile(r"\b(add(s|ed)?|support(s|ed)?|implement\w*|introduc\w+|new|allow(s|ed)?|"
                         r"enable[sd]?|option)\b", re.I)),
)
_REVERT = re.compile(r"This reverts commit ([0-9a-f]{7,40})", re.I)
_CHERRY = re.compile(r"\(cherry picked from commit ([0-9a-f]{40})\)")
_PUBLIC_DEF = re.compile(r"^\+\s*(?:async\s+)?(?:def|class)\s+(?P<name>[A-Za-z]\w*)")


def changelog_signal(paths: list[str]) -> dict | None:
    """The kind a project's own changelog fragment gives this change, if any."""
    kinds, fragments = [], []
    for path in paths:
        parts = PurePosixPath(path).parts
        if len(parts) >= 2 and parts[-2].lower() in _FRAGMENT_DIRS:
            match = _FRAGMENT_TYPE.match(parts[-1].lower())
            if match and match["type"] in _FRAGMENT_KINDS:
                fragments.append(path)
                kinds.append(_FRAGMENT_KINDS[match["type"]])
    if not kinds:
        edited = [p for p in paths if PurePosixPath(p).stem.lower() in _LOG_FILES]
        return {"kind": None, "edited_changelog": edited} if edited else None
    # A change shipping a bugfix fragment is a fix even if it also notes a
    # doc tweak; only the substantive kinds compete.
    ranked = [k for k in (BUG_FIX, FEATURE, REFACTOR, OTHER) if k in kinds]
    return {"kind": ranked[0], "fragments": fragments}


def message_signal(message: str) -> dict:
    """What the commit message claims. Weak evidence; see the module docstring."""
    subject = message.strip().splitlines()[0] if message.strip() else ""
    conventional = _CONVENTIONAL.match(subject.lower())
    if conventional and conventional["type"] in _CONVENTIONAL_KINDS:
        return {"kind": _CONVENTIONAL_KINDS[conventional["type"]], "evidence": f"prefix {conventional['type']}:"}
    for kind, pattern in _WORDS:
        found = pattern.search(subject)
        if found:
            return {"kind": kind, "evidence": f"word '{found.group(0)}'"}
    return {"kind": UNKNOWN, "evidence": "no recognised wording"}


def diff_signal(root, base: str, sha: str, source_paths: list[str]) -> dict:
    """Shape of the source diff: new public definitions and repeated edits."""
    if not source_paths:
        return {"public_definitions_added": [], "mechanical": False}
    diff = subprocess.run(["git", "-C", str(root), "diff", "-U0", "--no-color", base, sha, "--", *source_paths],
                          capture_output=True, text=True, errors="replace", timeout=120).stdout
    added_defs, per_line_files, path = [], defaultdict(set), None
    changed = 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            continue
        if line.startswith("--- ") or not path:
            continue
        if line[:1] in "+-" and line[1:].strip():
            changed += 1
            per_line_files[(line[0], line[1:].strip())].add(path)
            definition = _PUBLIC_DEF.match(line)
            if definition and not definition["name"].startswith("test"):
                added_defs.append(f"{path}:{definition['name']}")
    # The same edit in three or more files, making up most of the change, is a
    # sweep: a rename, a codemod, a typing pass. Large, but rarely hard.
    repeated = sum(1 for files in per_line_files.values() if len(files) >= 3)
    repeated_lines = sum(len(files) for files in per_line_files.values() if len(files) >= 3)
    mechanical = len(source_paths) >= 3 and changed > 0 and repeated_lines / changed >= 0.6
    return {"public_definitions_added": added_defs[:20], "mechanical": mechanical,
            "repeated_edit_share": round(repeated_lines / changed, 3) if changed else 0.0,
            "distinct_repeated_edits": repeated}


def size_tier(source_files: int) -> str:
    if source_files <= 1:
        return "single_file"
    if source_files <= 5:
        return "multi_file"
    if source_files <= 20:
        return "wide"
    return "sweeping"


def history_links(root, ref: str = "--all") -> tuple[dict[str, str], dict[str, list[str]]]:
    """Reverts and backports across the whole clone, from git's own trailers.

    Returns `reverted -> reverting commit` and `original -> [backport commits]`.
    One `git log` per repository, so this costs nothing per candidate.
    """
    log = subprocess.run(["git", "-C", str(root), "log", ref, "--format=%H%x00%B%x01"],
                         capture_output=True, text=True, errors="replace", timeout=600).stdout
    reverted, backported = {}, defaultdict(list)
    for entry in log.split("\x01"):
        sha, _, body = entry.strip().partition("\x00")
        if not sha:
            continue
        for target in _REVERT.findall(body):
            reverted.setdefault(target, sha)
        for original in _CHERRY.findall(body):
            backported[original].append(sha)
    return reverted, dict(backported)


def _resolve(prefix_map: dict[str, str], sha: str) -> str | None:
    if sha in prefix_map:
        return prefix_map[sha]
    return next((v for k, v in prefix_map.items() if sha.startswith(k)), None)


def classify(record: dict, message: str, root, links) -> dict:
    """All signals for one candidate, plus the kind they add up to and why."""
    paths = [f["path"] for f in record["files"]]
    sources = [f["path"] for f in record["files"] if f["kind"] == "source"]
    tests = [f["path"] for f in record["files"] if f["kind"] == "test"]
    reverted, backported = links
    changelog = changelog_signal(paths)
    said = message_signal(message)
    reverts = _REVERT.findall(message)
    shape = diff_signal(root, record["base_sha"], record["sha"], sources)

    if changelog and changelog.get("kind"):
        kind, basis, confidence = changelog["kind"], "changelog_fragment", "high"
    elif reverts:
        kind, basis, confidence = BUG_FIX, "reverts_a_commit", "medium"
    elif shape["mechanical"]:
        kind, basis, confidence = REFACTOR, "repeated_edit_across_files", "medium"
    elif said["kind"] != UNKNOWN:
        kind, basis, confidence = said["kind"], "commit_message", "low"
    else:
        kind, basis, confidence = UNKNOWN, "none", "none"
    return {
        "kind": kind, "kind_basis": basis, "kind_confidence": confidence,
        "changelog": changelog, "message": said,
        "reverts": reverts, "reverted_by": _resolve(reverted, record["sha"]),
        "backported_to": backported.get(record["sha"], []),
        "test_changed": bool(tests), "test_files": len(tests),
        "size_tier": size_tier(len(sources)), "source_files": len(sources),
        "source_loc": sum(f.get("added", 0) + f.get("deleted", f.get("removed", 0))
                          for f in record["files"] if f["kind"] == "source"),
        **shape,
    }


def bins(rows: list[dict]) -> dict:
    """Counts by kind, by size tier, and by kind and test presence together."""
    return {
        "by_kind": dict(Counter(r["kind"] for r in rows).most_common()),
        "by_kind_basis": dict(Counter(r["kind_basis"] for r in rows).most_common()),
        "by_size_tier": dict(Counter(r["size_tier"] for r in rows).most_common()),
        "by_kind_and_test": dict(Counter(f"{r['kind']}|{'tested' if r['test_changed'] else 'untested'}"
                                         for r in rows).most_common()),
        "mechanical": sum(r["mechanical"] for r in rows),
        "backported": sum(bool(r["backported_to"]) for r in rows),
        "reverted_later": sum(bool(r["reverted_by"]) for r in rows),
    }
