# JS/TS review — 12 September 2026

The separate import/export extraction pass is a sound direction. Reusing the
existing Tree-sitter tree, preserving export names, and testing aliases,
re-exports, JSX and source spans are useful improvements. However, several
supported constructs currently produce incorrect or missing relationships.

Validation: all 83 existing tests pass, and the TypeScript sample's Node runtime
test passes. Eight additional focused probes reproduce the gaps below. These
were chosen to challenge specific semantics; their failure rate is not an
estimate of overall accuracy. Production code was not modified for this review.

Run from task_repo:

```sh
fleet/.venv/bin/python graph_audit/jsts_review/reproduce.py
```

Sources, expected outcomes and observed results are in [results.json](results.json).
The runner reports gaps without deliberately failing the existing test suite.

## Findings, ordered by impact

1. **[P1] A single unparenthesized arrow parameter does not shadow an import.**
   In `import {run} from './lib'; const f = run => run();`, the extractor credits
   both the parameter declaration and its call to the import. Neither is an
   import use. `walk` handles the plural `parameters` field but does not bind
   an arrow's singular `parameter`; declaration filtering misses it too.
   Fix both parameter registration and declaration exclusion.
   [jsts.py:338](/Users/adityagk/Desktop/projects/task_repo/fleet/src/fleet/graph/jsts.py:338)

2. **[P1] A named default declaration invents an additional named export.**
   `export default function run(){}` currently exports both `default` and
   `run` in the graph. Consequently, the invalid `import {run}` resolves and
   emits an invocation edge. Register only `default` for that declaration;
   a separate explicit named export can expose `run` if present.
   [jsts.py:237](/Users/adityagk/Desktop/projects/task_repo/fleet/src/fleet/graph/jsts.py:237)

3. **[P1] Star-export lookup ignores export precedence and forwards defaults.**
   `export * from './lib'` wrongly forwards lib's default. Also, adding an
   explicit `export function run(){}` to a star barrel makes `run` ambiguous
   when lib exports a different run, although the explicit export takes
   precedence. Resolve explicit exports before star exports and never use
   stars to resolve `default`. Preserve ambiguity for genuinely conflicting
   star exports. Independent Node module imports confirmed the expected
   behavior (default-only module keys: `[default]`; star barrel keys: `[]`;
   explicit override selects its own implementation).
   [bindings.py:281](/Users/adityagk/Desktop/projects/task_repo/fleet/src/fleet/graph/bindings.py:281)
   The [ECMAScript ResolveExport algorithm](https://tc39.es/ecma262/multipage/ecmascript-language-scripts-and-modules.html#sec-source-text-module-records-resolveexport)
   specifies explicit resolution and the default exclusion before star lookup.

4. **[P2] Block-local handling of `var` creates false imported calls.**
   `function f(){ if(true){var run=()=>1;} run(); }` calls f's local run, even
   when the module imports run. The extractor places that declaration in the
   if-block scope, so the final call is incorrectly linked to the import.
   Scope records need a function/module boundary; hoist `var` declarations to
   it while keeping `let` and `const` block-scoped.
   [jsts.py:319](/Users/adityagk/Desktop/projects/task_repo/fleet/src/fleet/graph/jsts.py:319)

5. **[P2] Recognized CommonJS imports lose their uses.**
   `const lib=require('./lib.cjs'); function f(){lib.run();}` records the
   binding but zero uses. `walk` adds lib to `scope.locals` before `require`
   registers the binding; `finish` then treats it as shadowed. This affects
   destructured require bindings too. Handle recognized require bindings
   before adding ordinary local declarations. This is independent of the
   documented lack of `module.exports` symbol resolution: the witnessed use
   should still be retained even if its member target remains unknown.
   [jsts.py:319](/Users/adityagk/Desktop/projects/task_repo/fleet/src/fleet/graph/jsts.py:319)

6. **[P2] Namespace re-exports forward the namespace name into the target.**
   `export * as tools from './lib'` followed by `tools.run()` tries to resolve
   lib's `tools.run`, so the import and use remain unresolved. The namespace
   denotes lib itself; only the remaining `run` segment belongs in target
   lookup. Give namespace exports an explicit kind instead of conflating
   their empty local name with other export forms.
   [bindings.py:256](/Users/adityagk/Desktop/projects/task_repo/fleet/src/fleet/graph/bindings.py:256)

7. **[P2] TS extension substitution selects emitted JS before TS source.**
   With both lib.js and lib.ts present, `import {run} from './lib.js'` in a TS
   file targets lib.js. For the proposed TS source graph, TypeScript lookup
   should prefer lib.ts. Model source/type lookup separately from runtime
   lookup and use extension-specific candidate orders; the current generic
   .ts/.tsx/.mts/.cts list also mixes distinct module extensions.
   [bindings.py:211](/Users/adityagk/Desktop/projects/task_repo/fleet/src/fleet/graph/bindings.py:211)
   [TypeScript's documented extension substitution](https://www.typescriptlang.org/docs/handbook/modules/reference.html#file-extension-substitution)
   gives the relevant lookup order. .mts/.cts candidates additionally cannot
   work until those extensions are registered for parsing.

## Recommendation

Keep this architecture. Fix lexical scopes and export semantics before
expanding graph-dependent task generation. Add the eight probes to regression
coverage as each behavior is fixed. Existing CommonJS tests assert only the
file target; also assert retained uses and ownership. Existing default-export
tests need the negative named-import case, and star tests need both override
and default-exclusion cases.

Type-only import/export metadata is also worth preserving in the next schema
iteration, so source/type dependencies are distinguishable from runtime ones.
This is a representation improvement rather than a claim that every current
type-reference edge is wrong. Config aliases and dynamic imports are already
documented limitations and were not treated as newly introduced defects here.
