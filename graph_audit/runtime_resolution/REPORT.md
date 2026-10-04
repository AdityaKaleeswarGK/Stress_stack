# Deterministic Python runtime resolver: experiment

## Logic implemented

Read immutable Git snapshots; do not checkout or execute repository code to inspect metadata. Prefer a literal root .python-version pin; otherwise choose the newest CPython minor explicitly declared in supported tox configuration, restricted to the v1 catalogue (3.7–3.14). Apply literal package Python constraints. Record evidence, alternatives, selected runtime and reasons for deferral. Compare base/fixed declarations and flag runtime changes.

Supported: tox.ini envlist/env_list including brace expansion; native pyproject tool.tox.env_list; requires-python; setup.cfg python_requires; literal setup.py python_requires parsed via AST. Ignore specialized tox factors, PyPy and free-threaded runtimes. Dynamic metadata, conflicting pins, interpreter overrides and unresolved patch-level requirements defer to review. requirements.txt and classifiers do not select Python.

V1 intentionally does not resolve CI-only configurations, Dockerfile stages, nested monorepos, Poetry syntax, complex tox inheritance or dynamically assembled setup metadata. It is a bounded prototype, not a universal environment generator. A declaration proves configured intent, not successful CI execution.

## Static experiment

All 21 glom candidates had root tox declarations and produced a base selection. Both base and fixed snapshots were inspected. Choices: {'3.14': 3, '3.11': 4, '3.10': 8, '3.9': 4, '3.7': 2}.

PR #271: base b56250387e7398b04457bb1e48edcce47f914998 declares Python 3.7–3.10. Fixed d285e7399c4586486cfd1adb203163b5d5bb65e7 adds 3.11. Resolver chooses 3.10 for historical base health and flags 3.11 as a newly declared target; no title matching or test outcomes were used to select these versions.

Click HEAD 150d1071d69c5cdad7de78590013ffe56cf9e3bb and Pluggy HEAD d23f110b240d67ee503eba0082f30cae73f3e1e3 both select 3.14 from declared tox configurations, consistent with their >=3.10 package constraints. Pluggy was statically inspected only.

## Sandbox verification

- Unchanged glom PR #271 base, Python 3.10: **194 passed, zero failed**. Prior Python 3.11 run of that same base had 192 passed and two failed. This supports the runtime compatibility diagnosis. The historical requirements have unpinned dependencies, so this is not a controlled interpreter-only experiment with identical dependency versions.
- Click, Python 3.14: first run **1,945 passed, 24 failed, 24 skipped, one xfailed**; all failures were pager tests using `less`.
- Rebuilt the Click environment with the `less` OS utility, keeping the source and Python selection unchanged: **1,969 passed, zero failed, 24 skipped, one xfailed**. The two pip-freeze snapshots are identical. Both attempts and logs are preserved.
- Click's default pytest configuration deselected 31,000 stress cases. Neither run is a full CI matrix or stress-suite validation.

Execution used fresh restricted Docker Desktop containers, linux/amd64 emulation, two initial workers, no test networking, non-root runtime, dropped capabilities, read-only root, and resource/time limits. Installation used network access within image builds. Minor image tags were resolved to recorded image digests. Tests ran once per attempt.

The Click recipe was a deliberately small smoke recipe (`pip install . pytest`), not the repository's locked uv/tox CI workflow. Adding less was a manual evidence-driven recipe correction, not an automatic capability of the deterministic resolver. No target application source was changed.

## Conclusions

The new logic fixes the earlier unsupported Python selection for this glom base and works on another repository with a different packaging layout. It cannot establish an entire working environment from the interpreter alone: system utilities, package/build tooling, test configuration and lockfile-aware recipes remain separate requirements. All 21 choices were statically resolved, but only the glom #271 selection and Click selection were newly executed. Do not describe the whole batch as revalidated.

## Verification and artifacts

Fleet suite: 319 tests passed, including 10 new resolver tests; one existing Click deprecation warning. git diff --check passed.

- decisions.json: per-snapshot evidence and decisions.
- execution-summary.json: initial glom/Click runs.
- execution-less-summary.json: corrected Click environment run.
- Each execution directory: Dockerfile, build/pull/run logs, result.json, source snapshot, dependency listing and JUnit report.
- fleet/src/fleet/history/python_runtime.py: reusable resolver API, not yet wired into the production validation CLI.

Next step: integrate declaration resolution into candidate preparation, then support a small set of verified install/test recipes. Preserve unresolved cases for later agent-assisted setup.
