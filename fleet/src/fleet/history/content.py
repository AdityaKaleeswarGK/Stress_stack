"""Cheap diff screening using syntax only; never imports or runs mined code.

Path classification alone admits release constants, comments and docstrings.
Only discard a candidate when every source file has an understood, excluded
change. Unsupported syntax/languages, missing blobs and large files are kept
with an explicit unknown result. AST equality is not a runtime proof.
"""
from __future__ import annotations

import ast
import re
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
        # Without `type_comments`: with it, the parser turns `# type: ignore`
        # and `# type: int` comments into tree nodes, so deleting one reads as
        # an executable change. pre-commit.ci commits that only dropped stale
        # `# type: ignore[...]` comments passed this screen for exactly that
        # reason. Type comments are read by type checkers, never at runtime.
        old, new = (ast.parse(text) for text in (before, after))
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


# Languages compared by token stream. Headers parse as C++, which accepts C.
_TREE_SITTER_GRAMMARS = {
    ".go": "go", ".c": "c", ".h": "cpp", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".c++": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp", ".ipp": "cpp", ".rs": "rust", ".java": "java",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
}


# Comments that a compiler or type checker reads. Syntactically comments,
# semantically code: gin's "fix: sonic on arm64" changed only `//go:build`
# lines, which decide which files are compiled, and was rejected as
# comment-only until these were kept as tokens.
_DIRECTIVE_COMMENT = re.compile(
    rb"^(//go:|//line |//export |//\s*\+build|//\s*@ts-|///\s*<reference|/\*\s*#__PURE__)")


def _tokens(parser, text: str) -> list[tuple[str, bytes]]:
    """Every leaf token in source order, with ordinary comments left out.

    Directive comments stay in. In a Go file that uses cgo (`import "C"`),
    every comment stays in, because the comment above that import is C source.
    """
    source = text.encode("utf-8", errors="replace")
    keep_all_comments = b'import "C"' in source
    tree = parser.parse(source)
    tokens, stack = [], [tree.root_node]
    while stack:
        node = stack.pop()
        if "comment" in node.type:
            if keep_all_comments or _DIRECTIVE_COMMENT.match(node.text.lstrip()):
                tokens.append(("directive_comment", node.text))
            continue
        if node.child_count == 0:
            tokens.append((node.type, node.text))
            continue
        stack.extend(reversed(node.children))
    return tokens


def token_change_kind(before: str, after: str, path: str) -> str:
    """Whether an edit to a non-Python file changed anything but comments and layout.

    Whitespace is never a token, and comment subtrees are skipped, so two
    files with the same token stream differ only in comments and formatting.
    This direction is safe even where a grammar misparses (C++ macros often
    do): a misread comment becomes tokens and makes the files differ, which
    keeps the change. It can over-keep, never over-reject.
    """
    grammar = _TREE_SITTER_GRAMMARS.get(PurePosixPath(path).suffix.lower())
    if grammar is None:
        return "unknown"
    try:
        from tree_sitter_language_pack import get_parser

        parser = get_parser(grammar)
        same = _tokens(parser, before) == _tokens(parser, after)
    except Exception:  # a missing grammar or a parser failure: no verdict
        return "unknown"
    return "comments_or_formatting_only" if same else "executable_syntax_changed"


def inspect_source_changes(clone: GitRepo, base: str, fixed: str, stats: PatchStats) -> list[dict]:
    findings = []
    for change in stats.source_files:
        row = {"path": change.path, "kind": "unknown"}
        findings.append(row)
        suffix = PurePosixPath(change.path).suffix.lower()
        if change.binary or (suffix != ".py" and suffix not in _TREE_SITTER_GRAMMARS):
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
            if suffix == ".py":
                row["kind"] = python_change_kind(*texts, change.path)
                if row["kind"] == "unknown":
                    row["reason"] = "python_syntax_not_analyzable"
            else:
                row["kind"] = token_change_kind(*texts, change.path)
                row["method"] = "tree_sitter_tokens"
                if row["kind"] == "unknown":
                    row["reason"] = "tokens_not_analyzable"
        except (GitError, ValueError, UnicodeError, OSError, subprocess.SubprocessError):
            # New/deleted/renamed files and unavailable blobs cannot be compared
            # this way. Preserve them for investigation rather than assume noise.
            row["reason"] = "snapshot_file_unavailable_or_too_large"
    return findings
