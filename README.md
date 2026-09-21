# task_repo

Working home for a repo-to-benchmark-task pipeline: point it at a real software
repository, get back tasks an AI coding agent can be evaluated (or trained) against,
with acceptance decided by measured container runs, not model opinion.

## Status

Early. Phase 1 of `fleet` — map a repo via tree-sitter/`ast`, no LLM, no
linting — is built, tested, and verified against real repos. See
[`deep_research/`](deep_research/README.md) for the research behind it.

## Where things stand

- **Prior implementation:** [`stress_stack`](../stress_stack) (sibling directory) —
  a working v1 built as a take-home assignment. Ten container-validated tasks per
  repo, Python-first with partial Go/Rust/TS-JS support. Not vendored into this
  repo; read in place.
- **Comparable release:** HuggingFace's
  [Repo2RLEnv](https://github.com/huggingface/Repo2RLEnv), cloned into
  [`deep_research/implementations/Repo2RLEnv`](deep_research/implementations/Repo2RLEnv)
  for study. Adopts an external spec (Harbor), pluggable pipelines, RL-style
  reward shaping, and Hub publishing — all things `stress_stack` doesn't do today.
- **Decided:** output targets [Harbor](https://github.com/harbor-framework/harbor)'s
  task spec, serving both benchmark and RL-training consumers; task synthesis
  is Python-first behind a language-agnostic pipeline contract. Reasoning in
  [`deep_research/notes/decisions.md`](deep_research/notes/decisions.md).
- **Open:** whether to keep `stress_stack`'s own container/runner or delegate
  execution to Harbor's runtime the way Repo2RLEnv does — see the same file.
- **Also studied:** the user's own [DGAT](https://github.com/HyperKuvid-Labs/DGAT)
  (dependency-graph + LLM-annotation tool) and AlphaStack (a code-gen agent
  that wraps DGAT) — CLI shape, tree-sitter usage, and where DGAT's own
  dependency graph turned out to have real gaps (drops external imports,
  no symbol data — AlphaStack has to patch both). See
  [`deep_research/notes/dgat.md`](deep_research/notes/dgat.md) and
  [`alphastack.md`](deep_research/notes/alphastack.md).
- **Shipped:** `fleet scan <repo>` — per-file language detection, `ast`/
  tree-sitter parsing (with correct `is_async`/`is_generator` and
  class/receiver-qualified names — a real audit against a live repo found
  both silently wrong), `.fleetignore`, Python + JS/TS import-edge
  resolution, a repo-wide symbol graph written to disk, plus
  [`fleet/viewer.html`](fleet/viewer.html) — now rendering the graph with
  **3d-force-graph**, the same library DGAT's own UI uses — to inspect one
  visually. Verified against `stress_stack`'s `glom` (Python, 569 symbols),
  `pluggy` (Python, 297), `cast` (Go, 146), and a real external TypeScript
  project (83 symbols, 4 real internal edges resolved). Full audit + fixes:
  [`deep_research/notes/decisions.md`](deep_research/notes/decisions.md#correctness-audit-against-a-real-repo-and-four-real-bugs-it-found).
  Installed globally via `uv tool install --editable fleet/` — `fleet scan
  <any-path>` now runs from anywhere, like `dgat`.
- **Restructured so languages are data.** The graph layer was one hand-written
  parser function per language; adding Java would have meant ~130 lines across
  three files. It is now a `LanguageSpec` plus a tree-sitter `.scm` query per
  language, driven by one shared engine that never branches on a language's
  name — 303 lines of per-language data over 1245 lines of machinery written
  once. **Java added as proof**, costing one spec and one 26-line query, no new
  dependency and no engine change. This reverses an earlier decision to reject
  queries; that decision's stated hazard (field patterns silently vanishing)
  was re-tested and turns out to apply *only* to `?`-optional fields, so it is
  now a lint-enforced rule rather than a reason to avoid the approach. Two bugs
  surfaced: Go type declarations were being dropped entirely (48 missing in
  `cast` alone), and `it` as a JS test prefix flagged `items`/`iterate`.
  Schema 0.2.0 adds `contain` and `inherit` edges. All 21 prior tests pass
  unmodified, plus 18 new. Reasoning:
  [`decisions.md`](deep_research/notes/decisions.md#reversing-the-previous-section-back-to-a-shared-engine-with-the-per-language-part-as-data);
  how to add a language:
  [`queries/README.md`](fleet/src/fleet/graph/queries/README.md).
- **Shipped:** `fleet mine <owner/repo> --since 60d` — the first real
  `Pipeline`, and history mining that inverts Repo2RLEnv's selection in three
  places. **Issue-first:** candidates are closed issues, and the PR that fixed
  each one is read from GitHub's own issue timeline rather than regexed out of
  PR bodies — which accepts PRs that claimed a fix and never merged (7 of them
  over 60 days of `click`) and misses links made in the UI, where no keyword
  exists anywhere. The issue body is also leak-free *by construction*: written
  before the fix existed, it cannot name the fixing commit or the grading
  tests, which is why `pr_runtime` needs eight leak-stripping regexes and
  `fleet` needs none. **A window, not a count:** `git clone --shallow-since`
  puts the graft at the window boundary (`click` at 2 months: 91 commits, 2.8s,
  versus a history of thousands), `git log --first-parent` walks integration
  points only, and `limit` demotes to a cost cap counted under its own
  rejection reason. **The base commit is `parents[0]` of the merge point**, not
  the API's `pull_request.base.sha` — that field is the base tip at last sync
  and goes stale the moment the base advances, which is invisible until a
  sandbox rejects the gold patch. Rebase/squash/merge/fast-forward are
  classified with the evidence each verdict rests on, and answer `UNKNOWN`
  rather than guess where a clone genuinely cannot tell (the reflog never
  travels with one). Switching the candidate query from the issues endpoint to
  `/search/issues` took a real run from **41 API calls and 3,262 rejections to
  6 calls and 27** — the server can filter `is:issue` and a closed-date range;
  the list endpoint can only filter on *updated* time and mixes PRs in. No
  `gh` dependency (it wasn't installed here) and no new package: ~200 lines of
  `urllib`, works unauthenticated, caches to disk because 60 requests/hour
  makes an uncached re-run unaffordable. 77 new tests — including a real
  `git rebase` rather than a fixture — and all 83 prior tests pass unmodified.
  Reasoning:
  [`decisions.md`](deep_research/notes/decisions.md#phase-2a-shipped-fleet-mine--issue-first-window-based-candidate-discovery).
- **Next:** phase 2b — validate each candidate by running the suite at the
  base commit before and after the gold patch, keeping only real
  fail-to-pass transitions; then the Harbor emitter, still a stub. After
  that, `stress_stack`'s excision pipeline as a second entry, and `invoke`
  edges — call sites are already recorded on every parsed file, but resolving
  a bare callee name without type information needs its own pass.

## Layout

```
task_repo/
├── README.md              # this file
├── deep_research/          # prior-art study: implementations, papers, our notes
│   ├── implementations/    # gitignored clones (re-clone on a fresh checkout)
│   ├── papers/              # fetched papers/notes, tracked
│   └── notes/               # our comparison + design notes, tracked
│       ├── decisions.md     # v2 architecture decisions + phase-1 report
│       ├── dgat.md          # DGAT deep-dive
│       ├── alphastack.md    # AlphaStack deep-dive
│       └── repo2rlenv.md    # Repo2RLEnv deep-dive
└── fleet/                  # the improved stack — Fleet
    ├── src/fleet/
    │   ├── graph/            # phase 1 (shipped): language detection -> parse -> graph
    │   ├── history/          # phase 2a (shipped): issue/PR mining -> candidates.json
    │   │   ├── client.py      #   GitHub REST: stdlib, token-optional, disk-cached
    │   │   ├── linkage.py     #   issue -> merged-PR ground truth from the timeline
    │   │   ├── window.py      #   --shallow-since clone + --first-parent walk
    │   │   ├── merge_style.py #   merge/squash/rebase + the true base commit
    │   │   └── discover.py    #   gate ordering + rejection accounting
    │   ├── pipelines/        # Pipeline protocol + registry (`history`)
    │   └── emitter/          # Harbor task.toml writer (stub)
    └── tests/
```
