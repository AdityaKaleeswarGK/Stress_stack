"""Candidate selection: a time window in, an accounted-for verdict out.

The ordering of the gates is the design. Cheap, local checks run first and
the expensive per-issue timeline call runs only on what survives them —
because on an unauthenticated budget of 60 requests/hour, one wasted
timeline call on a `wontfix` issue is 1.7% of the hour's mining.

    closed issues in window      1 call per 100 issues
      -> shape + quality gates    free
      -> budget cap               free
      -> timeline linkage         1 call per surviving issue  <- the spend
      -> merge point + base       free (local clone)

Every drop is counted under a named `Rejection`, so a thin harvest reports
*why*: "this repo doesn't link issues to PRs" and "your window was too
tight" are different problems with the same candidate count, and a bare
`skipped: 47` tells you neither.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Iterable

from fleet.history.client import GitHubClient, GitHubError, RateLimited
from fleet.history.linkage import (
    closed_issue_numbers,
    parse_timeline,
    parse_timestamp,
    rejection_for,
    resolve_fix_pr,
)
from fleet.history.merge_style import classify
from fleet.history.models import (
    Candidate,
    DiscoveryReport,
    Issue,
    LinkEvidence,
    MergedPR,
    MergePoint,
    Rejection,
)
from fleet.history.patch import PatchStats
from fleet.history.window import GitRepo

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SelectionPolicy:
    """Every threshold, in one place, with the reason it has that value.

    Deliberately few knobs. Repo2RLEnv's `pr_runtime` ships fourteen options
    and still hard-codes its most consequential gate (a PR must carry a test
    change); knobs that nobody tunes are just surface area.
    """

    # An issue whose body is a sentence fragment is not a problem statement.
    # Title-only issues ("crash on startup") give an agent nothing to act on
    # and nothing to verify against.
    min_issue_body_words: int = 12
    # `not_planned` covers wontfix, duplicate, and stale-bot closures — the
    # issue was closed *without* being solved, so there is no fix to mine.
    require_completed: bool = True
    # Bots file release chores and dependency bumps, not bug reports.
    skip_bot_authors: bool = True
    # Labels that mark an issue as something other than a defect.
    exclude_labels: frozenset[str] = frozenset(
        {"question", "duplicate", "invalid", "wontfix", "documentation", "docs", "discussion"}
    )
    # A cost cap on timeline calls, not a quality filter — applied after the
    # free gates so the budget buys plausible candidates rather than the
    # newest ones. `None` means "spend whatever the window needs".
    max_candidates: int | None = None

    # --- patch shape ---
    #
    # All three size gates judge *source* lines, not the diff total. A
    # one-line fix shipped with a 71-line test (a real `click` candidate) is
    # a one-line fix: measuring the total would let `min_loc` wave it
    # through for the wrong reason and `max_loc` throw it out for the wrong
    # reason. See patch.py.
    #
    # Nonempty, by default — see CommitFilter.min_source_loc. A floor of 3
    # rejects one-line fixes, which are the most valuable candidates when
    # they arrive with a strong regression test.
    min_source_loc: int = 1
    # Above this the PR is a refactor or a feature, not a fix: the diff stops
    # being reconstructible from the issue text, so no agent can be expected
    # to reproduce it and no reward signal means anything.
    max_source_loc: int = 400
    # A fix spread across many files is usually a rename or a sweep.
    max_source_files: int = 10
    # Test-only and docs-only diffs leave nothing to fix.
    require_source_change: bool = True
    # Off by default: a fail-to-pass oracle needs a test change, but a
    # diff-similarity task does not, and phase A shouldn't pre-judge which
    # kind of task a candidate becomes. `has_test_change` is recorded on
    # every candidate either way, so phase B can filter without re-mining.
    require_test_change: bool = False

    def issue_rejection(self, issue: Issue) -> Rejection | None:
        """The first gate `issue` fails, or None if it passes them all."""
        if self.require_completed and issue.state_reason and issue.state_reason != "completed":
            return Rejection.NOT_COMPLETED
        if self.skip_bot_authors and issue.author_is_bot:
            return Rejection.BOT_AUTHORED
        if self.exclude_labels and {label.lower() for label in issue.labels} & self.exclude_labels:
            return Rejection.LABEL_EXCLUDED
        if issue.body_words < self.min_issue_body_words:
            return Rejection.ISSUE_BODY_TOO_THIN
        return None

    def patch_rejection(self, stats: PatchStats) -> Rejection | None:
        """The first size/shape gate this patch fails, or None."""
        if self.require_source_change and not stats.has_source_change:
            return Rejection.NO_SOURCE_CHANGE
        if self.require_test_change and not stats.has_test_change:
            return Rejection.NO_TEST_CHANGE
        if len(stats.source_files) > self.max_source_files:
            return Rejection.TOO_MANY_SOURCE_FILES
        if stats.source_loc < self.min_source_loc:
            return Rejection.DIFF_TOO_SMALL
        if stats.source_loc > self.max_source_loc:
            return Rejection.DIFF_TOO_LARGE
        return None


def issue_from_payload(payload: dict[str, Any]) -> Issue:
    """Build an `Issue` from a REST issues-endpoint item."""
    user = payload.get("user") or {}
    login = user.get("login") or ""
    return Issue(
        number=payload.get("number", 0),
        title=payload.get("title") or "",
        body=payload.get("body") or "",
        closed_at=payload.get("closed_at") or "",
        state_reason=payload.get("state_reason") or "",
        url=payload.get("html_url") or "",
        author=login,
        author_is_bot=user.get("type") == "Bot" or login.endswith("[bot]"),
        labels=tuple(
            label.get("name", "") if isinstance(label, dict) else str(label)
            for label in (payload.get("labels") or [])
        ),
    )


@dataclass(slots=True)
class MainlineIndex:
    """Merge points from a local clone, indexed by the PR number they landed.

    Built once per run. A PR whose number is missing landed somewhere this
    window can't see — a release branch, or before the graft.
    """

    by_pr: dict[int, MergePoint] = field(default_factory=dict)
    points: list[MergePoint] = field(default_factory=list)

    @classmethod
    def build(cls, points: Iterable[MergePoint]) -> MainlineIndex:
        ordered = list(points)
        by_pr: dict[int, MergePoint] = {}
        for point in ordered:
            # Newest-first input; keep the earliest occurrence of a PR number
            # so a later revert-of-a-revert doesn't shadow the original.
            if point.pr_number is not None:
                by_pr[point.pr_number] = point
        return cls(by_pr=by_pr, points=ordered)

    def for_pr(self, number: int) -> MergePoint | None:
        return self.by_pr.get(number)


def attach_merge_point(
    candidate: Candidate,
    index: MainlineIndex,
    repo: GitRepo | None,
    *,
    pr_facts: dict[str, Any] | None = None,
) -> Candidate | Rejection:
    """Resolve the candidate's true base commit from the local mainline.

    The base commit is `parents[0]` of the point where the PR landed — not
    the API's `pull_request.base.sha`, which is the base branch tip at last
    sync and goes stale the moment the base advances during review. Getting
    this wrong is invisible until a sandbox rejects the gold patch.
    """
    point = index.for_pr(candidate.pr.number)
    if point is None:
        return Rejection.MERGE_POINT_NOT_FOUND

    if pr_facts:
        # API facts sharpen the verdict: the head SHA settles fast-forward
        # against rewrite, and the commit count separates a squash of many
        # commits from a single-commit PR.
        style, evidence = classify(
            point,
            head_sha=(pr_facts.get("head") or {}).get("sha"),
            pr_commit_count=pr_facts.get("commits"),
            reachable=repo.contains if repo else None,
        )
        point = replace(point, style=style, style_evidence=evidence)

    if not point.base_sha:
        return Rejection.BASE_BEFORE_GRAFT
    if repo is not None and not repo.has_object(point.base_sha):
        # The base is upstream of our graft. Recoverable by deepening, which
        # is the caller's decision to spend — we report rather than silently
        # re-fetching the world.
        return Rejection.BASE_BEFORE_GRAFT
    return replace(candidate, merge=point)


def apply_patch_gates(
    candidate: Candidate,
    repo: GitRepo,
    policy: SelectionPolicy,
    report: DiscoveryReport,
) -> Candidate | None:
    """Measure the fix and apply the size/shape gates.

    Returns the candidate with its `patch` attached, or None after recording
    a rejection. Runs last in the funnel because it is the only gate that
    needs file contents, so measuring a candidate we were going to drop for
    a free reason would be the one genuinely wasteful ordering.
    """
    merge = candidate.merge
    if merge is None:
        return candidate

    stats = repo.diff_stats(merge.base_sha, merge.sha)
    if stats is None or not stats.files:
        report.reject(Rejection.DIFF_UNAVAILABLE)
        return None

    rejection = policy.patch_rejection(stats)
    if rejection:
        report.reject(rejection)
        return None

    return replace(candidate, patch=stats)


def discover_via_api(
    repo: str,
    *,
    since: datetime,
    until: datetime | None = None,
    client: GitHubClient,
    policy: SelectionPolicy | None = None,
    clone: GitRepo | None = None,
    fetch_pr_facts: bool = False,
) -> DiscoveryReport:
    """Mine `repo` issue-first over the window, using GitHub's own linkage."""
    policy = policy or SelectionPolicy()
    report = DiscoveryReport(repo=repo, since=since.isoformat(), until=until.isoformat() if until else None)

    index = MainlineIndex.build(clone.first_parent_points(since=since, until=until)) if clone else MainlineIndex()

    issues_scanned = 0
    timelines_fetched = 0
    # How many issues had a PR that *claimed* the fix and never merged. This
    # is the number that justifies the timeline call over body-regex mining:
    # every one of these is a false positive keyword mining would accept.
    unmerged_traps = 0
    opaque_connected = 0
    survivors: list[Issue] = []

    try:
        for payload in client.issues_closed_in_window(
            repo, since=since.isoformat(), until=until.isoformat() if until else None
        ):
            issues_scanned += 1
            if "pull_request" in payload:
                # The issues endpoint returns PRs too — every PR is an issue
                # in GitHub's model. Counted but not itemised: on the
                # fallback path these run to the thousands and were never
                # candidates in any meaningful sense.
                report.reject(Rejection.IS_PULL_REQUEST)
                continue

            issue = issue_from_payload(payload)
            closed_at = parse_timestamp(issue.closed_at)
            if closed_at is None or closed_at < since or (until and closed_at > until):
                # `since` filters on *updated* time upstream, so the window
                # arrives as a superset and is tightened here.
                report.reject(Rejection.OUTSIDE_WINDOW)
                continue

            rejection = policy.issue_rejection(issue)
            if rejection:
                report.reject(rejection)
                continue
            survivors.append(issue)
    except RateLimited:
        report.detail["rate_limited_during"] = "issue listing"
        raise
    finally:
        report.detail.update(issues_scanned=issues_scanned)

    # Budget applies here: after the free gates, before the paid calls.
    if policy.max_candidates is not None and len(survivors) > policy.max_candidates:
        for dropped in survivors[policy.max_candidates :]:
            report.reject(Rejection.OVER_BUDGET)
        survivors = survivors[: policy.max_candidates]

    for issue in survivors:
        try:
            events = client.issue_timeline(repo, issue.number)
            timelines_fetched += 1
        except RateLimited:
            report.detail["rate_limited_during"] = f"timeline of issue #{issue.number}"
            break
        except GitHubError as exc:
            logger.warning("issue #%d: timeline fetch failed: %s", issue.number, exc)
            report.reject(Rejection.TIMELINE_FETCH_FAILED)
            continue

        links = parse_timeline(events, repo=repo)
        unmerged_traps += len(links.unmerged_pr_numbers)
        opaque_connected += links.opaque_connected

        resolution = resolve_fix_pr(issue, links)
        rejection = rejection_for(resolution)
        if rejection or resolution.pr is None:
            report.reject(rejection or Rejection.LINK_UNCONFIRMED)
            continue
        pull_request, evidence = resolution.pr, resolution.evidence

        merged_at = parse_timestamp(pull_request.merged_at)
        if merged_at is not None and merged_at < since:
            # The fix landed before the window, so the local clone cannot
            # reach its merge point.
            report.reject(Rejection.PR_OUTSIDE_WINDOW)
            continue

        candidate = Candidate(repo=repo, issue=issue, pr=pull_request, evidence=evidence)

        if clone is not None:
            pr_facts = None
            if fetch_pr_facts:
                try:
                    pr_facts = client.pull_request(repo, pull_request.number)
                except GitHubError as exc:
                    logger.debug("PR #%d facts unavailable: %s", pull_request.number, exc)
            resolved = attach_merge_point(candidate, index, clone, pr_facts=pr_facts)
            if isinstance(resolved, Rejection):
                report.reject(resolved)
                continue
            candidate = resolved

            graded = apply_patch_gates(candidate, clone, policy, report)
            if graded is None:
                continue
            candidate = graded

        report.candidates.append(candidate)

    report.detail.update(
        timelines_fetched=timelines_fetched,
        api_calls=client.calls_made,
        cache_hits=client.cache_hits,
        rate_remaining=client.rate_remaining,
        unmerged_pr_traps=unmerged_traps,
        opaque_connected_events=opaque_connected,
        mainline_points=len(index.points),
    )
    return report


def discover_via_git(
    repo_name: str,
    clone: GitRepo,
    *,
    since: datetime,
    until: datetime | None = None,
    policy: SelectionPolicy | None = None,
) -> DiscoveryReport:
    """Mine linkage from commit messages alone — no API, no token, no network.

    The fallback path, and the only one available on a machine with neither
    `gh` nor a token. It reads the two things GitHub bakes into a merge
    subject — the PR number in `(#N)` and any `Fixes #M` in the body — which
    recovers linkage for squash-merge repos without a single request.

    What it cannot do, and must not pretend to: the issue's *text* lives in
    the API, so candidates from this path carry a PR number and a base
    commit but an empty problem statement. It answers "which merges closed
    an issue", not "what was the issue". Use it to scope a window cheaply,
    then spend API budget on the survivors.
    """
    policy = policy or SelectionPolicy()
    report = DiscoveryReport(
        repo=repo_name, since=since.isoformat(), until=until.isoformat() if until else None
    )
    points = clone.first_parent_points(since=since, until=until)
    with_pr_number = 0
    with_keyword = 0

    for point in points:
        as_pr = MergedPR(
            number=point.pr_number or 0,
            merged_at=point.committer_date,
            url=f"https://github.com/{repo_name}/pull/{point.pr_number}" if point.pr_number else "",
            title=point.subject,
        )
        if point.pr_number is None:
            # A direct push to the mainline: no PR, so no issue linkage to read.
            report.reject(Rejection.NO_MERGED_PR)
            continue
        with_pr_number += 1
        message = clone.commit_message(point.sha)
        issue_numbers = closed_issue_numbers(message)
        if not issue_numbers:
            report.reject(Rejection.NO_MERGED_PR)
            continue
        with_keyword += 1
        if len(issue_numbers) > 1:
            # One merge closing several issues has no single oracle.
            report.reject(Rejection.AMBIGUOUS_MULTI_PR)
            continue
        if not point.base_sha:
            report.reject(Rejection.BASE_BEFORE_GRAFT)
            continue

        issue_number = issue_numbers[0]
        candidate = Candidate(
            repo=repo_name,
            issue=Issue(
                number=issue_number,
                title="",
                body="",
                closed_at="",
                state_reason="",
                url=f"https://github.com/{repo_name}/issues/{issue_number}",
            ),
            pr=as_pr,
            evidence=LinkEvidence.KEYWORD_CLAIM,
            merge=point,
        )
        graded = apply_patch_gates(candidate, clone, policy, report)
        if graded is None:
            continue
        report.candidates.append(graded)

    if policy.max_candidates is not None and len(report.candidates) > policy.max_candidates:
        for dropped in report.candidates[policy.max_candidates :]:
            report.reject(Rejection.OVER_BUDGET)
        report.candidates = report.candidates[: policy.max_candidates]

    report.detail.update(
        mainline_points=len(points),
        merges_with_pr_number=with_pr_number,
        merges_with_closing_keyword=with_keyword,
        api_calls=0,
        linkage="commit-message keywords (weak); issue text unavailable without the API",
    )
    if with_pr_number and not with_keyword:
        # Measured on `pallets/click`: 22 merges in a 60-day window, every one
        # carrying a `(#N)` suffix, and *not one* carrying a closing keyword.
        # The issue linkage exists only in GitHub's database. Saying so beats
        # reporting zero candidates and letting it read as "nothing to mine".
        report.detail["offline_limitation"] = (
            f"{with_pr_number} merge(s) name a PR but none name an issue: this repo keeps its "
            "issue linkage in GitHub's database, not in commit messages. The API path "
            "(drop --offline) can read it; the offline path cannot."
        )
    return report
