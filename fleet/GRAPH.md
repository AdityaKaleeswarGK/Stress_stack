# Repo graph: the current version

**Python, Rust and JS/TS** resolve imported names to their definitions and
record where those names are used. Go/Java are outside this milestone.

The idea is simple. Given:

```python
from orchard.model import double as twice

def build(count):
    return twice(count)
```

Fleet records:

```text
service.py imports double from model.py, locally named twice
build references twice
that reference points to model.py::double
model.py is imported by service.py
```

The import is a binding: a local name linked to its origin. Functions and classes
remain children of their source file. References connect them across files.
An imported library such as `json` is kept too, including which functions use it.

## Try the working sample

From the `task_repo` directory:

```sh
fleet/.venv/bin/python -m fleet.cli scan fleet/tests/sample_repository --out /tmp/sample_graph.json
fleet/.venv/bin/python -m fleet.cli imports /tmp/sample_graph.json python/src/orchard/model.py
fleet/.venv/bin/python -m fleet.cli imports /tmp/sample_graph.json typescript/src/model.ts
```

Open `fleet/viewer.html` and load that JSON. Select a file to see imported
names, their targets, the functions/classes referencing them, and incoming files.
The JSON query works without the viewer and can be used by a coding agent.

The sample is a small real Python package, Rust crate and TypeScript package.
Its Python runtime check, Rust integration test and TypeScript `node --test`
run all pass. Each one's fixtures cover the same ground: aliases, re-exports,
nested functions/modules, an external standard-library import, and a parameter
that shadows an imported name.

To rerun the sample itself from `task_repo`:

```sh
PYTHONPATH=fleet/tests/sample_repository/python/src fleet/.venv/bin/python -m unittest discover -s fleet/tests/sample_repository/python -p runtime_check.py
cargo test --offline --manifest-path fleet/tests/sample_repository/rust/Cargo.toml --target-dir /tmp/fleet-sample-rust-target
node --test fleet/tests/sample_repository/typescript/runtime_check.ts
```

The TypeScript sample needs Node 22.6 or newer — it is run directly, through
Node's own type stripping, with no build step and no installed packages.

## What changed

- Python retains nested definitions, qualified owners, docstrings, source spans
  and complete import aliases. Parameters, local assignments, comprehensions,
  lambdas and pattern captures can shadow imports.
- Rust uses the existing Tree-sitter parse. It handles grouped/nested `use`,
  aliases, module declarations, inline modules and associated calls such as
  `Box::new`. Cargo metadata connects integration tests to the library crate;
  explicit local path dependencies can use their Cargo alias.
- JS/TS resolve a specifier the way Node does — relative path, then each
  extension in the family, then `<dir>/index.<ext>` — including TypeScript's
  habit of writing the emitted `./util.js` for `util.ts`. What a name means
  once the file is found comes from that file's recorded **exports**, not from
  matching its top-level definitions: `export { internal as public }` and
  `export { x } from "./other"` both mean the visible name and the defining
  name differ, and barrel files are followed to the file that really defines
  the name. Default, named, namespace, type-only, side-effect and
  `export *` forms are all bindings, as are the two common `require()` shapes.
  TypeScript's interfaces, enums, type aliases and enum members are extracted,
  so an imported type has something to resolve onto.
- Python recognizes ordinary packages and common `src` layouts. A directory
  actually imported as `src` remains a package. Custom roots can be listed in
  `[tool.fleet.graph] python_roots = ["custom_root"]` in `pyproject.toml`.
- One resolver supplies saved bindings and reference edges. Imported-by is the
  reverse view of those file dependencies, not another inference pass.
- `.fleetignore` now handles paths, root anchoring, `**` and `!` using PathSpec.
  For example: `/generated/*` followed by `!/generated/keep.py`. If the whole
  parent directory was excluded, re-include that parent first. Repository
  `.gitignore` and global Git settings are not read in this version.
- Graph schema 0.4 saves the actual reference records, docstrings and a file's
  exports, plus source/configuration hashes, parser versions and the extractor
  fingerprint. Old graphs must be rescanned to gain those missing facts.
- Generated ambient declaration files (`worker-configuration.d.ts` and
  friends) are excluded by default. They are a tool's snapshot of a platform's
  globals, not a repository's own code: one six-file repo here reported 1,101
  symbols, 1,045 of them from that one generated file.

## Read the output correctly

`internal` means the imported target was resolved in the scanned source.
`external` means a standard-library/dependency name was recognized, or no matching
local package was found after resolution. The latter is a practical heuristic,
recorded as `no_matching_local_package`; it does not verify installation or a
dependency version. Known local packages with missing symbols, and explicitly
relative imports, remain unresolved rather than being classified as external.
`conditional` means a target was found but its import's
condition has not been evaluated. Other states are `ambiguous`, `unresolved`
and `unsupported` with a reason.

For JS/TS the same words are used with Node's rules behind them. A bare
specifier (`"zod"`, `"@scope/pkg"`) is a package by definition and is always
`external` — `builtin_module` for Node's own modules, otherwise
`stdlib_or_declared_dependency` when package.json declares it and
`no_matching_local_package` when nothing does. A *relative* specifier that
matched no file stays `unresolved` (`relative_specifier_not_found`) rather
than being called external: that is a genuine miss, such as an asset import or
a tsconfig path alias. A name the target file does not export is
`module_resolved_symbol_unknown` — the file dependency is still recorded,
because the file really is loaded.

Each binding contains `uses`: owner, source expression/span and any resolved
target. A use also retains the binding's original target when a member such as
an enum variant or method cannot be resolved. The graph can therefore show
which function references that imported type without inventing a method target.

**No recorded references does not prove an import is unused.** Python wildcard
imports, string annotations and dynamic behavior, Rust macro tokens and inferred
receiver types, and many exported constants are not fully resolved. In JS/TS,
`module.exports = ...`, dynamic `import()` and tsconfig `paths` aliases are not
interpreted, an exported `const` resolves to its file but to no symbol because
there is no definition node for it, and optional chaining or a computed member
(`a?.b`, `a[k]`) is recorded at the root name rather than as a name path.
Python and JS reassignment are both treated conservatively rather than
attempting data-flow analysis.
Rust files without a manifest can use a visibly diagnosed inferred module layout.
Cargo workspace-inherited dependency configuration and conditional module-file
selection are not fully implemented. Rust visibility is not type-checked.

This is an import-use graph, not complete runtime call tracing or test coverage.
Direct same-file calls without an import are still only syntax records. Static
relations assume ordinary language bindings; monkey-patching and runtime import
hooks can change behavior. The scanner never executes the target repository.

## Verification

Run Fleet's tests:

```sh
fleet/.venv/bin/python -m pytest fleet/tests
```

Run the repeatable real-repository checks:

```sh
fleet/.venv/bin/python graph_audit/validate_bindings.py
```

See [the results](../graph_audit/binding_results/SUMMARY.md). They include source-
witnessed relationships and graph/span consistency checks. Counts and the small
set of passing questions are not a broad accuracy benchmark.

Task generation and optional LLM summaries come next, now that Python, Rust
and JS/TS share one import-use foundation.
