# Hard gates and cheap signals on pytest — first analysis (2026-11-21)

**Repository:** [pytest-dev/pytest](https://github.com/pytest-dev/pytest) at `9efd0121a` (2026-11-19), full clone, 17,927 commits, 7,874 on the mainline.
**Window:** the 400 most recent mainline changes. **No Docker, no LLM, no API calls.**
**Code:** `fleet` hard gates (`history/commits.py`, `history/content.py`) with the 10-file cap removed, plus the new `history/signals.py`. Run with `run.py`; per-change decisions in `decisions.json`, totals in `summary.json`.

pytest was chosen because it is large, keeps its tests in `testing/` rather than `tests/`, and — the important part — every PR adds a towncrier fragment (`changelog/NNNN.bugfix.rst`, `.feature.rst`, ...). The maintainers' own label is therefore a ground truth to grade the other signals against.

## Funnel

| Stage | Count |
|---|---|
| Mainline changes inspected | 400 |
| Rejected: no source change | 203 |
| Rejected: docstring-only source change | 2 |
| **Kept** | **195** |

Gates took 66 s and signals 11 s on a laptop.

## Were the rejects right? Yes, all 205.

No rejected change touched a Python file outside `testing/` or `doc/`. What was rejected:

| What | Count |
|---|---|
| Dependency bumps (dependabot, `build(deps)`) | 66 |
| Documentation only | 61 + 29 with changelog/root files |
| pre-commit bot updates touching only config | 26 |
| Tests only (new regression tests, flaky-test fixes) | 9 |
| CI / packaging / root config | ~10 |
| Docstring-only source edits (AST screen) | 2 |

Six rejects carry a `bugfix` changelog fragment but no source change: five are release cherry-pick merges that only move changelog text, and one fixes documentation typos. All correctly rejected.

Two categories are rejected *by scope*, not by mistake, and are worth remembering:

- **Configuration-only real fixes.** `420cf37a3` raised the pluggy version floor to fix #9236. A genuine bug fix, outside the source-only pilot.
- **Test-only commits** such as `f1211ba21` ("Add regression test for #2568"). Not fail-to-pass tasks, but natural *test-writing* tasks (SWT-bench style) for later.

## Were the keeps right? Mostly, with three leaks.

Of 195 kept, 159 changed tests and 36 did not. Kinds:

| Kind | Count | Decided by |
|---|---|---|
| bug fix | 93 | mostly changelog fragment |
| feature | 56 | mostly changelog fragment |
| unknown | 27 | nothing recognised |
| refactor | 15 | commit message (`[refactor]` prefix) |
| other (doc/trivial/misc fragment) | 4 | changelog fragment |

By size: 136 single-file, 49 two-to-five files, 10 six-to-twenty, none above 20. Removing the 10-file cap changed exactly one decision in this window (a 15-file internal refactor).

**Leak 1 — tool-generated rewrites pass the gates.** At least seven kept changes are `[pre-commit.ci]` autoupdates or ruff autofix sweeps (`e84e063e7`, `c9c19f8e6`, `27601124f`, `c23393a20`, `2ca0f8ad7`, `efa117ad4`, `d89ac92ef`). They change the syntax tree, so the AST screen keeps them, and the "same edit in 3+ files" mechanical detector caught none of them because each autofix edit differs. The reliable signal is the **author**, not the message: git records `pre-commit-ci[bot]` exactly.

**Leak 2 — changes with no observable behaviour.** Type-annotation fixes (`fd90f701b`, labelled `bugfix` by the project), an f-string swap (`0b21993de`), a contributor's name change in source (`082f267b3`), dead-code removal (`7d90c44cc`). None can produce a fail-to-pass test. Each would cost a full container validation to reject.

**Leak 3 — "unknown" is mostly a message-reading bug, not missing information.** 21 of the 27 unknowns are `Merge pull request #N from user/branch` commits. Their subject line says nothing, but the **PR title is on the next line of the same commit** (for example `6665a79fb` → "ENH: allow suspicious-looking reasons in skip …"), and the branch name is often descriptive (`rm-dead-code`, `fix/iter-markers-mro-close`). The signal only reads the subject line.

## How good is the commit message as a kind signal?

Against the project's own changelog label, on the 177 changes that have one:

| Message said | Count | Share |
|---|---|---|
| The same kind as the changelog | 71 | 40% |
| Nothing recognisable | 79 | 45% |
| A different kind | 27 | 15% |

The most common error: 17 changes the project filed as *features* have messages that read as fixes. This matches the literature (about 44% of commit messages lack the *what* or *why*): the message is a weak tiebreaker, never a gate.

**Backports are a strong but rare bug-fix signal.** 24 kept changes were cherry-picked to a release branch; 23 of them are bug fixes by the project's label.

## Conclusions

1. The hard gates are sound on this repository: no false rejects found in 205.
2. Most of the remaining waste is changes that pass the gates but have no testable behaviour (bots, annotations, renames, dead code). These are cheap to detect deterministically.
3. The kind signal works when the project labels its own changes (75% of kept changes here) and is weak otherwise. Repos without changelog fragments will lean on the message, which was right only 40% of the time here.

## Suggested next fixes (not yet made)

| Fix | Type | Catches |
|---|---|---|
| Read the PR title from merge-commit bodies, and the branch name | signal | most of the 27 unknowns |
| Label bot-authored commits (`[bot]` author, `pre-commit.ci`) | label, demote | leak 1 |
| AST mask for annotation-only and import-only edits | label, demote (annotations can matter at runtime) | part of leak 2 |
| "Removal" signal: source mostly deletions | signal | dead-code and deprecation-removal changes |
| Test the same gates on a repository **without** changelog fragments | measurement | how weak the signals are in the common case |

## Limits of this analysis

- One repository, one 400-change window, mostly 2025–2026 history.
- The changelog fragment is the project's label, not verified truth; for example, pytest files annotation fixes as bug fixes.
- No change was run in a container, so "would produce a fail-to-pass test" is judged by reading, not measured.

## Addendum — gate 6 type-comment bug, fixed (2026-11-21)

The comments/docstrings screen (`history/content.py`) parsed source with `ast.parse(..., type_comments=True)`. That turns every `# type: ignore` comment into a tree node carrying its **line number**. Two kinds of non-code edit therefore looked like code changes:

- deleting a `# type: ignore[...]` comment (three pre-commit.ci commits: `e84e063e7`, `c9c19f8e6`, `c23393a20`);
- adding or removing docstring lines anywhere *above* such a comment, which shifts its line number (`8d40684d1`, `ec12ff382`, `abdb2b601`, all docstring edits to `hookspec.py`).

The screen now parses without type comments. Re-running the same 400-change window: kept went from **195 to 189**, docstring/comment-only rejections from 2 to 8, and no change moved the other way. All six newly rejected changes were checked by hand and touch only comments or docstrings in source. Regression tests were added to `tests/test_history_content.py`.

The other four bot/autofix commits listed under Leak 1 genuinely change code and are still kept; catching them needs the separate author and branch-name check.
