# Deep research

Prior art we study before designing our own repo-to-benchmark stack. Three kinds of
thing live here:

| Folder | What goes in it | Tracked in git? |
|---|---|---|
| `implementations/` | full clones of comparable tools, read and (where practical) run | no — gitignored, re-clone on a fresh checkout (same convention Repo2RLEnv itself uses for its own `references/`) |
| `papers/` | fetched PDFs / notes on the papers behind those tools | yes |
| `notes/` | our own comparison write-ups, architecture-decision notes | yes |

This file is the index and the first comparison note. As more implementations and
papers get added, split out per-topic notes into `notes/` and keep this file as the
map.

---

## Implementations studied

### `stress_stack` — ours, v1

Not vendored here — it's a full git repo with venvs, run output, and three nested
target-repo clones (`glom`, `pluggy`, `cast`) inside it, at
`../../stress_stack` (sibling of this repo: `/Users/adityagk/Desktop/projects/stress_stack`).
Originally built as a take-home assignment ("LH2"); see its own `README.md` and
`REPORT.md` for the full account.

**What it does:** takes one repo (URL or local path), profiles its ecosystem, then
runs three pipelines — environment (hygiene → deps → container), knowledge (symbol
graph → coverage), and task generation (mine → validate → select → emit) — to
produce exactly **10 container-validated benchmark tasks**, each with `input/`,
`solution/`, `verifier/`, `evidence/`, plus a `tasks.json` manifest.

### `Repo2RLEnv` — HuggingFace, released 2026-09-08

Cloned at `implementations/Repo2RLEnv` (`git clone
https://github.com/huggingface/Repo2RLEnv.git`, Apache-2.0).

**What it does:** takes a repo (GitHub/GitLab/local) and runs one of 6 pluggable
**pipelines** (`pr_diff`, `pr_runtime`, `commit_runtime` stable; `cve_patches`,
`code_instruct`, `equivalence_tests` experimental) to emit tasks in **Harbor's**
spec — an external, adopted task format/runtime, not one it invents. Datasets push
straight to the HF Hub; Docker bootstrap images push to GHCR/Docker Hub/ECR/GAR.
Consumption (sandboxes, 25+ agent harnesses, training-stack integration) is
explicitly delegated to Harbor — Repo2RLEnv considers itself synthesis-only.

---

## Comparison

| Dimension | `stress_stack` (ours, v1) | `Repo2RLEnv` (HF) |
|---|---|---|
| Output spec | bespoke (`input/`/`solution/`/`verifier/`/`evidence/` + `task.json`) | adopts [Harbor](https://github.com/harbor-framework/harbor)'s `task.toml`, plus a `[metadata.repo2env]` extension for provenance |
| Task sources | 2, fixed in the pipeline: history-mined merged PRs (touch src+tests), excision (remove a tested function, ask for it back) | 6, pluggable behind a `Pipeline` Protocol + registry — PR diffs, PR runtime (SWE-bench-style), commit runtime, CVE patches, LLM-authored instruct, function-equivalence tests |
| Extensibility | add a source ⇒ edit the mine/validate/select/emit pipeline in place | add a source ⇒ implement the Protocol, register it, write an RFC first; a contract test (`test_pipeline_contract.py`) enforces the shape |
| Acceptance / gating | 8 measured gates, always container-run; **no model ever decides accept/reject** | reward-shaped, not just pass/fail: `test_execution` (sandboxed F2P/P2P) and/or `diff_similarity` (scored against the oracle diff, one component LLM-judged) |
| Environment setup | deterministic per-language tool table (ruff/uv, gofmt/go mod, cargo/clippy, prettier/eslint), probed against the repo; agent is only a fallback | general-purpose: an LLM ReAct agent iterates shell commands in a fresh container until build + test-collection succeed ("bootstrap"), cached content-addressed per `(repo, ref)`, spend-capped per repo |
| Determinism proof | full suite run **twice** in the container and diffed — a shipped, named invariant | bootstrap is cached/content-addressed; I did not find an explicit run-twice-and-diff gate on the *emitted task* itself — open question, not a confirmed gap |
| Anti-contamination | `.git/` excluded from the container build context entirely; `--network=none --cap-drop=ALL` at verify | named invariant (`_env_guard.py`, applied to every task): scrub git history back to `base_commit` **and** block PyPI/GitHub egress — "the environment enforces, the prompt never asks" |
| Task count | exactly 10 per repo, quota'd (≥4 history, ≤4 excision) + a diversity floor across modules | open-ended (`--pipeline-opt limit=N`), no fixed quota or diversity floor observed |
| Distribution | local `output/` only — nothing published | pushes datasets to the HF Hub (content-addressed hash), bootstrap images to a container registry, writes a Harbor-compatible `registry.json` |
| Runtime / harness integration | none — you get task folders and bring your own runner | none of its own, by design — delegates to Harbor: 25+ agent harnesses (Claude Code, OpenHands, Codex CLI, …), Local Docker/Modal/Daytona/E2B/Runloop |
| Language coverage | Python full; Go/Rust/TS-JS partial (see `stress_stack/REPORT.md` §6 for the exact matrix); C/C++ recognised and refused with a reason | Py/Go/Node/Rust for sandboxed pipelines; `pr_diff` is language-agnostic (text-only, thin image) |
| Model's role | prose (task statements) + difficulty judging only, both optional, neither gates acceptance | varies by pipeline: none at generation (`pr_runtime`/`cve_patches`), full task authoring (`code_instruct`/`equivalence_tests`), one reward component at verify time (`pr_diff`) |
| Cost tracking | not a first-class concern | every LLM call threads `cost_usd`; bootstrap enforces `max_llm_spend_usd` and short-circuits over budget |
| Design process | none formalized — single assignment deliverable | RFC-gated: every new pipeline needs a numbered RFC before implementation (10 on record: 6 shipped, 4 draft) |

## What looks genuinely worth borrowing

1. **Align with an external spec instead of inventing our own.** Adopting (or at
   least interoperating with) something like Harbor is the single biggest-leverage
   idea here — it inherits an agent-harness ecosystem, sandboxes, and training-stack
   integration for free. Doesn't have to mean full Harbor adoption; at minimum we
   should know what interoperability would cost before ruling it out.
2. **Pluggable pipeline contract + RFC gate.** Turns "add a task source" from an
   in-place pipeline edit into a registered, contract-tested plugin. Maps directly
   onto `stress_stack`'s mine/validate/select/emit, which currently hardcodes its
   two sources.
3. **Reward-shaping, not just pass/fail** — only matters if we ever want to serve RL
   training rather than pure benchmarking. Open question below.
4. **Cost-budgeted LLM bootstrap as a fallback**, for repos our deterministic
   per-language table can't cover — complementary to, not a replacement for,
   `stress_stack`'s approach where the table already applies (deterministic beats
   agentic when both work).
5. **A publishing story.** `stress_stack` has zero distribution today; even a
   minimal "push tasks + evidence somewhere shareable" makes runs comparable across
   people and time.

## What ours already does well — not obviously worth changing

1. **Determinism as a shipped, named gate** (run twice, compare). Not confirmed on
   Repo2RLEnv's emitted tasks — worth verifying rather than assuming we should copy
   anything here.
2. **Exact task-count quota + diversity floor** — good for a "benchmark" framing
   (comparable N across repos). Repo2RLEnv's open-ended `limit` suits a "dataset"
   framing better. Which framing *we* want is the open question below.
3. **Excluding `.git` from the build context entirely** is arguably simpler and
   harder to get wrong than scrub-to-base-commit (nothing to scrub correctly) —
   worth keeping regardless of what else changes.

## Open questions (ours to answer, not derivable from the code)

- Are we building a **benchmark** (fixed N, pass/fail, comparable across repos) or
  a **dataset for RL training** (open-ended, reward-shaped, published)? This decides
  almost everything else below.
- If training matters at all, do we adopt Harbor's spec, stay bespoke, or emit
  both?
- Do we keep task generation Python-only for v2, or design the pluggable-pipeline
  layer multi-language from day one?

## Reading backlog

Seeded from Repo2RLEnv's own [`docs/reference/RELATED_WORK.md`](implementations/Repo2RLEnv/docs/reference/RELATED_WORK.md)
(not yet fetched into `papers/` — this is the todo list, grouped the way they grouped it).

**Direct provenance for their pipelines:**
- SWE-RL (Wei et al., NeurIPS'25) — [arXiv:2502.18449](https://arxiv.org/abs/2502.18449) — behind `pr_diff`'s reward
- SWE-bench (Jimenez et al., ICLR'24) — [arXiv:2310.06770](https://arxiv.org/abs/2310.06770) — behind `pr_runtime`
- SWE-bench-Live (Zhang et al., NeurIPS'25) — [arXiv:2505.23419](https://arxiv.org/abs/2505.23419)
- R2E-Gym (Jain et al., COLM'25) — [arXiv:2504.07164](https://arxiv.org/abs/2504.07164) — behind `commit_runtime`
- SWE-smith (Yang et al., NeurIPS'25 Spotlight) — [arXiv:2504.21798](https://arxiv.org/abs/2504.21798)
- Magicoder / OSS-Instruct (Wei et al., ICML'24) — [arXiv:2312.02120](https://arxiv.org/abs/2312.02120) — behind `code_instruct`
- RepoLaunch (Microsoft) — [arXiv:2603.05026](https://arxiv.org/abs/2603.05026) — behind their `bootstrap/`, directly comparable to `stress_stack`'s hygiene/deps/container pipeline

**Adjacent frameworks (the "consumption" layer — where our output would plug in if we ever go that way):**
- Harbor (harbor-framework/harbor) — the spec Repo2RLEnv adopts
- NeMo Gym (NVIDIA), SkyRL (Berkeley NovaSky), verifiers (Prime Intellect), OpenEnv (Meta+HF), rLLM (Agentica)

**Automated env setup (the `bootstrap` problem, i.e. our hygiene/deps/container pipeline's problem):**
- Repo2Run — [arXiv:2502.13681](https://arxiv.org/abs/2502.13681)
- EnvBench (JetBrains, ICLR'25) — [arXiv:2503.14443](https://arxiv.org/abs/2503.14443)
- SetupBench (Microsoft) — [arXiv:2507.09063](https://arxiv.org/abs/2507.09063)

**SWE training datasets at scale:**
- SWE-Gym (Pan et al., ICML'25) — [arXiv:2412.21139](https://arxiv.org/abs/2412.21139)
- SWE-rebench (Nebius) — [arXiv:2505.20411](https://arxiv.org/abs/2505.20411) — continuous mining-to-RL, closest at-scale analogue to what `stress_stack` would need to become for "100 repos" (see its own `REPORT.md` §5)

Full list with the NVIDIA/Microsoft downstream model lines: see the source file
linked above.
