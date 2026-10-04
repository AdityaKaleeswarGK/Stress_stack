# Glom initial-filter audit

Pinned history: `30b477ab65560914a38f331614947d0894701044`. No historical code, tests, sandbox or agent was executed. Fleet regression tests: 309 passed (one existing Click warning).

## Findings

- Independently checked raw Git additions/deletions, file paths, first-parent bases, uniqueness and history-window membership for all 47 original changes and all 100 corrected changes.
- Original 25: 9 retained; 8 version-only, 4 config-only, and 4 comment/docstring-only source changes removed.
- Corrected run: 21 selected, 79 rejected; stopped at 100 measured changes, within the 200-commit window. The limit of 25 is a ceiling, not a quota.
- Corrected exclusions: 51 without source changes, 20 version-only, 8 without executable Python syntax changes.
- Fixed config recognition for MANIFEST.in, coverage settings, Read the Docs, Travis and requirements variants. Python AST comparison catches prose inside source files and dedicated version-only edits.

## Original 25, individually reviewed

| Commit | Subject | Corrected decision |
|---|---|---|
| `6fd41340f3` | fix Path.__getitem__ off-by-one (GH-299), add test extra to setup.py | Retain for later investigation |
| `e515fb33c7` | Keep PathAccessError printable for Scope/S paths (#249) (#298) | Retain for later investigation |
| `64141ba479` | Docs refresh: fix dead embeds, stale content, add glompad links, remove backward-compat exports | Retain for later investigation |
| `c1b2d38754` | bumping version for v25.12.1dev | version_only_source_change |
| `59563e89bd` | bump version for v25.12.0 release | version_only_source_change |
| `9716620ce5` | Add Python 3.13 and 3.14 tests and fixes (#296) | no_source_change |
| `533a1025b6` | add security.md to manifest | no_source_change |
| `b13e704284` | assignment warning | no_executable_source_change |
| `920c13c4a8` | bump version for v24.11.1dev | version_only_source_change |
| `b23c4bcd39` | bump version for v24.11.0 release | version_only_source_change |
| `bc653da9af` | Add Python 3.12 (#285) | Retain for later investigation |
| `24c21dcc44` | Add --scalar flag to CLI (#280) | Retain for later investigation |
| `5cef40707d` | fix packaging tox job | no_source_change |
| `2e0c552078` | Upgrade requirements (#279) | no_executable_source_change |
| `50ec6c742d` | bump version for v23.5.1dev | version_only_source_change |
| `e13258ae91` | bump version for v23.5.0 release | version_only_source_change |
| `cbd7e8de4d` | mark a couple cli import failure lines as uncovered | no_executable_source_change |
| `1336a7c6ba` | a few cli docs and message updates | Retain for later investigation |
| `9082ab6f98` | Add support for TOML files to the CLI. (#277) | Retain for later investigation |
| `573cb911ae` | Fix typo in `core.py` (#275) | no_executable_source_change |
| `74bcba58d5` | bump version for v23.4.1 dev | version_only_source_change |
| `6b6fd93db8` | bump version for v23.4.0 release | version_only_source_change |
| `a971ff5b2e` | exclude rtd yaml from sdist | no_source_change |
| `d285e7399c` | Handle Python 3.11 (#271) | Retain for later investigation |
| `34315014fe` | bump yaml and switch to safe load (#262) | Retain for later investigation |

## Remaining scope limits

- These are mainline changes, not verified merged PRs. The original 25 have 9 PR-number hints and 16 without one. PR grouping and confirmed issue closure are still unverified.
- Real source changes include features, API removals, compatibility work, refactoring and performance work. Static eligibility does not establish a good problem statement, fail-to-pass tests, or absence of regressions.
- Configuration-only exclusion deliberately loses real installation fixes, such as PR #263 (setup.py imp-to-importlib migration).
- AST screening is Python-specific and heuristic. Docstrings/version values may affect introspection; unsupported syntax/languages and unavailable file comparisons are retained as unknown. Path classification is still convention-based.
- Source line totals include accompanying comments and docstrings. They are not counts of behavioral changes.

## Reproduce mechanical verification

```sh
fleet/.venv/bin/python graph_audit/initial_filter/verify.py graph_audit/initial_filter/original.json graph_audit/initial_filter/corrected.json
```
