"""Cheap Python diff screening using syntax only; never imports mined code.

Path classification alone admits release constants, comments and docstrings.
Only discard a candidate when every source file has an understood, excluded
change. Unsupported syntax/languages, missing blobs and large files are kept
with an explicit unknown result. AST equality is not a runtime proof.
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import PurePosixPath

from fleet.history.patch import PatchStats
from fleet.history.window import GitRepo, GitError, _run

_VERSION_NAMES = {"__version__", "version", "version_info", "__version_info__"}
_VERSION_FILES = {"version.py", "_version.py", "__version__.py"}
_MAX_SOURCE_BYTES = 2_000_000


class _WithoutDocstrings(ast.NodeTransformer):
    def _strip(self, node):
        self.generic_visit(node)
        if node.body and isinstance(node.body[0], ast.Expr):
            value = node.body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                node.body = node.body[1:]
        return node

    visit_Module = _strip
    visit_FunctionDef = _strip
    visit_AsyncFunctionDef = _strip
    visit_ClassDef = _strip


def _literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (str, int, float)) and not isinstance(node.value, bool)
    return isinstance(node, (ast.Tuple, ast.List)) and all(_literal(n) for n in node.elts)


def _mask_version_values(tree: ast.Module) -> None:
    # Only module-level literal assignments in dedicated version modules.
    # Changes to version computation, function bodies or other constants remain.
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if (node.value is not None and _literal(node.value) and targets
                and all(isinstance(t, ast.Name) and t.id.lower() in _VERSION_NAMES for t in targets)):
            node.value = ast.Constant(value="<version value>")


def python_change_kind(before: str, after: str, path: str) -> str:
    """Classify an existing Python file's edit, without executing either side."""
    try:
        old, new = (ast.parse(text, type_comments=True) for text in (before, after))
        if ast.dump(old) == ast.dump(new):
            return "comments_or_formatting_only"
        old, new = (_WithoutDocstrings().visit(tree) for tree in (old, new))
        if ast.dump(old) == ast.dump(new):
            return "docstrings_only"
        if PurePosixPath(path).name.lower() in _VERSION_FILES:
            for tree in (old, new):
                _mask_version_values(tree)
            if ast.dump(old) == ast.dump(new):
                return "version_values_only"
        return "executable_syntax_changed"
    except (SyntaxError, ValueError, RecursionError):
        return "unknown"


def inspect_source_changes(clone: GitRepo, base: str, fixed: str, stats: PatchStats) -> list[dict]:
    findings = []
    for change in stats.source_files:
        row = {"path": change.path, "kind": "unknown"}
        findings.append(row)
        if change.binary or not change.path.endswith(".py"):
            row["reason"] = "unsupported_source_type"
            continue
        try:
            texts = []
            for sha in (base, fixed):
                spec = f"{sha}:{change.path}"
                size = int(_run(["git", "cat-file", "-s", spec], cwd=clone.path, timeout=30))
                if size > _MAX_SOURCE_BYTES:
                    raise ValueError("source_file_exceeds_static_analysis_budget")
                texts.append(_run(["git", "show", spec], cwd=clone.path, timeout=30))
            row["kind"] = python_change_kind(*texts, change.path)
            if row["kind"] == "unknown":
                row["reason"] = "python_syntax_not_analyzable"
        except (GitError, ValueError, UnicodeError, OSError, subprocess.SubprocessError):
            # New/deleted/renamed files and unavailable blobs cannot be compared
            # this way. Preserve them for investigation rather than assume noise.
            row["reason"] = "snapshot_file_unavailable_or_too_large"
    return findings
