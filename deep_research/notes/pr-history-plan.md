# PR and history mining: proposed next stage

Checked 12 September 2026 against Fleet's current implementation and upstream sources. This is a research and implementation plan; production code was not changed and no mined task was sandbox-validated in this review.

## Recommendation

Make the first deliverable a reliable candidate catalogue. Prefer merged PRs with a documented issue, reconstruct the complete change at its historical base, and let the agent assess problem quality only after cheap deterministic checks. Acceptance requires reproducible execution. Keep older candidates available; use environment compatibility to decide which to validate first.

The unit is a complete fix, usually one PR with one or more linked issues. One PR containing several commits should not accidentally become several incomplete tasks. Conversely, a PR that mixes unrelated issues should be deferred unless its task can be separated and independently validated.

## What Repo2RLEnv actually does

| Path | Current behavior |
| --- | --- |
| `commit_runtime` | Walks local Git history; `skip_merge_commits=True`, `limit=50`, `clone_depth=200` by default. |
| `pr_runtime` | Explicitly lists **merged** PRs; defaults to `limit=50` and requiring a linked issue. |
| Meaning of 50 | A configurable discovery count, not a structural compatibility threshold or 50 accepted tasks. Commit retrieval applies the count before later quality filtering. |

These facts come from the [options source](https://raw.githubusercontent.com/huggingface/Repo2RLEnv/main/src/repo2rlenv/spec/options.py), [commit pipeline](https://raw.githubusercontent.com/huggingface/Repo2RLEnv/main/src/repo2rlenv/pipelines/commit_runtime.py), and [Git log wrapper](https://raw.githubusercontent.com/huggingface/Repo2RLEnv/main/src/repo2rlenv/git_local.py). The current PR documentation contains an older options example showing 100; the implementation is the basis for the defaults above.

Its [GitHub listing implementation](https://raw.githubusercontent.com/huggingface/Repo2RLEnv/main/src/repo2rlenv/github.py) requests merged PRs, over-fetches up to `min(limit * 3, 1000)`, then filters dates and returns at most `limit`. Consequently, choosing an old date range does not guarantee that retrieval reaches it. Fleet should use resumable pagination and explicit coverage reporting rather than copy this behavior.

Skipping multi-parent commits is a commit-mining choice. It does not mean rejecting merged PRs: squash merges have one parent, while the PR pipeline handles PR-level changes. Fleet's local commentary claiming that Repo2RLEnv's merged-PR path accepts unmerged PRs should be corrected.

## What already exists in Fleet

- `pipelines/history.py` and `history/commits.py`: local history discovery, diff statistics, size gates, and `commits.json`. It correctly reports zero emitted tasks.
- `history/client.py`, `linkage.py`, and `discover.py`: issue discovery, caching, timeline parsing, and candidate selection, currently separate from the CLI's Phase 1 pipeline.
- `history/window.py` and `merge_style.py`: Git access, first-parent traversal, and preliminary integration classification.
- No sandbox acceptance stage is wired into this history pipeline.

Keep this structure. Complete and correct the existing stages before adding another framework. Also reconcile stale clone descriptions: `clone_full` currently defaults to including blobs, although several callers' descriptions still say blobless.

## Fix these assumptions first

1. **A mention is not a closing relationship.** `linkage.select_fix_pr` currently chooses a merged cross-reference by proximity to issue closure. A single unrelated mention can win. Any nonempty `closed_by_commit` then upgrades the selected PR's evidence without checking that commit belongs to it. A closing commit with no cross-reference is rejected immediately. Preserve mentions as weak candidates and resolve actual closure separately.
2. **Missing PR numbers are unresolved metadata, not proof of a direct push.** The Phase 1 integration gate drops unknown single-parent commits before API enrichment. This loses rebase merges and squash commits with edited messages. Keep these records in an enrichment queue. First-parent traversal alone does not group a multi-commit rebase into one PR.
3. **Tiny changes can be meaningful.** Both `CommitFilter` and `SelectionPolicy` default to three source lines. A one-line replacement counts as two lines and is rejected, even if a strong regression test exists. Record the size and make the minimum configurable, but default to allowing nonempty behavioral changes. Treat size as scope, not proven difficulty.
4. **Issue text is not automatically free of solution details.** `models.Issue` currently claims the body cannot contain future fix information. Bodies are editable and can already include a proposed fix. Store creation, update, retrieval times and available edit provenance; review solution leakage instead of asserting temporal safety.
5. **Source and test changes can share a file.** File-path classification is a useful initial statistic, but Rust inline `#[cfg(test)]` modules require hunk/entity-level separation for the hidden test patch. Fixtures and snapshots also matter. A modified test or new parameterized case may establish F2P without a new test function.

The six existing history test modules passed in this review. Additional in-memory probes demonstrated that a mention-only PR merged one minute after manual closure is accepted; an unrelated closing SHA strengthens its evidence; and a closing SHA alone returns `no_merged_pr`. Those probes establish gaps in the current rules, not measured precision on real repositories.

## Resolve issue–PR links using evidence

Use both directions of GitHub's structured linkage:

- From a PR, request `closingIssuesReferences` with manually linked issues included. GitHub describes this as issues the PR **may** close, so check merged state and closure evidence too. [Pull request GraphQL reference](https://docs.github.com/en/graphql/reference/pulls#pullrequest).
- From an issue, request `closedByPullRequestsReferences(includeClosedPrs: true)` and relevant `ClosedEvent.closer` timeline entries. Include reopened events when identifying the applicable closure. A closer may be a commit or PR; it can also be absent or another object. [Issue GraphQL reference](https://docs.github.com/en/graphql/reference/issues).
- For a closing commit, query `GET /repos/{owner}/{repo}/commits/{sha}/pulls`, verify merged state, repository and historical reachability, and connect the returned PR to that specific SHA. [Commit-associated PR API](https://docs.github.com/en/rest/commits/commits#list-pull-requests-associated-with-a-commit).
- Keep `Fixes #N` and ordinary cross-references as fallback signals with their original evidence. Closing keywords depend on the target branch; a commit message can close an issue without the containing PR appearing as a linked PR. [GitHub linking rules](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue).

Represent evidence as `recorded_closure`, `closing_reference`, `keyword_claim`, or `mention_only`, with source event/URL and timestamps. Missing evidence means unknown; conflicting evidence means review. Timestamps help investigate ambiguity but should not manufacture certainty. Even recorded closure is provenance, not proof that a patch solves the described behavior.

Use a PR-centered record with an `issues[]` collection and evidence per issue. Deduplicate by repository and landed fix, then group equivalent patches/backports before splitting training and evaluation data.

## Reconstruct the actual historical task

| Integration form | Base snapshot | Fixed snapshot |
| --- | --- | --- |
| Ordinary merge commit | Its first parent on the selected target branch | The merge commit |
| Squash | Parent of the landed squash commit | The squash commit |
| Rebase/fast-forward with several commits | Parent of the earliest verified landed commit in the PR group | Last verified landed commit in that group |

Resolve full SHAs and validate ancestry. A commit-message suffix is a hint, not a sufficient merge-style classifier. For rebases, combine PR commit associations, ancestry and patch correspondence; defer ambiguous or interleaved groups. Do not assume the PR's last commit represents its entire fix.

Derive the patch from the selected base and fixed trees. This keeps the patch and base consistent and includes integrated conflict resolutions. A different valid task convention could use a PR merge-base/head pair, but mixing one convention's base with another convention's patch is incorrect.

Build the agent's graph from **that base snapshot**, cache it by commit and scanner version, and supply only information available in the task workspace. The mining agent can inspect the historical fix privately; the solving agent must not receive the gold patch, future graph, future Git objects or grading tests.

## Filtering order

| Stage | Work | Output |
| --- | --- | --- |
| 1. Inventory | Enumerate merged PRs and local history; cache metadata; retain unresolved single-parent commits. | Raw records and explicit pagination coverage. |
| 2. Cheap checks | Remove empty changes and clearly out-of-scope work; measure source/test/config changes separately; group duplicate PRs and patches. | Measured candidates with reasons. |
| 3. Resolve | Attach issue evidence and exact base/fixed SHAs; reconstruct the complete patch. | Candidates whose identity can be audited. |
| 4. Prioritize | Prefer clear issue descriptions, regression tests and supported historical environments; assess layout similarity as a secondary feature. | Ranked batches for review. |
| 5. Agent review | Judge whether the issue describes a reproducible, self-contained task and whether the patch mixes unrelated work. | Selection decision and explanation. |
| 6. Execute | Provision the historical environment; validate base, test-only and gold-patched states. | Per-test evidence or a named failure. |
| 7. Publish internally | Freeze artifacts, hashes, commands, versions, logs and data-split group. | Verified task records only. |

For the first pass, defer very large patches (the existing 400 changed source lines / 10 source files can be provisional budget thresholds), ambiguous multi-PR fixes, and candidates needing synthesized regression tests. Save them for later rather than permanently discard them. A dependency or configuration change needed to reproduce the fix is part of the task; it must not disappear under a blanket “config is noise” rule.

Expose three different budgets: records retrieved, candidates reviewed, and sandbox minutes. A budget stop is `deferred`, with a resume cursor, not a quality rejection. Search-window overlap must be deduplicated; incomplete search responses or exhausted page caps must mark the catalogue incomplete.

## Use structural similarity to manage environment cost

Your folder-comparison idea is useful for prioritization, with two additions:

1. Compare relevant source and test paths to a pinned reference SHA, ignoring generated/vendor files. Start with path-set overlap and known renames; do not build full graphs across every commit.
2. Read historical runtime constraints, manifests, lockfiles, build/test commands and workspace layout. These distinguish environment changes that directory names miss.

Initially annotate each candidate with these features. After the pilot, group compatible periods of history for recipe reuse. A large structural change starts a potential new group, not a permanent cutoff. Similar files do not guarantee compatible dependencies; renamed directories do not prove incompatibility. Cache environments using exact resolved dependencies, toolchain and recipe hashes, even when recipes are shared.

Do not invent a similarity threshold yet. Compare recency-only, layout-only and environment-aware ranking against actual provisioning success and verified tasks per sandbox minute. Keep a small sample of low-ranked and deferred cases to detect useful candidates the ranking would otherwise hide.

## Sandbox acceptance

Use isolated, clean workspaces with the historical toolchain and a frozen environment specification:

1. **Base:** establish that installation and test collection work and record existing outcomes.
2. **Base + test patch:** run the regression tests and selected existing guards. At least one relevant test must fail because of the reported behavior, not a missing dependency, broken harness or unrelated collection failure.
3. **Base + test patch + gold fix:** run the same verifier. All designated F2P tests and existing regression guards must pass. Record failures outside those sets too.
4. Repeat the two comparison states in fresh workspaces to check reproducibility. Tests disappearing, skipping or failing to parse never count as passing.

For the initial evaluation-quality pool, require a nonempty P2P guard and successful selected commands. Record a separate status for candidates with unrelated pre-existing failures; do not silently drop failed tests until a task becomes green. Use the original relevant suite or broader integration checks as budget permits. Mutation checks can later assess whether tests reject plausible wrong fixes; coverage alone does not show that assertions are useful.

If new regression tests need additional test dependencies, provision an explicit harness-only overlay identically for both comparison states. Defer cases where the test/fix/environment changes cannot yet be separated reliably. Test-only changes must not expose the solution.

This follows the useful F2P/P2P principle in [Repo2RLEnv's runtime validation documentation](https://raw.githubusercontent.com/huggingface/Repo2RLEnv/main/docs/pipelines/pr_runtime.md), while making Fleet's initial acceptance criteria explicit. Successful execution proves the recorded checks, not complete behavioral correctness.

## Concrete implementation sequence

1. **Correct identity and evidence:** amend `models.py`, `linkage.py`, `client.py`, `merge_style.py`, and `discover.py`; preserve unknown integration points in `commits.py`; add targeted fixtures for false mentions, commit closures, reopened issues, edited messages and multi-commit rebases.
2. **Wire a candidate catalogue:** connect these stages through `pipelines/history.py`; emit one record per fix with issue evidence, exact SHAs, patch statistics, test availability, compatibility features, status and reasons. Consolidate duplicated filter settings. Keep `fleet mine` clearly identified as discovery until validation is implemented.
3. **Pilot discovery:** use existing glom and pluggy clones, then one TS/JS and one Rust repository. Manually audit a stratified sample of roughly 30 candidates, including old/tiny/ambiguous cases. These are experiment budgets, not history cutoffs.
4. **Add runtime validation:** start with Python and a small audited batch; then add JS/TS test adapters and Rust inline-test splitting. Emit no verified tasks until the acceptance sequence passes.
5. **Tune selection from results:** report link precision in the audited sample, unresolved merge groups, setup success, F2P yield, reproducibility and sandbox cost. Choose thresholds from these measurements; expand history and no-test candidates afterward.

The next milestone is a small catalogue whose issue, PR, base and complete patch are demonstrably correct. Agent selection will then operate on useful evidence, and the sandbox will decide which candidates become tasks.
