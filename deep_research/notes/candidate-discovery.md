# Non-history candidate discovery: `stress_stack` vs Repo2RLEnv

How each project finds the function/snippet a non-history-mined task is built
from. Companion to [`repo2rlenv.md`](repo2rlenv.md) and
[`decisions.md`](decisions.md) — read before designing Fleet's non-history
pipeline.

## `stress_stack`: mine what's already proven

Three real artifacts, used in sequence:

1. **The graph** (`graph.py`) — every symbol as a node: path, qualified name,
   kind (function/method/class), body line range, has-docstring, is-test.
2. **The coverage map** (`coverage_map.py`) — not a static import-guess. Runs
   the repo's own suite with `coverage.py`'s **dynamic contexts** (each line
   records *which test* executed it), aggregated per-symbol into
   `covering_tests` (set of tests that ever touched this function) and
   `covered_lines`/`ratio` (fraction of the body actually exercised).
3. **Candidate discovery** (`candidates.py:mine_excision`) keeps only symbols
   where `excision_possible`: kind ∈ {function, method}, ≥1 covering test, not
   itself a test. Binary, cheap — the whole hard filter.

Ranking (`CoveredSymbol.focus_score`) is the interesting part — **not**
"more covering tests is better":

```
score = ratio × 0.35 + breadth × 0.30 + substance × 0.20 + (0.15 if docstring)
```

`breadth` is an **inverted U** over the *count* of covering tests:
- fewer than 2 → 0.25 (one test either over-constrains, coupling to the exact
  implementation, or under-constrains — a hollow stub would still pass it)
- more than a calibrated **ceiling** → 0.1 (this function is infrastructure;
  removing it causes huge collateral)
- in between → linearly interpolated, 1.0 down to 0.5

The ceiling is calibrated **per repository**: `15% of the total distinct
tests observed in the suite` (floor: `_MIN_TESTS_FOR_EXCISION + 1`), not a
fixed constant — so "infrastructure" means the same relative thing in a
40-test repo and a 4,000-test repo. Concrete case from the code comments:
glom's `_t_eval`, executed by 114 of 202 tests, is correctly excluded —
reimplementing it means rewriting the core, not answering a bounded question.

No LLM anywhere in this path. The existing, human-written test **is** the
verifier, for free. What ships later (in `validate.py`/`excision.py`) is the
empirical proof: stub the body, confirm the covering tests fail for a real
behavioral reason (not an import crash), confirm they pass again with the
body restored.

## Repo2RLEnv's two non-history pipelines: manufacture new proof

Neither has a graph or coverage map. Discovery is a cheap AST/heuristic
filter, decoupled from whether anything already tests the code:

- **`equivalence_tests`** (`_function_extractor.py`): module-level functions
  only, no async, name-pattern denylist (dunder/`test_*`/`main`/`cli`/`_*`),
  1+ args, body 5–60 LOC, must `return <expr>`, must NOT contain a
  side-effect keyword (`open(`, `subprocess.`, `requests.`, `print(`,
  `os.environ`, `input(`, `sys.exit`). That denylist *is* the purity check —
  a syntactic guess, not a measurement. Survivors are randomly sampled
  (optionally seeded). Existing coverage plays no role — the LLM writes a
  brand-new equivalence test against a frozen copy of the function, so an
  untested function is just as eligible as a heavily-tested one.
- **`code_instruct`**: looser still — a random 30–200 line window from any
  file, not reliably even function-scoped.

The LLM invents the verifier from nothing, which is exactly why both
pipelines needed heavy quality gates bolted on after shipping (repo-anchoring,
symbol-collision, test-strength, dedup, stub-must-fail/oracle-must-pass,
retry-with-feedback) — see `repo2rlenv.md`'s pipeline table for the audits
that drove each gate.

## The actual fork

| | `stress_stack` excision | Repo2RLEnv synthesis pipelines |
|---|---|---|
| Candidate pool | bounded — only functions someone already tested | unbounded — any function/snippet that parses |
| Verifier | the real, human-written covering test(s) | LLM-authored, needs heavy post-hoc gating |
| Selection signal | measured (coverage ratio + calibrated test-count sweet spot) | syntactic guess (name/LOC/side-effect denylist) |
| LLM cost | zero, ever | one+ calls per candidate, plus retries |
| Trustworthiness | high by construction | only as good as the gate stack catching bad synthesis |

## Open design question for Fleet

Not just "port excision" — decide whether to pick one philosophy globally or
combine them: keep excision as the primary (free, trustworthy) path, and fall
back to synthesis-with-gates only for functions that fail `excision_possible`
for lack of a covering test, rather than committing to one approach
repo-wide. Not decided yet — surfacing it for the architecture pass.
