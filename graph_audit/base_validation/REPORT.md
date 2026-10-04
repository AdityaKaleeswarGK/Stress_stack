# Base-only sandbox validation

Completed all 21 candidates with two queued workers. No reference patches or new regression tests were applied.

## Results

- 21/21 environments built and test suites executed.
- 20 bases passed every collected test; one base had two assertion failures.
- Aggregate test executions: {'pass': 4044, 'fail': 2, 'error': 0, 'skip': 0}. Counts span different historical snapshots, not unique tests.
- Runtime: Docker Desktop Linux VM, runc, linux/amd64 emulation on an ARM Mac. gVisor/Firecracker were not available.
- Tests: python -m pytest glom/test -ra -p no:cacheprovider, with JUnit reports. This covers that test directory, not every CI job, doctest or Python-version matrix.
- Each test container had no network, non-root user, dropped capabilities, no-new-privileges, read-only root, temporary scratch space, 2 GiB memory, one CPU and a process limit.
- Historical requirements were installed unchanged. Python 3.7 was selected for pytest 4.1.1 histories, 3.9 for remaining PyYAML 5.4.1 histories, and 3.11 for the others. These are pilot environment choices, not proof of exact original CI reproduction.
- Some historical dependency constraints were unpinned. Resolved dependencies and image digests were saved per candidate; rebuilding later must reuse those artifacts or freeze those versions.
- Confirmed all runs imported /workspace/glom/__init__.py. Original checkout status remained unchanged.

## Failed baseline: d285e7399c / PR #271

Subject: Handle Python 3.11. Base run on Python 3.11: 192 passed, 2 failed, no collection errors.

- test_regular_error_stack: traceback formatting mismatch involving Python 3.11 caret lines.
- test_long_target_repr: exception formatting failed.

These failures are consistent with the PR addressing Python 3.11 compatibility. They are not evidence of sandbox failure, and this candidate should not be discarded merely because its base is not all-green. The future paired run should check whether the reference fix resolves these failures while preserving the other 192 tests. That has not been executed.

## Scope of conclusion

Base execution works for this batch. Passing bases are not yet validated training tasks: fail-to-pass, fixed-state pass-to-pass and repeatability checks remain. This pilot runs once per base and is not an agentic setup system.

| Candidate | Python | Passed | Failed | Status | Build + test seconds |
|---|---|---:|---:|---|---:|
| 6fd41340f3 | python:3.11-slim | 202 | 0 | base_healthy | 49.58 |
| e515fb33c7 | python:3.11-slim | 200 | 0 | base_healthy | 48.09 |
| 64141ba479 | python:3.11-slim | 200 | 0 | base_healthy | 47.34 |
| bc653da9af | python:3.11-slim | 200 | 0 | base_healthy | 42.76 |
| 24c21dcc44 | python:3.11-slim | 199 | 0 | base_healthy | 37.64 |
| 1336a7c6ba | python:3.11-slim | 196 | 0 | base_healthy | 40.48 |
| 9082ab6f98 | python:3.11-slim | 195 | 0 | base_healthy | 30.47 |
| d285e7399c | python:3.11-slim | 192 | 2 | base_tests_failed | 30.55 |
| 34315014fe | python:3.9-slim | 194 | 0 | base_healthy | 36.86 |
| c97b71a1e4 | python:3.9-slim | 194 | 0 | base_healthy | 39.89 |
| ec19f8b29e | python:3.9-slim | 194 | 0 | base_healthy | 36.8 |
| 13432c2c03 | python:3.9-slim | 194 | 0 | base_healthy | 38.08 |
| de604f59bf | python:3.9-slim | 194 | 0 | base_healthy | 38.87 |
| 71a82580e9 | python:3.9-slim | 194 | 0 | base_healthy | 40.11 |
| 85a7a3a4a8 | python:3.9-slim | 194 | 0 | base_healthy | 39.59 |
| db6fee719c | python:3.7-slim | 194 | 0 | base_healthy | 37.19 |
| 94b63752c6 | python:3.7-slim | 193 | 0 | base_healthy | 35.07 |
| 24cc25ff95 | python:3.7-slim | 193 | 0 | base_healthy | 30.92 |
| 032f252873 | python:3.7-slim | 189 | 0 | base_healthy | 30.79 |
| c31c91e96c | python:3.7-slim | 167 | 0 | base_healthy | 31.53 |
| 55bbcd5d8f | python:3.7-slim | 166 | 0 | base_healthy | 30.76 |

## Artifacts

results/summary.json contains all outcomes. Each candidate directory contains result.json, Dockerfile, build.log, run.log, source snapshot and evidence/{junit.xml,dependencies.txt}. Image pull logs are stored at results/pull-*.log. Setup time and image downloads are not included in the per-candidate durations above.
