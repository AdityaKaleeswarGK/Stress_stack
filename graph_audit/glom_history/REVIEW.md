# glom history verification

Verified 12 September 2026. Scope: Fleet's revised history discovery and filtering, using a fresh public clone of `mahmoud/glom`. Fleet production code was not edited.

**Verdict:** local history measurement works on this repository and the previous false-positive linkage behavior is corrected. Candidate selection is not ready for end-to-end use: repository selection, merge-message extraction, closure enrichment and file classification need the fixes below. No task was certified by sandbox execution.

## Repository and results

Pinned glom HEAD: `fd70d3051a6a1bcb5152ec2d8e2cdc8e852f95e6` (8 September 2026).

The pre-existing stress_stack clone was older (`30b477ab65560914a38f331614947d0894701044`); it was left unchanged. The fresh audit clone is `/private/tmp/fleet-glom-history-review-20260912`.

| Check | Result |
| --- | --- |
| Existing Fleet history tests | 158 passed in 17.98 seconds |
| All commits reachable from glom HEAD | 1,050 |
| First-parent commits | 424 |
| Diffs measured | 423; the root has no parent |
| Kept | 58: 41 multi-parent merges, 17 classified as squash |
| Deferred for enrichment | 184 |
| Filtered out | 182 |
| Accounting | 58 + 184 + 182 = 424 |
| Parent, reachability and aggregate diff checks | All 242 kept/deferred records passed |
| Diverse sampled full-patch replay | 14/14 reconstructed exactly the recorded fixed tree |
| Records with test-file changes | 38 kept, 55 deferred |

Drops: 175 `no_source_change`, 4 `diff_too_large`, 1 `diff_too_small`, 1 `diff_unavailable`, 1 `base_before_graft`.

The replay used a private temporary Git index, applied the full binary-capable diff to its recorded base, then compared the resulting Git tree hash with the fixed snapshot. This establishes patch/base consistency for those samples. It does not establish source/test separation, behavioral correctness or runtime F2P/P2P.

## Findings to address before the next stage

### 1. P1 — The normal CLI command can mine the wrong repository

At [cli.py:167](/Users/adityagk/Desktop/projects/task_repo/fleet/src/fleet/cli.py:167), an omitted `--repo-path` becomes `Path.cwd()`. [HistoryPipeline._open_clone](/Users/adityagk/Desktop/projects/task_repo/fleet/src/fleet/pipelines/history.py:62) accepts that directory whenever it contains `.git`, without checking its identity against the requested repository.

Reproduction from the task_repo workspace:

```sh
fleet/.venv/bin/fleet mine mahmoud/glom --out graph_audit/glom_history/default_cli.json
```

It printed “Cloning full history” and produced **zero commits labeled `mahmoud/glom`**, because it actually read the workspace's unborn Git history. With `--repo-path` pointing to the fresh glom clone, the same command produced the 424-point result above. A nonempty unrelated checkout would silently produce more plausible but equally incorrect data.

Fix: distinguish an explicit local path from an omitted one. Clone the requested repository when no matching local checkout has been selected. Record the resolved HEAD and repository identity in the output and make the progress message reflect the actual source.

### 2. P1 — The API discovery path has no route to a confirmed candidate

The revised [resolve_fix_pr](/Users/adityagk/Desktop/projects/task_repo/fleet/src/fleet/history/linkage.py:180) now correctly refuses to equate a mention with closure. However, every return path is `UNKNOWN` or `NEEDS_REVIEW`. There is no wired enrichment that produces `RESOLVED`; [discover_via_api](/Users/adityagk/Desktop/projects/task_repo/fleet/src/fleet/history/discover.py:326) immediately maps those results to rejection counts and continues. The unresolved record and its closing-commit evidence are not retained in the discovery output.

Live focused discovery for June 2026 read three closed issues and three timelines. It accepted **zero**, reporting two `no_merged_pr` and one `link_unconfirmed`. A broader bounded run read 20 issues and spent its eight-timeline budget on eight of them; it also accepted zero. The broader run is budget-limited and is not a measurement of all glom issue linkage.

Concrete positive candidate: [PR #298](https://github.com/mahmoud/glom/pull/298) is merged, its body says `Fixes #249`, its merge SHA is `e515fb33c7af491c6a20b2618591736361bb1d08`, and [issue #249](https://github.com/mahmoud/glom/issues/249) closed immediately afterward. Its patch includes explicit regression tests. The current resolver returns `mention_only / unknown`; that is an honest assessment of the particular REST evidence it consumes, but the confirmation stage needed to advance it does not exist yet.

Fix: implement structured closing-link/closure-event enrichment and commit-to-PR resolution; preserve unconfirmed candidates with evidence and retry status. Do not restore the old timestamp shortcut. Keep cases with no confirmed fix unresolved.

### 3. P1 — Standard merge messages lose the PR number

[pr_number_from_subject](/Users/adityagk/Desktop/projects/task_repo/fleet/src/fleet/history/linkage.py:307) only accepts the trailing `(#N)` form. **38 retained merge records** have a standard `Merge pull request #N from …` subject but `pr_number: null`.

Example: [PR #196](https://github.com/mahmoud/glom/pull/196) has merge SHA `94b63752c686a42bf71baeec15e250257aee167d`. Fleet keeps that commit, but `MainlineIndex.for_pr(196)` returns `None`, so attaching an issue candidate to this known merge point fails.

Fix: extract both standard message forms, recording that message parsing is evidence rather than authoritative identity. Confirm identities against API metadata where available. Add this actual merge subject to the fixtures. Continue retaining unknown single-parent records for enrichment.

### 4. P2 — Non-code files inflate source counts and pass source-only gates

[classify_path](/Users/adityagk/Desktop/projects/task_repo/fleet/src/fleet/history/patch.py:77) defaults unrecognized files to application source.

- [PR #281](https://github.com/mahmoud/glom/pull/281) changes only `LICENSE`. Fleet keeps it as **21 source lines**.
- PR #298 has **5 changed lines in `glom/core.py`**, but its 9-line `codecov.yml` addition makes Fleet report **14 source lines**.

Fix: recognize extensionless documentation/license files and additional build/coverage configuration, and reserve an unknown category for files that have not been established as source. Configuration can be required by a real task; count and retain it separately instead of letting it satisfy the application-source gate.

## What improved

- The source-line minimum is now 1. One-line replacements survive; for example, PR #275 is retained with two changed source lines.
- Unknown single-parent changes now enter `needs_enrichment` instead of being discarded solely for missing PR metadata.
- A nearby merged mention no longer becomes a confirmed issue fix, and an unrelated closing SHA no longer strengthens that mention.
- The history pipeline still correctly reports zero emitted tasks while producing discovery metadata.

## Runtime boundary

The Docker CLI is installed, but the Docker daemon was unavailable even after checking with the needed socket access. No containers or glom test environments were run. Consequently, **there are no newly sandbox-verified tasks from this audit**.

After the four corrections, rerun this audit and use issue #249 / PR #298 as the first focused runtime candidate: establish the historical base environment, apply only the regression tests, verify meaningful failures, apply the source fix, and require F2P plus existing regression guards to pass in fresh runs.

## Reproduction and evidence

Run from `/Users/adityagk/Desktop/projects/task_repo`, with the audit clone present:

```sh
fleet/.venv/bin/fleet mine mahmoud/glom --repo-path /private/tmp/fleet-glom-history-review-20260912 --out graph_audit/glom_history/commits.json
fleet/.venv/bin/python graph_audit/glom_history/verify.py local /private/tmp/fleet-glom-history-review-20260912
fleet/.venv/bin/python graph_audit/glom_history/verify.py api /private/tmp/fleet-glom-history-review-20260912
```

The API command reads public data and caches responses. Existing cache entries are reused; use a separate cache directory for a fresh future comparison. Audit artifacts:

- [commits.json](/Users/adityagk/Desktop/projects/task_repo/graph_audit/glom_history/commits.json): complete Phase 1 output.
- [local_verification.json](/Users/adityagk/Desktop/projects/task_repo/graph_audit/glom_history/local_verification.json): measured invariants, 14 replay results, missing PR numbers and Fleet history source hashes.
- [api_discovery_focused.json](/Users/adityagk/Desktop/projects/task_repo/graph_audit/glom_history/api_discovery_focused.json): June 2026 discovery result.
- [linkage_examples.json](/Users/adityagk/Desktop/projects/task_repo/graph_audit/glom_history/linkage_examples.json): public issue/timeline data and actual resolver verdicts for #249 and #299.
- [verify.py](/Users/adityagk/Desktop/projects/task_repo/graph_audit/glom_history/verify.py): repeatable audit script.

API evidence for PRs #298, #196 and #281 is saved alongside these files. No private credentials were needed.
