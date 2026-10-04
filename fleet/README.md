# Fleet

The improved stack discussed in [`../README.md`](../README.md) and designed
in [`../deep_research/notes/decisions.md`](../deep_research/notes/decisions.md).

## Install (run `fleet` from any path, like `dgat`)

```bash
uv tool install --editable /Users/adityagk/Desktop/projects/task_repo/fleet
```

`uv`'s tool-bin directory (`~/.local/bin`) is already on PATH on this
machine, so `fleet` becomes a normal global command afterward — run it from
anywhere, against any repo path:

```bash
cd ~
fleet scan /path/to/any/repo/on/your/laptop
```

`--editable` means edits to this source take effect immediately, no
reinstall — same property `pip install -e .` gives DGAT. To update after a
`pyproject.toml` dependency change, or to remove it entirely:

```bash
uv tool install --editable . --reinstall   # after changing dependencies
uv tool uninstall fleet                    # remove the global command
```

The project-local `.venv/` (used during development, e.g. for `pytest`)
still exists separately and is untouched by this — the two don't conflict.

## Initial history filtering

```bash
fleet filter owner/repo --repo-path /path/to/existing/clone
fleet filter owner/repo --max-commits 200 --max-changes 100 --limit 25
```

`fleet filter` writes `.fleet/OWNER--REPO/filtered_candidates.json`. It reads
committed Git history and stops when it retains 25 candidates, measures 100
changes, or reaches the 200-commit mainline window (or the available history).
Each limit is configurable. Rejected changes consume the inspection budget,
not a candidate slot. The clone remains intact; these are walk limits, not
shallow-clone depths. Without `--repo-path`, Fleet creates/reuses its workspace
clone; that initial clone may need network access.

The shared filename/directory rules classify each changed file as source,
test, documentation, or configuration. Test-only, documentation-only,
configuration-only and mixed changes without source code are excluded.
Source-only fixes are eligible, as are source changes accompanied by tests.
The default source-file cap remains 10 (`--max-source-files`); there is **no
400-line cap**. Line counts are additions plus deletions, not net growth.
The report retains totals, per-role counts and every changed path, including
documentation/configuration accompanying an eligible source change. Binary
files are flagged because Git cannot supply their line counts.

Each candidate includes its base/fixed SHAs, commit message, timestamps,
inferred merge style, and any PR number or closing keywords in the message.
Missing metadata does not exclude a source change. A Git-message PR number
is a hint, not GitHub verification; individual rebased commits can still need
PR grouping. Issue numbers are keyword claims, not confirmed closed issues.
The output also includes rejected changes with reasons and the stopping
condition. It is a separate initial-filter artifact and does not overwrite
the enriched `candidates.json` used by `fleet agent` and `fleet validate`.

This stage performs no GitHub API enrichment, LLM review, Docker execution or
runtime acceptance. Candidates have `validation: "not_run"`; no task or problem
statement is certified. `--ref`, `--since` and `--until` restrict committed
history; `--out` writes an additional report copy. `fleet mine` also has no
default source-line cap; its optional `--max-source-loc` can impose one explicitly.

## Current graph work

Start with [GRAPH.md](GRAPH.md) for the short explanation and runnable examples.

Python, Rust and JS/TS connect imported aliases to source files/definitions
and record which named functions/classes reference them. Go/Java are outside
the current milestone and are no longer scanned by default; their legacy
parsers have not been deleted.

The graph uses Python AST, Tree-sitter and deterministic resolution. It does
not run an LLM. Source parsing and import resolution are separate concerns:
adding a grammar gives syntax support, while name resolution needs language rules.

- `graph/pyast.py`: Python definitions, scopes, aliases and import uses.
- `graph/rust.py`: Rust use trees and lexical references from Tree-sitter.
- `graph/jsts.py`: JS/TS imports, exports and lexical references, from the
  same tree. A language names its second pass through `LanguageSpec.binder`.
- `graph/bindings.py`: module/source-root/Cargo/Node lookup and graph connections.
- `graph/models.py`: shared records and JSON serialization.
- `graph/query.py`: imports, imported-by and used-by from the saved graph.
- `graph/ignore.py`: repository-relative `.fleetignore`, using PathSpec.
- `graph/scan.py`: inventory, metadata, source hashes and graph assembly.
- `graph/engine.py`, `queries/`, `languages/`: shared syntax extraction.
- `pipelines/` and `emitter/`: task-generation scaffolding; not expanded in this milestone.

Validation: run `.venv/bin/python -m pytest` in this directory. The local
virtual environment now includes PathSpec; reinstall other Fleet environments
with the updated dependencies before using this version.

## CLI

```bash
fleet scan <path> [--out repo_graph.json] [--languages python,rust]
```

## Viewing a scan

`viewer.html` is a local page — open it directly in a browser (`open
viewer.html` or drag it in), then drag a `repo_graph.json` onto it (or use
its file picker). The JSON is read locally via the File API; nothing is
uploaded anywhere. Two tabs: **Files** (browse every file's extracted
symbols, and its resolved imports/imported-by — packages labeled and
distinguished from repo files, imported symbol names shown, both clickable)
and **Graph** — a 3D force-directed graph via
[`3d-force-graph`](https://github.com/vasturiano/3d-force-graph) (loaded
from a CDN — needs internet the first time; the Files tab works fully
offline regardless), configured to match DGAT's own `ui.html` directly:
near-black background, nodes colored by top-level directory and sized by
connectivity (in-degree weighted over out-degree, not code volume), the same
purple/lavender link palette with animated directional flow particles, the
same physics tuning and size-aware camera auto-fit, and a spin toggle.
Hover a node or edge to highlight its connections (DGAT's own file
references this via a `__hovered` flag that nothing there ever sets — wired
up for real here), click a node to jump to its file detail.

Initial filtering also compares existing Python source files with the standard
library AST parser, without importing or executing historical code. It excludes
changes whose source edits consist entirely of comments, formatting, docstrings,
or literal version values in dedicated version modules. Each decision retains
per-file evidence. Unsupported languages, unparseable syntax, added/deleted or
renamed files, and files over the static-analysis budget remain candidates with
an `unknown` finding. This is a screening heuristic: docstrings and version
values can affect runtime introspection. Source line counts still measure the
whole source diff, including accompanying comments and docstrings.

Configuration-only fixes (including real packaging fixes) are outside this
pilot's source-focused scope. Passing this filter does not establish that a
change is a bug fix, a complete PR, or a useful training problem.

### Deterministic Python runtime selection (pilot)

`fleet.history.python_runtime.resolve_snapshot(repo, ref)` reads committed
configuration without executing repository code. V1 uses a literal root
`.python-version` pin, otherwise the newest CPython minor in a supported tox
`envlist` / `env_list` (including native `tool.tox.env_list`). It checks literal
`requires-python` / `python_requires` constraints from package metadata.
Requirements files and PyPI classifiers do not select the interpreter.

The result records declarations and their paths, eligible versions and the
selected version. `resolved_from_declarations` is not an execution verdict.
Missing evidence, conflicts, dynamic metadata, interpreter overrides and
patch-level constraints without an exact pin require review. V1 does not parse
CI-only runtime declarations, Dockerfile stages, Poetry constraints or nested
monorepo configurations. Its explicit runtime catalogue is Python 3.7–3.14;
unsupported versions are not substituted automatically.

`resolve_pair(repo, base, fixed)` exposes common versions and newly declared
versions, flagging changes in eligible runtimes for review. A compatibility PR
may need both a historical-health environment and a separate target-runtime
before/after comparison. Image digests and actual resolved dependency versions
must be recorded by execution, since a minor version declaration is not a lock.

The focused experiment and logs are in `graph_audit/runtime_resolution/`.

### Runtime-capable candidate investigation (v2 pilot)

`fleet.runtime_review.RuntimeInvestigator` provides the candidate reviewer with
bounded `run_runtime_check` execution. The controller supplies prepared immutable
base/fixed snapshots and a pinned dependency image. The agent supplies a hypothesis,
a complete pytest probe and/or `testing/` selectors, and whether to repeat. Every
experiment tests both snapshots with the same probe; selected upstream tests use
the fixed test tree on both. Results, test-level transitions, source-integrity checks,
logs and authored tests are persisted and returned to the agent for follow-up.

The Pluggy integration is `graph_audit/runtime_agent_v2/run.py`, using the existing
OpenRouter reviewer and prepared v1 environments. It runs two targeted candidates,
not the whole historical corpus. There are at most two agents/containers, four
experiments per candidate, ten model calls per candidate, 90 seconds per container,
and a $0.20 estimated API ceiling. Containers have no network, credentials or Docker
socket and cannot change host repository snapshots. Model-selected probes execute
inside containers only; environment installation remains controller-managed.

F2P is **not the only useful task category**. Newly added tests passing on both
snapshots are recorded separately as potential coverage work. Meaningful refactors,
performance and build/type contracts may be retained with appropriate evidence;
absence of F2P alone is not grounds for rejection. Bug-fix certification still needs
a relevant distinguishing regression; coverage and preservation evidence cannot be
relabeled as a demonstrated fix. Agent-authored probe outcomes remain subject to
semantic review and never set `training_ready` automatically.
