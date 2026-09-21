"""How a PR landed, and therefore which commit its patch applies against.

Pure functions over facts someone else gathered, so the rules are testable
without a clone or a network call.

Why this exists at all: Repo2RLEnv takes the base commit from the REST API's
`pull_request.base.sha` (src/repo2rlenv/github.py:64). That field is the base
*branch tip at last sync*, not the commit the work landed on — the moment the
base branch advances during review, it is stale, the gold patch fails to
apply, and the candidate dies inside the sandbox as "gold patch failed to
apply at base_commit" (pipelines/pr_runtime_validate.py:262). The first
parent of the merge point is the truth, and it costs nothing to read.

What is decidable, and what isn't
---------------------------------
Two parents is proof of a merge commit. A missing `(#N)` subject suffix is
strong evidence of a rebase, because GitHub's "Squash and merge" and "Merge"
both append that suffix while "Rebase and merge" preserves the original
commit messages verbatim. But a rebase cannot be *proven* from a clone: the
reflog is local-only and never travels with one, `author_date !=
committer_date` is also what cherry-picks and `commit --amend` produce, and
patch-id equality is evidence rather than proof. The honest answer is a
verdict plus the evidence it rests on, and `UNKNOWN` when neither is
available — never a confident guess. With a token, GraphQL's
`HeadRefForcePushedEvent` settles it, and that is a later enrichment.
"""

from __future__ import annotations

from typing import Callable

from fleet.history.models import MergePoint, MergeStyle

# A reachability test: "is this SHA an ancestor of the mainline?" Supplied by
# `window.GitRepo.contains` in production and by a set in tests.
Reachable = Callable[[str], bool]


def classify(
    point: MergePoint,
    *,
    head_sha: str | None = None,
    pr_commit_count: int | None = None,
    reachable: Reachable | None = None,
) -> tuple[MergeStyle, str]:
    """Return `(style, evidence)` for one mainline integration point.

    `head_sha` and `pr_commit_count` come from the API when available; the
    classification degrades to subject-shape evidence without them, which is
    what the git-local path runs on.
    """
    if len(point.parents) >= 2:
        # Certain. GitHub's "Create a merge commit" — parents[0] is the
        # mainline, parents[1] the PR head, both still in history.
        if head_sha and len(point.parents) >= 2 and point.parents[1].startswith(head_sha[:7]):
            return MergeStyle.MERGE_COMMIT, "two parents; parents[1] matches PR head"
        return MergeStyle.MERGE_COMMIT, "two parents"

    if not point.parents:
        # The graft boundary of a shallow clone, or a root commit. Either way
        # there is no base commit to check out beneath it.
        return MergeStyle.UNKNOWN, "no parent recorded (graft boundary or root commit)"

    # --- single parent: the branch was rewritten, ff'd, or pushed direct ---

    if head_sha:
        if point.sha.startswith(head_sha[:7]) or head_sha.startswith(point.sha[:7]):
            return MergeStyle.FAST_FORWARD, "mainline commit is the PR head itself"
        if reachable is not None and reachable(head_sha):
            # Head is in history but isn't this commit: the branch landed
            # intact, so this commit isn't where the rewrite happened.
            return MergeStyle.FAST_FORWARD, "PR head reachable from mainline"

    if point.pr_number is not None:
        # GitHub appends `(#N)` on squash and on merge commits, never on
        # "Rebase and merge" — which replays the author's original subjects
        # untouched. One parent plus the suffix is therefore squash.
        evidence = "single parent with `(#N)` subject suffix (squash adds it, rebase does not)"
        if pr_commit_count is not None and pr_commit_count > 1:
            evidence += f"; PR had {pr_commit_count} commits collapsed into one"
        return MergeStyle.SQUASH, evidence

    if pr_commit_count is not None and pr_commit_count >= 1:
        return (
            MergeStyle.REBASE,
            "single parent, no `(#N)` suffix, PR commits absent from history — replayed with new SHAs",
        )

    # No suffix and no PR facts: a rebase-merge and a direct push to the
    # mainline look identical from here. Refuse to guess.
    return MergeStyle.UNKNOWN, "single parent, no `(#N)` suffix and no PR metadata to disambiguate"


def base_commit_for(point: MergePoint) -> str:
    """The commit a patch for `point` applies against.

    `parents[0]` in every style: for a merge commit it is the mainline side,
    for a squash it is the commit the single collapsed commit sits on, and
    for a rebase group it is the parent of the earliest replayed commit (the
    caller passes that commit as `point`).
    """
    return point.base_sha


def rebase_group_base(points: list[MergePoint]) -> str:
    """The base for a run of consecutive replayed commits.

    A rebase-merge puts N commits on the mainline, so the base is the first
    parent of the *oldest* one, not of the newest. `points` is expected in
    newest-first order, the order `git log` emits.
    """
    if not points:
        return ""
    return points[-1].base_sha


def patch_ids_match(left: str | None, right: str | None) -> bool:
    """Whether two commits carry the same change, ignoring SHA and metadata.

    `git patch-id` hashes the diff alone, so a rebased or cherry-picked copy
    of a commit matches its original. The strongest rebase evidence
    obtainable from a clone — still evidence, not proof, since two commits
    can legitimately carry an identical diff.
    """
    return bool(left) and bool(right) and left == right
