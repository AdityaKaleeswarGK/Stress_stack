from pathlib import Path
import json

from fleet.graph.parser import parse_source
from fleet.graph.resolve import resolve_edges
from fleet.graph.scan import scan_repo
from fleet.graph.query import file_context

SAMPLE = Path(__file__).parent / 'sample_repository'


def binding(file, local):
    return next(b for b in file.bindings if b.local == local)


def test_sample_repository_cross_file_bindings_and_reverse_views():
    graph = scan_repo(SAMPLE)
    files = {f.path: f for f in graph.files}
    py = files['python/src/orchard/service.py']
    rs = files['rust/src/service.rs']
    assert binding(py, 'Box').target_symbol == 'python/src/orchard/model.py::Basket'
    assert binding(py, 'twice').target_symbol == 'python/src/orchard/model.py::double'
    assert [u['owner'] for u in binding(py, 'twice').uses] == ['build']
    assert binding(py, 'local_double').uses[0]['owner'] == 'nested.run'
    assert binding(py, 'model').uses[0]['target_symbol'] == 'python/src/orchard/model.py::double'
    assert binding(py, 'json').status == 'external'
    assert binding(rs, 'Box').uses[1]['target_symbol'] == 'rust/src/model.rs::Basket.new'
    assert binding(rs, 'max').status == 'external'
    assert rs.bindings[-1].uses[0]['owner'] == 'nested.run'
    assert binding(files['rust/tests/integration.rs'], 'Basket').target_symbol == 'rust/src/model.rs::Basket'
    # Both queries consume serialized facts, not ephemeral parser objects.
    saved = json.loads(json.dumps(graph.to_dict()))
    context = file_context(saved, 'python/src/orchard/model.py')
    assert 'python/src/orchard/service.py' in context['imported_by']
    assert any(u['owner'] == 'nested.run' for u in context['used_by'])
    assert all(u['owner'] != 'shadow' for u in context['used_by'])
    assert files['python/src/orchard/model.py'].to_dict()['symbols'][0]['docstring']


def test_python_multi_import_aliases_and_package_reexports():
    files = [parse_source('src/p/__init__.py', 'from .a import make as build\n'),
             parse_source('src/p/a.py', 'def make(): pass\n'),
             parse_source('src/p/b.py', 'def other(): pass\n'),
             parse_source('test.py', 'from p import a, b, build as run\nimport p.a\ndef f():\n run()\n p.a.make()\n')]
    edges = resolve_edges(files)
    test = files[-1]
    assert [b.target_file for b in test.bindings] == ['src/p/a.py', 'src/p/b.py', 'src/p/a.py', 'src/p/a.py']
    assert binding(test, 'run').uses[0]['target_symbol'] == 'src/p/a.py::make'
    assert binding(test, 'p').uses[0]['target_symbol'] == 'src/p/a.py::make'
    assert any(e.source == 'test.py::f' and e.target == 'src/p/a.py::make' and e.kind == 'invoke' for e in edges)


def test_python_shadowing_class_scope_comprehension_and_lambda():
    file = parse_source('x.py', '''from helper import run
class C:
    run = 3
    def f(self):
        return run()
def shadow(run):
    return run()
def outer():
    return [run() for run in []], (lambda run: run())
''')
    assert [(u['owner'], u['expression']) for u in binding(file, 'run').uses] == [('C.f', 'run')]


def test_python_nested_calls_not_duplicated_and_generator_flag():
    file = parse_source('x.py', '''from helper import run
def outer():
    def inner():
        yield run()
    return inner
''')
    symbols = {s.qualified_name: s for s in file.symbols}
    assert not symbols['outer'].is_generator
    assert symbols['outer.inner'].is_generator
    assert [(c.name,c.caller) for c in file.calls] == [('run','outer.inner')]
    assert len(binding(file,'run').uses) == 1


def test_python_external_base_never_matches_unrelated_local_symbol():
    files = [parse_source('x.py', 'from foreign import Base\nclass Child(Base): pass\n'),
             parse_source('other.py', 'class Base: pass\n')]
    assert not any(e.kind == 'inherit' for e in resolve_edges(files))
    assert files[0].bindings[0].status == 'external'
    assert files[0].bindings[0].reason == 'no_matching_local_package'


def test_python_imported_base_and_annotations_keep_declaring_owner():
    files = [parse_source('base.py', 'class Base: pass\n'),
             parse_source('child.py', 'from base import Base as Parent\nclass Child(Parent): pass\ndef f(x: Parent) -> Parent: return x\n')]
    edges = resolve_edges(files)
    assert any(e.kind == 'inherit' and e.source == 'child.py::Child' and e.target == 'base.py::Base' for e in edges)
    assert [(u['owner'],u['kind']) for u in files[1].bindings[0].uses] == [('Child','base'),('f','annotation'),('f','annotation')]


def test_python_unicode_source_spans_and_raw_aliases_roundtrip():
    source = 'from thing import foo as bar\ndef café():\n    """é"""\n    return bar()\n'
    file = parse_source('unicode.py', source)
    symbol = file.symbols[0]
    assert source.encode()[symbol.start_byte:symbol.end_byte].decode().startswith('def café')
    assert source.encode()[symbol.body_start_byte:symbol.body_end_byte].decode().startswith('"""é"""')
    assert file.to_dict()['imports'][0]['raw'] == 'from thing import foo as bar'
    assert file.to_dict()['calls'][0]['expression'] == 'bar'


def test_conditional_targets_and_wildcards_are_explicit():
    files = [parse_source('a.py', 'def go(): pass\n'),parse_source('b.py', 'if True:\n from a import go\nfrom a import *\ngo()\n')]
    resolve_edges(files)
    assert [b.status for b in files[1].bindings] == ['conditional','unsupported']
    assert files[1].bindings[0].conditional
    assert files[1].bindings[0].target_symbol == 'a.py::go'


def test_cyclic_reexports_terminate_and_duplicate_module_roots_are_ambiguous():
    files = [parse_source('a.py','from b import X\n'),parse_source('b.py','from a import X\n')]
    resolve_edges(files)
    assert all(b.status != 'internal' for f in files for b in f.bindings)
    files = [parse_source('one/src/p.py','def go(): pass\n'),parse_source('two/src/p.py','def go(): pass\n'),parse_source('consumer.py','from p import go\n')]
    resolve_edges(files, {'one/pyproject.toml': {}, 'two/pyproject.toml': {}})
    assert files[-1].bindings[0].status == 'ambiguous'


def test_rust_alias_nested_use_module_self_and_reexport():
    files = [parse_source('src/lib.rs','pub mod items; pub mod user; pub use items::Thing;'),
             parse_source('src/items.rs','pub struct Thing; pub fn go() {}'),
             parse_source('src/user.rs','use crate::items::{self as model, go as run}; fn f() { run(); model::go(); }'),
             parse_source('tests/api.rs','use demo::Thing; fn accept(x: Thing) {}')]
    resolve_edges(files, {'Cargo.toml': {'package': {'name': 'demo'}}})
    assert [u['target_symbol'] for b in files[2].bindings for u in b.uses] == ['src/items.rs::go','src/items.rs::go']
    assert files[3].bindings[0].target_symbol == 'src/items.rs::Thing'


def test_rust_shadowing_and_child_module_do_not_inherit_parent_use_scope():
    file = parse_source('src/lib.rs','use std::cmp::max; fn f(max: i32) { let x = max; } mod nested { fn f() { max(1,2); } }')
    assert file.bindings[0].uses == []


def test_rust_manifest_does_not_connect_an_undeclared_module():
    files = [parse_source('src/lib.rs','use crate::ghost::Thing;'),parse_source('src/ghost.rs','pub struct Thing;')]
    resolve_edges(files, {'Cargo.toml': {'package': {'name':'demo'}}})
    assert files[0].bindings[0].target_file is None


def test_rust_path_attribute_and_library_target_name():
    files = [parse_source('custom/root.rs','#[path="model.rs"] pub mod types;'),
             parse_source('custom/model.rs','pub struct Thing;'),parse_source('tests/t.rs','use api::types::Thing; fn f(x: Thing) {}')]
    resolve_edges(files, {'Cargo.toml': {'package': {'name':'distribution'}, 'lib': {'name':'api','path':'custom/root.rs'}}})
    assert files[-1].bindings[0].target_symbol == 'custom/model.rs::Thing'


def test_snapshot_changes_with_metadata_and_deleted_files(tmp_path):
    (tmp_path/'a.py').write_text('def go(): pass\n')
    (tmp_path/'b.py').write_text('from a import go\ngo()\n')
    first = scan_repo(tmp_path)
    assert first.snapshot == scan_repo(tmp_path).snapshot
    (tmp_path/'a.py').unlink()
    second = scan_repo(tmp_path)
    assert first.snapshot['id'] != second.snapshot['id']
    assert not second.files[0].bindings[0].target_file


def test_src_can_be_a_package_not_only_a_source_root():
    files = [parse_source('src/__init__.py',''),parse_source('src/engine.py','class Engine: pass\n'),parse_source('main.py','from src.engine import Engine\nEngine()\n')]
    resolve_edges(files)
    assert files[-1].bindings[0].target_symbol == 'src/engine.py::Engine'


def test_reference_before_import_is_not_misbound():
    file = parse_source('x.py','run()\nfrom helper import run\nrun()\n')
    assert [u['line'] for u in file.bindings[0].uses] == [3]
    assert file.diagnostics[0]['reason'] == 'reference_before_import'


def test_fleetignore_paths_negation_and_directory_pruning(tmp_path):
    for p in ['root.py','nested/root.py','nested/skip.py','deep/a/skip.py','generated/keep.py','generated/drop.py']:
        target = tmp_path / p
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text('pass\n')
    (tmp_path/'.fleetignore').write_text('/root.py\n**/skip.py\ngenerated/*\n!generated/keep.py\n')
    graph = scan_repo(tmp_path)
    assert {f.path for f in graph.files} == {'nested/root.py','generated/keep.py'}


def test_rust_local_path_dependency_uses_cargo_alias():
    files = [parse_source('app/src/lib.rs','use renamed::Thing; pub fn f(x: Thing) {}'),
             parse_source('library/src/lib.rs','pub struct Thing;')]
    metadata = {'app/Cargo.toml': {'package': {'name':'app'}, 'dependencies': {'renamed': {'path':'../library','package':'library'}}},
                'library/Cargo.toml': {'package': {'name':'library'}}}
    resolve_edges(files,metadata)
    assert files[0].bindings[0].target_symbol == 'library/src/lib.rs::Thing'


def test_rust_enum_patterns_reference_import_without_shadowing_it():
    file = parse_source('src/lib.rs','use other::Thing; fn f(x: Thing) { match x { Thing::One(n) => n } }')
    assert 'Thing::One' in [u['expression'] for u in file.bindings[0].uses]


def test_python_match_capture_shadows_import():
    file = parse_source('x.py','from a import go\ndef f(x):\n match x:\n  case go:\n   return go()\n')
    assert not file.bindings[0].uses


def test_python_package_value_shadows_same_named_submodule():
    files = [parse_source('p/__init__.py','sub = 42\n'),
             parse_source('p/sub.py','def go(): pass\n'),
             parse_source('user.py','from p import sub\ndef f(): return sub.go()\n')]
    edges = resolve_edges(files)
    imported = files[-1].bindings[0]
    assert imported.target_symbol is None
    assert imported.reason == 'module_resolved_symbol_unknown'
    assert not any(e.kind=='invoke' for e in edges)
    assert not any(e.kind=='import' and e.source=='user.py' and e.target=='p/sub.py' for e in edges)


def test_missing_local_package_defaults_to_external_but_local_gaps_do_not():
    files = [parse_source('pkg/__init__.py', 'class Known: pass\n'),
             parse_source('pkg/user.py', 'from third_party import Client\nfrom pkg import Missing\nfrom .missing import Item\nimport pkg.missing\n'),
             parse_source('src/lib.rs', 'use missing_crate::Client; use crate::missing::Item;')]
    resolve_edges(files, {'Cargo.toml': {'package': {'name': 'demo'}}})
    assert [b.status for b in files[1].bindings] == ['external', 'unresolved', 'unresolved', 'unresolved']
    assert [b.status for b in files[2].bindings] == ['external', 'unresolved']
    assert files[1].bindings[0].reason == files[2].bindings[0].reason == 'no_matching_local_package'
