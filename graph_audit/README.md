# Graph hypothesis audit

For the implemented Python/Rust import-use graph, start with
[Fleet's short guide](../fleet/GRAPH.md) and
[current validation](binding_results/SUMMARY.md). Reproduce those checks with
`fleet/.venv/bin/python graph_audit/validate_bindings.py`.
The original `results/` directory is the pre-change baseline; it is preserved.

This is a repeatable assessment of Fleet's existing graph against the proposed
agent context: files, classes, functions, documentation, dependencies, callers,
and eventual test coverage. It does not modify Fleet's scanner or the scanned
repositories, install dependencies in them, or execute their code.

Start with [REVIEW.md](REVIEW.md) for interpretation and
[results/SUMMARY.md](results/SUMMARY.md) for measurements. Fresh graphs and
diagnostics are under `results/<repository>/`.

The follow-up [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) compares DGAT and
AlphaStack, documents relevant upstream research, and specifies ordered fixes
with acceptance criteria. Small read-only implementation experiments can be
rerun with `fleet/.venv/bin/python graph_audit/research_probes.py`; their recorded
output is [research/probes.json](research/probes.json). They are not an end-to-end
benchmark of DGAT or AlphaStack.

Run from the task_repo workspace:

```sh
fleet/.venv/bin/python graph_audit/run.py
```

The default corpus is Fleet plus twelve explicitly named repositories in its
parent projects directory. This includes Python, JavaScript, TypeScript, and Go
and Rust projects. Java is covered by fixtures, not by a real Java repository.
The top-level `projects/glom` copy is distinct from `stress_stack/glom` used in
earlier conversation checks.

Use `--projects /path/to/projects --out /path/to/audit-output` to change paths.
Output directories should be outside the scanned repositories. `--strict`
returns exit code 1 when any acceptance or source-checked question fails. The
default returns 0 after successfully recording the audit even when gaps exist.
The gaps include explicitly unimplemented capabilities, so these are not added
to Fleet's regular regression suite as unconditional passing requirements.

- `cases.json`: 32 small source fixtures with hand-specified expected results.
- `real_cases.json`: 15 questions grounded in inspected source from the corpus.
- `run.py`: scans, reports structural checks, and evaluates both sets of cases.
- `results/results.json`: complete observed and expected results, runtime/parser
  versions, scanner source fingerprint, and repository measurements.
- `results/<repo>/repo_graph.json`: fresh Fleet output, loadable in the existing
  Fleet viewer.
- `results/<repo>/diagnostics.json`: import records without a resolved edge on
  their line, incoming file lists, and missing-definition examples.
- `results/<repo>/manifest.json`: commit, tracked working-tree status, scanned
  file hashes, and selected root configuration hashes. This is scan provenance,
  not a complete build/environment lock.

The Python definition inventory uses a separate recursive AST visitor to find
nested and conditional definitions. It shares Python's parser with Fleet and
therefore does not independently validate the Python grammar itself. Non-Python
definition recall is not measured across the whole corpus.

The source-checked questions inspect saved JSON, matching how a separate agent
would consume the artifact. Witness fragments are checked against the current
source and scanned file hashes before scoring. The list was deliberately chosen
to exercise relevant capabilities; its pass fraction is not general accuracy.

Old Fleet graphs lack source/configuration fingerprints. Their counts can be
compared descriptively but cannot establish precision, recall, or an improvement
on identical source. Legacy stress_stack graphs in sibling output directories
use a different schema and are not scored as Fleet graphs.

No LLM enrichment experiment, runtime coverage collection, historical checkout
execution, or solver performance benchmark is included in this audit.
