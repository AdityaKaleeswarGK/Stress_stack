# Candidate reviewer pilot — 2026-09-30

## Scope and execution
Implemented an independent read-only reviewer pilot, reusing the existing immutable candidate snapshots and recorded baseline evidence. It does not change the Fleet production review entry point or run additional sandbox tests. Two independent candidate conversations run concurrently at most. The requested OpenRouter model `openai/gpt-6-luna-pro` was used; no automatic model substitutions or paid retries.

The detailed SYSTEM.md rubric covers behavior, coherence, specification, regression tests, runtime evidence, environment and stability. The agent can page through complete diffs and historical base/fixed files. Outputs include an evidence-cited assessment, proposed behavior-focused problem statement, and concrete next checks. The wrapper requires the entire patch to be read and validates evidence IDs. Evidence-ID validation establishes that evidence was available, not that every model interpretation is correct.

Three cases were chosen deliberately, with expectations saved before API execution; the documentation case is a negative control from the earlier filter output and is not in the corrected 21-candidate corpus.

## Results

| Candidate | Decision | Assessment |
|---|---|---|
| 6fd41340: Path indexing boundary | ready_for_runtime_validation | Retained a meaningful small fix; identified relevant new assertions and ancillary test configuration. Explicitly said F2P/P2P are unverified. |
| d285e739: Python 3.11 compatibility | ready_for_runtime_validation | Identified the two compatibility changes, interpreted the two base failures appropriately, and requested Python 3.11 paired checks plus Python 3.10 preservation checks. Did not reject the task merely because the base failed. |
| 573cb911: docstring typo | exclude | Correctly recognized an editorial-only change despite its location in a Python source file. |

The documentation case initially cited `candidate_metadata`, which was supplied but lacked a registered citation ID. The wrapper rejected that finish call; the model corrected its citation on the next bounded call. Registering metadata as an explicit evidence ID will avoid this unnecessary repair in the next version.

All three decisions align with the pre-recorded expectations. This is a smoke test on hand-selected cases, not evidence of a general accuracy rate. It does not cover source-only fixes without tests, ambiguous specifications, flaky tests, or adversarial repository instructions.

## Cost and verification

- 9 paid model calls, 3 per candidate; 12-call maximum.
- OpenRouter-reported total cost: **$0.01349621** (about 1.35 US cents), returned for every call.
- 169,645 reported input tokens and 8,994 completion tokens across repeated contexts. Caching/pricing affects the billed total; use returned cost rather than multiplying token totals by uncached prices.
- Conservative request reservation total: $0.0386019 against the local $0.05 ceiling, with provider price limits. This is a local estimate, not an account-wide billing cap.
- Five local guard tests passed: no corpus-ready decision, complete-patch requirement, observed evidence IDs, budget refusal, paging coverage and truthful baseline scope.
- Credentials are loaded in memory from the existing Stress_stack user configuration (or environment), sent only in OpenRouter authorization headers, and not written into pilot artifacts.

## Findings and next implementation step

The cheap model made sensible triage decisions in these three cases. Keep deterministic runtime certification separate from agent recommendations. The agent's compatibility review describes the Python 3.10 base run as “preservation evidence”; this establishes old-runtime base health only, not pass-to-pass preservation after the fix. The model also proposes running new regression tests on base without always explicitly repeating the transplant step in its next-check wording. The runner must enforce applying the identical regression tests to both snapshots; prose must never define certification.

The pilot records runtime requests but intentionally has no execution tool. Next integrate a bounded sandbox tool that runs approved recipes, uses identical regression tests on base and fixed, records per-test F2P/P2P/pass-to-fail plus collection/setup errors, and returns measurements to the reviewer. For compatibility tasks use the same dependencies within each interpreter pair and distinguish the target-version checks from preservation checks. Only that deterministic gate may mark a candidate corpus-ready.

To reduce future context usage, offer symbol/search-oriented historical reads and compact evidence first; the pilot sometimes read several irrelevant file windows before locating the target. Do not increase the batch size before adding meaningful source-only and ambiguous-task controls.

## Artifacts

- SYSTEM.md: reusable detailed persona and rubric.
- pilot.py: executable two-worker experiment with bounded spend/calls and read-only tools.
- test_pilot.py: offline guard tests.
- expectations.json: pre-run qualitative expectations.
- model.json: requested model pricing/support snapshot.
- summary.json: combined decisions, usage and reported cost.
- results/<SHA>/input.json and result.json: candidate inputs, tool actions/observations, usage and final assessment. Provider private reasoning is not persisted.

The runner refuses to overwrite existing results to prevent accidental paid reruns. No further paid tests were run after these three cases.
