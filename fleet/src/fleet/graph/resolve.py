"""Graph assembly: import binding resolution, legacy file-import strategies,
lexical containment and local inheritance.

Language-aware bindings live in bindings.py and now cover Python, Rust and the
JS/TS family. The dotted helper below remains for the legacy parsers, which
map file to file without reading what an import actually binds.
"""

from __future__ import annotations

from typing import Callable

from fleet.graph.models import CONTAIN, IMPORT, INHERIT, Edge, ParsedFile
from fleet.graph.spec import LanguageSpec, spec_for, specs_in_family

# Kinds that a base name can legitimately refer to.
_INHERITABLE = frozenset({"class", "struct", "trait", "interface", "enum", "type"})


# ---------------------------------------------------------------------------
# import: dotted (Python, Java)
# ---------------------------------------------------------------------------


def _split_source_root(spec: LanguageSpec, path: str) -> tuple[str, str]:
    """Split a path into (everything above its source root, the part below it).

    A source root is not always at the top: a Cargo workspace or a
    multi-module Gradle build puts each crate/module in its own directory, so
    `services/api/src/parser.rs` has its root in the middle. The prefix is
    kept rather than discarded because it identifies *which* crate — without
    it, two crates that both have `src/lib.rs` would produce the same module
    name and happily resolve each other's imports.

    Returns ("", path) when the language declares no source roots, which is
    Python's case and leaves its behaviour untouched.
    """
    best: tuple[str, str] | None = None
    for root in spec.source_roots:
        marker = root.strip("/")
        if path.startswith(marker + "/"):
            candidate = ("", path[len(marker) + 1 :])
        else:
            index = path.rfind(f"/{marker}/")
            if index == -1:
                continue
            candidate = (path[:index], path[index + len(marker) + 2 :])
        # Shortest remainder wins, i.e. the deepest/most specific root — so
        # Java's "src/main/java" beats a bare "src" on the same path.
        if best is None or len(candidate[1]) < len(best[1]):
            best = candidate
    return best if best is not None else ("", path)


def _module_parts(spec: LanguageSpec, path: str) -> list[str]:
    stem = _split_source_root(spec, path)[1]
    for extension in spec.extensions:
        if stem.endswith(extension):
            stem = stem[: -len(extension)]
            break
    parts = stem.split("/") if stem else []
    # An index file is imported as the thing that contains it: Python's
    # `pkg/__init__.py` is `pkg`, Rust's `foo/mod.rs` is `foo` and its
    # `src/main.rs` is the crate root itself.
    if parts and parts[-1] in spec.index_names:
        parts = parts[:-1]
    return parts


def _dotted_name(spec: LanguageSpec, path: str) -> str:
    """The module name a file is imported as.

    `glom/core.py` -> `glom.core`; `glom/__init__.py` -> `glom`, because a
    package's own index file is imported as the package itself. For Rust,
    `src/models.rs` -> `crate::models` and `src/main.rs` -> `crate`.
    """
    parts = _module_parts(spec, path)
    if spec.root_name:
        parts = [spec.root_name] + parts
    return spec.separator.join(parts)


def _resolve_markers(spec: LanguageSpec, path: str, module: str) -> str:
    """Expand a `self::`/`super::`-style relative path against its own file.

    Rust's `use super::sibling::Other` inside `src/a/b.rs` means
    `crate::a::sibling::Other`. Languages that spell relative imports with
    leading dots instead go through `_relative_dotted`; a spec declares one
    convention or the other and this is skipped when it declares neither.
    """
    segments = module.split(spec.separator)
    own = _module_parts(spec, path)
    if spec.root_name:
        own = [spec.root_name] + own
    consumed = 0
    for segment in segments:
        if spec.self_marker and segment == spec.self_marker:
            consumed += 1
        elif spec.parent_marker and segment == spec.parent_marker:
            own = own[:-1] if own else own
            consumed += 1
        else:
            break
    if consumed == 0:
        return module
    return spec.separator.join(own + segments[consumed:])


def _relative_dotted(path: str, module: str) -> str:
    """`from ..pkg import x` inside `a/b/c.py` -> `a.pkg`.

    Level 1 means the file's own directory whether the file is a plain module
    or that package's `__init__.py`, so only levels beyond the first climb.
    """
    level = len(module) - len(module.lstrip("."))
    suffix = module[level:]
    directory = path.rsplit("/", 1)[0] if "/" in path else ""
    parts = directory.split("/") if directory else []
    strip = max(0, level - 1)
    base_parts = parts[: len(parts) - strip] if strip <= len(parts) else []
    base = ".".join(base_parts)
    if suffix:
        return f"{base}.{suffix}" if base else suffix
    return base


def resolve_dotted(specs: list[LanguageSpec], files: list[ParsedFile]) -> list[Edge]:
    # Keyed by (crate/module root, module name), not module name alone. In a
    # Cargo workspace every crate has a `crate` root and possibly a
    # `crate::config`; without the root in the key they would all collide and
    # resolve across crate boundaries that don't exist.
    by_module: dict[tuple[str, str], str] = {}
    spec_by_language = {spec.name: spec for spec in specs}
    for parsed in files:
        spec = spec_by_language.get(parsed.language or "")
        if spec is not None:
            root = _split_source_root(spec, parsed.path)[0]
            by_module.setdefault((root, _dotted_name(spec, parsed.path)), parsed.path)

    edges: list[Edge] = []
    for parsed in files:
        spec = spec_by_language.get(parsed.language or "")
        if spec is None:
            continue
        own_root = _split_source_root(spec, parsed.path)[0]
        for imported in parsed.imports:
            module = imported.module
            if module.startswith("."):
                resolved = _relative_dotted(parsed.path, module)
            else:
                resolved = _resolve_markers(spec, parsed.path, module)
            if not resolved:
                continue
            # `from pkg import submodule` is ambiguous from syntax alone:
            # `submodule` may be a name inside `pkg/__init__.py`, or the module
            # `pkg/submodule.py`. Python tries the latter first, and so do we —
            # most specific candidate wins.
            #
            # The order is load-bearing, not cosmetic. Rust's `use
            # crate::parser` splits into module `crate` plus symbol `parser`,
            # and `crate` is a real file (`src/main.rs`). Trying the bare
            # module first pointed every such import at main.rs.
            separator = spec.separator
            candidates = [f"{resolved}{separator}{n}" for n in imported.symbols] + [resolved]
            # A path that resolves nothing may still be rooted at the crate:
            # after `mod app;`, `use app::AppController` addresses
            # `crate::app`. Tried last so a genuine external crate named the
            # same as a local module doesn't win by accident.
            if spec.root_name and not resolved.startswith(spec.root_name + separator):
                rooted = f"{spec.root_name}{separator}{resolved}"
                candidates += [f"{rooted}{separator}{n}" for n in imported.symbols] + [rooted]
            for candidate in candidates:
                target = by_module.get((own_root, candidate))
                if target and target != parsed.path:
                    edges.append(
                        Edge(
                            source=parsed.path,
                            target=target,
                            kind=IMPORT,
                            line=imported.line,
                            raw=imported.raw,
                            symbols=tuple(imported.symbols),
                        )
                    )
                    break  # first match wins; a resolved import connects once
    return edges


RESOLVERS: dict[str, Callable[[list[LanguageSpec], list[ParsedFile]], list[Edge]]] = {
    "dotted": resolve_dotted,
}

# Families whose imports are resolved by `bindings.BindingResolver` — per
# imported *name*, with its uses — rather than by a strategy above. Running
# both for one family would emit the same file dependency twice, once labeled
# with every name the statement imported and once with a single name.
#
# JS/TS moved here when it gained real bindings: the Node-style path search it
# used to need now lives in `BindingResolver.specifier`, which the name lookup
# needs anyway to follow a re-export to the next file.
_BOUND_FAMILIES = frozenset({"python", "rust", "javascript"})


# ---------------------------------------------------------------------------
# contain / inherit
# ---------------------------------------------------------------------------


def contain_edges(files: list[ParsedFile]) -> list[Edge]:
    """file -> top-level symbol, and symbol -> nested symbol.

    Directory containment is deliberately not emitted: it is derivable from any
    path by splitting on "/", and materialising it would roughly double the
    edge count for something a viewer can compute for free.
    """
    edges: list[Edge] = []
    for parsed in files:
        known = {symbol.qualified_name: symbol.id for symbol in parsed.symbols}
        for symbol in parsed.symbols:
            parent_id = known.get(symbol.parent) if symbol.parent else None
            edges.append(
                Edge(
                    source=parent_id or parsed.path,
                    target=symbol.id,
                    kind=CONTAIN,
                    line=symbol.start_line,
                )
            )
    return edges


def inherit_edges(files: list[ParsedFile], *, include_bindings: bool = True) -> list[Edge]:
    """Inheritance follows an import binding or an unshadowed local declaration."""
    from fleet.graph.bindings import BindingResolver
    edges = [e for e in BindingResolver(files).resolve() if e.kind == INHERIT] if include_bindings else []
    for parsed in files:
        for symbol in parsed.symbols:
            if symbol.kind not in _INHERITABLE:
                continue
            for base in symbol.bases:
                root = base.split(".")[0]
                if any(b.local == root for b in parsed.bindings):
                    continue
                matches = [s for s in parsed.symbols if s.name == base and s.kind in _INHERITABLE
                           and s.parent == symbol.parent]
                if len(matches) == 1 and matches[0].id != symbol.id:
                    edges.append(Edge(symbol.id, matches[0].id, INHERIT, symbol.start_line, base))
    return edges


def resolve_edges(files: list[ParsedFile], metadata: dict | None = None) -> list[Edge]:
    """Every edge this repo's files support.

    Import resolution runs once per *family*, not once per language — that is
    what lets js/ts/tsx resolve against each other, and it replaces the
    previous `id(resolver) in already_run` de-duplication, which was working
    around the missing concept rather than expressing it.
    """
    by_family: dict[str, list[ParsedFile]] = {}
    for parsed in files:
        spec = spec_for(parsed.language or "")
        if spec is not None:
            by_family.setdefault(spec.family, []).append(parsed)

    from fleet.graph.bindings import BindingResolver
    edges: list[Edge] = BindingResolver(files, metadata).resolve()
    for family, group in sorted(by_family.items()):
        if family in _BOUND_FAMILIES:
            continue
        specs = specs_in_family(family)
        strategy = next((spec.resolver for spec in specs if spec.resolver), "")
        resolver = RESOLVERS.get(strategy)
        if resolver is not None:
            edges.extend(resolver(specs, group))

    edges.extend(contain_edges(files))
    edges.extend(inherit_edges(files, include_bindings=False))
    return edges
