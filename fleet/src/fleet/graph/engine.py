"""The one tree-sitter walk, driven by a language's query file.

Languages share query execution and symbol extraction. Rust then interprets
its use trees and lexical bindings using the same parsed tree.

The capture vocabulary a query speaks:

    @definition.<kind>   a node that becomes a symbol; <kind> is recorded
                         verbatim (function, method, class, struct, trait,
                         enum, interface, type, test)
    @name                the declared name inside that definition
    @value               where a definition's body really lives, when it isn't
                         the definition node itself — `const f = () => {}`
                         needs line numbers from the declarator but async- and
                         generator-ness from the arrow function
    @scope, @scope.name  a construct that names the definitions inside it
                         without being a symbol itself (a Rust `impl` block)
    @base                a supertype: `extends`, `implements`, `impl X for`
    @annotation          a decorator/attribute, matched against TestRules
    @import, @import.module, @import.symbol
    @call, @call.name
    @test.label          the string a registering call names its test with

Captures are read by *match*, not flat, so a definition arrives already
grouped with its own name, bases and annotations.
"""

from __future__ import annotations

from typing import Any

from fleet.graph.models import ExtractedCall, ExtractedImport, ExtractedSymbol, ParsedFile
from fleet.graph.spec import LanguageSpec

try:  # pragma: no cover - exercised by availability, not by branch
    import tree_sitter
    from tree_sitter_language_pack import get_parser

    TREE_SITTER_AVAILABLE = True
except ImportError:  # pragma: no cover
    tree_sitter = None  # type: ignore[assignment]
    get_parser = None  # type: ignore[assignment]
    TREE_SITTER_AVAILABLE = False


_QUERY_CACHE: dict[tuple[str, str], Any] = {}

# Node types that are a definition's body across every grammar seen so far.
# A grammar that uses a different one only needs adding here once, for all
# languages — not per language.
_BODY_TYPES = frozenset(
    {"block", "statement_block", "compound_statement", "field_declaration_list", "declaration_list"}
)

_QUOTES = "\"'`"


def _text(node: Any) -> str:
    return node.text.decode("utf-8", errors="replace")


def _unquote(text: str) -> str:
    """`"./auth"` -> `./auth`. Go, JS and Python all quote module paths."""
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] in _QUOTES and stripped[-1] == stripped[0]:
        return stripped[1:-1]
    return stripped


def _body_node(node: Any) -> Any | None:
    body = node.child_by_field_name("body")
    if body is not None:
        return body
    # Fall back to a typed child for grammar constructs without a body field.
    for child in node.named_children:
        if child.type in _BODY_TYPES:
            return child
    return None


def _has_keyword(node: Any, keyword: str) -> bool:
    """Whether `node` has a literal keyword token as a direct child. Keywords
    are anonymous tokens, so this checks `.children` (which includes them),
    not `.named_children` (which doesn't)."""
    return any(child.type == keyword for child in node.children)


def _is_generator(node: Any) -> bool:
    return "generator" in node.type or _has_keyword(node, "*")


def _key(node: Any) -> tuple[int, int]:
    """Identity for a node, by byte span.

    Byte range, not object identity: the same underlying node reached via
    `.parent` is a *different* Python object than the one a query capture
    handed back (confirmed empirically against tree-sitter-rust), so `is`
    comparison silently never matches. This cost a real bug once.
    """
    return (node.start_byte, node.end_byte)


def _split_patterns(source: str) -> list[str]:
    """Break a query file into its top-level patterns.

    Only needed for the fallback in `_compile`, so it can find out *which*
    pattern a grammar rejects instead of losing the whole file to one of them.
    """
    patterns: list[str] = []
    buffer: list[str] = []
    depth = 0
    for line in source.splitlines():
        stripped = line.strip()
        if not buffer and (not stripped or stripped.startswith(";")):
            continue
        buffer.append(line)
        in_string = escaped = False
        for character in line:
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == ";":
                break  # comment runs to end of line
            if character == '"':
                in_string = True
            elif character in "([":
                depth += 1
            elif character in ")]":
                depth -= 1
        if depth <= 0:
            patterns.append("\n".join(buffer))
            buffer = []
            depth = 0
    if buffer:
        patterns.append("\n".join(buffer))
    return patterns


def _compile(spec: LanguageSpec) -> tuple[Any, tuple[tuple[str, str], ...]]:
    """Compile a language's query, tolerating patterns its grammar can't have.

    JavaScript, TypeScript and TSX share one query file but are three
    grammars, and they disagree about real structure — plain JS puts a
    superclass directly under `class_heritage`, TypeScript wraps it in an
    `extends_clause`. tree-sitter rejects a pattern that cannot match as an
    "Impossible pattern", and rejects the *entire* query with it.

    So: compile the file whole, which is the normal path and catches genuine
    typos. Only if that fails, retry pattern by pattern and keep the ones this
    grammar accepts, recording the rest. Dropping is therefore never silent —
    `dropped_patterns` reports it and the test suite asserts on it.
    """
    source = spec.query_source()
    cache_key = (spec.grammar, source)
    cached = _QUERY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    from tree_sitter_language_pack import get_language

    language = get_language(spec.grammar)
    try:
        compiled = (tree_sitter.Query(language, source), ())
    except Exception:
        kept: list[str] = []
        dropped: list[tuple[str, str]] = []
        for pattern in _split_patterns(source):
            try:
                tree_sitter.Query(language, pattern)
            except Exception as error:
                first_line = next(
                    (ln.strip() for ln in pattern.splitlines() if ln.strip()), pattern
                )
                dropped.append((first_line, str(error)))
            else:
                kept.append(pattern)
        compiled = (tree_sitter.Query(language, "\n".join(kept)), tuple(dropped))
    _QUERY_CACHE[cache_key] = compiled
    return compiled


def dropped_patterns(spec: LanguageSpec) -> tuple[tuple[str, str], ...]:
    """Patterns `spec`'s grammar rejected, as (pattern, reason). Empty is the
    expected state for a single-grammar language; a shared query file may
    legitimately have entries for the dialects it doesn't apply to."""
    return _compile(spec)[1]


def _kind_of(capture_name: str) -> str | None:
    return capture_name[len("definition.") :] if capture_name.startswith("definition.") else None


class _Container:
    """A node that qualifies the names of definitions inside it."""

    __slots__ = ("name", "bases", "qualified", "kind")

    def __init__(self, name: str, bases: tuple[str, ...], kind: str = "scope") -> None:
        self.name = name
        self.bases = bases
        self.qualified = name
        self.kind = kind


def extract(spec: LanguageSpec, path: str, code: str) -> ParsedFile:
    """Run `spec`'s query over `code` and build a ParsedFile. Returns a
    ParsedFile with `parser == "none"` if tree-sitter or the grammar is
    unavailable — never a guess."""
    result = ParsedFile(path=path, language=spec.name)
    if not TREE_SITTER_AVAILABLE:
        return result
    try:
        parser = get_parser(spec.grammar)  # type: ignore[misc]
        query, _dropped = _compile(spec)
    except Exception as exc:
        result.diagnostics.append({"reason": "parser_or_query_unavailable", "message": str(exc)})
        return result

    tree = parser.parse(code.encode("utf-8"))
    root = tree.root_node
    result.parser = "tree_sitter"
    result.has_syntax_error = root.has_error
    # Dropped patterns are not reported here. Which patterns a grammar rejects
    # is a fact about the language, identical for every file it parses;
    # attaching it to each one repeated it 8 times per .js file once the
    # TypeScript-only patterns arrived. `scan.py` reports it once per scan,
    # and `dropped_patterns(spec)` answers it directly.

    # One grammar construct often needs several query patterns — a class with
    # and without a superclass, an import with each shape of clause. Merging
    # matches by the node they anchor on means a query author can write those
    # as separate, readable patterns instead of one deeply-optional monster,
    # and still get a single symbol out.
    cursor = tree_sitter.QueryCursor(query)
    merged = _merge_matches(cursor.matches(root))
    if cursor.did_exceed_match_limit:
        result.diagnostics.append({"reason": "query_match_limit_exceeded"})

    # Pass 1: every construct that can qualify a name — both real definitions
    # and name-only scopes (Rust `impl`). Keyed by byte span so pass 2 can look
    # up a node it reached by walking `.parent`.
    containers: dict[tuple[int, int], _Container] = {}
    for anchor, node, captures in merged:
        if anchor == "scope":
            name, bases = _container_of(captures)
        elif anchor == "definition" and _definition_kind(captures) in _CONTAINER_KINDS:
            # Callable scopes preserve ownership of nested declarations/calls.
            name, bases = _container_of(captures)
        else:
            continue
        if name:
            containers[_key(node)] = _Container(name, bases, _definition_kind(captures) or "scope")

    # Pass 2: definitions, qualified against the containers found above.
    # `_enclosing` walks up the real tree, so it handles Python methods, JS
    # class bodies and Rust impl blocks with one rule instead of three
    # hand-written parent-walkers. Outermost first, so a nested class's
    # container already carries its own full chain by the time we reach it.
    symbols: list[ExtractedSymbol] = []
    imports: list[ExtractedImport] = []
    calls: list[ExtractedCall] = []
    for anchor, node, captures in sorted(merged, key=lambda item: _key(item[1])):
        if anchor == "definition":
            symbol = _symbol_of(spec, path, node, captures, containers)
            if symbol is not None:
                symbols.append(symbol)
        elif anchor == "import":
            imports.append(_import_of(node, captures))
        elif anchor == "call":
            name_nodes = captures.get("call.name") or []
            if name_nodes:
                calls.append(
                    ExtractedCall(
                        name=_text(name_nodes[0]),
                        line=node.start_point[0] + 1,
                        caller=_enclosing_name(node, containers),
                    )
                )

    result.symbols = sorted(symbols, key=lambda s: (s.start_line, s.qualified_name))
    result.imports = sorted(imports, key=lambda i: i.line)
    result.calls = sorted(calls, key=lambda c: c.line)
    binder = BINDERS.get(spec.binder)
    if binder is not None:
        binder(result, root)
    return result


def _bind_rust(result: ParsedFile, root: Any) -> None:
    from fleet.graph.rust import extract_bindings

    extract_bindings(result, root)


def _bind_jsts(result: ParsedFile, root: Any) -> None:
    from fleet.graph.jsts import extract_bindings

    extract_bindings(result, root)


# The second pass a language declares through `LanguageSpec.binder`. Imported
# lazily so a language's binding rules are only loaded when a file needs them.
BINDERS: dict[str, Any] = {"rust": _bind_rust, "jsts": _bind_jsts}


# Which capture makes a match "about" a definition, a scope, an import or a
# call — in precedence order, so a node matched by several patterns (a JS
# `describe(...)` is both a test definition and a call expression) yields one
# thing, not two.
_ANCHOR_ORDER = ("definition", "scope", "import", "call")


def _anchor_of(captures: dict[str, list[Any]]) -> tuple[str, Any] | None:
    for capture_name, nodes in captures.items():
        if _kind_of(capture_name) is not None:
            return "definition", nodes[0]
    for anchor in ("scope", "import", "call"):
        if captures.get(anchor):
            return anchor, captures[anchor][0]
    return None


def _merge_matches(matches: Any) -> list[tuple[str, Any, dict[str, list[Any]]]]:
    """Collapse matches that describe the same node into one capture set.

    Query authors get to write a class-with-superclass and a class-without as
    two plain patterns rather than one pattern bristling with `?`, and still
    get exactly one symbol out the other end.
    """
    merged: dict[tuple[int, int], tuple[int, str, Any, dict[str, list[Any]]]] = {}
    for _pattern, captures in matches:
        found = _anchor_of(captures)
        if found is None:
            continue
        anchor, node = found
        rank = _ANCHOR_ORDER.index(anchor)
        key = _key(node)
        existing = merged.get(key)
        if existing is None:
            merged[key] = (rank, anchor, node, {k: list(v) for k, v in captures.items()})
            continue
        old_rank, old_anchor, old_node, old_captures = existing
        for capture_name, nodes in captures.items():
            bucket = old_captures.setdefault(capture_name, [])
            seen = {_key(n) for n in bucket}
            bucket.extend(n for n in nodes if _key(n) not in seen)
        if rank < old_rank:
            merged[key] = (rank, anchor, node, old_captures)
        else:
            merged[key] = (old_rank, old_anchor, old_node, old_captures)
    return [(anchor, node, captures) for _rank, anchor, node, captures in merged.values()]


# Kinds that can lexically hold other definitions.
_CONTAINER_KINDS = frozenset({"class", "struct", "trait", "interface", "enum", "module", "function", "method"})


# Kinds a query uses as a catch-all, to be overridden whenever a more specific
# pattern also matched the same node. Go declares structs, interfaces and plain
# aliases all as `type_declaration`, so its query has a specific pattern for
# each shape plus a generic one — without this, whichever happened to be
# enumerated first would win and `type Cast struct{}` could come back as a
# bare "type".
_FALLBACK_KINDS = frozenset({"type"})


def _definition_kind(captures: dict[str, list[Any]]) -> str | None:
    kinds = [_kind_of(name) for name in captures if _kind_of(name) is not None]
    specific = [kind for kind in kinds if kind not in _FALLBACK_KINDS]
    if specific:
        return specific[0]
    return kinds[0] if kinds else None


def _container_of(captures: dict[str, list[Any]]) -> tuple[str, tuple[str, ...]]:
    """The name a container gives its children, and any bases it passes down.

    Only `@scope.base` propagates. A Rust `impl GlobalAlloc for Bump` really
    does hand `GlobalAlloc` to each method it contains, because the impl block
    produces no symbol of its own to hang it on. A Java class's `extends
    Animal`, captured as plain `@base`, belongs to the class alone — letting
    that propagate would report every method as inheriting from Animal.
    """
    name_nodes = captures.get("scope.name") or captures.get("name") or []
    base_nodes = captures.get("scope.base", [])
    return (_text(name_nodes[0]) if name_nodes else ""), tuple(_text(n) for n in base_nodes)


def _enclosing(node: Any, containers: dict[tuple[int, int], _Container]) -> _Container | None:
    """Nearest ancestor that is itself a captured definition or scope.

    This one rule replaces what were three separate hand-written parent walks
    — JS `class_body` climbing, Rust impl-block byte matching, and Go's
    receiver lookup (Go supplies its container through `@scope.name` in the
    query instead, since a Go method is not lexically inside its type).
    """
    own = _key(node)
    parent = node.parent
    while parent is not None:
        container = containers.get(_key(parent))
        if container is not None and _key(parent) != own:
            return container
        parent = parent.parent
    return None


def _enclosing_name(node: Any, containers: dict[tuple[int, int], _Container]) -> str:
    container = _enclosing(node, containers)
    return container.qualified if container else ""


def _symbol_of(
    spec: LanguageSpec,
    path: str,
    node: Any,
    captures: dict[str, list[Any]],
    containers: dict[tuple[int, int], _Container],
) -> ExtractedSymbol | None:
    kind = _definition_kind(captures)
    if kind is None:
        return None

    label_nodes = captures.get("test.label") or []
    name_nodes = captures.get("name") or []
    if label_nodes:
        name = _unquote(_text(label_nodes[0]))
    elif name_nodes:
        name = _text(name_nodes[0])
    else:
        return None  # anonymous — nothing to record it under
    if not name:
        return None

    # `@value` is where the body actually is when the definition node is only a
    # binding: `const f = async () => {}` takes its lines from the declarator
    # but its async-ness from the arrow function.
    target = captures["value"][0] if "value" in captures else node

    # A Go method is not lexically inside its type, so it names its own
    # container through @scope.name rather than being found by walking up.
    own_scope = captures.get("scope.name") or []
    if own_scope:
        parent = _text(own_scope[0])
        qualified_name = f"{parent}.{name}"
        bases = tuple(_text(n) for n in captures.get("scope.base", []))
    else:
        container = _enclosing(node, containers)
        if container is not None:
            qualified_name = f"{container.qualified}.{name}"
            parent = container.qualified
            bases = container.bases
        else:
            qualified_name = name
            parent = ""
            bases = ()
    # A definition's own bases always win over any a container passed down.
    own_bases = tuple(_text(n) for n in captures.get("base", []))
    if own_bases:
        bases = own_bases
    # A free function that turned out to live in a class, impl or receiver is
    # a method. Saying so here means no query has to spell out two variants of
    # the same definition pattern.
    enclosing = _enclosing(node, containers)
    if parent and kind == "function" and (own_scope or (enclosing and enclosing.kind in {"class", "struct", "trait", "scope"})):
        kind = "method"

    # A container's `qualified` is set here so a method inside a nested class
    # gets the full chain, not just its immediate parent's bare name.
    self_container = containers.get(_key(node))
    if self_container is not None:
        self_container.qualified = qualified_name

    annotations = _annotations_of(node, captures)
    body = _body_node(target)
    is_test = kind == "test" or spec.tests.matches(name, annotations)

    return ExtractedSymbol(
        name=name,
        qualified_name=qualified_name,
        kind=kind,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        first_body_line=(body.start_point[0] + 1) if body else node.start_point[0] + 1,
        last_body_line=(body.end_point[0] + 1) if body else node.end_point[0] + 1,
        body_start_byte=body.start_byte if body else None,
        body_end_byte=body.end_byte if body else None,
        is_test=is_test,
        is_async=_has_keyword(target, "async"),
        is_generator=_is_generator(target),
        bases=bases,
        path=path,
        parent=parent,
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        signature=_text(target).split("{", 1)[0].strip(),
        docstring=_source_docs(node),
    )


def _source_docs(node: Any) -> str:
    """Source documentation comments immediately preceding a declaration."""
    docs = []
    previous = node.prev_named_sibling
    while previous is not None:
        value = _text(previous).strip()
        if previous.type == "attribute_item":
            previous = previous.prev_named_sibling
            continue
        if value.startswith(("///", "/**")):
            docs.append(value)
        else:
            break
        previous = previous.prev_named_sibling
    return "\n".join(reversed(docs))


def _annotations_of(node: Any, captures: dict[str, list[Any]]) -> tuple[str, ...]:
    """Decorators and attributes attached to a definition, however the grammar
    models them.

    Java nests them inside `modifiers`, so the query captures them as
    @annotation. Rust makes `#[test]` a *preceding sibling* of the function it
    marks, which no query pattern anchored on the function can reach — so
    siblings are collected structurally here. Both are grammar shapes, not
    language names, which is why this stays generic: a future language using
    either shape needs no change.
    """
    texts = [_text(n) for n in captures.get("annotation", [])]
    sibling = node.prev_named_sibling
    depth = 0
    while sibling is not None and depth < 8:
        if "attribute" not in sibling.type and "decorator" not in sibling.type:
            break
        texts.append(_text(sibling))
        sibling = sibling.prev_named_sibling
        depth += 1
    return tuple(texts)


def _import_of(node: Any, captures: dict[str, list[Any]]) -> ExtractedImport:
    module_nodes = captures.get("import.module") or []
    module = _unquote(_text(module_nodes[0])) if module_nodes else _unquote(_text(node))
    return ExtractedImport(
        raw=_text(node).strip(),
        module=module,
        symbols=[_text(n) for n in captures.get("import.symbol", [])],
        line=node.start_point[0] + 1,
    )
