# Hard gates and cheap signals on Django — second analysis (2026-11-21)

**Repository:** [django/django](https://github.com/django/django) `main` at `c67d8727`, cloned with history since 2024-11-01 (2,101 commits; a full clone failed mid-transfer). History is linear: no merge commits.
**Window:** the 400 most recent mainline changes. **No Docker, no LLM, no API calls.**
**Code:** same pipeline and script as the pytest analysis (`../initial_filter_pytest/run.py`). Totals in `summary.json`, per-change decisions in `decisions.json`.

Django was chosen as the opposite of pytest. It has no typed changelog fragments, its commit messages start with "Fixed #N --" whatever the change is, and it ships real library code in `django/test/` (the test client, runner and utilities its users import).

## Funnel

| Stage | First run | After fixes |
|---|---|---|
| Rejected: no source change | 193 | 192 |
| Rejected: comment/docstring-only source edit | 9 | 9 |
| **Kept** | **198** | **199** |

## What the first run got wrong, and the fixes

**1. Real library code dropped as "tests".** Four changes to `django/test/` were rejected as "no source change", because any folder named `test` counted as a test suite. Three are real fixes with regression tests:

- `7ab2d8035` Fixed #17631, `encode_multipart()` with same-named fields and files
- `06463d3c1` Fixed #27734, parallel test workers reusing database clones
- `2d9d61967` Fixed #37089, `ignore_warnings()` matching substrings

*Fix (`history/patch.py`):* a singular `test` or `testing` folder **nested inside a package** is source unless the file is named like a test. Root-level suites (`tests/`, `testing/`) and plural `pkg/tests/` folders stay tests. This covers `django/test/`, `numpy/testing/` and `lib/sqlalchemy/testing/` while keeping `glom/test/test_*.py` and pytest's `testing/` as tests. All four are kept after the fix; nothing else moved.

**2. Translation catalogues counted as code.** "Updated translations from Transifex" touched 108 `.po`/`.mo` files and was kept as a 108-file source change. *Fix:* `.po`, `.pot` and `.mo` are classified with prose. Three such commits are now rejected.

**3. The kind signal was wrong for features.** Django uses "Fixed #N" for features too. Of 38 changes Django itself filed as new features, the first run labelled 33 as bug fixes.

*Fix (`history/signals.py`):* versioned release notes are a project-written label. A change that edits a patch-release file (`docs/releases/6.0.1.txt`) is a bug fix, because only fixes are backported. One that edits a minor-release file (`docs/releases/6.1.txt`) is labelled a feature with low confidence, because projects such as pandas also list bug fixes in their minor-release notes.

## After the fixes

| Kind | Count | Decided by |
|---|---|---|
| bug fix | 136 | 55 patch release notes, the rest commit message |
| feature | 43 | 35 minor release notes, the rest commit message |
| unknown | 18 | nothing recognised |
| refactor | 2 | — |

90 of 199 kept changes now carry Django's own label. Against those labels the commit message alone was **right 49%, wrong 41%, silent 10%**. The message is wrong more often here than on pytest (20%), because "Fixed #N" claims a fix for features too. It remains a tiebreaker only.

## Were the rejects right?

Yes, apart from the four classifier errors above, which are fixed. The remaining rejects are documentation (118), tests only (40), CI (13) and a few config files. All nine comment/docstring-only rejections were checked by hand and touch only comments or docstrings in source.

## Still open

- **Backports are invisible in this clone.** Django backports with `[6.0.x]` commits carrying "Backport of <sha> from main" on stable branches. The single-branch clone has none, and the trailer differs from `cherry picked from commit`. Detecting them needs the stable branches and a second trailer pattern.
- **109 kept changes have no project label**, so their kind rests on the weak message.
- One repository window, two years of history, no container runs.
