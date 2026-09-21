"""The extensibility contract, plus the edge kinds added alongside it.

`test_graph.py` covers behaviour per language. This file covers the claim the
refactor was for: that a language is data, and that the generic machinery does
not quietly lose things.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fleet.graph import CONTAIN, INHERIT, parse_source
from fleet.graph.engine import TREE_SITTER_AVAILABLE, dropped_patterns
from fleet.graph.resolve import contain_edges, inherit_edges, resolve_edges
from fleet.graph.spec import all_specs, spec_for, supported_languages

pytestmark = pytest.mark.skipif(not TREE_SITTER_AVAILABLE, reason="tree-sitter not installed")


# ---------------------------------------------------------------------------
# A language is data
# ---------------------------------------------------------------------------


def test_every_registered_language_ships_the_query_it_names() -> None:
    """A spec without its `.scm` is a packaging bug — it would degrade to
    silently parsing nothing rather than failing loudly, so assert it here."""
    for spec in all_specs():
        if spec.backend == "tree_sitter":
            assert spec.query_source().strip(), f"{spec.name} has an empty query"


def test_only_expected_dialect_patterns_are_dropped() -> None:
    """The engine tolerates patterns a grammar can't have, so js/ts/tsx can
    share one query file. That tolerance must never hide a typo: every drop is
    accounted for here, and a new one fails this test until it's explained."""
    expected_drops = {
        # Plain JS has no extends_clause/implements_clause under class_heritage
        # (2), and none of TypeScript's six type-level declaration patterns:
        # interface, enum, type alias, abstract class and the two enum-member
        # shapes. Eight is the whole TS-only half of a shared query file.
        "javascript": 8,
        # ...and TypeScript/TSX have no bare identifier there.
        "typescript": 1,
        "tsx": 1,
        "go": 0,
        "rust": 0,
        "java": 0,
    }
    for name, expected in expected_drops.items():
        spec = spec_for(name)
        assert spec is not None
        dropped = dropped_patterns(spec)
        assert len(dropped) == expected, f"{name} dropped {[p for p, _ in dropped]}"


def test_no_query_uses_an_optional_field_pattern() -> None:
    """The one silent-failure mode queries actually have, locked out.

    A field pattern marked `?` that is also written in the wrong order
    relative to the grammar's own field order does not error — it silently
    matches with that capture missing. Verified directly on tree-sitter
    0.26.0: `(impl_item type: (type_identifier) @t trait: (type_identifier)?
    @tr)` drops `GlobalAlloc` from a real trait impl and reports nothing.
    Without `?`, the same wrong order is a hard "Impossible pattern" error.

    So: never `?` on a field. Write separate single-field patterns instead and
    let `engine._merge_matches` recombine them by node span — a pattern with
    one field cannot have a wrong field order. This is why that merge step
    exists, and this test is what keeps the rule.
    """
    predicates = ("#any-of?", "#eq?", "#match?", "#not-eq?", "#not-any-of?")
    for spec in all_specs():
        if spec.backend != "tree_sitter":
            continue
        for number, line in enumerate(spec.query_source().splitlines(), start=1):
            code = line.split(";", 1)[0]
            for predicate in predicates:
                code = code.replace(predicate, "")
            assert "?" not in code, f"{spec.query}.scm:{number} uses an optional pattern: {line!r}"


def test_rust_trait_impl_capture_survives_field_order() -> None:
    """The concrete case the rule above protects: a trait impl must yield both
    the Self type and the trait, in either reading order, because they are
    captured by two separate patterns rather than one ordered pair."""
    parsed = parse_source(
        "a.rs",
        "impl GlobalAlloc for Bump {\n    unsafe fn alloc(&self) {}\n}\nimpl Bump {\n    fn new() {}\n}\n",
    )
    by_name = {symbol.qualified_name: symbol for symbol in parsed.symbols}
    # Trait impl: qualified by the Self type, carrying the trait as a base.
    assert by_name["Bump.alloc"].bases == ("GlobalAlloc",)
    # Inherent impl: same qualification, no trait to carry.
    assert by_name["Bump.new"].bases == ()


def test_java_works_without_touching_the_engine() -> None:
    """Java was added as a spec plus a query and nothing else. If this passes,
    the layout does what it claims; if adding a language ever needs an engine
    change, that is the abstraction leaking."""
    assert "java" in supported_languages()
    parsed = parse_source(
        "src/main/java/com/foo/Dog.java",
        "package com.foo;\n"
        "import com.foo.Animal;\n"
        "class Dog extends Animal implements Pet {\n"
        "    @Test\n"
        "    public void testBark() { helper(); }\n"
        "    public void walk() {}\n"
        "}\n",
    )
    assert parsed.parser == "tree_sitter"
    by_name = {symbol.qualified_name: symbol for symbol in parsed.symbols}

    assert by_name["Dog"].kind == "class"
    assert by_name["Dog"].bases == ("Animal", "Pet")
    # Qualified by the enclosing class through the generic rule, not a
    # Java-specific parent walk.
    assert by_name["Dog.testBark"].kind == "method"
    assert by_name["Dog.testBark"].is_test is True
    # @Test marks one method; a sibling without it must not be swept up.
    assert by_name["Dog.walk"].is_test is False
    assert [imp.module for imp in parsed.imports] == ["com.foo.Animal"]


def test_java_imports_resolve_through_the_shared_dotted_strategy() -> None:
    """Java reuses Python's "dotted" resolver. The only Java-shaped fact is
    `source_roots`, declared as data — `src/main/java/com/foo/Bar.java` has to
    lose its build-tool prefix before it reads as `com.foo.Bar`."""
    files = [
        parse_source("src/main/java/com/foo/Dog.java", "package com.foo;\nimport com.foo.Animal;\nclass Dog {}\n"),
        parse_source("src/main/java/com/foo/Animal.java", "package com.foo;\nclass Animal {}\n"),
    ]
    imports = [e for e in resolve_edges(files) if e.kind == "import"]
    assert [(e.source, e.target) for e in imports] == [
        ("src/main/java/com/foo/Dog.java", "src/main/java/com/foo/Animal.java")
    ]


# ---------------------------------------------------------------------------
# Regressions the refactor fixed
# ---------------------------------------------------------------------------


def test_rust_imports_resolve_through_the_shared_dotted_strategy() -> None:
    """Rust reuses the same "dotted" strategy as Python and Java; all that
    differs is declared data — `::` as separator, `crate` as root, and
    mod/lib/main as index names. Before this, Rust had no resolver at all, so
    every Rust file reported "imported by 0"."""
    files = [
        parse_source("src/main.rs", "mod app;\nmod models;\nuse app::AppController as App;\n"),
        parse_source("src/app.rs", "use crate::models::AppState;\nuse crate::parser;\n"),
        parse_source("src/models.rs", "pub struct AppState;\n"),
        parse_source("src/parser.rs", "pub fn parse() {}\n"),
    ]
    edges = {(e.source, e.target) for e in resolve_edges(files) if e.kind == "import"}
    # `crate::models::AppState` addresses the *module* models, not a file
    # named AppState.
    assert ("src/app.rs", "src/models.rs") in edges
    # A bare `use app::…` is rooted at the crate after `mod app;`.
    assert ("src/main.rs", "src/app.rs") in edges


def test_a_crate_rooted_import_beats_the_bare_crate_root() -> None:
    """`use crate::parser` splits into module `crate` plus symbol `parser`,
    and `crate` is itself a real file — `src/main.rs`. Candidates are ordered
    most-specific-first for exactly this reason; the other order pointed every
    such import at main.rs, which looked plausible and was wrong."""
    files = [
        parse_source("src/main.rs", "mod parser;\n"),
        parse_source("src/app.rs", "use crate::parser;\n"),
        parse_source("src/parser.rs", "pub fn parse() {}\n"),
    ]
    edges = {(e.source, e.target) for e in resolve_edges(files) if e.kind == "import"}
    assert ("src/app.rs", "src/parser.rs") in edges
    assert ("src/app.rs", "src/main.rs") not in edges


def test_a_nested_crate_root_is_found_and_scoped_to_its_own_crate() -> None:
    """A source root is not always at the top of the tree. A Cargo workspace
    gives every crate its own `src/`, so the root has to be found anywhere in
    the path — and the directory above it has to stay part of the index key,
    or two crates that both define `crate::config` resolve each other's
    imports and invent edges across a boundary that doesn't exist."""
    files = [
        parse_source("services/api/src/lib.rs", "pub mod config;\n"),
        parse_source("services/api/src/config.rs", "pub struct Config;\n"),
        parse_source("services/api/src/handler.rs", "use crate::config::Config;\n"),
        parse_source("services/worker/src/lib.rs", "pub mod config;\n"),
        parse_source("services/worker/src/config.rs", "pub struct Config;\n"),
    ]
    edges = {(e.source, e.target) for e in resolve_edges(files) if e.kind == "import"}
    assert ("services/api/src/handler.rs", "services/api/src/config.rs") in edges
    assert ("services/api/src/handler.rs", "services/worker/src/config.rs") not in edges


def test_rust_super_and_self_relative_imports_resolve() -> None:
    files = [
        parse_source("src/a/b.rs", "use super::sibling::Thing;\n"),
        parse_source("src/a/sibling.rs", "pub struct Thing;\n"),
    ]
    edges = {(e.source, e.target) for e in resolve_edges(files) if e.kind == "import"}
    assert ("src/a/b.rs", "src/a/sibling.rs") in edges


def test_go_type_declarations_are_captured_at_all() -> None:
    """The previous implementation dropped every Go type declaration in
    silence: it looked for a `name` field on `type_declaration`, which the
    grammar does not have, so the name came back None and the symbol was
    skipped. Scanning a real repo found 48 missing this way."""
    parsed = parse_source(
        "types.go",
        "package m\ntype Cast struct{}\ntype Shape interface{}\ntype ID string\n",
    )
    kinds = {symbol.name: symbol.kind for symbol in parsed.symbols}
    assert kinds == {"Cast": "struct", "Shape": "interface", "ID": "type"}


def test_class_bases_do_not_leak_onto_its_methods() -> None:
    """Only a container captured as @scope.base passes bases down — that is
    real for a Rust impl block, which has no symbol of its own to hang the
    trait on. A class's own `extends` belongs to the class alone."""
    parsed = parse_source(
        "D.java",
        "class Dog extends Animal {\n    public void bark() {}\n}\n",
    )
    by_name = {symbol.qualified_name: symbol for symbol in parsed.symbols}
    assert by_name["Dog"].bases == ("Animal",)
    assert by_name["Dog.bark"].bases == ()


def test_rust_impl_still_hands_its_trait_to_the_methods_inside_it() -> None:
    """The counterpart to the test above, and the reason the distinction has
    to exist rather than bases simply never propagating."""
    parsed = parse_source(
        "a.rs",
        "struct Bump;\nimpl GlobalAlloc for Bump {\n    unsafe fn alloc(&self) {}\n}\n",
    )
    by_name = {symbol.qualified_name: symbol for symbol in parsed.symbols}
    assert by_name["Bump.alloc"].kind == "method"
    assert by_name["Bump.alloc"].bases == ("GlobalAlloc",)


def test_js_iterator_named_functions_are_not_reported_as_tests() -> None:
    """`it` as a name prefix used to flag `items`/`iterate`/`iterator`. Real
    JS tests come from calling `it`/`describe`, which the query catches."""
    parsed = parse_source(
        "a.js",
        "function iterate() {}\nfunction items() {}\nit('really is a test', () => {});\n",
    )
    flagged = {symbol.name for symbol in parsed.symbols if symbol.is_test}
    assert flagged == {"really is a test"}


# ---------------------------------------------------------------------------
# Edge kinds
# ---------------------------------------------------------------------------


def test_two_impls_of_the_same_trait_method_stay_two_nodes() -> None:
    """Rust's `impl From<A> for E` and `impl From<B> for E` both produce
    `E.from`. The generic parameter that tells them apart isn't in this model,
    so ids would collide and the two functions would merge into one graph
    node. Found in calc-rs, which has exactly this pair."""
    parsed = parse_source(
        "p.rs",
        "enum E { A }\n"
        "impl From<io::Error> for E {\n    fn from(e: io::Error) -> Self { E::A }\n}\n"
        "impl From<json::Error> for E {\n    fn from(e: json::Error) -> Self { E::A }\n}\n",
    )
    froms = [s for s in parsed.symbols if s.qualified_name == "E.from"]
    assert len(froms) == 2
    assert len({s.id for s in froms}) == 2, "both impls collapsed onto one id"
    # Symbols that don't collide keep a clean, readable id.
    enum_symbol = next(s for s in parsed.symbols if s.kind == "enum")
    assert enum_symbol.id == "p.rs::E"


def test_contain_edges_run_file_to_class_to_method() -> None:
    parsed = parse_source("m.py", "class Dog:\n    def bark(self):\n        pass\n")
    edges = {(e.source, e.target) for e in contain_edges([parsed])}
    assert ("m.py", "m.py::Dog") in edges
    assert ("m.py::Dog", "m.py::Dog.bark") in edges


def test_inherit_edges_connect_a_base_name_to_its_definition() -> None:
    files = [
        parse_source("animal.py", "class Animal:\n    pass\n"),
        parse_source("dog.py", "from animal import Animal\nclass Dog(Animal):\n    pass\n"),
    ]
    edges = [e for e in inherit_edges(files) if e.kind == INHERIT]
    assert [(e.source, e.target) for e in edges] == [("dog.py::Dog", "animal.py::Animal")]


def test_ambiguous_base_names_are_dropped_not_guessed() -> None:
    """Two classes named `Base` in different files, and a third extending
    `Base`: which one it means is not decidable from a name, so no edge is
    emitted. Being silently wrong here would poison every task built on it."""
    files = [
        parse_source("one.py", "class Base:\n    pass\n"),
        parse_source("two.py", "class Base:\n    pass\n"),
        parse_source("three.py", "class Child(Base):\n    pass\n"),
    ]
    assert inherit_edges(files) == []


def test_a_base_defined_in_the_same_file_wins_over_a_remote_one() -> None:
    files = [
        parse_source("far.py", "class Base:\n    pass\n"),
        parse_source("near.py", "class Base:\n    pass\nclass Child(Base):\n    pass\n"),
    ]
    edges = [(e.source, e.target) for e in inherit_edges(files)]
    assert ("near.py::Child", "near.py::Base") in edges
    assert ("near.py::Child", "far.py::Base") not in edges


def test_unresolved_bases_are_not_an_error() -> None:
    """`class Handler(BaseHTTPRequestHandler)` names a stdlib type. It
    correctly has no node in this repo's graph, and that is not a gap."""
    parsed = parse_source("h.py", "class Handler(BaseHTTPRequestHandler):\n    pass\n")
    assert inherit_edges([parsed]) == []


def test_calls_are_recorded_but_not_yet_turned_into_edges() -> None:
    """Call sites are captured so the `invoke` pass has data to work from;
    emitting edges from bare names without type information would be guesswork,
    so nothing does it yet."""
    parsed = parse_source("m.py", "def outer():\n    helper()\n")
    assert [(c.name, c.caller) for c in parsed.calls] == [("helper", "outer")]
    assert all(edge.kind != "invoke" for edge in resolve_edges([parsed]))


# ---------------------------------------------------------------------------
# Families
# ---------------------------------------------------------------------------


def test_a_typescript_file_resolves_an_import_onto_a_tsx_file() -> None:
    """js/ts/tsx are one family, which is how their imports find each other.
    The previous design had no way to say this and de-duplicated resolver
    functions by `id()` to work around it."""
    files = [
        parse_source("src/index.ts", "import { App } from './App';\n"),
        parse_source("src/App.tsx", "export const App = () => null;\n"),
    ]
    imports = [e for e in resolve_edges(files) if e.kind == "import"]
    assert [(e.source, e.target) for e in imports] == [("src/index.ts", "src/App.tsx")]


def test_scan_reports_edges_broken_down_by_kind(tmp_path: Path) -> None:
    (tmp_path / "animal.py").write_text("class Animal:\n    def speak(self):\n        pass\n")
    (tmp_path / "dog.py").write_text("from animal import Animal\nclass Dog(Animal):\n    pass\n")

    from fleet.graph import scan_repo

    stats = scan_repo(tmp_path).statistics()
    by_kind = stats["edges_by_kind"]
    assert by_kind["import"] == 1
    assert by_kind["inherit"] == 1
    assert by_kind[CONTAIN] == stats["symbols_total"]
