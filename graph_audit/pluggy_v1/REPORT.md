# Pluggy pipeline v1 — completed run and audit

## Outcome

**Keep three changes in the initial candidate corpus: PRs #646, #632 and #617.** Their reference changes have repeatable paired evidence under the recorded environment. This is candidate selection, not a claim that training-ready problem statements and all required preservation checks are finished.

The run also demonstrated why the reviewer must remain evidence-driven: it correctly flagged a hidden tracing regression, but one suggested wrapper problem was not reachable through normal plugin registration.

## Exact scope

- Repository: pytest-dev/pluggy, local immutable HEAD `d23f110b240d67ee503eba0082f30cae73f3e1e3`.
- Up to 200 first-parent history points available; **100 changes measured**; candidate limit **50**.
- Original filter: **9 selected, 91 rejected**. It stopped at the 100-change window, not the 50-candidate limit.
- Full-window comparison with candidate limit 100 selected exactly the same nine. **Nothing in this window was excluded by the 50 limit.** Older history was not evaluated, so this is not a completeness claim for all Pluggy history.
- All nine passed original-base tests and fixed-snapshot tests. There were 33 completed main batch sandbox invocations, including paired repetitions for the three candidates showing F2P.
- Eleven OpenRouter reviews completed: nine selected changes and two rejected controls. Model `openai/gpt-6-luna-pro`; at most two reviewer conversations and two sandbox jobs concurrently.
- Public GitHub PR metadata was fetched; all nine selected PR merge SHAs match the mined candidates. Authoritative issue-closing events were not fetched; a closed issue plus a PR closing claim is recorded as evidence, not upgraded into verified closing linkage.

## Candidate decisions

| PR | Change | Existing paired evidence | Audited decision |
|---|---|---|---|
| #646 | Remove multiple hook implementations; deduplicate callers | 1 F2P, 125 P2P; repeated | **Include.** Clear behavior assertion. The added unregister test itself passes on both snapshots. |
| #632 | Python 3.14 parameter inspection / deferred annotations | 4 F2P, 135 P2P; repeated | **Include.** Added independent issue #629 regression also fails on base and passes on fixed twice. |
| #617 | Raw plugin distribution listing / compatibility facade | 4 raw F2P, 137 P2P; repeated | **Include as API feature.** One test demonstrates missing public API; three others fail on missing private module imports and must not be counted as independent behavioral bug reproductions. |
| #590 | Lazy version loading | 0 F2P, 124 P2P | **Hold for a regression test.** Meaningful source-only possibility; missing test changes do not justify exclusion. |
| #640 | Hook dispatch cleanup / optimization | 0 F2P, 124 P2P | **Hold for a supported behavior or performance specification.** Agent's suggested wrapper-factory test fails during registration on both versions; do not use its proposed problem statement. |
| #639 | Downstream python-lsp-server test script | 0 F2P, 124 P2P | **Hold as tooling work.** Library tests never exercise this script. Mutable upstream state and `pytest || true` make its results unsuitable as-is. |
| #616 | Raise minimum Python version / modernization | 0 F2P, 124 P2P | **Reject this reference patch for the v1 corpus.** Targeted tracing test passes on base and fails on fixed twice, despite all bundled tests passing. |
| #714 | Lint/style cleanup | 0 F2P, 141 P2P | **Exclude.** No demonstrated task-relevant behavioral fix. |
| #615 | Agent settings and guidance | 0 F2P, 124 P2P | **Exclude.** Configuration was misclassified as source; classifier corrected. |

The tables count per-test transitions from identical fixed `testing/` trees on base and fixed. Raw counts require semantic inspection: a missing private import is weaker evidence than a failing public behavior assertion. No pass-to-fail was seen in the bundled paired suites; the additional tracing probe exposed one outside that coverage.

## Did the initial filter discard useful work?

All 91 rejected diffs were preserved and audited by paths/change content:

- 76 CI/lint/dependency/workflow configuration changes.
- 6 mixed configuration, packaging or tests/configuration changes.
- 4 documentation-only changes.
- 3 test-only changes.
- 1 downstream runner rejected by the 10-source-file cap.
- 1 coverage configuration change rejected by executable-source screening.

**Two useful broader tooling tasks were found among rejects:**

1. **PR #672 — downstream runner.** A substantial Python driver, recipes and CI workflow. Rejecting solely because more than ten source-classified files changed loses meaningful developer tooling. Deletions and recipe files also contribute to this count. Recovered into a deferred tooling queue; no sandbox execution of its external downstream projects was performed.
2. **PR #669 — coverage paths and aggregation.** Correctly excluded under the deliberately source-focused runtime policy, but useful if coverage/build tooling tasks are in scope. The Python edit removes a coverage pragma, so AST equivalence says nothing about coverage-report behavior. Retained as an optional tooling task needing focused tests.

The same broad policy excludes changes such as Python-version CI expansion and build tooling migration. They are not necessarily unimportant engineering work; they are outside the chosen source-runtime task scope. No missed core library implementation fix was identified in the audited rejection window, but this review is not proof of zero false negatives.

## How well did the cheap reviewer perform?

Useful findings:

- Kept small and source-only behavioral possibilities rather than treating code size or missing test files as disqualifiers.
- Excluded agent settings and mechanical lint cleanup.
- Identified #639's unconditional suppression of downstream test failures.
- Identified a **falsey callable tracing risk in #616**. Independent public tracing checks confirmed the base calls the registered processor and the fixed version silently skips it; both outcomes repeated.
- Recovered the downstream and coverage tooling cases for further investigation.

Problems found:

- For #640, it inferred a wrapper invocation problem without checking the registration constraint requiring generator functions. A public-API probe is rejected on both versions. This is an unsupported task proposal, not a verified fix.
- For #617, it described all four raw F2P results as relevant behavior tests. Three actually stop at a new private-module import; only the public distribution API case directly supports the feature task.
- For #632, the bundled F2P results mainly concern the new warning/argument behavior, not the reported deferred-annotation bug. A separate public registration probe was required and succeeded.

**Conclusion:** useful as a conservative triage reviewer with executable follow-up checks. Do not let its importance judgment or raw F2P counts automatically certify a task. This is an eleven-case audit, not an accuracy benchmark.

## Pipeline corrections and operational issues

- Fixed `.claude/settings.json` and `.claude/settings.local.json` classification as configuration without hiding executable scripts under `.claude/`. A shadow rerun now selects **8**, dropping only #615; the original nine-candidate artifacts remain preserved.
- The initial runner used archive-copy timestamp preservation as a non-root user, which failed before tests. Preserved all 27 failed-attempt logs; corrected copying and verified one candidate before resuming. These failures were not interpreted as Pluggy failures.
- Three reviews could not finish because the prompt example used `baseline` while the batch called that evidence `base_original`; generic validation errors caused wasted calls. Their judgments were recovered unchanged by a documented evidence alias, with no further model calls. The batch now supplies that alias and the wrapper returns specific validation errors and available evidence IDs.
- One response was truncated inside tool JSON. A single bounded completion repair finished it. The wrapper now handles malformed tool JSON as a repairable observation, and prompt output is bounded more tightly.
- Existing Fleet suite and focused runner/reviewer guards were checked; test results are recorded in `verification.json`.

## Cost, environment and limits

**OpenRouter reported $0.10913158 total** (about 11 US cents), across **43 paid calls**, including one completion repair. Cost was returned for all calls. Conservative local reservations totaled $0.3300218 against the $0.50 batch ceiling. Three citation repairs were local and free. API credentials were never written to the artifacts.

Execution used Linux amd64 Docker Desktop/runc, read-only containers, non-root users, no test network, CPU/memory/process/time limits, and two workers. Python 3.14 was selected from declarations for eight candidates; #590 used Python 3.13. Dependencies were resolved once per interpreter and frozen in shared images for paired runs. **This is not historical `uv.lock` replay.** Git archives used a synthetic setuptools-scm version of 1.6.0; package-version-sensitive work needs additional packaging validation. Benchmarks, downstream projects, Windows/PyPy and the full historical CI matrix were not run.

The OpenRouter reviewers had read-only tools. The orchestrating assistant implemented and executed the three targeted audit probes after inspecting their proposals and issue evidence; autonomous model-controlled sandbox execution remains a later integration step.

## Saved v1 outputs

- `final_corpus.json`: three included candidate IDs, all audited dispositions, deferred tooling candidates and explicit training-readiness limits.
- `manifest.json`: pinned ref, limits, invocation counts and hashes of principal artifacts.
- `filtered.json`, `audit_window.json`, `filtered_after_config_fix.json`: original and corrected static screening evidence.
- `rejection_audit.json`, `rejection_audit_inputs.json`: audit of all 91 rejected changes with raw diffs.
- `sandbox_summary.json`, `sandbox/<SHA>/`: per-test paired results, frozen environment details and logs.
- `agent_summary.json`, `agent/<SHA>/`: prompts/inputs, tool observations, raw decisions, recoveries and usage.
- `issue629_probe/`, `wrapper_probe/`, `tracer_probe/`: repeated targeted evidence, including the failed reviewer proposal.

This is a saved **v1 experiment with an audit stage**, not a generic production CLI or a fully autonomous task-certification system. Keep source-file overflow and meaningful configuration work in review queues; require behavior-relevant tests and preservation checks before promoting candidates into training tasks.
