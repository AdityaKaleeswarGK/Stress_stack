"""The shapes a parsed file comes back as, independent of which parser ran.

Nothing in here knows about a specific language. Every backend (tree-sitter
queries, Python's `ast`) produces these same types directly — there is no
intermediate dict, so adding a field means editing one place, and a typo is a
type error rather than a silently missing key.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = "0.4.0"


@dataclass
class ImportBinding:
    """One imported local name, its source, and witnessed uses (never LLM inferred)."""

    id: str
    module: str
    name: str
    local: str
    scope: str
    line: int
    raw: str
    kind: str = "from"
    reexport: bool = False
    conditional: bool = False
    uses: list[dict[str, Any]] = field(default_factory=list)
    status: str = "unresolved"
    reason: str = "not_resolved"
    target_file: str | None = None
    target_symbol: str | None = None
    # Python `import pkg.mod` binds pkg while loading pkg.mod.
    bound_module: str = ""
    module_scope: str = ""
    start_byte: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExtractedImport:
    raw: str
    module: str
    symbols: list[str] = field(default_factory=list)
    line: int = 1


@dataclass(frozen=True, slots=True)
class ExtractedSymbol:
    name: str
    qualified_name: str
    kind: str  # function, method, class, struct, trait, enum, interface, type, test
    start_line: int
    end_line: int
    first_body_line: int
    last_body_line: int
    is_test: bool = False
    is_async: bool = False
    is_generator: bool = False
    # Names this symbol inherits from or implements — `("Animal",)` for
    # `class Dog(Animal)`, `("GlobalAlloc",)` for `impl GlobalAlloc for
    # BumpAllocator` (attached to each method that impl produces, since a Rust
    # impl block has no symbol of its own). Plain text here; `resolve.py`
    # turns them into `inherit` edges where the name matches a symbol in this
    # repo.
    bases: tuple[str, ...] = ()
    docstring: str = ""
    # Exact byte span of the body, when the backend supplied one (tree-sitter
    # only — `ast` doesn't expose byte offsets the same way). A single-line
    # definition like `func Mul(a, b int) int { return a * b }` has its body on
    # the signature's own line, so a line-range replace would delete the
    # declaration too. This is what excision will need later.
    body_start_byte: int | None = None
    body_end_byte: int | None = None
    path: str = ""
    # Qualified name of the symbol lexically containing this one, when any —
    # the `contain` edge's other end, and what `qualified_name` was built from.
    parent: str = ""
    # Set only when a file holds more than one symbol with the same qualified
    # name, to keep `id` unique. Rust makes this ordinary rather than exotic:
    # `impl From<io::Error> for E` and `impl From<json::Error> for E` both
    # produce `E.from`, and what really tells them apart — the generic
    # parameter — is not something this model records. Without a suffix both
    # would be one node, silently merging two distinct functions.
    id_suffix: str = ""
    start_byte: int | None = None
    end_byte: int | None = None
    signature: str = ""

    @property
    def id(self) -> str:
        name = f"{self.qualified_name}{self.id_suffix}"
        return f"{self.path}::{name}" if self.path else name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "kind": self.kind,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "first_body_line": self.first_body_line,
            "last_body_line": self.last_body_line,
            "is_test": self.is_test,
            "is_async": self.is_async,
            "is_generator": self.is_generator,
            "body_start_byte": self.body_start_byte,
            "body_end_byte": self.body_end_byte,
            "bases": list(self.bases),
            "parent": self.parent,
            "id": self.id,
            "docstring": self.docstring,
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
            "signature": self.signature,
        }


@dataclass(frozen=True, slots=True)
class ExtractedCall:
    """A syntactic call site. Import-bound targets are resolved separately."""

    name: str
    line: int
    # Qualified name of the symbol this call appears inside, if any.
    caller: str = ""
    expression: str = ""


@dataclass
class ParsedFile:
    path: str
    language: str | None
    # Which backend actually produced this: "ast" | "tree_sitter" | "none".
    # "none" (known extension, nothing extracted) must never be confused with a
    # genuinely empty file — a graph that can't tell those apart reports a gap
    # as a fact.
    parser: str = "none"
    imports: list[ExtractedImport] = field(default_factory=list)
    symbols: list[ExtractedSymbol] = field(default_factory=list)
    calls: list[ExtractedCall] = field(default_factory=list)
    has_syntax_error: bool = False
    # An empty/whitespace-only file always has parser == "none" — that's
    # correct, not a missing-grammar problem. Tracked separately so stats don't
    # conflate "nothing to parse" with "couldn't parse it".
    is_empty: bool = False
    bindings: list[ImportBinding] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    # Rust module declarations and inline-module ownership, from Tree-sitter.
    modules: list[dict[str, Any]] = field(default_factory=list)
    # What a module makes importable, for languages that say so explicitly.
    # Python and Rust do not need this — a Python importer addresses a symbol
    # by its own name, and Rust's visibility is not type-checked here — but a
    # JS/TS name arrives through an export clause that can rename it
    # (`export { internal as public }`) or forward it somewhere else entirely
    # (`export { x } from "./other"`). Without recording that, resolving an
    # imported name would mean guessing that it matches a top-level
    # definition, which is wrong exactly where re-exports are involved.
    # Entries: {"exported", "local", "module", "line"} — `module` non-empty
    # only for a re-export, `local` empty when no name backs it
    # (`export default <expression>`).
    exports: list[dict[str, Any]] = field(default_factory=list)
    source_hash: str = ""
    bound_names: list[str] = field(default_factory=list)

    @property
    def tests(self) -> list[ExtractedSymbol]:
        return [s for s in self.symbols if s.is_test]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "language": self.language,
            "parser": self.parser,
            "has_syntax_error": self.has_syntax_error,
            "is_empty": self.is_empty,
            "source_hash": self.source_hash,
            "bound_names": self.bound_names,
            "bindings": [b.to_dict() for b in self.bindings],
            "calls": [asdict(c) for c in self.calls],
            "diagnostics": self.diagnostics,
            "modules": self.modules,
            "exports": self.exports,
            "imports": [
                {"raw": i.raw, "module": i.module, "symbols": i.symbols, "line": i.line}
                for i in self.imports
            ],
            "symbols": [s.to_dict() for s in self.symbols],
        }


# Edge kinds. `import` and `contain` are structural facts; `inherit` is a
# name match and so can be wrong where two classes share a name across files.
IMPORT = "import"
CONTAIN = "contain"
INHERIT = "inherit"
INVOKE = "invoke"  # emitted when an imported callee is resolved.


@dataclass(frozen=True, slots=True)
class Edge:
    source: str
    target: str
    kind: str
    line: int = 0
    raw: str = ""
    # Local names an import actually brought into scope (`authHandler`,
    # `AppEnv`), where the language's grammar makes them findable. Empty for a
    # bare `import "./sideEffect"` — not a claim that nothing was imported.
    symbols: tuple[str, ...] = ()
    conditional: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "line": self.line,
            "raw": self.raw,
            "symbols": list(self.symbols),
            "conditional": self.conditional,
        }
