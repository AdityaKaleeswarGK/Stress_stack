# Decisions — v2 architecture ("Fleet")

Follow-up to the comparison in [`../README.md`](../README.md). Three questions
were on the table; here's where each landed and why, now that we've read the
actual mechanisms rather than just the summary table.

## 1. Output goal: benchmark, training dataset, or both?

**Both.** Not a hedge — a concrete finding drove this: Harbor's `task.toml`
already supports a `test_execution` reward kind that is just our pass/fail
gate expressed as a number, and `reward_kinds` is a list (a task can carry
`test_execution` alone and be read as pure pass/fail by anything that only
cares about the boolean). Nothing about adopting Harbor's spec forces a
training framing on tasks that are really benchmark tasks. The reverse isn't
true: a stress_stack-style boolean-only task would need real rework (a reward
schema, a publishing story) to ever serve training. Build for the superset.

The "exactly 10, quota'd, diversity floor" selection policy stress_stack ships
stays — but as a selection step layered *on top of* an otherwise open-ended
pipeline, not baked into the pipeline itself. The same pipeline should serve
"give me 10 comparable tasks" and "give me everything that validated."

## 2. Task spec: adopt Harbor.

Confirmed directly (not inferred). Output = Harbor's `task.toml` +
`[metadata.fleet]` for our own provenance, same shape as Repo2RLEnv's
`[metadata.repo2env]` (see
[`../implementations/Repo2RLEnv/docs/reference/SPEC.md`](../implementations/Repo2RLEnv/docs/reference/SPEC.md)).

Left open, deliberately: whether we also keep stress_stack's own
container/runner as the execution engine, or delegate to Harbor's runtime the
way Repo2RLEnv does (it ships no sandbox of its own — see SPEC.md's "Sandbox
model — we don't have one"). stress_stack's runner is where the
determinism-proof and 8-gate rigor actually live; ripping it out for Harbor's
runtime trades tested infrastructure for ecosystem reach, and that's not a
call to make unilaterally. It doesn't block anything below — the
pipeline/emitter work is needed either way.

## 3. Language scope: Python-first synthesis, language-agnostic contract.

Decided by a pattern in *both* codebases, not by picking a side: the deep,
AST-level task-synthesis logic is Python-only in both projects.
`stress_stack.excision` walks a real Python AST to find a function body's
exact extent; Repo2RLEnv's `equivalence_tests` and `code_instruct` pipelines
are listed as Python-only in their own pipeline table, and only the pipelines
that *don't* need AST-level understanding (`pr_runtime`, `commit_runtime`,
`cve_patches` — which lean on the generic bootstrap agent + per-language test
log parsers, not on AST) claim Go/Rust/Node.

So: the `Pipeline` protocol and the environment/bootstrap layer stay
language-agnostic from the start (cheap — it's an interface, not an
implementation). The first real pipelines (porting `excision` and the
history-mining pipeline) are Python-only, matching where both prior projects
actually earned their language claims.

## What's carried over from stress_stack, deliberately unchanged

- No model ever decides accept/reject — every gate is a measured container run.
- Determinism as a shipped gate: run twice, diff, must match. Not confirmed on
  Repo2RLEnv's emitted tasks (only on their bootstrap cache) — keep as ours.
- `.git/` excluded from the container build context entirely, not scrubbed
  after the fact — simpler and harder to get wrong than Repo2RLEnv's
  scrub-to-base-commit approach (`_env_guard.py`), though their egress
  DNS-blackhole (PyPI + GitHub CDNs → `0.0.0.0`) is worth adding as a second
  layer regardless — `--network=none` alone stops a live fetch, but any future
  pipeline that needs *some* network at agent runtime would reopen exactly the
  hole they observed being exploited.
- The AST-based excision engine itself — planned to be ported near-verbatim.
  It already handles the edge cases (single-line bodies, generators,
  docstring-only functions, trailing comments) a rewrite would rediscover the
  hard way.

## What's adopted from Repo2RLEnv

- `Pipeline` protocol + registry + contract test, replacing stress_stack's
  single hardcoded mine/validate/select/emit sequence. `excision` and
  `history` become two independent, separately-addable pipelines instead of
  two branches inside one.
- Reward-shaped output (`reward_kinds`) — trivial for us to add since we
  already compute exact pass/fail from real gates.
- Cost tracking + a spend budget threaded through any LLM call, even though
  our model use stays narrow (task prose, difficulty judging) — consistency
  and safety, not a new capability.
- LLM-driven bootstrap as a *fallback* environment-setup path for ecosystems
  our deterministic per-language table doesn't reach (Rust coverage, TS/JS
  test execution) — a complement, not a replacement: deterministic wins
  wherever the table already applies.
- Lightweight RFC-before-implementation for each new pipeline, matching their
  `docs/rfcs/` discipline.

## Explicitly deferred, not decided against

- Hub / registry publishing — real, but only worth doing once generation
  produces something worth publishing.
- The container/runner question from §2 above.

## Phase 1 shipped: `fleet scan` — map a repo (no LLM, no linting)

First real slice, in `fleet/src/fleet/graph/`. Deliberately narrow scope,
matching the "let's start simple" framing it was built under: per-file
language detection by extension → the right parser → a repo-wide symbol
graph → JSON on disk. No task generation, no candidate ranking, no
environment/hygiene work yet — those come later.

Ported near-verbatim from stress_stack's `parsers/tree_sitter_core.py` /
`parsers/tree_sitter_backend.py` (same author — no license concern, unlike
the Repo2RLEnv material elsewhere in this repo). Two calls made explicitly,
both consistent with stress_stack's own reasoning rather than DGAT's:

- **Python stays on `ast`, not tree-sitter** — "same parser CPython uses,
  resolves dotted imports/decorators for free" (stress_stack's own words).
- **No regex fallback** when a tree-sitter grammar is missing for a detected
  language — a file is reported as unparsed rather than guessed at. Simpler,
  and matches stress_stack's "never report a success you didn't measure."
  Easy to add back if a language needs it.

DGAT's actual contribution turned out to be different from what its README
implies: its own `.scm` queries only extract *imports* for the dependency
graph, not function/class-level symbols — stress_stack's tree-sitter table
(`LanguageSpec` per language, one generic `parse()`) already does more.
What DGAT's CLI shape did inform: `fleet scan` mirrors `dgat scan` — a single
verb, rich console output, JSON written to disk, decoupled from any later
serve/query step. See `../notes/dgat.md`.

**Connecting the files** (`graph/edges.py`, added after the first pass):
raw per-file imports alone aren't a *connected* graph. Python imports now
resolve to actual file-to-file edges — absolute (`import pkg.mod`), relative
at any level (`from ..core import X`), and the `from pkg import submodule`
case where `submodule` could be either a name in `pkg/__init__.py` or
`pkg/submodule.py` (both are tried; the first that exists in the scanned
tree wins). An import that resolves to nothing in this repo (stdlib, a
third-party package) correctly produces no edge. Other languages keep their
raw, unresolved imports for now — one more Python-first scope call,
consistent with the rest of this project. `LANGUAGE_RESOLVERS` is a registry
of one, so adding Go/Rust resolution later is additive, not a rewrite.

**Verified, not just written** — `.venv/bin/pytest -q` (8 tests, including
four edge-resolution cases + the external-import negative case) plus live
scans against stress_stack's own fixture repos, and — at the user's
explicit push-back against single-repo testing — the whole `stress_stack/`
tree in one pass, forcing genuine per-file dispatch across Python and Go
side by side rather than one language at a time:

| Repo | Language | Files | Symbols | Tests | Edges | Issues |
|---|---|--:|--:|--:|--:|---|
| `glom` | Python (ast) | 32 | 569 | 180 | 55 | none |
| `pluggy` | Python (ast) | 31 | 297 | 128 | 33 | none |
| `cast` | Go (tree-sitter) | 21 | 146 | 31 | 0² | none |
| `stress_stack/` (whole tree) | Python + Go together | 1,601¹ | 21,601 | 7,894 | 1,233 | 2 SyntaxWarnings³ |

¹ Only ~191 of these are distinct source (`src/` 63, `tests/` 44, `glom/` 32,
`pluggy/` 31, `cast/` 21) — the rest is stress_stack's own `output-verify/`
and `output-pluggy/` directories, full repo copies from past benchmark runs.
Fleet correctly parsed all of it; the number just isn't the flex it looks
like. This is the concrete case for `.gitignore`-awareness (already on the
"not done yet" list below) — stress_stack's own `.gitignore` almost
certainly excludes these, and respecting it would have avoided re-scanning
duplicates for free, instead of guessing at a hardcoded `output/` denylist
entry that would wrongly exclude a repo that legitimately has one.
² `cast` is a flat, single-package repo — no internal imports to resolve, so
zero edges is correct, not a gap.
³ Both in `output-verify/tasks/pr-196/{input,solution}/glom/matching.py` — a
real oddity in glom's own source (a stray backslash-space in a string
literal), not a Fleet bug. Only visible correctly after a related fix below.

Three real bugs found during verification, all fixed:
1. `unparsed_known_language` counted every empty file (e.g. `test/__init__.py`)
   as a missing-grammar problem, because an empty file's parser is always
   `"none"` by construction. Fixed by tracking `ParsedFile.is_empty`
   separately so the stat means what it says.
2. `ExtractedSymbol.body_start_byte` / `body_end_byte` — flagged in the code's
   own comment as what excision will need later — were computed but never
   written into the JSON output. Fixed; confirmed present on a real scanned
   symbol (`cast/alias.go:resolveAlias`, bytes 1836–2146).
3. `ast.parse(code)` was called without `filename=`, so its own
   `SyntaxWarning`s (not just hard errors) reported the source as
   `<unknown>` instead of the real path — useless for actually locating the
   file. Fixed by passing `filename=result.path`.

All three were the kind of gap that only shows up by actually running the
tool against real, messy repos — not by reading the code.

Not done yet, deliberately: no incremental/hash-based re-scan (DGAT's XXH3
approach — worth adding once repeated scans on the same repo are common), no
`.gitignore` respect, C/C++ extensions stay unrouted (matching stress_stack's
own `SHELVED_LANGUAGES` stance).

## `.fleetignore` — real ignore file, not a hardcoded set (`graph/ignore.py`)

Three prior approaches existed and none of them were actually finished:
- stress_stack ships a `.dgatignore` at its own root, `.gitignore` syntax,
  inherited from DGAT — but nothing in stress_stack's own code reads it. Dead
  configuration, confirmed by grep.
- AlphaStack (`iteration-1_alpha_stack`) takes a different, simpler approach:
  a hardcoded `SKIP_DIRS = {...}` Python set in `utils/helpers.py`. Works,
  but not user-editable and not a file at all.
- Fleet's own phase-1 pass had the same shape as AlphaStack's: a hardcoded
  `EXCLUDED_DIRECTORIES` frozenset in `scan.py`.

Replaced with a real loaded file: built-in `DEFAULT_PATTERNS` (`.gitignore`
syntax, scoped to python/javascript/typescript/go per the languages Fleet
actually parses today) plus a project's own `.fleetignore`, which adds to
those defaults rather than replacing them. Deliberately a subset of real
`.gitignore` syntax — trailing `/` for a directory name, `*`/`?` glob against
a filename, anything else an exact name match, all at any depth. No `!`
negation, no leading-`/` anchoring, no `**`, no character classes — none of
the defaults or DGAT's own `.dgatignore` need them, and getting a partial
implementation of full `.gitignore` syntax subtly wrong is worse than a
documented, narrower one.

**A regression, caught by re-running the already-proven fixtures, not by
reading the diff.** The old `EXCLUDED_DIRECTORIES` list carried a blanket
"skip anything starting with `.`" rule; moving to a fully data-driven ignore
file dropped that blanket rule in favor of explicit entries — and
`.stress_stack/` (stress_stack's own working-state directory, full of
per-task `input`/`solution` repo copies) wasn't carried over into
`DEFAULT_PATTERNS`. Re-scanning glom immediately after the change: **32
files → 3,572**; cast: **21 → 548**. Both fully explained by
`.stress_stack/tasks/*/{input,solution}/` getting walked as if it were the
repository's own source. Fixed by adding `.stress_stack/` to the defaults,
in the same category as `.git/` and `.fleet/` — a known tool's working-state
directory, not a guess specific to one repo. Re-verified: all three fixtures
back to their exact previously-recorded numbers (glom 32/569/180/55 edges,
pluggy 31/297/128/33, cast 21/146/31/0).

Confirms the value of testing against real repos on every change, not just
once: a change motivated by wanting *more* correctness (a real ignore file
instead of a hardcoded list) silently regressed correctness in a different
place, and the fixtures caught it immediately.

## Correctness audit against a real repo, and four real bugs it found

Prompted by the user pointing `fleet scan` at their own live project
(`agustus`, a TypeScript Cloudflare Worker) and asking directly whether
"method" classifications and import capture were actually correct.
Cross-checked `repo_graph.json` line-by-line against the real source
(`src/auth.ts`, `index.ts`, `mcp.ts`, `time-groups.ts`), and against how
DGAT and AlphaStack handle the same problem (`dgat.md`, `alphastack.md`).

**Completeness: good news.** Every real function/method/class across all four
files was captured, with correct line ranges and correct `function` vs.
`method` classification per tree-sitter's own grammar terms (an object
literal's shorthand method, e.g. `{ fetch(req) {...} }`, really is a
`method_definition` node — labeling it "method" is accurate, not a bug).

**Four real bugs found, all in the tree-sitter path (the `ast` path for
Python was untouched and unaffected):**

1. **`is_async` was silently always `False`.** `auth.ts`'s `handleAuthorize`,
   `handleGoogleStart`, `handleGoogleCallback`, and its exported `fetch`
   method are all genuinely `async` — the JSON reported `is_async: false`
   for every one. `is_generator` had the identical bug (untested by the
   fixtures until now — none of them had a generator function). Root cause:
   `tree_sitter_spec.py`'s symbol dict never computed either field, and
   `parser.py` never read them back out, so both silently took their
   dataclass default (`False`) regardless of the real source. Fixed:
   `_has_keyword(node, "async")` checks `node.children` (which includes
   anonymous keyword tokens; `named_children` doesn't) for a literal
   `async` token; `_is_generator_node` checks for
   `generator_function_declaration` or a literal `*` child.
2. **Qualified names never included an enclosing class or receiver.** A JS
   class method or a Go receiver method came back indistinguishable from a
   bare top-level function of the same name — `walk_definitions` was a flat
   iterative walk with no parent tracking at all. Fixed by rewriting it
   recursive with a `container` parameter: entering a node whose kind is in
   `LanguageSpec.container_kinds` (`class`, `impl`) sets the container name
   for everything nested inside it. Go methods are qualified differently —
   by their **receiver type**, not lexical nesting, since a Go method isn't
   declared inside its receiver's own type declaration (`func (c *Cast)
   String()` qualifies to `Cast.String` via a new `_go_receiver_type()` that
   reads the `receiver` field and unwraps a pointer type). Verified against
   a constructed Go snippet with both a pointer and a value receiver on the
   same type — both correctly qualify to `Circle.Area` / `Circle.String`.
3. **Import `module` was the entire raw statement, not the specifier.**
   `from pkg import X` classes of bugs — `agustus`'s `src/index.ts` imports
   `"./auth"` and `"./mcp"`, both real files in the repo, but the `module`
   field held the full multi-line import statement text, unusable for any
   resolver. Root cause: `parser.py` set `module=imp["raw"]` verbatim — a
   shortcut from the initial port that was never revisited. Confirmed the
   fix against AlphaStack's own extractor (`treesitter_parser.py`), which
   independently does the same thing Fleet now does: read the import node's
   string/source child specifically, not the whole statement. Fixed via a
   new `_module_text()`, per-language: JS/TS reads the `source` field
   directly (a real field on `import_statement` in tree-sitter's own
   grammar); Go reads the `interpreted_string_literal` child; Rust strips
   the `use `/`;` wrapping.
4. **Go's grouped `import (...)` block was captured as one blob.** The old
   code matched whole `import_declaration` nodes; a grouped import with
   three paths came back as one `ExtractedImport` whose raw text was the
   entire block. Fixed by matching `import_spec` instead — one entry per
   path, whether the source used the grouped or bare single-import form.
   Verified against a constructed file with both `import ( "fmt" "math" )`
   and a separate bare `import "errors"` — all three came back as distinct
   imports with correct `module` values (`fmt`, `math`, `errors`).

**Cross-referencing DGAT and AlphaStack turned out to run the other
direction too** — DGAT's own dependency graph drops every external/
unresolved import entirely and never exposes symbols at all (confirmed via
AlphaStack's `dependencies.py`, which exists specifically to patch those two
DGAT gaps with its own tree-sitter re-walk). Fleet was already ahead of
DGAT's own graph in both respects before this audit. AlphaStack's own
extractor has the identical qualified-name and async gaps Fleet just fixed —
so fixes 1 and 2 go beyond both references, not just past stress_stack.
Full account: `dgat.md`, `alphastack.md`.

**New capability from fix 3: JS/TS edge resolution** (`edges.py:
resolve_js_edges`), mirroring DGAT's own resolver shape in `dgat.cpp` —
relative-path resolution against the importing file's directory, then try
a set of extensions and `/index.<ext>` in the same order Node's own module
resolution does. Registered for all three JS-family language tags via one
shared resolver (fixed a real dedup bug in `resolve_edges` in the process —
without it, a repo with both `.ts` and `.js` files would have run the JS
resolver twice and double-counted every edge). Not ported: tsconfig.json
path-alias resolution (`@/foo` → a configured root, real in `dgat.cpp`) —
noted, not built; neither fixture repo exercises it.

Verified on `agustus` end to end: edges went from **0 → 4**
(`src/index.ts → src/auth.ts`, `src/index.ts → src/mcp.ts`, `src/mcp.ts →
src/time-groups.ts`, `tests/time-groups.test.ts → src/time-groups.ts`) —
every real internal import in the project, both extension-omitted
(`"./auth"`) and extension-included (`"../src/time-groups.ts"`) styles
resolving correctly. All 4 Python/Go fixtures re-scanned afterward with
**zero change** to file/symbol/test/edge counts, confirming the rewrite
didn't regress the untouched `ast` path or Python edge resolution.

Test count: 8 → 14 (`test_go_method_receiver_and_grouped_imports`,
`test_js_class_method_qualified_name_and_async_flag`,
`test_js_import_module_is_the_specifier_not_the_whole_statement`,
`test_js_edges_resolve_relative_imports_with_and_without_extension`,
plus the pipeline-contract stub).

## Viewer rewritten onto DGAT's actual graph stack

The circular SVG layout from the first pass is gone. `viewer.html`'s Graph
tab now uses **`3d-force-graph`** (loaded from a CDN, pinned to `1.73.4`) —
confirmed as DGAT's own choice by reading its `ui.html` directly
(`<script src="https://unpkg.com/3d-force-graph@1.73.4/...">`), not assumed.
It's a Three.js-based force-directed 3D graph; nodes are files (colored by
language, sized by `sqrt(symbol count)`), links are resolved import edges,
and clicking a node jumps to that file's detail in the Files tab — same
interaction the old SVG view had, adapted to the library's own event API
(`onNodeClick`) instead of hand-rolled SVG event listeners.

One real verification wrinkle, not a bug in the page: this session's
sandboxed preview pane renders a **static snapshot** for local files outside
its recognized project root — a screenshot taken after `loadGraph()` and a
tab switch showed stale pre-load DOM, while direct JS queries against the
live page (`forceGraphInstance.graphData().nodes.length`, checking for a
real `<canvas>` element) confirmed correct state throughout. Verified this
distinction explicitly (a screenshot mismatch that direct state queries
then contradicted) before concluding it was a tool artifact, not chasing a
phantom bug. A real browser — which is how anyone actually using this opens
it — doesn't have this limitation.

## Viewer was silently dropping external imports, and never said what was imported

Two more findings from the user actually reading the viewer's output closely
against `agustus`.

**External/unresolved imports were invisible.** The Imports section only
ever read from `outgoing[path]` — resolved edges — so a file's package
imports (`zod`, `@cloudflare/workers-oauth-provider`, `node:assert/strict`)
never appeared at all, silently. Not a data gap (`ParsedFile.imports` always
held every import, resolved or not) — a viewer gap: it only ever looked at
the edge list. Fixed by rendering from `f.imports` (every import) and
cross-referencing each one against `outgoing[path]` **by line number** to
decide resolved-vs-not: resolved → the existing file link; unresolved → a
`package` badge with the raw module string. Line-number matching is a
simplification (two imports on one line via `import a; import b` would
collide) — reasonable for what this views, not airtight for pathological
input.

**"Imported by" named the file but never what was actually pulled from it.**
Fixing this needed new data, not just new rendering: `ExtractedImport.symbols`
was populated for Python (`from x import a, b` → `["a", "b"]`) but **always
empty for every tree-sitter-parsed language** — the tree-sitter import
capture never extracted an import clause's names at all, just the module
string. Inspected the actual grammar structure empirically before writing
the fix (`get_parser("typescript").parse(...)`, walking the tree) rather
than assuming: confirmed `import_clause` → `named_imports` → `import_specifier`
→ `identifier`, and — the detail that mattered — a TypeScript `type`-only
specifier (`type AuthRequest`) keeps `type` as a sibling keyword token
*inside* `import_specifier`, never touching the `identifier` child, so
reading just that child strips it for free with no special-casing. Added
`_import_symbols()` covering default (`import Foo from ...`), namespace
(`import * as ns`), and named/type-mixed imports; wired the result onto
`Edge.symbols` at resolution time, in both `resolve_python_edges` and
`resolve_js_edges`. Verified against the real file: every edge now carries
the exact real names — `src/index.ts → src/auth.ts` reports `["authHandler",
"AppEnv"]`, matching `import { authHandler, type AppEnv } from "./auth"`
exactly. The viewer's "Imported by" rows now read "`src/index.ts` imports
`authHandler, AppEnv`" instead of just naming the file.

Not done: Go and Rust still report `symbols: []` on every import (Go's
`import "fmt"` doesn't name symbols the way JS does — there's nothing to
extract, that's correct; Rust's `use std::collections::HashMap` genuinely
does name one, extraction just isn't written yet).

## Graph tab ported onto DGAT's exact configuration, not a lookalike

Explicit ask: match DGAT's own graph UI, not just use the same library.
Previously only the library choice (`3d-force-graph`) was confirmed from a
grep; this pass read `ui.html`'s actual `ForceGraph3D()` configuration block
line by line and ported every value, not just the library name:

- **Nodes colored by top-level directory cluster, not language.** DGAT's
  `clusterKey(relPath)` is the first path segment (`"(root)"` for anything
  at the top level); colors cycle round-robin through a fixed 12-color
  palette (`#a78bfa` violet, `#22d3ee` cyan, `#34d399` emerald, ... ) as new
  clusters are discovered. Ported verbatim, including the palette order —
  this only applies to the Graph tab; the Files tab keeps its per-language
  dots, which is a different, still-useful question ("what language is
  this") that DGAT's own tool doesn't need to answer at all.
- **Nodes sized by connectivity, not code volume.** `val = 1 + depended_by ×
  0.7 + depends_on × 0.3` — a widely-imported file gets bigger than one that
  merely imports a lot itself. Replaces the earlier `sqrt(symbol count)`
  sizing from the first 3d-force-graph pass.
- **Exact visual/physics parameters carried over**: `backgroundColor
  '#05050a'`, `nodeRelSize(4)`, `nodeOpacity(0.92)`, the purple/lavender link
  palette (`rgba(160,140,255,0.65)` default, `rgba(167,139,250,0.95)`
  hovered) with 4 animated directional particles per link, `d3Force('charge')
  .strength(-180)` and `d3Force('link').distance(60)`, and the same
  size-aware camera auto-fit (padding 260/140/80 for <10/<50/≥50 nodes, so a
  small graph doesn't zoom in until one node fills the screen) plus a spin
  toggle that orbits the camera over a 30-second period.
- **A real gap in DGAT's own file, closed rather than reproduced.** Its
  `linkColor`/`linkWidth` accessors both branch on `l.__hovered` — but
  grepped the whole file for `onLinkHover` and any place that sets
  `__hovered`: nothing does. `onNodeHover` there only assigns
  `state.hoveredNode`; no link ever gets flagged, so DGAT's own hover
  highlight is dead code, always falling to the non-hovered color/width.
  The intent is unambiguous from the accessors themselves, so this port
  wires `onNodeHover`/`onLinkHover` to actually mark touching links and call
  `.refresh()` — verified directly (not by eyeballing a render): hovering a
  node with 2 edges attached marks exactly those 2 `__hovered`, confirmed by
  reading `graphData().links` back after the simulated hover.

Verified against a real 6-file, 3-cluster fixture (`(root)`, `src`, `tests`)
built from the actual `agustus` scan shape: cluster colors assigned in
exact palette order, and every node's `val` matched the formula by hand
(`src/time-groups.ts`, imported by 2 files and importing 0 → `1 + 2×0.7 +
0×0.3 = 2.4`, exactly what rendered). Background, charge, and link-distance
all read back exactly as set. Spin toggles on and off cleanly; reset calls
`zoomToFit` without error.

## Tested against two new real repos — a real Rust crate and a full-stack JS app

`rust-bump-allocator` (a real crate — never tested against real Rust before,
only constructed snippets) and `recipehub` (React + Node/Express, 29
hand-written files after `node_modules` pruning in both the root and
`server/`). Two real, confirmed-on-real-code bugs found and fixed; one
correctly-diagnosed non-bug.

**Bug: Rust `impl` blocks never qualified their methods, on any impl,
ever.** `impl_item` has no `name` field in tree-sitter-rust's own grammar —
confirmed empirically (`get_parser("rust").parse(...)`, dumping field names)
before writing anything: only `type` (the Self type) and optionally `trait`.
The existing container-propagation logic gated *both* "does this node get
listed as a symbol" and "does it contribute a container name to its
children" on the same `_node_name()` call succeeding — which it never does
for `impl_item`, so every method in every impl block, trait or inherent,
silently lost its qualification back to a bare name. Fixed by decoupling
the two: a new `_rust_impl_type()` reads the `type` field directly
(confirmed it resolves to the Self type even for `impl Trait for Type`,
where the trait name appears first in the source text — field lookup is by
grammar role, not text position), used only for what a node contributes to
its children's qualification, independent of whether the node itself is
named. Verified on the real crate: `unsafe impl GlobalAlloc for
BumpAllocator { fn alloc... fn dealloc... }` now correctly produces
`BumpAllocator.alloc` / `BumpAllocator.dealloc`, not `GlobalAlloc.alloc` and
not bare `alloc`.

**Bug: `pub use` re-exports kept "pub " stuck on the module text.**
`_module_text`'s Rust branch stripped a leading `"use "` string but `pub use
crate::state::{...}` doesn't start with that — the visibility sits in front
as tree-sitter's own `visibility_modifier` sibling token. Fixed with a
regex stripping any `pub`/`pub(crate)`/`pub(super)`/`pub(in ...)` prefix
before the `"use "` strip. Verified on the crate's real `lib.rs`: `pub use
crate::state::{get_stats, ArenaStats};` now extracts to
`crate::state::{get_stats, ArenaStats}`, matching the plain `use` case.

Full crate audited line-by-line after both fixes, not just the two symbols
that motivated them: all 17 symbols across 6 files, all imports, and all 8
`#[test]` functions (both attribute-based and naming-convention detection)
matched the real source exactly, integration-test crate-name imports
(`memory_allocator::state::{...}`, since `tests/*.rs` compile as separate
crates importing the library by its published name, not `crate::`)
included.

**Bug: the single most common modern JS pattern was invisible — `const f =
() => {}`.** `recipehub`'s `CreateArea.jsx` has ~15 handler functions, every
one written as `const handleClick = () => {...}`; the file's own reported
symbol count was 1 (just the exported `function CreateArea(...)`
declaration). This never surfaced in all the prior `agustus` testing purely
by luck of authoring style — every function in that repo happens to use the
`function foo() {}` keyword form. `definitions` is a flat node-type → kind
table, and `variable_declarator` (what `const x = ...` produces) isn't
safely one of its entries — it also matches every ordinary `const x = 5`.
Fixed with a new `LanguageSpec.variable_function` hook: given a
`variable_declarator`, check whether its `value` field is
`arrow_function`/`function_expression`/`generator_function`; if so, and the
`name` field is a plain `identifier` (not a destructuring pattern — `const
[a, setA] = useState(false)` must never become a symbol named `a`), treat
it as a function definition using the *value* node for line ranges,
`is_async`, and `is_generator`. Verified the destructuring exclusion
explicitly, not just the positive case. Reachable through `export
const ... = () => {}` for free, since `export_statement` isn't a
`definitions` match and the generic recursion just walks through it to the
`variable_declarator` underneath.

Impact on the real repo: `recipehub`'s total symbol count went **24 → 117**
after this one fix — roughly 5×. `CreateArea.jsx` alone went from 1 symbol
to the full 16 (1 exported function + 15 arrow-function handlers), an exact
match against reading the file by hand.

**Correctly diagnosed as a grammar limitation, not a bug — didn't "fix" it.**
2 of `recipehub`'s 29 files reported `has_syntax_error: true` despite
extracting their symbols cleanly. Found the exact `ERROR` node in both via
`tree.root_node` traversal rather than guessing: `⏹️ Stop & Process` in
`CreateArea.jsx` line 538, `& Content`-shaped text in `Stories.jsx` line
194 — both a bare `&` inside JSX text content. This is valid JSX (JSX text
isn't parsed for HTML entities the way real HTML/XML is, so a literal `&`
is unambiguous to Babel/React) — a real, narrow limitation in
tree-sitter-javascript's JSX-text tokenizer, not a problem in either file.
Out of scope to fix (would mean patching the grammar itself, not this
project's code) — documented here so "syntax error" in a future scan isn't
misread as "the file is actually broken" when it demonstrably still parses
well enough to extract every real symbol correctly around the error.

Also confirmed clean on `recipehub`: 39 edges resolved correctly across the
`server/` subtree (`server.js → routes/*.js → models/*.js`, middleware →
models), each carrying the exact real imported names (`recipeRoutes`,
`verifyToken`, `User`, ...), and both `node_modules` directories (root and
`server/`) fully pruned by `.fleetignore`'s defaults with zero manual
configuration.

## Retiring the shared `LanguageSpec` engine for one function per language

The generic `walk_definitions()` + `LanguageSpec` table (`tree_sitter_spec.py`)
had, by this point, quietly stopped being generic: `_rust_impl_type`,
`_go_receiver_type`, `_js_variable_function`, and three `if language == ...`
branches inside `_module_text()` were all real per-language logic bolted onto
what was supposed to be one shared function. Every new language quirk meant
threading another conditional into control flow every *other* language also
ran through — exactly how the Rust `impl_item`-has-no-name-field bug and the
JS arrow-function gap both happened above.

Checked prior art properly before picking a replacement, rather than
reasoning from first principles:

- **DGAT** (`dgat.cpp` + `queries/*.scm`) shells out to the `tree-sitter
  query` CLI with one `.scm` pattern file per language, no hand-rolled
  traversal at all — but its queries only ever capture `@import`
  (`queries/rust.scm`, `queries/go.scm` read in full). DGAT never extracts
  functions/classes at all, so it's precedent for "don't hand-roll
  traversal," not for symbol extraction specifically.
- **AlphaStack** (`src/utils/treesitter_parser.py`) *is* real precedent for
  symbols — every extractor is `language.query(pattern).captures(root)`,
  dispatched by one small function per `(language, kind)` pair, no manual
  recursion anywhere. Its `ParseResult` is simpler than what Fleet needs
  though: flat `classes: List[str]` / `functions: List[str]`, no qualified
  names, no is_test/is_async/is_generator. Its code also doesn't run as-is —
  written against an older tree-sitter Python binding; this project's
  installed tree-sitter (0.26.0) replaced `Language.query()` with standalone
  `Query`/`QueryCursor` classes.

Prototyped the query approach for real against the installed API (nested
`matches()` patterns correctly pair a Rust impl's Self type with its methods,
a JS class with its methods, a Go receiver with pointer-type unwrapping) —
and hit a real, silent-failure mode building the Rust trait-capture query:
**query field-patterns must be listed in the same order fields appear in the
grammar's own children, or an optional field silently vanishes with no
error.** Writing `type:` before `trait:` (natural reading order) silently
dropped `trait` on a real trait impl; only fixed by reordering to match
Rust's actual grammar (`trait` before `type` in `impl Trait for Type`). A
hand-written walk reading `node.child_by_field_name("trait")` has no
equivalent risk. Decided against queries specifically because of this.

**Landed:** one dedicated function per language (`parse_rust`, `parse_go`,
`parse_javascript` — also `typescript`/`tsx` — `parse_c`, `parse_cpp`) in a
renamed `tree_sitter_langs.py`, dispatched through a plain
`PARSERS: dict[str, Callable]`, mirroring `edges.py`'s own
`LANGUAGE_RESOLVERS` pattern rather than inventing a new convention. No
class — nothing stateful to justify one; every parse call is a pure
`(path, code) -> result` function. Each does its own single iterative
stack-walk (matching stress_stack's own original style, not the recursive
closure the old shared engine needed). Qualification (a method by its
enclosing class/impl) is resolved via each node's `.parent` chain rather
than threading container state through recursive calls — confirmed first
that tree-sitter node identity does *not* survive across separate tree
accesses in this binding (`node is node` is `False` for the same underlying
node reached two different ways, verified directly), so impl-to-method
matching compares byte ranges, not object identity.

**New capability added in the same pass** (the actual goal driving the
migration, not just cleanup): class inheritance / trait implementation, as
a new `bases: tuple[str, ...]` field on `ExtractedSymbol`. Confirmed neither
Fleet nor stress_stack captured this before — read stress_stack's original
`tree_sitter_backend.py` directly, which is even simpler than Fleet's own
prior version (a plain iterative walk, no qualified-name concept at all).
Captures the *name* only (`"Animal"`, `"GlobalAlloc"`), not a resolved
file/symbol reference — that's separate, later work, same shape as
import→edge resolution. Python via `ast.ClassDef.bases`; JS/TS via
`class_declaration`'s `class_heritage` child; Rust via `impl_item`'s
existing `trait` field, read alongside the `type` field already used for
Self-type qualification. Rust has no impl-level symbol to attach `bases` to
(only its methods produce symbols), so each method an impl produces carries
the trait name individually — asymmetric versus Python/JS attaching it to
the class itself, but correct and needs no new data-model concept. Go's
interface satisfaction is deliberately not attempted: nothing in a single
file's syntax ever states "Circle implements Shape" — the compiler infers it
structurally, possibly across the whole package — so there's no node for
either a walk or a query to find.

**Real bug found by the real-repo regression sweep, not by the unit
tests:** the new `parse_javascript` hardcoded `get_parser("javascript")`
internally, but is also registered as the parser for `typescript`/`tsx`.
Every `.ts`/`.tsx` file was being parsed with the *JavaScript* grammar
instead of its own — silently reporting `has_syntax_error: true` on any real
type annotation, interface, or generic. `agustus` (real TypeScript) caught
it immediately: 83 → 28 symbols, 0 → 5 syntax errors. None of the unit
tests caught this, because their `.ts`-suffixed fixtures happened not to use
any TS-only syntax. Fixed by giving `parse_javascript` a `language`
parameter the dispatch table actually passes through
(`PARSERS["typescript"] = lambda path, code: parse_javascript(path, code,
"typescript")`), and added a regression test with real TS-only syntax
(`interface Foo { bar: string; }`) that fails under the JS grammar and
passes under TypeScript's, so this can't silently regress again.

Full regression sweep after the fix, all matching this session's
already-recorded numbers exactly (files/symbols/tests/edges unchanged,
`bases` populated only where expected): `tui-calculator` 9/20/7/9,
`glom` 32/569/180/55, `pluggy` 31/297/128/33, `agustus` 6/83/7/4,
`recipehub` 29/117/0/39 (2 syntax errors, same pre-existing JSX-`&` case
above), `rust-bump-allocator` 6/17/8/0 with
`BumpAllocator.{alloc,dealloc}.bases == ("GlobalAlloc",)` and every other
symbol correctly `bases == ()`. 20 tests passing overall — 3 new since the
last count in this file: Python multi-base inheritance, JS `extends`, and
the TS-grammar regression test above.

## Reversing the previous section: back to a shared engine, with the per-language part as data

The section above retired the shared `LanguageSpec` engine for one function per
language, and rejected tree-sitter queries specifically over a silent-failure
mode. Both of those calls were right about the evidence in front of them, and
the second one is reversed here only because the evidence turned out to be
narrower than recorded. Worth stating plainly rather than quietly re-adopting a
rejected design.

**What went wrong with one-function-per-language.** Nothing subtle: it works,
and every audit fix in it is real. It just doesn't extend. Adding Java meant
~130 lines across three files (`languages.py`, `tree_sitter_langs.py`,
`edges.py`), most of it a transcription of an existing parser's stack walks —
the same iterative `stack.pop()` / `stack.extend(reversed(...))` loop appears
seven times across five languages, two or three times per language because
symbols, tests and imports each get their own pass. And `resolve_edges` had to
de-duplicate js/ts/tsx by `id(resolver) in already_run`, which is a workaround
for having no way to say those three are one family.

**Re-reading the prior art with the extensibility question in front.** The
earlier read (recorded above) was accurate about what each project *extracts*.
What it didn't weigh is the cost curve:

| | DGAT | AlphaStack |
|---|---|---|
| per-language artifact | `queries/<lang>.scm`, loaded by filename | 22 queries inlined in Python |
| code per language | none | `_extract_<lang>_{imports,classes,functions,signatures}` |
| languages reached | **15** | 5 |
| `python.scm` / parser size | **4 lines** | 604 lines |

DGAT reached fifteen languages because the per-language artifact is *data* and
the consumer is written once. AlphaStack used the same query mechanism and
still hit an M×N wall, because it wrote per-language consumer code — five
languages, four concerns, twenty functions. So "use queries" was never the
lesson; **"per-language part is data, consumer is written once"** is. Fleet's
one-function-per-language design was AlphaStack's failure mode with a
different traversal mechanism.

**The rejected hazard, re-tested on the installed binding.** The recorded
reason for rejecting queries was that field patterns in the wrong order
"silently vanish with no error". Re-ran it directly on tree-sitter 0.26.0:

- `(impl_item type: (...) @t trait: (...) @tr)` — wrong order, **required**
  fields → **hard error**, `Impossible pattern at row 0, column 38`.
- `(impl_item type: (...) @t trait: (...)? @tr)` — wrong order, **optional**
  field → silently matches with `@tr` missing. `GlobalAlloc` disappears from a
  real trait impl, exactly as recorded.

So the hazard is real, and it is confined entirely to `?`. That makes it a
rule rather than a reason to abandon the approach: **never mark a field
pattern optional.** Write separate single-field patterns and merge them by
node span — a pattern with one field has no field order to get wrong.
`engine._merge_matches` exists for this, and
`test_no_query_uses_an_optional_field_pattern` fails the build if any `.scm`
grows a `?`. The Go query hit the required-field case during this work
(`receiver` precedes `name` in the grammar) and it surfaced immediately as a
compile error, which is the behaviour you want.

**What keeps this from decaying into the old shared engine.** The previous
engine died because per-language logic accreted inside it: `_rust_impl_type`,
`_go_receiver_type`, `_js_variable_function`, and `if language == ...` inside
`_module_text()`. The invariant now is narrower and checkable: **`engine.py`
contains no branch on a language's name.** Everything that used to be such a
branch is either a capture in a `.scm` file or a rule keyed on *grammar shape*
rather than language identity:

- Go's receiver → `@scope.name` captured on the method itself.
- Rust's impl block → `@scope` + `@scope.base`, which is also the only path by
  which bases propagate to contained definitions (a Java class's `extends`,
  captured as plain `@base`, deliberately does not — otherwise every method
  reports as inheriting from `Animal`).
- Rust's `#[test]` → "collect preceding attribute/decorator siblings", which is
  a grammar shape any language may use, not a Rust check.
- Go's `type_declaration` ambiguity → a `_FALLBACK_KINDS` set, so a specific
  pattern beats a generic one on the same node.

Qualification is the clearest win: **"name a definition by the nearest
enclosing captured definition"** is one generic rule that replaced three
hand-written parent-walkers — JS `class_body` climbing, Rust impl byte-range
matching, and Go's receiver lookup. The byte-range-not-object-identity finding
from the audit above is preserved inside it (`engine._key`), since `.parent`
still returns a different Python object for the same node.

**Cost of adding Java, as the actual test of the claim:** `queries/java.scm`
(24 lines) and `languages/java.py` (one `LanguageSpec`). No new dependency —
the grammar already ships in tree-sitter-language-pack. No engine change, no
resolver change: Java reuses Python's `dotted` strategy, and its one genuinely
Java-shaped fact (`import com.foo.Bar` addresses
`src/main/java/com/foo/Bar.java`) is declared as `source_roots` data.

**A real bug the migration surfaced.** Go type declarations were being dropped
entirely — `_GO_DEFINITIONS` mapped `type_declaration` to `"type"`, but
`_node_name()` looked for a `name` field that `type_declaration` does not have,
so every one returned `None` and was skipped in silence. On stress_stack's
`cast`: previously 146 symbols, now 194, and the difference is exactly the 48
type declarations (32 `type`, 9 `struct`, 7 `interface`) that were missing —
142 functions + 4 methods = the old 146 exactly. Also fixed: `it` as a JS test
name prefix, which flagged `items`/`iterate`/`iterator` as tests.

**Regression sweep, all five languages, against real repos.** `glom`
33/570/181 (56 import, 23 inherit), `pluggy` 31/297/128 (33 import) — symbol
count matches the previously-recorded 297 exactly; `click` 81/1312/539;
`cast` 21/194/31 (see above); `TinyGoRPC` 8/50/16; `calc-rs` 8/55/4;
`rust-bump-allocator` 6/17/8 with `BumpAllocator.{alloc,dealloc}.bases ==
("GlobalAlloc",)` preserved; `FlappyBird-TS-Canvas` 9/46/8 (14 import);
`recipehub` 29/117/0 (39 import, 2 pre-existing JSX-`&` syntax errors,
unchanged). All 21 pre-existing tests pass **unmodified** apart from import
paths — including every assertion added by the audit above — plus 18 new ones.

**Schema 0.1.0 → 0.2.0.** `contain` (file→class→method) and `inherit` edges
added, both free from data already being captured; `edges_by_kind` and
`calls_total` added to statistics; `parent` added to each symbol. `inherit` is
the only edge kind that can be wrong (it matches a base *name*), so it is
conservative: same-file definition wins, a single unambiguous repo-wide match
is accepted, anything more ambiguous is dropped rather than guessed. `invoke`
is deliberately **not** emitted — call sites are recorded on every parsed file
so the data is on disk, but resolving a bare callee name without type
information produces real false positives and deserves its own pass.
`viewer.html` filters the force graph to edges whose endpoints are both file
paths (symbol-level edges would reference nodes that don't exist in a
file-level view) and shows the by-kind breakdown in the header so nothing
looks like it went missing.

## Phase 2a shipped: `fleet mine` — issue-first, window-based candidate discovery

The first real `Pipeline` entry, and it inverts Repo2RLEnv's selection
direction in three places. Each departure was checked against the live
GitHub API before it was built, not argued from the docs.

### Issues are the candidates; PRs are looked up from them

`pr_runtime` mines merged PRs and regexes `Fixes #\d+` out of PR bodies to
find the issue (`_linked_issue_number`). That guess fails in both
directions, and one live issue — `pallets/click#3822` — shows both failures
at once:

```
xref  #10292  pr=True  merged_at=None        open PR
xref  #3835   pr=True  merged_at=None        claimed the fix, closed unmerged
xref  #3858   pr=True  merged_at=2026-09-08  the actual fix
connected                                    linked in the UI — no keyword anywhere
```

`/repos/{o}/{r}/issues/{n}/timeline` records all of it, works
**unauthenticated**, and is the authoritative answer to "which merged PR
closed this issue". Body-regexing accepts #3835 and can miss #3858
entirely. Over 60 days of `click`, 7 PRs claimed a fix and never merged —
`unmerged_pr_traps` in every run report counts them, so the cost of the
per-issue call stays justified by data rather than by assertion.

The second, larger prize is leak-freedom **by construction**. An issue body
was written before the fix existed, so it cannot name the commit that fixed
it or the tests that grade it. That deletes the reason `pr_runtime` carries
eight leak-stripping regexes and a rule dropping everything after a `Tests
added` header — that rule exists because a PR body names the exact
FAIL_TO_PASS tests. `fleet` needs no equivalent.

REST's one gap: a `connected` event (a link made through the UI sidebar)
carries no target number. GraphQL's `ConnectedEvent.subject` does. Runs
report the count (9 over the same window) rather than under-yielding
silently; closing it is a token-only enrichment, deferred.

### A time window, not "the most recent 50"

`--shallow-since=<date>` puts the graft exactly at the window boundary, so
early history is never fetched — `click` at 2 months is 91 commits and 2.8s
against a history of thousands, and `--depth=N` can only approximate that
with an N that differs per repo by orders of magnitude. `git log
--first-parent` then walks integration points only, stepping over the
inside of every merged branch. Deliberately not `--merges`: a squash lands
as a single-parent commit, so `--merges` would yield nothing at all on a
squash-merge repo.

`limit` demotes from a selection gate to a cost cap (`max_candidates`),
applied *after* the free gates and counted under its own rejection reason —
`over_budget` means "spend more", not "this repo is unsuitable", and
collapsing the two into one number hides which it was.

### The base commit comes from the merge point, not the API

`pr_runtime` takes `pull_request.base.sha` (`github.py:64`). That is the
base branch tip *at last sync*; the moment the base advances during review
it is stale, and the failure is invisible until the sandbox rejects the gold
patch as `gold patch failed to apply at base_commit`
(`pr_runtime_validate.py:262`). The first parent of the merge point is the
truth and costs nothing. Verified on `click#3740`: `base_sha 9c4dfdaebe0e`
matches `git log --first-parent`'s `parents=9c4dfda`, and the fix PR merged
one second before the issue closed — the time-proximity signal the selector
uses to disambiguate several merged PRs referencing one issue.

### Rebase detection: a verdict plus its evidence, or `UNKNOWN`

Two parents proves a merge commit. A *missing* `(#N)` subject suffix is
strong evidence of a rebase, because GitHub appends that suffix when
squashing and when merging but never when rebasing — "Rebase and merge"
replays the author's subjects verbatim. What cannot be proven from a clone:
the reflog is local-only and never travels with one, `author_date !=
committer_date` is equally what cherry-picks and `--amend` produce, and
`patch-id` equality is evidence rather than proof. So `classify` returns the
style **and the evidence it rests on**, and answers `UNKNOWN` when a
rebase-merge and a direct push are indistinguishable — `models.py` carries
`dates_diverge` as a documented non-signal the classifier deliberately does
not consult. `HeadRefForcePushedEvent` settles it with a token; also
deferred. The rebase path is tested against a genuinely rebased commit, not
a fixture: real repo, real `git rebase`, asserting the SHA changed and the
patch-id didn't.

### Search over the list endpoint — a 7× call reduction, measured

First implementation used `/repos/{o}/{r}/issues?state=closed&since=…`. That
endpoint filters on *updated* time, the only thing it offers, and returns
pull requests mixed in. Over 60 days of `click`: **3,300 items across 33
requests**, of which 1,703 were PRs and 1,528 were issues closed long before
the window. `/search/issues?q=repo:… is:issue is:closed closed:A..B` asks
the server the actual question: **33 items, one request, no PRs, all in
window.** End to end the run went 41 calls → 6, and 3,262 rejections → 27,
every one of which is now a real judgment rather than listing noise.

Two limits come with it, both handled rather than hoped past: search caps at
1,000 results per query, so an oversized window is bisected by date
(silently losing a window's tail is the worst failure mode available — the
output still looks plausible); and search bills to a separate, much smaller
budget, so a failure there falls back to the list endpoint, where the
pull-request gate still earns its place.

### No `gh`, no new dependency

Repo2RLEnv shells out to `gh` for every call. `gh` was not installed on the
machine this was built on, which is the argument: the client is ~200 lines
of `urllib`, works unauthenticated at 60 requests/hour, and adds nothing to
`pyproject.toml`. Responses are cached to disk by URL — at 60/hour, an
uncached re-run while tuning filters burns the whole hourly budget, so the
cache is what makes iteration possible rather than a nicety.

### Boundary

Phase 2a is discovery only: `candidates.json`, not task directories. The
pipeline reports `tasks_emitted=0` and a `phase: "A: discovery"` detail
rather than overstating what it produced — `base.py`'s contract says a
pipeline must never emit an unvalidated task, and the Harbor emitter is
still a stub. Phase 2b is the fail-to-pass validation run; every field it
needs is already on `Candidate`. 77 new tests, all 83 prior tests pass
unmodified.

### The offline path's ceiling, measured

A no-token path exists (`--offline`) that reads `(#N)` and `Fixes #M` out of
commit messages, needing no network at all. Its ceiling was measured rather
than assumed, and it is low: over the same 60 days of `click`, **22 merges
all carrying a `(#N)` suffix and not one carrying a closing keyword.** The
issue linkage lives only in GitHub's database. So the offline path yields
zero there, and rather than reporting "0 candidates" — which reads as
"nothing to mine" — a run now reports *why*: `merges_with_pr_number=22,
merges_with_closing_keyword=0` plus a plain-language `offline_limitation`
note pointing at the API path. It stays useful for repos that do write
`Fixes #N` into merge commits, and for cheaply scoping a window before
spending API budget; it is not a substitute for the timeline.
