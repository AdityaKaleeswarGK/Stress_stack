# AlphaStack — architecture and what it revealed about DGAT

Companion to [`dgat.md`](dgat.md) and [`decisions.md`](decisions.md)'s
correctness-audit section. `/Users/adityagk/Desktop/capstone/iteration-1_alpha_stack`
— the user's own prior project, so no license concern reusing anything.

## What it actually is

Not a repo-mapping tool at all — an autonomous coding **agent** that generates
whole software projects from scratch (`generated_projects/`, `test_output/`,
a `benchmarks/` folder of problem specs, a Go TUI). Dependency analysis is a
small supporting piece (`src/utils/dependencies.py`,
`import_map.py`, `dependency_file_generator.py`), not the product.

## The real finding: it wraps DGAT, and had to patch two holes in it

`dependencies.py`'s `DependencyAnalyzer` docstring says it plainly: *"Adapter
around the `dgat` package... Runs `dgat scan`... loads
dep_graph.json/file_tree.json."* AlphaStack doesn't reimplement graph-building
— it shells out to `dgat scan` and consumes its output directly.

But `_supplement_with_treesitter()` exists specifically because DGAT's own
graph has two gaps, confirmed straight from its own docstring:

1. **External (unresolved) imports are dropped entirely.** A file whose only
   import is `import requests` disappears from DGAT's dependency graph
   *completely* — not marked unresolved, just absent.
2. **Class/function symbols aren't exposed at all.** DGAT's `dep_graph.json`
   is file-level only: nodes, edges, LLM descriptions. No symbol data.

AlphaStack re-walks the whole tree a second time with its own tree-sitter
extractor to patch both: adding `kind: "external"` entries for anything
DGAT silently dropped, and populating `file_symbols[path] = {"classes":
[...], "functions": [...]}` from scratch.

**This means Fleet was already ahead of DGAT's own graph in both respects**
before any of this session's fixes — it always captured every import
(resolved or not) per file, and always captured symbols with kind/lines.
The gap this session actually found and fixed was narrower: qualified names
and `is_async`/`is_generator` weren't computed for tree-sitter-parsed
symbols, and the import `module` field stored the whole raw statement
instead of the extracted specifier. See `decisions.md` for the fixes.

## Its own tree-sitter extractor (`treesitter_parser.py`)

Uses tree-sitter's **query** syntax (`language.query("(class_declaration
name: (identifier) @class_name)")`) rather than manual tree-walking — the
same declarative idiom DGAT's `.scm` files use, just inline in Python instead
of separate files. For JS import module names specifically: finds the
import_statement, walks its children for a `string`-typed one, and does
`_text(child, source_bytes).strip("\"'")` — extract the string node's text,
then strip the quote characters. This directly confirmed the fix Fleet
needed: use the string/source node's own text, not the whole statement's.

Two things AlphaStack's own extractor does **not** do, matching gaps in
DGAT rather than fixing them: no qualified names (`_extract_js_ts_functions`
and `_extract_js_ts_classes` are separate flat lists, never linked), no
`is_async` tracking anywhere. Fleet's fixes go beyond both references here,
not just stress_stack's original — see `decisions.md`.

## Its own import-resolution logic (`_resolve_internal_import`)

Filesystem-based rather than dotted-module-name-based: normalizes relative
imports (`.b`, `..sub.c`) against the importing file's directory, absolute
imports (`pkg.mod`) against the project root, and probes for `<path>.py` or
`<path>/__init__.py` on disk. Conceptually the same idea as Fleet's Python
resolver (`edges.py:resolve_python_edges`), just probing the filesystem
instead of matching against a precomputed dotted-module-name index — a
reasonable alternative, not obviously better or worse. Cross-checking it
didn't change Fleet's Python resolver.

## What Fleet took vs. didn't

**Took:** confirmation that a matched import node's *string/source child*
(not its whole text) is the correct place to read a module specifier from —
this shaped `tree_sitter_spec.py:_module_text`'s JS/TS extraction.

**Didn't take:** the query-syntax approach to tree-sitter extraction (Fleet
stays with stress_stack's manual-walk `LanguageSpec` table — already
proven, and the manual walk is what made parent-aware qualified-name
tracking straightforward to add); wrapping an external tool rather than
owning extraction directly (Fleet's whole point is being the map-building
layer itself, not a thin client over one).
