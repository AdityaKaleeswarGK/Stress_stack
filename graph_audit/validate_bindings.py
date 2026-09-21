"""Save current graphs and independently source-witnessed import-use checks.

Runs no code or dependency installers in the real repositories. The sample
repository has separate runtime checks documented in fleet/GRAPH.md.
"""
from __future__ import annotations
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'fleet/src'))
from fleet.graph.scan import scan_repo, write_graph
from fleet.graph.query import file_context

REPOS = ['glom', 'pluggy', 'click', 'calc-rs', 'rust-bump-allocator', 'tui-calculator', 'FlappyBird-TS-Canvas', 'agustus']
# Expected answers specified from source, not generated from the resulting graph.
CHECKS = [
    ('glom', 'glom/mutation.py', 'Path', 'glom/core.py::Path', 'Assign.__init__', 'Path.from_text', 'glom/core.py::Path.from_text', 'path = Path.from_text(path)'),
    ('glom', 'glom/mutation.py', 'Path', 'glom/core.py::Path', 'Delete.__init__', 'Path.from_text', 'glom/core.py::Path.from_text', 'path = Path.from_text(path)'),
    ('pluggy', 'src/pluggy/_manager.py', '_multicall', 'src/pluggy/_callers.py::_multicall', 'PluginManager.__init__', '_multicall', 'src/pluggy/_callers.py::_multicall', 'self._inner_hookexec = _multicall'),
    ('pluggy', 'testing/test_pluginmanager.py', 'PluginManager', 'src/pluggy/_manager.py::PluginManager', 'test_pm', 'PluginManager', 'src/pluggy/_manager.py::PluginManager', 'def test_pm(pm: PluginManager)'),
    ('calc-rs', 'src/engine.rs', 'ASTNode', 'src/models.rs::ASTNode', 'MathEngine.evaluate', 'ASTNode', 'src/models.rs::ASTNode', 'pub fn evaluate(&self, node: ASTNode'),
    ('rust-bump-allocator', 'src/allocator.rs', 'reserve_space', 'src/state.rs::reserve_space', 'BumpAllocator.alloc', 'reserve_space', 'src/state.rs::reserve_space', 'reserve_space(layout)'),
    ('rust-bump-allocator', 'tests/test_state.rs', 'get_stats', 'src/state.rs::get_stats', 'tests.test_state_mutation', 'get_stats', 'src/state.rs::get_stats', 'let before = get_stats().used;'),
    ('rust-bump-allocator', 'tests/test_state.rs', 'reserve_space', 'src/state.rs::reserve_space', 'tests.test_bounds_detection', 'reserve_space', 'src/state.rs::reserve_space', 'reserve_space(huge_layout);'),
    # JS/TS. A type annotation is a use, an enum member resolves past the enum
    # it came from, and a name imported through a barrel file resolves to the
    # file that actually defines it.
    ('FlappyBird-TS-Canvas', 'engine/renderer.ts', 'GameSnapshot', 'types/game.ts::GameSnapshot', 'Renderer.render', 'GameSnapshot', 'types/game.ts::GameSnapshot', 'public render(snapshot: GameSnapshot): void {'),
    ('FlappyBird-TS-Canvas', 'engine/renderer.ts', 'GamePhase', 'types/game.ts::GamePhase', 'Renderer.render', 'GamePhase.Menu', 'types/game.ts::GamePhase.Menu', 'if (snapshot.phase === GamePhase.Menu)'),
    ('FlappyBird-TS-Canvas', 'main.ts', 'GamePhase', 'types/game.ts::GamePhase', 'gameLoop', 'GamePhase.Active', 'types/game.ts::GamePhase.Active', 'function gameLoop(timestamp: number) {'),
    ('agustus', 'src/mcp.ts', 'groupBySavedPeriod', 'src/time-groups.ts::groupBySavedPeriod', 'createServer', 'groupBySavedPeriod', 'src/time-groups.ts::groupBySavedPeriod', 'groupBySavedPeriod(words, { timeZone })'),
    ('agustus', 'src/index.ts', 'AppEnv', 'src/auth.ts::AppEnv', 'fetch', 'AppEnv', 'src/auth.ts::AppEnv', 'fetch(request: Request, env: AppEnv, ctx: ExecutionContext)'),
    ('sample', 'typescript/src/service.ts', 'Box', 'typescript/src/model.ts::Basket', 'build', 'Box', 'typescript/src/model.ts::Basket', 'import { Basket as Box } from "./index.ts";'),
]


def main():
    output = ROOT / 'graph_audit/binding_results'
    output.mkdir(exist_ok=True)
    graphs = {}
    results = {'scope': 'Python/Rust/JS-TS import binding and use checks', 'repositories': [], 'checks': []}
    roots = {name: ROOT.parent/name for name in REPOS}
    roots['sample'] = ROOT / 'fleet/tests/sample_repository'
    for name, root in roots.items():
        before = time.perf_counter()
        graph = scan_repo(root)
        elapsed = time.perf_counter() - before
        data = graph.to_dict()
        graphs[name] = data
        # Ensure every persisted location comes from the scanned source bytes.
        for file in data['files']:
            source = (root/file['path']).read_bytes()
            assert hashlib.sha256(source).hexdigest() == graph.snapshot['content_hashes'][file['path']]
            for b in file['bindings']:
                for use in b['uses']:
                    assert source[use['start_byte']:use['end_byte']].decode() == use['source_text']
        # Independently verify graph endpoints and inverse file queries.
        ids = {f['path'] for f in data['files']} | {s['id'] for f in data['files'] for s in f['symbols']}
        assert len([s['id'] for f in data['files'] for s in f['symbols']]) == len({s['id'] for f in data['files'] for s in f['symbols']})
        assert all(e['source'] in ids and e['target'] in ids for e in data['edges'])
        for file in data['files']:
            expected = sorted({e['source'] for e in data['edges'] if e['kind']=='import' and e['target']==file['path']})
            assert file_context(data,file['path'])['imported_by'] == expected
        destination = output/name/'repo_graph.json'
        destination.parent.mkdir(exist_ok=True)
        write_graph(graph,destination)
        status = graph.statistics()
        distinct_pairs = {(e.source,e.target) for e in graph.edges if e.kind=='import'}
        results['repositories'].append({'repository': name, 'seconds': round(elapsed,4), **status, 'distinct_file_import_pairs':len(distinct_pairs), 'snapshot_id':graph.snapshot['id']})
    for name, path, local, target, owner, expression, use_target, witness in CHECKS:
        source = (roots[name]/path).read_text()
        assert witness in source, f'Stale source witness: {name}/{path}: {witness}'
        file = next(f for f in graphs[name]['files'] if f['path']==path)
        matches = [b for b in file['bindings'] if b['local']==local and b['target_symbol']==target]
        observed = [u for b in matches for u in b['uses'] if u['owner']==owner and u['expression']==expression]
        passed = any(u['target_symbol']==use_target for u in observed)
        # Check the requested target is an actual saved source definition.
        assert any(s['id']==use_target for f in graphs[name]['files'] for s in f['symbols'])
        results['checks'].append({'repository':name, 'file':path,'local':local,'expected_binding':target,'owner':owner,'expression':expression,'expected_reference':use_target,'source_witness':witness,'passed':passed,'observed':observed})
    (output/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    lines = ['# Python/Rust/JS-TS import-use validation', '', 'Fresh source scans; real repository code was not executed. The sample repository has separately passing Python, Rust and TypeScript runtime checks.', '', '| Repository | Files | Symbols | File dependency pairs | Import bindings | Recorded uses | Scan seconds |','|---|---:|---:|---:|---:|---:|---:|']
    for r in results['repositories']:
        lines.append(f"| {r['repository']} | {r['files_total']} | {r['symbols_total']} | {r['distinct_file_import_pairs']} | {r['import_bindings']} | {r['import_uses']} | {r['seconds']} |")
    lines += ['', 'Counts are inventory measurements, not precision/recall. A binding can have zero recorded uses; that does not prove it is unused.', '', '| Source-checked use | Result |', '|---|---|']
    for c in results['checks']:
        lines.append(f"| {c['repository']}: {c['owner']} → {c['expression']} → {c['expected_reference']} | {'PASS' if c['passed'] else 'FAIL'} |")
    lines += ['', f"{sum(c['passed'] for c in results['checks'])}/{len(results['checks'])} focused source checks pass. These are development checks, not a held-out accuracy benchmark.", '', 'Saved spans, symbol-ID uniqueness, edge endpoints and reverse file-import queries were checked across every scan. Conditional imports remain labeled; macro tokens, wildcard resolution, string annotations, receiver type inference, `module.exports` assignment, dynamic `import()` and tsconfig path aliases remain outside this resolver.', '']
    (output/'SUMMARY.md').write_text('\n'.join(lines))
    print('\n'.join(lines))
    if not all(c['passed'] for c in results['checks']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
