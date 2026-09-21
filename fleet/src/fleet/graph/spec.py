"""Language registration and parser configuration.

Queries share an extraction contract. Resolving imported names also requires
language-specific package and scope rules; Python/Rust implement those in bindings.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

_QUERY_DIR = Path(__file__).parent / "queries"


@dataclass(frozen=True, slots=True)
class TestRules:
    """How this language marks a test, declaratively.

    Covers the three real conventions: a name prefix (Python `test_`, Go
    `Test`), an annotation/attribute/decorator (Java `@Test`, Rust `#[test]`),
    and a registering call (`describe(...)`, `it(...)`) — the last handled in
    the query itself via `@definition.test`, so it needs no entry here.
    """

    name_prefixes: tuple[str, ...] = ()
    # Matched as a substring against the text of any `@annotation` capture
    # attached to (or immediately preceding) the definition.
    annotations: tuple[str, ...] = ()

    def matches(self, name: str, annotations: tuple[str, ...]) -> bool:
        if any(name.startswith(prefix) for prefix in self.name_prefixes):
            return True
        return any(marker in text for text in annotations for marker in self.annotations)


@dataclass(frozen=True, slots=True)
class LanguageSpec:
    name: str
    extensions: tuple[str, ...]

    # tree-sitter grammar to parse with. Distinct from `name` because
    # TypeScript and TSX are separate grammars that share JavaScript's node
    # names: parsing a `.ts` file with the JavaScript grammar reports a syntax
    # error on every type annotation, and `.tsx` needs its own grammar again.
    grammar: str = ""
    # Basename of the `.scm` file in `queries/`. Defaults to `name`; set it to
    # share one query across grammars (js/ts/tsx all use `javascript.scm`).
    query: str = ""
    # "tree_sitter" or "python_ast". Python keeps `ast` — the same parser
    # CPython uses — because it resolves dotted imports without a node table
    # and hands us docstrings for free. Expressed as a declared backend rather
    # than a branch in the dispatcher.
    backend: str = "tree_sitter"

    # Second pass over the same tree, by name, from `engine.BINDERS`: reads
    # import bindings, exports and lexical uses out of a syntax tree the
    # backend has already produced. Declared rather than branched on, so
    # `engine` never asks which language it is holding. Empty means the
    # language records raw imports and nothing more — no binding edges are
    # invented for it.
    binder: str = ""

    # Languages whose files can satisfy each other's imports. js/ts/tsx are one
    # family: a `.ts` file importing "./foo" may legitimately land on `foo.tsx`.
    # Empty means the language forms a family of its own.
    family: str = ""

    # Import-resolution strategy, by name, from `resolve.RESOLVERS`:
    #   "dotted"   — Python-style: a file's path *is* its module name.
    #   "relative" — Node-style: "./x" joined against the importer's directory.
    #   ""         — none yet. Raw imports are still recorded; no edges are
    #                invented for them.
    resolver: str = ""
    # Filenames that stand in for their own directory: `__init__` for Python,
    # `index` for Node.
    index_names: tuple[str, ...] = ()
    # Directory prefixes stripped before a path is read as a module name, for
    # languages whose build tools insert one: Java's `import com.foo.Bar`
    # addresses `src/main/java/com/foo/Bar.java`. Longest match wins, so order
    # here doesn't matter.
    source_roots: tuple[str, ...] = ()

    # --- knobs the "dotted" strategy reads, so it can serve more than Python.
    # What joins module path segments: "." for Python and Java, "::" for Rust.
    separator: str = "."
    # The name an absolute path starts from, where the language has one: Rust
    # writes `crate::models`, Python and Java just write the module. Also tried
    # as a prefix on an otherwise-unresolved path, which is what makes
    # `use app::Controller` find `src/app.rs` after a `mod app;`.
    root_name: str = ""
    # Words meaning "this module" and "the module above", for languages that
    # spell relative imports as words. Python instead uses leading dots, which
    # `_relative_dotted` handles; a spec declares one convention or the other,
    # and the resolver picks by what's declared rather than by language name.
    self_marker: str = ""
    parent_marker: str = ""

    tests: TestRules = field(default_factory=TestRules)

    def __post_init__(self) -> None:
        # Frozen dataclass: fill the defaults-from-`name` through object.__setattr__.
        if not self.grammar:
            object.__setattr__(self, "grammar", self.name)
        if not self.query:
            object.__setattr__(self, "query", self.name)
        if not self.family:
            object.__setattr__(self, "family", self.name)

    def query_source(self) -> str:
        """The `.scm` text for this language. Raises if the file is missing —
        a spec without its query is a packaging bug, not a runtime condition."""
        return (_QUERY_DIR / f"{self.query}.scm").read_text(encoding="utf-8")


_SPECS: dict[str, LanguageSpec] = {}
_BY_EXTENSION: dict[str, LanguageSpec] = {}


def register(spec: LanguageSpec) -> LanguageSpec:
    _SPECS[spec.name] = spec
    for extension in spec.extensions:
        _BY_EXTENSION[extension] = spec
    return spec


def detect_language(path: str | Path) -> str | None:
    """Detect a file's language from its extension. `None` if unrecognized."""
    spec = _BY_EXTENSION.get(Path(path).suffix.lower())
    return spec.name if spec else None


def spec_for(language: str) -> LanguageSpec | None:
    return _SPECS.get(language)


def specs_in_family(family: str) -> list[LanguageSpec]:
    return [spec for spec in _SPECS.values() if spec.family == family]


def supported_extensions() -> frozenset[str]:
    return frozenset(_BY_EXTENSION)


def supported_languages() -> frozenset[str]:
    return frozenset(_SPECS)


def all_specs() -> list[LanguageSpec]:
    return list(_SPECS.values())
