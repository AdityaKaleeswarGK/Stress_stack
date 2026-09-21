"""Read-only corpus scans and explicit graph acceptance probes.

Run from task_repo with fleet/.venv/bin/python graph_audit/run.py.
Writes fresh artifacts only under --out; never replaces a source repo's graph.
The acceptance probes describe desired capabilities, including unimplemented
ones. Their results are not a statistical estimate of real-world accuracy.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import time

WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE / "fleet/src"))

from fleet.graph.parser import parse_source
from fleet.graph.resolve import resolve_edges
from fleet.graph.scan import scan_repo


REPOSITORIES = (
    "glom", "pluggy", "click", "agustus", "recipehub", "calc-rs",
    "rust-bump-allocator", "tui-calculator", "TinyGoRPC", "tiny-rpc",
    "zero-copy-http-parser", "FlappyBird-TS-Canvas",
)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_metadata(root):
    def run(*args):
        proc = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True,
            timeout=15,
        )
        return proc.stdout.strip() if proc.returncode == 0 else None
    return {"head": run("rev-parse", "HEAD"),
            "status_porcelain": run("status", "--porcelain", "--untracked-files=no")}


def python_definitions(code):
    """Independent recursive inventory; includes nested and conditional defs."""
    found = []
    class Visitor(ast.NodeVisitor):
        scope = []
        def definition(self, node):
            self.scope.append(node.name)
            found.append((".".join(self.scope), node.lineno))
            self.generic_visit(node)
            self.scope.pop()
        visit_FunctionDef = definition
        visit_AsyncFunctionDef = definition
        visit_ClassDef = definition
    Visitor().visit(ast.parse(code))
    return found


def scan_one(root, label, out):
    started = time.monotonic()
    graph = scan_repo(root)
    payload = graph.to_dict()
    write_json(out / label / "repo_graph.json", payload)
    file_ids = {f.path for f in graph.files}
    symbols = [s for f in graph.files for s in f.symbols]
    id_counts = Counter(s.id for s in symbols)
    known = file_ids | set(id_counts)
    import_edges = [e for e in graph.edges if e.kind == "import"]
    incoming = Counter(e.target for e in import_edges)
    edge_lines = {(e.source, e.line) for e in import_edges}
    per_file = []
    missed_defs = []
    source_hashes = {}
    for parsed in graph.files:
        source = root / parsed.path
        source_hashes[parsed.path] = sha(source)
        unresolved = [asdict(i) for i in parsed.imports
                      if (parsed.path, i.line) not in edge_lines]
        per_file.append({
            "path": parsed.path, "language": parsed.language,
            "parser": parsed.parser, "syntax_error": parsed.has_syntax_error,
            "symbols": len(parsed.symbols), "calls_in_memory": len(parsed.calls),
            "calls_with_declared_symbol_owner": sum(
                c.caller in {s.qualified_name for s in parsed.symbols}
                for c in parsed.calls),
            "docstrings_in_memory": sum(bool(s.docstring) for s in parsed.symbols),
            "incoming_edge_count": incoming[parsed.path],
            "incoming_files": sorted({e.source for e in import_edges if e.target == parsed.path}),
            "imports_without_resolved_edge_on_line": unresolved,
        })
        if parsed.language == "python" and parsed.parser == "ast":
            reference = set(python_definitions(source.read_text()))
            actual = {(s.qualified_name, s.start_line) for s in parsed.symbols}
            missed_defs.extend({"path": parsed.path, "qualified_name": name, "line": line}
                               for name, line in sorted(reference - actual))
    roots = {f.path: {s.qualified_name for s in f.symbols} for f in graph.files}
    issues = {
        "duplicate_symbol_ids": {k: v for k, v in id_counts.items() if v > 1},
        "dangling_edges": [asdict(e) for e in graph.edges
                           if e.source not in known or e.target not in known],
        "symbol_parents_missing_in_file": [
            {"id": s.id, "parent": s.parent} for s in symbols
            if s.parent and s.parent not in roots[s.path]],
        "python_definitions_not_indexed": missed_defs,
    }
    old_path = root / "repo_graph.json"
    previous = None
    if old_path.is_file():
        old = json.loads(old_path.read_text())
        previous = {"path": str(old_path), "sha256": sha(old_path),
                    "schema": old.get("schema_version"),
                    "statistics": old.get("statistics"),
                    "import_edges": sum(e.get("kind") == "import" for e in old.get("edges", [])),
                    "comparison_caveat": "Old artifacts do not establish identical source, query, or configuration versions."}
    summary = {
        "label": label, "root": str(root), **graph.statistics(),
        "scan_seconds": round(time.monotonic() - started, 3),
        "import_records": sum(len(f.imports) for f in graph.files),
        "import_records_with_any_edge_on_line": sum(
            (f.path, i.line) in edge_lines for f in graph.files for i in f.imports),
        "import_count_caveat": "Records without edges include external imports and unsupported resolution; this is not recall. Multiple bindings on a line can be only partly resolved.",
        "files_with_incoming_edges": len(incoming),
        "most_imported_files": incoming.most_common(5),
        "docstrings_in_memory": sum(bool(s.docstring) for s in symbols),
        "docstrings_saved": sum(bool(s.get("docstring")) for f in payload["files"] for s in f["symbols"]),
        "calls_saved": sum(len(f.get("calls", [])) for f in payload["files"]),
        "issues": issues, "previous": previous,
    }
    manifest = {
        "git": git_metadata(root), "source_sha256": source_hashes,
        "root_config_sha256": {name: sha(root / name) for name in (
            "pyproject.toml", "setup.cfg", "setup.py", "package.json", "tsconfig.json",
            "go.mod", "go.sum", "Cargo.toml", "Cargo.lock", ".fleetignore",
        ) if (root / name).is_file()},
    }
    manifest["scanned_sources_digest"] = hashlib.sha256(
        json.dumps(source_hashes, sort_keys=True).encode()).hexdigest()
    write_json(out / label / "manifest.json", manifest)
    write_json(out / label / "diagnostics.json", {"summary": summary, "files": per_file})
    print(f"{label}: {len(graph.files)} files, {len(symbols)} symbols, "
          f"{len(import_edges)} import edges, {len(incoming)} imported files", flush=True)
    return summary


def run_cases():
    cases = json.loads((Path(__file__).parent / "cases.json").read_text())
    results = []
    for case in cases:
        files = [parse_source(p, c) for p, c in case["files"].items()]
        edges = resolve_edges(files)
        symbols = [s for f in files for s in f.symbols]
        check = case["check"]
        if check == "edges":
            observed = sorted([e.source, e.target] for e in edges if e.kind == case["kind"])
        elif check == "symbols":
            observed = sorted(s.id for s in symbols)
        elif check == "calls":
            observed = sorted([f.path, c.name, c.caller, c.line] for f in files for c in f.calls)
        elif check == "generator":
            observed = {s.qualified_name: s.is_generator for s in symbols
                        if s.qualified_name in case["expected"]}
        elif check == "unique_ids":
            observed = len({s.id for s in symbols}) == len(symbols)
        elif check == "calls_saved":
            observed = sum(len(f.to_dict().get("calls", [])) for f in files)
        elif check == "docstrings_saved":
            observed = [s.to_dict().get("docstring") for s in symbols]
        elif check == "imports":
            observed = [[f.path, i.module, i.symbols, i.raw] for f in files for i in f.imports]
        elif check == "body_spans":
            observed = all(s.body_start_byte is not None and s.body_end_byte is not None
                           for s in symbols if s.kind in {"function", "method"})
        else:
            raise ValueError(f"Unknown audit check: {check}")
        results.append({**case, "observed": observed,
                        "passed": observed == case["expected"]})
    return results


def run_real_checks(repositories, out):
    checks = json.loads((Path(__file__).parent / "real_cases.json").read_text())
    by_label = {r["label"]: r for r in repositories}
    results = []
    for case in checks:
        label = case["repository"]
        root = Path(by_label[label]["root"])
        graph = json.loads((out / label / "repo_graph.json").read_text())
        manifest = json.loads((out / label / "manifest.json").read_text())
        witnesses = []
        for path, fragment in case["requires"].items():
            source = (root / path).read_text()
            if fragment not in source:
                raise ValueError(f"Real-case source changed: {label}/{path}: {fragment}")
            if path in manifest["source_sha256"] and sha(root / path) != manifest["source_sha256"][path]:
                raise ValueError(f"Source changed during scan: {label}/{path}")
            witnesses.append({"path": str(root / path), "fragment": fragment,
                              "line": source[:source.index(fragment)].count("\n") + 1})
        symbols = {s["id"]: s for f in graph["files"] for s in f["symbols"]}
        check = case["check"]
        if check == "symbol":
            observed = case["target"] in symbols
        elif check == "edge":
            observed = any(e["kind"] == case["kind"] and e["source"] == case["source"]
                           and e["target"] == case["target"] for e in graph["edges"])
        elif check == "docstring":
            observed = bool(symbols.get(case["target"], {}).get("docstring"))
        elif check == "import_record":
            observed = any(f["path"] == case["source"] and
                           any(i["module"] == case["target"] for i in f["imports"])
                           for f in graph["files"])
        else:
            raise ValueError(check)
        results.append({**case, "observed": observed, "passed": observed == case["expected"],
                        "witnesses": witnesses})
    return results


def markdown_summary(report):
    lines = ["# Fleet graph hypothesis audit", "", f"Generated: {report['generated_at']}", "",
             "Fresh scans of local working trees. Scanner code is unchanged. Saved graphs are copied into this audit directory; source repositories are not modified.", "",
             "## Corpus measurements", "",
             "| Repository | Files | Symbols | Import edges | Files with incoming edges | Calls in memory / saved | Missing Python definitions* |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for r in report["repositories"]:
        lines.append(f"| {r['label']} | {r['files_total']} | {r['symbols_total']} | "
                     f"{r['edges_by_kind'].get('import', 0)} | {r['files_with_incoming_edges']} | "
                     f"{r['calls_total']} / {r['calls_saved']} | {len(r['issues']['python_definitions_not_indexed'])} |")
    lines += ["", "*Independent AST traversal includes nested and conditional definitions that Fleet intentionally does not fully index today. This measures a capability gap against the proposed complete hierarchy, not a regression against its existing narrow contract.", "",
              "Import counts do not measure precision or recall: external imports legitimately have no internal edge. A file-level edge also does not prove which symbol was imported or called.", "",
              "## Previous saved graphs", "",
              "| Repository | Previous schema | Previous import edges | Fresh import edges |",
              "|---|---|---:|---:|"]
    for r in report["repositories"]:
        if r["previous"]:
            old = r["previous"]
            lines.append(f"| {r['label']} | {old['schema']} | {old['import_edges']} | {r['edges_by_kind'].get('import', 0)} |")
    lines += ["", "Old artifacts have no matching source/configuration fingerprints, so differences are observations, not measured improvement on an identical snapshot.", "",
              "## Explicit acceptance probes", "",
              "These are hand-specified source fixtures, including missing features. Passing all would establish these cases only. `cases.json` contains the source and expected result; `results.json` contains observed results.", "",
              "| Probe | Category | Result |",
              "|---|---|---|"]
    for case in report["cases"]:
        lines.append(f"| {case['name']} | {case['category']} | {'PASS' if case['passed'] else 'GAP'} |")
    passed = sum(c["passed"] for c in report["cases"])
    lines += ["", f"{passed}/{len(report['cases'])} acceptance probes pass. This intentionally challenging set is not a representative accuracy benchmark.", "",
              "## Source-checked repository questions", "",
              "Expected answers were specified from inspected source; they are not inferred from the graph. Source fragments, locations and observed answers are recorded in results.json. These selected cases measure capabilities, not corpus-wide accuracy.", "",
              "| Repository | Question | Result |", "|---|---|---|"]
    for case in report["real_cases"]:
        lines.append(f"| {case['repository']} | {case['name']} | {'PASS' if case['passed'] else 'GAP'} |")
    lines += ["",
              "No target repository tests or dependency installers were executed. Coverage edges and LLM enrichment quality were not measured. No assertion of complete semantic resolution, task validity, or historical reproducibility follows from these scans."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects", type=Path, default=WORKSPACE.parent)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "results")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if any acceptance probe fails")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    source = WORKSPACE / "fleet/src"
    analyzer_hash = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        if path.suffix in {".py", ".scm"}:
            analyzer_hash.update(str(path.relative_to(source)).encode())
            analyzer_hash.update(path.read_bytes())
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analyzer_sha256": analyzer_hash.hexdigest(),
        "python": sys.version,
        "versions": {p: importlib.metadata.version(p) for p in (
            "tree-sitter", "tree-sitter-language-pack")},
        "repositories": [], "cases": run_cases(),
    }
    roots = [("fleet", WORKSPACE / "fleet")] + [(n, args.projects / n) for n in REPOSITORIES]
    for label, root in roots:
        if not root.is_dir():
            raise FileNotFoundError(root)
        report["repositories"].append(scan_one(root.resolve(), label, args.out))
    report["real_cases"] = run_real_checks(report["repositories"], args.out)
    write_json(args.out / "results.json", report)
    (args.out / "SUMMARY.md").write_text(markdown_summary(report))
    passed = sum(c["passed"] for c in report["cases"])
    print(f"Acceptance probes: {passed}/{len(report['cases'])}; output: {args.out}")
    if args.strict and (passed != len(report["cases"]) or
                        any(not c["passed"] for c in report["real_cases"])):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
