"""JS/TS import-use bindings: the same behaviour Python and Rust already have.

Every test here states the expected answer from the source in the test itself,
not from what the resolver happens to produce.
"""
from pathlib import Path

import pytest

from fleet.graph.engine import TREE_SITTER_AVAILABLE
from fleet.graph.parser import parse_source
from fleet.graph.resolve import resolve_edges
from fleet.graph.scan import scan_repo

pytestmark = pytest.mark.skipif(not TREE_SITTER_AVAILABLE, reason="tree-sitter not installed")

SAMPLE = Path(__file__).parent / "sample_repository"


def binding(file, local):
    return next(b for b in file.bindings if b.local == local)


def resolved(files):
    """Resolve a file set and hand back both the edges and the files, since
    binding status is only filled in by resolution."""
    return resolve_edges(files), {f.path: f for f in files}


def test_sample_typescript_bindings_resolve_through_the_barrel_file():
    """The sample's TypeScript package really runs under `node --test`; these
    are the same relationships that execution exercises."""
    graph = scan_repo(SAMPLE)
    files = {f.path: f for f in graph.files}
    service = files["typescript/src/service.ts"]

    # `Basket` is imported from index.ts, which only re-exports it. The
    # binding has to land on the file that defines it.
    assert binding(service, "Box").target_file == "typescript/src/model.ts"
    assert binding(service, "Box").target_symbol == "typescript/src/model.ts::Basket"
    assert binding(service, "twice").target_symbol == "typescript/src/model.ts::double"
    # A type-only import is still a binding with a resolved target.
    assert binding(service, "Countable").target_symbol == "typescript/src/model.ts::Countable"
    # Namespace import: the binding names the module, the *use* names a member.
    assert binding(service, "model").target_file == "typescript/src/model.ts"
    assert binding(service, "model").uses[0]["target_symbol"] == "typescript/src/model.ts::double"
    assert binding(service, "inspect").status == "external"

    # A nested function owns its own uses, and the parameter named `twice` in
    # `shadow` shadows the import rather than being credited to it.
    owners = [u["owner"] for u in binding(service, "twice").uses]
    assert owners == ["build", "nested.run"]


def test_a_default_export_resolves_to_the_declaration_behind_it():
    files = [
        parse_source("app.ts", 'import boot from "./boot";\nexport function start() { return boot(); }\n'),
        parse_source("boot.ts", "export default function launch() { return 1; }\n"),
    ]
    edges, by_path = resolved(files)
    assert binding(by_path["app.ts"], "boot").target_symbol == "boot.ts::launch"
    assert any(e.kind == "invoke" and e.source == "app.ts::start" and e.target == "boot.ts::launch" for e in edges)


def test_an_export_that_renames_is_followed_rather_than_name_matched():
    """`export { internal as public }` is the case that makes matching an
    imported name against top-level definitions wrong: the visible name and
    the defining name are different, in both directions."""
    files = [
        parse_source("use.ts", 'import { publicName } from "./impl";\nexport function f() { return publicName(); }\n'),
        parse_source("impl.ts", "function internalName() {}\nexport { internalName as publicName };\n"),
    ]
    _edges, by_path = resolved(files)
    assert binding(by_path["use.ts"], "publicName").target_symbol == "impl.ts::internalName"
    # The reverse mistake: `internalName` is not importable under that name.
    other = [parse_source("x.ts", 'import { internalName } from "./impl";\n'), by_path["impl.ts"]]
    resolve_edges(other)
    assert binding(other[0], "internalName").status == "unresolved"


def test_a_star_reexport_is_followed_to_the_defining_file():
    files = [
        parse_source("main.ts", 'import { helper } from "./barrel";\n'),
        parse_source("barrel.ts", 'export * from "./helpers";\n'),
        parse_source("helpers.ts", "export function helper() {}\n"),
    ]
    edges, by_path = resolved(files)
    assert binding(by_path["main.ts"], "helper").target_symbol == "helpers.ts::helper"
    pairs = {(e.source, e.target) for e in edges if e.kind == "import"}
    # Both hops are evidenced: main really does load barrel.
    assert ("main.ts", "barrel.ts") in pairs
    assert ("barrel.ts", "helpers.ts") in pairs


def test_a_reexport_cycle_terminates():
    """Two barrels re-exporting each other is a real thing people write. It
    must not recurse forever, and must not invent a target."""
    files = [
        parse_source("a.ts", 'export * from "./b";\n'),
        parse_source("b.ts", 'export * from "./a";\n'),
        parse_source("main.ts", 'import { nothing } from "./a";\n'),
    ]
    _edges, by_path = resolved(files)
    assert binding(by_path["main.ts"], "nothing").status == "unresolved"


def test_specifier_resolution_covers_index_extension_and_emitted_js():
    files = [
        parse_source("src/index.ts", "export const marker = 1;\n"),
        parse_source("src/util.ts", "export function helper() {}\n"),
        parse_source("app.ts", 'import { marker } from "./src";\n'),          # directory -> index
        parse_source("other.ts", 'import { helper } from "./src/util.js";\n'),  # NodeNext spelling
        parse_source("third.ts", 'import { helper } from "./src/util.ts";\n'),  # explicit
    ]
    _edges, by_path = resolved(files)
    assert binding(by_path["app.ts"], "marker").target_file == "src/index.ts"
    assert binding(by_path["other.ts"], "helper").target_symbol == "src/util.ts::helper"
    assert binding(by_path["third.ts"], "helper").target_symbol == "src/util.ts::helper"


def test_a_bare_specifier_is_a_package_and_a_missing_relative_one_is_not():
    files = [parse_source("a.ts", (
        'import { z } from "zod";\n'
        'import fs from "node:fs";\n'
        'import path from "path";\n'
        'import { gone } from "./missing";\n'
    ))]
    resolve_edges(files)
    a = files[0]
    # A package is external whether or not package.json was found; how well
    # that is evidenced is what the reason records.
    assert binding(a, "z").status == "external"
    assert binding(a, "fs").reason == "builtin_module"
    assert binding(a, "path").reason == "builtin_module"
    # A relative path that resolves to nothing is a real miss, not a package.
    assert binding(a, "gone").status == "unresolved"
    assert binding(a, "gone").reason == "relative_specifier_not_found"


def test_declared_dependencies_come_from_package_json(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"zod": "^3"}, "devDependencies": {"vitest": "^1"}}')
    (tmp_path / "a.ts").write_text('import { z } from "zod";\nimport { it } from "vitest";\nimport x from "untracked";\n')
    graph = scan_repo(tmp_path)
    a = next(f for f in graph.files if f.path == "a.ts")
    assert binding(a, "z").reason == "stdlib_or_declared_dependency"
    assert binding(a, "it").reason == "stdlib_or_declared_dependency"
    # Still external — a bare specifier is a package by Node's rules — but
    # nothing in the repository evidences it.
    assert binding(a, "x").reason == "no_matching_local_package"


def test_a_local_declaration_shadows_an_import_in_its_own_scope():
    files = [
        parse_source("m.ts", "export function value() { return 1; }\n"),
        parse_source("a.ts", (
            'import { value } from "./m";\n'
            "export function outer() { const value = 2; return value; }\n"
            "export function inner() { return value(); }\n"
        )),
    ]
    _edges, by_path = resolved(files)
    owners = [u["owner"] for u in binding(by_path["a.ts"], "value").uses]
    assert owners == ["inner"]  # not `outer`, whose const shadows it


def test_a_parameter_shadows_an_import_and_a_sibling_scope_does_not():
    files = [
        parse_source("m.ts", "export function run() {}\n"),
        parse_source("a.ts", (
            'import { run } from "./m";\n'
            "export function shadowed(run: () => void) { return run(); }\n"
            "export const arrow = () => run();\n"
        )),
    ]
    _edges, by_path = resolved(files)
    assert [u["owner"] for u in binding(by_path["a.ts"], "run").uses] == ["arrow"]


def test_extends_and_implements_become_inherit_edges_but_type_arguments_do_not():
    files = [
        parse_source("base.ts", "export class Base {}\nexport interface Sink<T> { push(v: T): void }\nexport interface Frame {}\n"),
        parse_source("derived.ts", (
            'import { Base, Sink, Frame } from "./base";\n'
            "export class Derived extends Base implements Sink<Frame> {\n"
            "  push(v: Frame): void {}\n"
            "}\n"
        )),
    ]
    edges, _by_path = resolved(files)
    inherits = {(e.source, e.target) for e in edges if e.kind == "inherit"}
    assert ("derived.ts::Derived", "base.ts::Base") in inherits
    assert ("derived.ts::Derived", "base.ts::Sink") in inherits
    # `Frame` is what Sink is parameterized with, not something Derived
    # inherits from. Recording it as a base would be a wrong edge.
    assert ("derived.ts::Derived", "base.ts::Frame") not in inherits


def test_an_enum_member_resolves_past_the_enum_itself():
    files = [
        parse_source("types.ts", "export enum Phase { Menu = 'M', Over = 'O' }\n"),
        parse_source("app.ts", 'import { Phase } from "./types";\nexport function f(p: Phase) { return p === Phase.Menu; }\n'),
    ]
    _edges, by_path = resolved(files)
    targets = [u["target_symbol"] for u in binding(by_path["app.ts"], "Phase").uses]
    assert "types.ts::Phase" in targets
    assert "types.ts::Phase.Menu" in targets


def test_a_side_effect_import_records_the_file_it_loads_and_no_name():
    files = [
        parse_source("polyfill.ts", "globalThis.x = 1;\n"),
        parse_source("app.ts", 'import "./polyfill";\n'),
    ]
    edges, by_path = resolved(files)
    b = by_path["app.ts"].bindings[0]
    assert (b.kind, b.local, b.target_file) == ("side_effect", "", "polyfill.ts")
    edge = next(e for e in edges if e.kind == "import" and e.source == "app.ts")
    assert edge.symbols == ()  # nothing was named, and it doesn't pretend one was


def test_commonjs_require_binds_in_its_two_common_shapes():
    files = [
        parse_source("lib.js", "function helper() {}\nmodule.exports = { helper };\n"),
        parse_source("named.js", 'const { helper } = require("./lib");\nfunction f() { return helper(); }\n'),
        parse_source("whole.js", 'const lib = require("./lib");\nfunction g() { return lib.helper(); }\n'),
    ]
    _edges, by_path = resolved(files)
    # `module.exports = ...` is not interpreted, so the name inside lib.js is
    # not resolved — but the file dependency is real and is recorded.
    assert binding(by_path["named.js"], "helper").target_file == "lib.js"
    assert binding(by_path["whole.js"], "lib").target_file == "lib.js"


def test_a_jsx_element_counts_as_a_use_of_its_import():
    files = [
        parse_source("Button.tsx", "export const Button = () => null;\n"),
        parse_source("App.tsx", 'import { Button } from "./Button";\nexport function App() { return <Button />; }\n'),
    ]
    _edges, by_path = resolved(files)
    assert [u["owner"] for u in binding(by_path["App.tsx"], "Button").uses] == ["App"]


def test_an_unexported_name_resolves_the_file_but_not_the_symbol():
    files = [
        parse_source("m.ts", "function hidden() {}\nexport function shown() {}\n"),
        parse_source("a.ts", 'import { hidden } from "./m";\n'),
    ]
    edges, by_path = resolved(files)
    b = binding(by_path["a.ts"], "hidden")
    assert b.status == "unresolved"
    assert b.reason == "module_resolved_symbol_unknown"
    # The import edge still stands: a.ts really does load m.ts.
    assert any(e.kind == "import" and e.source == "a.ts" and e.target == "m.ts" for e in edges)


def test_use_spans_point_at_the_real_source_bytes():
    """The audit asserts this across every real repository; assert it here too,
    so a span regression fails fast rather than at audit time."""
    code = 'import { helper } from "./m";\nexport function f() { return helper(); }\n'
    files = [parse_source("m.ts", "export function helper() {}\n"), parse_source("a.ts", code)]
    resolve_edges(files)
    use = binding(files[1], "helper").uses[0]
    assert code.encode()[use["start_byte"]:use["end_byte"]].decode() == use["source_text"] == "helper"
