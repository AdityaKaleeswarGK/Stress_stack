# Graph hypothesis assessment — 11 September 2026

The proposed file/class/function graph is worth developing as agent context.
The present implementation is a useful structural index, but does not yet
supply the relationships and retained information required by that proposal.
Fix the demonstrated graph gaps before evaluating whether LLM enrichment adds
value. Coverage will still require a separate execution measurement.

## What was measured

Fresh scans covered 13 local working trees, 256 source files, 2,740 symbols and
375 import edges. Fleet's existing 44 tests passed. Additional checks included
32 hand-specified acceptance probes and 15 source-checked repository questions.
The acceptance probes passed 10/32; the repository questions passed 7/15. These
are deliberately demanding capability checks, including unimplemented features,
not representative estimates of precision or recall.

No duplicate symbol IDs or dangling edge endpoints appeared in this corpus.
That establishes structural consistency only. A same-line Java overload fixture
does produce duplicate IDs, and false inheritance edges still point to valid
but incorrect nodes. Recipehub has two files reporting syntax errors.

The scans produced 15,755 in-memory call records and 799 nonempty docstrings.
Zero call records and zero docstrings were retained in the saved graph JSON.
The call count includes extraction issues such as duplicated nested calls; it
is not a count of verified unique calls or invocation edges.

Old calc-rs output contains zero import edges; a fresh scan contains 14.
Old rust-bump-allocator output contains zero; a fresh scan contains three.
Agustus, recipehub and tui-calculator retain their prior import counts. This
helps explain observations from old graphs, but those artifacts do not pin
their input hashes and therefore are not a controlled before/after experiment.

## Concrete agent questions

- FlappyBird: locating Renderer, locating its render method, and finding main.ts
  through reverse file imports work. Locating GameSnapshot fails: its interface
  declaration is absent. Resolving render's call to clearCanvas also fails.
- Glom: Path.from_text exists, but its nested create function is absent and its
  source docstring is unavailable from saved JSON.
- Pluggy: relative imports inside src/pluggy resolve. Public imports in
  testing/test_pluginmanager.py do not resolve to src/pluggy/__init__.py. The
  test-local class test_pm.A is absent, which matters when explaining plugin
  behavior inside tests.
- Calc-rs: engine's crate::models import resolves.
- Rust bump allocator: an integration test's memory_allocator::state import
  does not resolve through the Cargo package/library configuration.
- TinyGoRPC: the raw package import is present, but no Go import edges are
  emitted and the rpc.NewServer reference has no invocation edge.
- Agustus: the relative auth import resolves.

Tiny-rpc also has zero import edges, but its own files share one Go package;
zero file-import edges alone is not a defect there. Package membership and
cross-file symbol references are the useful relations for that case.

Full recursive Python inventories found 67 omitted qualified definitions in
glom, 436 in pluggy and 625 in click. Many are inside tests, nested functions,
or conditional blocks. Fleet explicitly extracts only top-level definitions
and one method level today; these numbers quantify that restriction rather
than imply every omitted definition is a desirable task candidate.

## The representation to test next

Use the hierarchy for lexical ownership, and typed edges for relationships.
Keep a single node per definition within a snapshot rather than duplicating a
callee beneath each caller. For example:

```text
engine/renderer.ts
└── Renderer
    ├── render
    └── clearCanvas

Renderer.render ──calls──> Renderer.clearCanvas
main.ts ──imports──> engine/renderer.ts
```

An entity should expose its snapshot identity, kind, qualified name, source
path and span, signature, source documentation, and lexical parent ID. Preserve
imports as bindings with original and local names, scope and source location.
Preserve call expressions and caller IDs even when the target remains unknown.

Relationships should distinguish lexical containment, package/type membership,
imports and re-exports, inheritance, interface/trait implementation, and calls
or references. Go receiver methods defined in a different file need semantic
type membership in addition to their lexical location. Rust trait methods
should not acquire an inheritance edge simply because the impl block has a
trait name.

For every resolved relationship retain the evidence location and resolution
method. For unresolved references retain the expression and a reason. An
external import and an internal import the resolver cannot handle must not
share a definitive "external package" label. An empty incoming list means
"no incoming imports resolved" unless the resolution scope is known complete.

Use per-test coverage as an additional observed relationship, identified by
snapshot, environment, test ID and run. A lexical containment graph helps map
executed lines to the appropriate symbol; it does not itself show which tests
executed those lines or asserted useful behavior. Existing static tests can
provide candidate links, but mark those as inferred rather than observed.

## Optional LLM enrichment

An LLM could make graph results easier to understand by summarizing a function's
responsibility, explaining a data transformation, or suggesting relevant
components for a natural-language request. That benefit is a hypothesis here;
it was not benchmarked in this run.

Keep generated summaries separate from extracted source documentation and
resolved relationships. A docstring already exists in source and requires no
LLM. "This method draws a frame from a game snapshot" is a useful generated
explanation. Whether Renderer.render calls Renderer.clearCanvas should be
answered from the source and applicable language semantics. An unresolved
dynamic relationship may be suggested by a model, but that suggestion should
not silently become a verified graph edge.

Each generated annotation should record entity ID, source/context hashes,
evidence spans, model and prompt version. A dependency-summary change may
invalidate a summary even if the entity's own body did not change. Generate
on demand for selected entities rather than summarizing every node upfront.
Keep solver-visible summaries grounded only in the task starting snapshot.

The presence of all the current failures without any LLM suggests the first
work is extraction and resolution correctness. It does not demonstrate that
enrichment is ineffective: an A/B comparison has not been performed.

## Scope of the next implementation

1. Preserve docstrings, calls, aliases, full expressions and diagnostic states
   in exported data; add nested lexical scopes and reliable snapshot-local IDs.
2. Resolve Python source roots and import bindings correctly, eliminating
   unrelated-name inheritance matches. Add the demonstrated type declarations
   and grouped declarations needed by the target languages.
3. Implement a bounded set of provable call/reference cases; explicitly report
   unresolved dynamic targets. Add project-aware Go/Rust/TS resolution where
   the selected corpus demonstrates demand.
4. Rerun this audit after each change, retaining before/after artifacts on
   identical snapshots. Keep known intentional limitations separate from
   regressions and grow the labeled cases as new behavior is supported.
5. Measure query usefulness on held-out source questions: target recall@k,
   unsupported/incorrect claims, latency and context tokens. Compare source
   search alone, source plus the graph, and source plus graph plus optional LLM
   summaries under the same context budget. The answer key must come from
   independent source inspection or runtime evidence, not generated summaries.

Start with glom and pluggy as the Python integration pair and FlappyBird as a
TypeScript pair. Keep Rust and Go as explicit capability tests. Do not require
perfect static resolution of all dynamic behavior before task work can start;
require accurate answers or explicit unknowns for the chosen agent queries.

There is reusable deterministic design in stress_stack's symbols.py and
graph.py: scoped import bindings, signatures, expression anchors, unresolved
reasons, and re-export resolution. Port individual mechanisms with regression
cases rather than importing its entire orchestration module. Re-deriving a
graph with the same algorithm is a consistency check, not an independent
semantic accuracy test.

The attached results contain the evidence needed to decide that next scope.
Fleet production code and all source repositories were left unchanged.
