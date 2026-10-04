# Historical candidate validation: implementation comparison and plan

Date: 2026-09-29. Research and planning only; no runtime pipeline changes or mined repository execution.

## Inspected versions

- SWE-rebench V2: cloned https://github.com/SWE-rebench/SWE-rebench-V2 to `/private/tmp/stress-stack-swe-rebench-v2`, commit `c71902a8cf8d2b725f63d51f199f4d3e56f68d2d`.
- Repo2RLEnv: existing clean checkout under `deep_research/implementations/Repo2RLEnv`, commit `1a8bad5774e062d0d73a7d79f9d6a95540801c83`. Findings apply to this inspected revision, not a claim about latest upstream.
- Fleet: current working tree, particularly `fleet/src/fleet/validation.py` and initial filtering.

## Important distinction: collection versus evaluation

SWE-rebench's paper describes discovery, environment synthesis, before/after test execution and semantic quality filtering. The public repository README describes a smaller release: prompts, image builders and evaluation of already constructed task records. Its `eval.py` reads existing FAIL_TO_PASS/PASS_TO_PASS lists and applies a supplied or golden patch plus the test patch. That evaluation alone does not discover new fail-to-pass lists. We must implement candidate validation separately; cloning this release does not give us the entire reported production collection system.

Paper: https://arxiv.org/html/2602.23866v1 (sections 3.2–3.6).

## Code findings

### SWE-rebench V2

- `base_dockerfiles/`: language/runtime-specific templates; no upstream Dockerfile required.
- `prompts/installer/agent.j2`: inspect documentation, manifests and CI; discover the project root; execute installation; record successful commands and verbose full-suite commands; prefer structured reports and lockfile-aware installation.
- `combine.Dockerfile.j2`: generates an instance image, clones repository, resets to base_commit, executes recorded installation. Explicit amd64 platform. Install commands have `|| true`; successful image construction therefore does not prove every installation command succeeded.
- `scripts/build_instance_images.py`: builds per-task images in a sequential loop in this release.
- `scripts/eval.py`: fresh Docker invocation per task, ThreadPoolExecutor for parallel evaluation, reads expected test IDs and writes reports. Its runner uses host networking and subprocess.run without an explicit timeout. These are characteristics of the released script, not evidence of how the private large-scale collection deployment was configured.
- Scaling evidence: reusable language bases, prepared instance images, configurable evaluation workers and structured per-task records. Full distributed scheduler/retry/storage internals are not exposed here.

Source: https://github.com/SWE-rebench/SWE-rebench-V2/tree/c71902a8cf8d2b725f63d51f199f4d3e56f68d2d

### Repo2RLEnv

- `bootstrap/runner.py:439`: supplied Dockerfile, cached bootstrap, or agent bootstrap. Detects language, starts a sandbox, records setup, commits an image, and checks a fresh container. Keep the fresh-container replay idea.
- `bootstrap/cache.py`: repository/ref/options cache, including reconstructed Dockerfile and result metadata.
- `pipelines/pr_runtime.py:850`: requires source and test patches, applies structural filters, narrows test commands where supported, validates and emits tasks. This excludes source-only candidates we intentionally retain for investigation.
- `pipelines/pr_runtime_validate.py:193`: applies tests to base, then solution plus tests; counts FAILED/ERROR to PASSED; a nonempty F2P list is enough for this function's verified verdict. No explicit requirement here that every previously passing test stays passing or that all post-fix tests succeed.
- The same sandbox is reused across PRs. Its Git cleanup preserves dependency/build/cache directories. Git reset does not reset processes, installed packages or all generated artifacts. Prefer fresh containers in Fleet.
- A bootstrap built for one ref is not automatic proof of dependency compatibility with older bases.

Source: https://github.com/huggingface/Repo2RLEnv/tree/1a8bad5774e062d0d73a7d79f9d6a95540801c83

### Fleet today

- Already uses fresh restricted Docker containers, JUnit parsing, base health, before/after states and repeat comparisons (five executions when all stages run).
- Still requires a closed issue and ready_for_review status; initial_filter_passed records cannot be passed directly into validation.
- Requires a separate test patch; source-only candidates need a deferred test-discovery/synthesis route.
- Extracts solution changes only from source-classified files: supporting fixtures/configuration can be omitted. Needs explicit complete patch accounting.
- Requires an existing image/install recipe; no automatic bounded setup-repair service.
- Environment cache includes base SHA, so nearby compatible candidates do not share a common dependency layer through this key.
- Rejects pre-fix error outcomes. Missing functionality and broken harnesses need separate diagnostic treatment.

## Proposed contract

Use `base_sha` for the buggy starting tree and `fixed_sha` for the landed reference result. Avoid ambiguous `head`: the fixed PR head normally passes; the base with verification tests should expose the issue.

A multi-commit PR is one candidate when its complete landed correction is recoverable. Merge/squash/rebase boundaries must be resolved and provenance recorded before execution. Defer ambiguous reconstruction rather than evaluating an arbitrary range or today's default branch.

Each validation bundle contains:

- immutable candidate and repository identifiers; base/fixed trees;
- complete reference diff, solution patch, verification patch, and explicit disposition of every changed file/hunk;
- problem-description provenance, without requiring an issue when a PR adequately describes the task;
- environment recipe, architecture, dependency lock/resolution record, image digest;
- build/rebuild commands, full-suite and optional focused-test commands;
- parser version, test IDs, source import/build provenance and validation-policy version.

Some files mix solution and test support; classify hunks or defer for review. Changes to manifests need an explicit dependency-migration treatment, not silent omission. Confirm solution plus verification reconstructs the intended fixed content, accounting for any documented supplemental tests or setup overlays.

## No Dockerfile: environment preparation

1. Inspect base snapshot CI, manifests/lockfiles, tox/Makefile, runtime-version files and documentation. Existing Dockerfiles are hints; deployment images may lack test dependencies.
2. Pick a maintained language/runtime template; use the historical declared versions where available. Record platform explicitly.
3. Build a minimal recipe from documented commands. Dependency installation uses controlled network access; historical code runs only in the sandbox.
4. If setup fails, give a bounded agent the setup logs and permitted recipe edits. Suggested pilot budget: one initial recipe plus two repairs and a wall-time/cost limit. These are proposed values.
5. Replay successful setup from a clean base image; verify nonempty test discovery, correct source path and working test reporting. Do not trust an agent's success statement or Docker build success alone.
6. Freeze successful artifacts/image digest. Record necessary compatibility edits separately and apply consistently; never silently alter target behavior, suppress tests or introduce the solution during setup.

Unsupported runtimes, private dependencies and unavailable services become explicit blocked categories. Recipe changes invalidate dependent results.

## Validation states and policy

- A: original base and original tests: establish existing guards and baseline health.
- B: base plus frozen verification patch: reproduce intended failure.
- C: base plus complete solution and the same verification patch: demonstrate reference success.

Run C then B for efficient first screening; only survivors get A and fresh repeats of B/C. This ordering is proposed, not an attribution to either reference project. If partial-suite screening is used, it needs a separate full-suite comparison before a full-suite claim.

Accept at least one relevant F2P and existing P2P guards; all required B-passing tests must pass C. Reject unexplained gold failures and missing/skipped guards. Capture FAIL_TO_FAIL, PASS_TO_FAIL, skips, collection errors, missing tests, timeouts and infrastructure errors separately. A missing symbol can be a valid reproduction of an absent interface, but a missing external dependency is not equivalent. Ambiguous error transitions need review.

For source-only candidates, first try existing tests. If none reproduce the issue, defer to a separate test-authoring queue. Freeze any newly authored tests and rerun both states. Never use reference-patch textual similarity as correctness.

Two agreeing repetitions improve confidence but do not prove absence of flakiness. Changed test definitions or weakened assertions also need review; matching IDs alone do not establish preserved guard strength.

## Efficient execution and accounting

- Runtime layer -> dependency layer -> isolated candidate source/build state. Share immutable dependency layers only under a fingerprint of relevant runtime, platform, resolved dependencies, build inputs and recipe.
- Fresh writable container per state; no reuse of writable caches/processes from other candidates. Package-download caches can be shared under controlled keys; compiled candidate artifacts must not leak between states.
- Start with two test workers and one build worker, subject to measured memory/CPU needs. Deduplicate simultaneous requests for the same image build.
- Persist jobs and attempts, stage outcomes, manifests and logs; resume completed work with content-keyed caches. Repeatability attempts bypass result reuse.
- Distinguish image builds, setup-agent commands, test executions, sandbox starts, CPU time and wall time.
- With N=25 and K first-pass survivors, C/B screening costs up to 50 test executions; A and repeated B/C add 3K, at most 125 before setup/retries/extra suite runs. Current corrected batch has 21 candidates, not 25: the analogous figures are 42 + 3K, at most 105.
- Reuse baseline results only for exactly matching source, environment, test content, commands and policy. Common repository membership is insufficient.
- Enforce execution timeouts, memory/CPU/process/log limits, cleanup of owned resources and no host credentials/Docker socket in candidate containers. Infrastructure retries are bounded and do not erase failed attempts.

## Implementation order (not started)

1. Candidate preparation contract and complete patch accounting; resolve PR boundaries.
2. Historical environment recipe builder and clean replay, Python/pytest first.
3. Paired verifier with structured evidence and explicit failure taxonomy.
4. Survivor baseline/repetition gate and regression-guard integrity review.
5. Persisted queue, environment cache, resumability and resource budgets.
6. Source-only test-authoring route and later semantic problem-statement review.

Pilot on five candidates representing a normal regression, source-only correction, fixture/config support, dependency migration and complex/ambiguous PR. Measure setup time, execution time, peak memory, cache hits, yield and failure reasons before scaling the batch. No promises of time/cost savings until measured.
