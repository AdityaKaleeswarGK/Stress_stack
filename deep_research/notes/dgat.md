# DGAT — architecture and what Fleet borrowed

Companion to [`decisions.md`](decisions.md)'s "Phase 1 shipped" section.
`/Users/adityagk/Desktop/capstone/DGAT` — the user's own prior project, so no
license concern in reusing anything from it, same as stress_stack.

## What it actually is

Not a Python tool — a **C++17 engine** (`dgat.cpp`, ~3,766 lines) wrapped by
a thin Python `click` CLI. It scans a codebase, extracts import relationships
via tree-sitter, generates natural-language descriptions of every file and
dependency edge via an LLM, and serves the result through a 3D graph UI.

```
DGAT/
├── dgat.cpp              # C++ core: file tree, dep graph, LLM calls, HTTP server
├── queries/*.scm         # tree-sitter query files, one per language (12: py, js,
│                         #   ts... via shared grammar, go, rust, c, c++, java, ruby,
│                         #   php, c#, cuda, ipython)
└── src/dgat/
    ├── cli.py            # click CLI: scan, backend, update, config, search, ...
    ├── scanner.py        # Python wrapper around the compiled C++ binary
    ├── server.py         # FastAPI server for the 3D UI
    ├── providers/        # LLM provider abstraction (openrouter, vllm, anthropic, ...)
    └── bin/dgat           # the compiled binary
```

Key design choices (from `docs/overview.md`):
- **XXH3-128 fingerprints** per file for incremental updates — `dgat update`
  only re-describes what actually changed.
- **Tree-sitter first, regex fallback** for import parsing.
- **State persists to disk** — `dgat scan` computes once; `dgat backend`
  loads the saved JSON and serves, no recompute.
- C++ chosen for speed: filesystem walking, parsing, and graph construction
  all run in parallel; only the LLM calls are network-bound (batched across 8
  workers).

## What its `.scm` queries actually do — less than expected

Read `queries/python.scm` directly:

```scheme
(import_statement
  name: (dotted_name) @import)
(import_from_statement
  module_name: (dotted_name) @import)
```

That's the entire Python query — **imports only**, for dependency-graph
edges. No function/class/method extraction, no body ranges, no test
detection. DGAT's real value-add is the LLM description layer on top of a
comparatively shallow structural extraction, not the tree-sitter usage
itself.

stress_stack's `parsers/tree_sitter_backend.py` — the thing Fleet actually
ported — does substantially more per file: a `LanguageSpec` table per
language covering definitions (function/class/struct/trait/...), imports,
byte-exact body ranges, and is-test detection (including JS's call-based
`describe`/`it`, which has no declaration node at all). Its own module
docstring says it "integrates tree-sitter grammar parsing from DGAT and
AlphaStack" — so DGAT is already an ancestor of this code, not a fresh
reference; stress_stack's version is the more capable descendant.

## What Fleet took vs. didn't

**Took:**
- The CLI shape — a single clear verb (`fleet scan`, mirroring `dgat scan`),
  rich console output (✓/✗/! markers, a files-by-language table), JSON
  written to disk as the artifact.
- The "compute once, consumers read the artifact" separation DGAT enforces
  between `scan` and `backend`.

**Didn't take:**
- The C++ implementation. Fleet's engine is stress_stack's Python/tree-sitter
  code, not a reimplementation in C++. Nothing about DGAT's `.scm`-query
  approach was actually richer than what stress_stack already had.
- LLM-generated descriptions — explicitly out of scope for this phase
  (`decisions.md`).
- Incremental/hash-based re-scanning — DGAT's XXH3 approach is worth adopting
  once repeated scans on an unchanged repo become common, but wasn't needed
  for a first "does this work at all" pass.
