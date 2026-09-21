# Adding a language

Two files. If you find yourself editing `engine.py`, `resolve.py`, `scan.py`
or the CLI to make a language work, stop — that is the abstraction leaking,
and the fix belongs in the query vocabulary or in a rule keyed on *grammar
shape*, never on the language's name.

1. **`queries/<name>.scm`** — what to extract.
2. **`languages/<name>.py`** — a `LanguageSpec`, then add it to the import
   list in `languages/__init__.py`.

Check the grammar is available first (most are, and need no new dependency):

```bash
python -c "from tree_sitter_language_pack import get_parser; get_parser('java')"
```

## Capture vocabulary

The engine understands these names and nothing else:

| capture | meaning |
|---|---|
| `@definition.<kind>` | a node that becomes a symbol; `<kind>` is recorded verbatim — `function`, `method`, `class`, `struct`, `trait`, `enum`, `interface`, `type`, `test` |
| `@name` | the declared name inside that definition |
| `@value` | where the body really is, when the definition node is only a binding (`const f = () => {}` takes lines from the declarator, async/generator from the arrow function) |
| `@scope` / `@scope.name` | a construct that names the definitions inside it without being a symbol itself — a Rust `impl` block |
| `@scope.base` | a base the scope hands *down* to the definitions it contains |
| `@base` | a supertype belonging to this definition alone — `extends`, `implements` |
| `@annotation` | a decorator/attribute, matched against the spec's `TestRules` |
| `@import` / `@import.module` / `@import.symbol` | an import, its module path, and each local name it binds |
| `@call` / `@call.name` | a call site and its callee name |
| `@test.label` | the string a registering call names its test with — `it('...')` |

Free, with no work per language: `is_async` (an `async` keyword token),
`is_generator` (a `*` token or a node type containing "generator"), body
line ranges and byte spans, qualified names, and `contain` edges.

`@base` vs `@scope.base` is a real distinction, not a naming choice. A Rust
`impl GlobalAlloc for Bump` genuinely hands `GlobalAlloc` to every method
inside it, because the impl block has no symbol of its own to carry it. A Java
class's `extends Animal` belongs to the class — propagating it would report
every method as inheriting from `Animal`.

## Rules

**Never mark a field pattern `?`.** This is the one way a query fails
silently. A field written in the wrong order relative to the grammar's own
field order is a hard `Impossible pattern` error — unless it is optional, in
which case it matches with the capture missing and says nothing. Verified on
tree-sitter 0.26.0:

```scheme
; silently drops @tr on a real trait impl — no error
(impl_item type: (type_identifier) @t trait: (type_identifier)? @tr)
```

Write separate single-field patterns instead. The engine merges matches by
node span, so they recombine into one symbol, and a pattern with one field has
no field order to get wrong:

```scheme
(impl_item type: (type_identifier) @scope.name) @scope
(impl_item trait: (type_identifier) @scope.base) @scope
```

`test_no_query_uses_an_optional_field_pattern` fails the build if a `?`
appears, so this rule is enforced rather than remembered.

**Field order follows the grammar, not reading order.** Go declares `receiver`
before `name`, so a pattern naming both must too. This one is loud — you get a
compile error — but the fix is to check the grammar, not to reorder by
guessing:

```bash
python -c "
from tree_sitter_language_pack import get_parser
n = get_parser('go').parse(b'func (c *C) M() {}').root_node.named_children[0]
print([n.field_name_for_child(i) for i in range(n.child_count)])"
```

**Several patterns per construct is fine, and usually clearer** than one
pattern bristling with alternations — they merge by node span. A pattern that
is impossible in one grammar is dropped for that grammar alone, which is how
`javascript.scm` serves JavaScript, TypeScript and TSX at once. Dropping is
never silent: `engine.dropped_patterns(spec)` reports it, and
`test_only_expected_dialect_patterns_are_dropped` pins the expected count so a
typo cannot hide as a dialect difference.

**A generic catch-all pattern loses to a specific one** on the same node, via
`engine._FALLBACK_KINDS`. Go needs this: struct, interface and plain alias are
all `type_declaration`.

## The spec

```python
JAVA = register(
    LanguageSpec(
        name="java",
        extensions=(".java",),
        # grammar / query / family default to `name`; set them to borrow
        # another grammar or share a query file across dialects.
        resolver="dotted",        # "dotted" | "relative" | "" (no edges)
        source_roots=("src/main/java",),
        tests=TestRules(name_prefixes=("test",), annotations=("Test",)),
    )
)
```

`resolver=""` is a legitimate answer. Go has no resolver because a Go import
path is absolute against the module path in `go.mod`, so resolving one means
reading that file first — real work, not a one-line strategy. Until it exists,
Go files keep their raw imports and contribute no import edges, which is
honest rather than guessed.

`family` is what lets languages resolve imports against each other: js/ts/tsx
share one, so a `.ts` file importing `"./foo"` can land on `foo.tsx`.
