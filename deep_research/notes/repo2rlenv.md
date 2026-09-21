# Repo2RLEnv — complete picture (intention + implementation)

Deep-dive companion to [`../README.md`](../README.md)'s comparison and
[`decisions.md`](decisions.md)'s architecture calls. Compiled from the actual
source (`pipelines/base.py`, `_env_guard.py`, `bootstrap/agent.py`,
`reward.py`), the spec (`docs/reference/SPEC.md`), all 6 per-pipeline docs, and
`docs/release_notes/HISTORY.md`, not just the top-level README.

## Intention

Their own framing (`CLAUDE.md`): **"synthesis → standardize → train + eval,
focused on training."** The clearest statement of *why* is buried in the
`cve_patches` doc, but it generalizes to the whole project: *"Lots of datasets
of CVE-fix pairs exist... What didn't exist before: a reusable **pipeline**
that turns [a repo] into tasks. Plenty of paper-only artifacts; no library —
until now."* SWE-bench, SWE-RL, R2E, Magicoder, SWE-smith, RepoLaunch are each
a one-off academic artifact — one paper, one fixed dataset. Repo2RLEnv turns
each *idea* into a re-runnable pipeline against **any** repo, and standardizes
the output into Harbor's spec so it isn't its own island.

Three layers, they own exactly one (`CLAUDE.md`):

| Layer | Who owns it |
|---|---|
| **Generation** — the 6 pipelines | Repo2RLEnv (their stated "moat") |
| **Spec** — the task format | Harbor's `task.toml` + `[metadata.repo2env]` extension |
| **Consumption** — sandboxes, agent harnesses, training loops | Harbor, entirely delegated |

## Architecture — repo to runnable dataset, end to end

```
GenerationInput (repo + pipeline + llm + output + qa + sandbox + auth)
        │           ← CLI flags or a YAML/TOML config file
        ▼
[optional] bootstrap: ReAct LLM agent iterates shell commands in a fresh
Docker container until build+test-collection succeeds → content-addressed
cache per (repo, ref)                          ← only sandbox-required pipelines
        ▼
one of 6 Pipeline implementations: discover candidates → synthesize/validate
→ QA gate → reject or emit
        ▼
_env_guard.py stamps every sandboxed task: git-history scrub to base_commit +
DNS-blackhole PyPI/GitHub in a docker-compose overlay
        ▼
emitter/harbor.py writes task.toml + instruction.md + solution/ +
environment/Dockerfile + tests/
        ▼
repo2rlenv validate  → fast structural check, no LLM/Docker required
        ▼
repo2rlenv push  → HF Hub (dataset) + registry (GHCR/ECR/GAR/Docker Hub,
auto-detected via an OCI-probe) → registry.json + manifest.json + dataset card
        ▼
anyone: repo2rlenv pull  →  harbor run -a <one of 25+ harnesses> -m <any
model> --env <docker|modal|daytona|e2b|runloop>
        ▼
reward lands in /logs/verifier/reward.{txt,json}
```

Auth resolves rather than asks: `gh auth token` for GitHub, `huggingface_hub`'s
own cache for the Hub, provider env vars via LiteLLM for the model.

## The 6 pipelines

| Pipeline | Candidate source | Core mechanism | Reward | Yield |
|---|---|---|---|---|
| `pr_diff` | merged PRs (`gh pr list`) | text-only; strips 8 leak-pattern families from title/body; **no execution gate at all** | 6-component weighted diff score (size/file-targeting/region-overlap/similarity/LLM-judge + a catastrophic-size hard cap ≤0.40), calibrated against an empty-patch baseline | 80–95% |
| `pr_runtime` (flagship) | merged PRs w/ linked issue | split diff → `patch` + `test_patch`; run `test_patch` alone pre-fix (discover F2P), then + gold patch (confirm the flip), inside the bootstrapped container | graded `f2p_rate × p2p_rate`, plus `resolved`/`command_resolved` eval booleans | 15–40% |
| `commit_runtime` | raw commits, conventional-commit filtered | same F2P harness as `pr_runtime` but walks commits directly — reaches squash-merge/no-PR repos `pr_runtime` can't; LLM rewrites commit/issue text into a clean, leak-free statement | same graded reward | 10–35%, ~0 on PR-first repos |
| `cve_patches` | OSV vulnerability DB → fix commit | reuses the `pr_runtime` F2P harness; when the fix ships no test (most CVEs), an **agentic LLM with shell access in the vulnerable sandbox writes a PoC regression test** and validates it | same graded reward | 5–25%, lowest — dominated by whether the repo's suite runs in a slim container at all |
| `code_instruct` | a sampled 30–200-line code window | one LLM call writes problem+test+solution grounded in that real file; 4 post-hoc gates (repo-anchoring, symbol-collision, test-strength, dedup) added after baseline tasks ignored the repo entirely | `test_execution` (self-contained pytest) | 60–90% after gates |
| `equivalence_tests` | a real, pure, extracted function | freezes it as `reference_<name>`; LLM writes **only the test** (ground truth already exists); candidate stubbed must FAIL, oracle-filled must PASS | `test_execution` | ~30–60%; purity/self-containment filtering dominates (click: 32→2 candidates after filtering, but trustworthy) |

Retired (v0.8.3): `mutation_bugs` (AST bug injection) and `refactor_synthesis`
(rename mining) — both binary-reward, Python-only, and judged lowest-signal:
"synthetic AST bugs are unrealistic, and renames are a near-no-op RL target."
Real editorial discipline: optimizing for training-signal quality, not
pipeline count.

## Cross-cutting mechanisms

- **Anti-contamination, reactively hardened.** `_env_guard.py`'s own docstring
  says they *watched* an agent defeat a naive fix twice: blocked from the web,
  it ran `git diff origin/main` (closed by pruning every ref past
  `base_commit` + `git gc`); when that closed, it ran
  `pip download <pkg>==<fixed>` and read the patch out of the wheel (closed by
  DNS-blackholing PyPI/GitHub in the container's compose overlay). Principle:
  **"the environment enforces, the prompt never asks."**
- **Reward is two real mechanisms**, both in `reward.py`:
  `calculate_diff_similarity_reward` (pure `difflib.SequenceMatcher` over
  normalized diff lines) and `grade_test_execution` (F2P/P2P rates →
  `f2p_rate × p2p_rate`, with SWE-bench's FULL/PARTIAL/NO resolution
  labeling). `pr_diff` layers its own richer 6-component verifier on top.
- **Bootstrap** is the one expensive, cached, budget-capped LLM agent in the
  system — a ReAct loop (`BASH`/`READ_FILE`/`LIST_DIR`/`SAVE_SETUP`/`GIVE_UP`)
  that iterates in a live Docker container until it hands back working
  `rebuild_cmds`/`test_cmds`, capped on both wall-clock and real dollars
  (`litellm` cost tracking checked every turn).
- **Publishing is real infrastructure.** `push` runs an OCI Distribution Spec
  probe against every registry with credentials in `~/.docker/config.json`,
  auto-picks one, pushes the bootstrap image, and rewrites each task's
  Dockerfile to a digest-pinned reference — a dataset pulled on a machine
  that's never seen the source repo still builds byte-identically.
- **Everything is measured against itself.** Nearly every pipeline doc ends
  with a real audit: solve rates on real agents (Sonnet/Codex/Qwen), yield
  percentages from real runs, root-caused failure modes with the fix that
  followed — e.g. `code_instruct`'s solve rate went 40%→80% once they found
  agents were failing on *file naming*, not logic, and added a
  delivery-contract line to the instruction.

## `Pipeline` protocol (actual source, `pipelines/base.py` — newer than the
`CLAUDE.md` summary)

```python
class Pipeline(Protocol):
    name: ClassVar[PipelineName]
    requires_bootstrap: ClassVar[bool] = False
    supported_languages: ClassVar[frozenset[LanguageHint] | None] = None
    experimental: ClassVar[bool] = False

    def __init__(self, input: GenerationInput, options: BaseModel, bootstrap: Any = None) -> None: ...
    def run(self, out_dir: Path) -> PipelineResult: ...
```

`check_language_compatibility()` gates a pipeline against the detected repo
language up front (raises `LanguageMismatchError` unless `--force-language`),
rather than letting a language-specific pipeline silently emit zero tasks.

## Evolution (from `docs/release_notes/HISTORY.md`)

v0.1 `pr_diff` only → v0.2 bootstrap → v0.3 `pr_runtime` + sandbox
verification → v0.4 polyglot log parsers + Harbor compliance → v0.5
`commit_runtime` (+ `pr_stream`, later removed as scope-creep) → v0.6 first
LLM-synthesis pipelines (`mutation_bugs`, `code_instruct`) → v0.7
`equivalence_tests` + `cve_patches` → v0.8.0 `refactor_synthesis` → **v0.8.3
audit: retired `mutation_bugs` + `refactor_synthesis` + `pr_stream`** → v0.8.4
GitLab + input-source abstraction, `commit_runtime` promoted stable →
anti-contamination pass (`_env_guard.py`, PR #69) → v0.8.6 `code_instruct`
repo-anchoring gates → v0.8.7 `equivalence_tests` import-safety gates → v0.8.8
docs site. In progress: `pr_to_env` (RFC 0007). Planned: `env_setup` (RFC
0008), `test_synthesis` (RFC 0009), `issue_runtime` (RFC 0010), graded
rewards for the (surviving) binary pipelines, an LLM-judged QA gate.
