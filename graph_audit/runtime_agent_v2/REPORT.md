# Runtime-capable reviewer v2

## What changed

Fail-to-pass is no longer the only category explicitly represented by the reviewer. New tests passing on both snapshots are recorded as potential coverage/preservation evidence. Meaningful coverage, refactoring, typing/build contracts and performance work may be retained with their own evidence requirements. They are not mislabeled as demonstrated bug fixes. F2P=0 alone is not an exclusion rule.

`transitions()` distinguishes newly collected tests passing on both, newly collected F2P, existing P2P, P2F, errors and unmatched tests. New IDs can reflect renames or parameterization; semantic diff review remains necessary. The existing source-focused discovery filter is unchanged: this update concerns review of mined candidates, not automatic inclusion of every test-only commit.

The reviewer now has actual `run_runtime_check` and `read_runtime_log` tools. It may author Python pytest probes, select existing tests, inspect results/logs, revise hypotheses and repeat paired runs without individual approvals. Implementation snapshots and dependency images are supplied by the controller. Tests run on BOTH versions with identical probe code; selected repository tests use the fixed test tree on both. Assertions are agent-authored and still require semantic review: runtime output alone cannot certify a training task.

## Executed pilot

Two independent reviewers used `openai/gpt-6-luna-pro`:

| Candidate | Agent-controlled runtime investigation | Result |
|---|---|---|
| Pluggy #646 | Selected the two new plugin-manager tests; repeated base/fixed execution | Duplicate-caller test fails then passes; unregister test passes on BOTH. Reviewer correctly described the latter as useful preservation coverage. |
| Pluggy #590 | Wrote a fresh-process version-lookup probe, repeated it, then authored a preservation probe for public import forms | New probe fails on base and passes on fixed twice; preservation probe passes on both. Previously held source-only change now has a concrete task discriminator. |

The #590 probe instruments `importlib.metadata.version` to observe the PR's declared on-demand lookup contract while still calling the real implementation. It does not alter the package implementation. This is evidence for that contract, not a timing benchmark or proof of every packaging environment. Wheel/sdist and uninstalled-checkout behavior remain open checks.

All measured states passed the source-file integrity check. Full requests, authored test source, tool observations, pytest reports, logs and per-state outcomes are under `experiments/` and `reviews/`. The agent initiated these executions; they were not manually substituted by the orchestrator.

## Limits and operational repair

- At most two agent conversations and two simultaneous containers.
- Four experiments per candidate; each runs base/fixed, optionally repeated (up to sixteen containers per candidate).
- Ten model calls per candidate invocation; 90 seconds per container; CPU, memory and process limits.
- No network, credentials, Docker socket or writable host source mounts inside containers; no agent-selected host shell or image.
- Existing pinned Pluggy environments only. Dependency/image creation is still controller-managed; this is not yet arbitrary-repository automatic environment generation.
- No automatic `training_ready` promotion. Meaningful coverage-only work is retained separately from bug-fix evidence.

The first tool execution encountered a non-root directory timestamp-copy error before tests. Those attempts were saved under `attempt1_setup_error/`. After correcting the driver and smoke-testing both snapshots, the investigations completed. A setup-failure circuit breaker now skips useless repetitions and blocks further attempts until controller repair. This setup issue was not counted as candidate behavior.

## Cost and verification

Total reported OpenRouter cost including the setup attempt: **$0.04124446**, across **20 calls**. The same $0.20 reservation ceiling covered both attempts; reservation total was $0.1391183. No additional model runs followed the successful two-case pilot.

Ten focused runtime guard tests and six reviewer-loop tests passed. The full Fleet suite passed 330 tests with the existing Click deprecation warning.

## Files

- `SYSTEM.md`: detailed runtime-enabled persona, task categories and evidence rules.
- `summary.json`: decisions, execution counts and combined cost accounting.
- `test_addition_audit.json`: all nine earlier Pluggy candidates, showing newly collected P2P versus F2P tests.
- `run.py`: two-candidate bounded pilot using the existing environments.
- `fleet/src/fleet/runtime_review.py`: reusable runtime tool and transition analysis.

The v1 results remain preserved; this v2 evidence supports promoting #590 for further corpus review rather than silently rewriting the earlier audit.
