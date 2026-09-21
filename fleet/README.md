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
