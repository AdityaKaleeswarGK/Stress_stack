"""Walk a repository and assemble its symbol graph — phase 1's deliverable.

No LLM, no linting/environment work here; see
../../../deep_research/notes/decisions.md.
"""

from __future__ import annotations

import json
import os
import hashlib
import tomllib
import importlib.metadata
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from fleet.graph.ignore import IgnoreRules, load_ignore_rules
from fleet.graph.models import SCHEMA_VERSION, Edge, ParsedFile
from fleet.graph.parser import parse_source
from fleet.graph.resolve import resolve_edges
from fleet.graph.spec import detect_language, spec_for


def iter_source_files(root: Path, rules: IgnoreRules) -> Iterator[Path]:
    """Every file under `root` that `rules` doesn't exclude, pruning ignored
    directories during the walk rather than filtering after — `.venv`/
    `node_modules` are never descended into, not just skipped once found."""
    for dirpath, dirnames, filenames in os.walk(root):
        relative_dir = Path(dirpath).relative_to(root)
        dirnames[:] = sorted(d for d in dirnames if not rules.matches((relative_dir / d).as_posix(), is_dir=True))
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            if path.is_symlink() or rules.matches((relative_dir / filename).as_posix(), is_dir=False):
                continue
            yield path


@dataclass
class RepoGraph:
    root: str
    files: list[ParsedFile] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    snapshot: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def statistics(self) -> dict[str, Any]:
        by_language: dict[str, int] = {}
        symbols_total = 0
        tests_total = 0
        calls_total = 0
        syntax_errors = 0
        unparsed = 0
        for parsed in self.files:
            key = parsed.language or "unknown"
            by_language[key] = by_language.get(key, 0) + 1
            symbols_total += len(parsed.symbols)
            tests_total += len(parsed.tests)
            calls_total += len(parsed.calls)
            if parsed.has_syntax_error:
                syntax_errors += 1
            if parsed.parser == "none" and not parsed.is_empty:
                unparsed += 1
        by_kind: dict[str, int] = {}
        for edge in self.edges:
            by_kind[edge.kind] = by_kind.get(edge.kind, 0) + 1
        return {
            "files_total": len(self.files),
            "files_by_language": dict(sorted(by_language.items())),
            "symbols_total": symbols_total,
            "tests_total": tests_total,
            # Recorded, not yet resolved into `invoke` edges — see resolve.py.
            "calls_total": calls_total,
            "syntax_errors": syntax_errors,
            # Known extension, nothing extracted — usually a missing
            # tree-sitter grammar for that language. Reported, not hidden.
            "unparsed_known_language": unparsed,
            "edges_total": len(self.edges),
            "edges_by_kind": dict(sorted(by_kind.items())),
            "import_bindings": sum(len(f.bindings) for f in self.files),
            "import_uses": sum(len(b.uses) for f in self.files for b in f.bindings),
            "binding_status": {state: sum(b.status == state for f in self.files for b in f.bindings)
                               for state in ("internal", "conditional", "external", "unresolved", "ambiguous", "unsupported")},
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "root": self.root,
            "snapshot": self.snapshot,
            "diagnostics": self.diagnostics,
            "statistics": self.statistics(),
            "files": [parsed.to_dict() for parsed in self.files],
            "edges": [edge.to_dict() for edge in self.edges],
        }


def scan_repo(root: Path, *, languages: frozenset[str] | None = None) -> RepoGraph:
    """Parse every recognized source file under `root` into a RepoGraph.

    `languages`, if given, restricts which detected languages are kept —
    unrecognized extensions are always skipped, independent of this filter.
    """
    rules = load_ignore_rules(root)
    graph = RepoGraph(root=str(root))
    metadata = {}
    hashes = {}
    active = languages if languages is not None else frozenset({"python", "rust", "javascript", "typescript", "tsx"})
    for path in iter_source_files(root, rules):
        relative_path = str(path.relative_to(root))
        is_config = path.name in {"pyproject.toml", "Cargo.toml", "package.json"}
        if not is_config and detect_language(relative_path) not in active:
            continue
        try:
            code = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError) as exc:
            graph.diagnostics.append({"path": relative_path, "reason": "read_error", "message": str(exc)})
            continue
        hashes[relative_path] = hashlib.sha256(code.encode()).hexdigest()
        if is_config:
            # package.json is JSON, the other two are TOML. A malformed one is
            # a diagnostic, not a crash: the scan still has the source files.
            load = json.loads if path.name == "package.json" else tomllib.loads
            try:
                parsed_config = load(code)
            except (tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
                graph.diagnostics.append({"path": relative_path, "reason": "invalid_configuration", "message": str(exc)})
            else:
                if isinstance(parsed_config, dict):
                    metadata[relative_path] = parsed_config
            continue
        parsed = parse_source(relative_path, code)
        if parsed.language is None:
            continue
        if languages is not None and parsed.language not in languages:
            continue
        graph.files.append(parsed)
    graph.edges = resolve_edges(graph.files, metadata)
    # Query patterns a grammar cannot have are dropped when the query is
    # compiled — a property of the language, the same for every file it
    # parses. Reported once here, so it stays visible without being repeated
    # on each file. Expected for a query file shared across dialects, and the
    # test suite asserts exactly which ones each grammar rejects.
    from fleet.graph.engine import TREE_SITTER_AVAILABLE, dropped_patterns
    for language in sorted({f.language for f in graph.files if f.language}):
        spec = spec_for(language)
        if spec is None or spec.backend != "tree_sitter" or not TREE_SITTER_AVAILABLE:
            continue
        graph.diagnostics.extend(
            {"language": language, "reason": "query_pattern_dropped", "pattern": pattern, "message": message}
            for pattern, message in dropped_patterns(spec)
        )
    ignore_path = root / ".fleetignore"
    if ignore_path.is_file():
        hashes[".fleetignore"] = hashlib.sha256(ignore_path.read_bytes()).hexdigest()
    analyzer = hashlib.sha256()
    for source in sorted(Path(__file__).parent.rglob("*")):
        if source.suffix in {".py", ".scm"}:
            analyzer.update(str(source.relative_to(Path(__file__).parent)).encode())
            analyzer.update(source.read_bytes())
    versions = {name: importlib.metadata.version(name) for name in ("tree-sitter", "tree-sitter-language-pack", "pathspec")}
    identity = [hashes, sorted(active), analyzer.hexdigest(), versions]
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True)
    graph.snapshot = {"content_hashes": hashes, "languages": sorted(active), "versions": versions,
                      "extractor_hash": analyzer.hexdigest(), "git_commit": commit.stdout.strip() if commit.returncode == 0 else None,
                      "id": hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()}
    return graph


def write_graph(graph: RepoGraph, out_path: Path) -> None:
    out_path.write_text(json.dumps(graph.to_dict(), indent=2), encoding="utf-8")
