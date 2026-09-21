"""Small implementation experiments; no production or sibling-repo changes.

Run with fleet/.venv/bin/python graph_audit/research_probes.py.
These are capability demonstrations, not comparative scanner benchmarks.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fleet/src"))
from fleet.graph.ignore import load_ignore_rules
from fleet.graph.parser import parse_source
from tree_sitter import Query, QueryCursor
from tree_sitter_language_pack import get_parser


def captures(language: str, source: str, query: str) -> list[dict]:
    parser = get_parser(language)
    tree = parser.parse(source.encode())
    assert not tree.root_node.has_error
    return [
        {key: [node.text.decode() for node in nodes] for key, nodes in match.items()}
        for _, match in QueryCursor(Query(parser.language, query)).matches(tree.root_node)
    ]


def main() -> None:
    results = {}
    go = "package p\ntype (\n A struct {}\n B interface {}\n)\n"
    results["go_grouped_types"] = {
        "fleet": [s.name for s in parse_source("types.go", go).symbols],
        "entity_anchored_query": captures("go", go,
            "(type_spec name: (type_identifier) @name) @definition.type"),
    }
    ts = 'import { foo, bar as baz } from "./dep";\ninterface Shape {}\ntype Id = string;\nenum Color {Red}\n'
    results["typescript_definitions"] = {
        "fleet": [s.name for s in parse_source("types.ts", ts).symbols],
        "dialect_query": captures("typescript", ts, """
            (interface_declaration name: (type_identifier) @name) @definition.interface
            (type_alias_declaration name: (type_identifier) @name) @definition.type
            (enum_declaration name: (identifier) @name) @definition.enum
        """),
    }
    results["optional_alias_capture"] = captures("typescript", ts, """
        (import_specifier name: (identifier) @name
          alias: (identifier)? @alias) @binding
    """)
    rust_parser = get_parser("rust")
    rust_function = rust_parser.parse(b"fn a() {}").root_node.named_children[0]
    results["rust_function_body_field"] = rust_function.child_by_field_name("body").type

    # Load only this inspected, filesystem-only method, avoiding app startup,
    # DGAT invocation, network calls, and optional third-party dependencies.
    alpha_path = Path("/Users/adityagk/Desktop/capstone/iteration-1_alpha_stack/src/utils/dependencies.py")
    source = alpha_path.read_text()
    cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "DependencyAnalyzer")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_resolve_internal_import")
    namespace = {"os": os, "Optional": Optional}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(alpha_path), "exec"), namespace)
    resolver = namespace["_resolve_internal_import"]
    results["alpha_source_sha256"] = hashlib.sha256(source.encode()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="fleet-research-") as temp:
        root = Path(temp)
        for path in ("pkg/__init__.py", "pkg/a.py", "pkg/b.py", "src/spkg/__init__.py", "src/spkg/b.py", "web/a.ts", "web/b.ts"):
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("")
        obj = SimpleNamespace(project_root=str(root))
        results["alpha_supplemental_resolver"] = []
        for source_file, module, expected in (
            ("pkg/a.py", ".b", "pkg/b.py"),
            ("pkg/a.py", "pkg.b", "pkg/b.py"),
            ("pkg/a.py", ".", "pkg/__init__.py"),
            ("pkg/a.py", "spkg.b", "src/spkg/b.py"),
            ("web/a.ts", "./b", "web/b.ts"),
        ):
            resolved = resolver(obj, str(root / source_file), module)
            observed = str(Path(resolved).relative_to(root)) if resolved else None
            results["alpha_supplemental_resolver"].append({
                "source": source_file, "module": module, "expected": expected,
                "observed": observed, "matches_expected": observed == expected,
                "assumption": "src is a configured package root" if module == "spkg.b" else None,
            })
        results["fleet_ignore"] = []
        for pattern, relative_path, expected in (
            ("/root.py", "root.py", True),
            ("/root.py", "nested/root.py", False),
            ("nested/skip.py", "nested/skip.py", True),
            ("**/skip.py", "nested/skip.py", True),
            ("*.py\n!keep.py", "keep.py", False),
        ):
            (root / ".fleetignore").write_text(pattern + "\n")
            # Match what scan.py supplies today: a basename, not a relative path.
            observed = load_ignore_rules(root).matches(Path(relative_path).name, is_dir=False)
            results["fleet_ignore"].append({
                "patterns": pattern.splitlines(), "path": relative_path,
                "git_style_expected": expected, "observed": observed,
                "matches_expected": observed == expected,
            })
    results["versions"] = {name: importlib.metadata.version(name) for name in ("tree-sitter", "tree-sitter-language-pack")}
    output = ROOT / "graph_audit/research/probes.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
