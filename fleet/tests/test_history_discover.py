"""Selection: which candidates survive, and whether every drop is accounted for.

The API is faked; the git repository is real, because the base-commit claim
("`parents[0]` of the merge point, not the API's stale `base.sha`") is only
meaningful against actual history.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from git_helpers import commit, commit_source_fix, git, init_repo

from fleet.history.client import GitHubClient, Response
from fleet.history.discover import (
    MainlineIndex,
    SelectionPolicy,
    attach_merge_point,
    discover_via_api,
    discover_via_git,
    issue_from_payload,
)
from fleet.history.models import Candidate, Issue, LinkEvidence, MergedPR, MergeStyle, Rejection
from fleet.history.window import GitRepo
from fleet.pipelines import PIPELINES
from fleet.pipelines.history import HistoryPipeline

REPO = "o/r"
SINCE = datetime(2026, 1, 1, tzinfo=UTC)


def issue_payload(
    number: int,
    *,
    body: str = "The parser crashes on an empty input file and prints a traceback instead of an error.",
    closed_at: str = "2026-06-01T12:00:00Z",
    state_reason: str = "completed",
    labels: tuple[str, ...] = ("bug",),
    user_type: str = "User",
    login: str = "reporter",
    is_pr: bool = False,
) -> dict:
    payload = {
        "number": number,
        "title": f"crash on empty input ({number})",
        "body": body,
        "closed_at": closed_at,
        "state_reason": state_reason,
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "user": {"login": login, "type": user_type},
        "labels": [{"name": name} for name in labels],
    }
    if is_pr:
        payload["pull_request"] = {"url": "…"}
    return payload


def xref_event(number: int, merged_at: str | None) -> dict:
    return {
        "event": "cross-referenced",
        "source": {
            "issue": {
                "number": number,
                "state": "closed",
                "title": f"fix #{number}",
                "html_url": f"https://github.com/{REPO}/pull/{number}",
                "repository": {"full_name": REPO},
                "pull_request": {"merged_at": merged_at},
            }
        },
    }


class FakeAPI:
    """Serves search results, issue lists, and timelines from in-memory dicts.

    `search_status` lets a test break the search endpoint to exercise the
    fallback onto the list endpoint.
    """

    def __init__(
        self,
        issues: list[dict],
        timelines: dict[int, list[dict]],
        pulls: dict[int, dict] | None = None,
        *,
        search_status: int = 200,
        search_total: int | None = None,
    ):
        self.issues = issues
        self.timelines = timelines
        self.pulls = pulls or {}
        self.search_status = search_status
        self.search_total = search_total
        self.urls: list[str] = []

    def __call__(self, url: str, headers: dict[str, str]) -> Response:
        self.urls.append(url)
        status = 200
        body: object = []
        if "/search/issues" in url:
            if self.search_status != 200:
                return Response(
                    status=self.search_status,
                    body=b'{"message": "Validation Failed"}',
                    headers={"X-Ratelimit-Remaining": "8"},
                )
            # Search never returns pull requests, so the fake shouldn't either.
            items = [issue for issue in self.issues if "pull_request" not in issue]
            body = {
                "total_count": self.search_total if self.search_total is not None else len(items),
                "incomplete_results": False,
                "items": items,
            }
        elif "/issues?" in url:
            body = self.issues
        elif "/timeline" in url:
            number = int(url.split("/issues/")[1].split("/")[0])
            body = self.timelines.get(number, [])
        elif "/pulls/" in url:
            number = int(url.rstrip("/").split("/")[-1])
            body = self.pulls.get(number, {})
        return Response(
            status=status,
            body=json.dumps(body).encode(),
            headers={"X-Ratelimit-Remaining": "58"},
        )

    def client(self) -> GitHubClient:
        return GitHubClient(transport=self)

    @property
    def timeline_calls(self) -> int:
        return sum(1 for url in self.urls if "/timeline" in url)

    @property
    def search_calls(self) -> int:
        return sum(1 for url in self.urls if "/search/issues" in url)

    @property
    def list_calls(self) -> int:
        return sum(1 for url in self.urls if "/search/" not in url and "/issues?" in url)


# --- the accepted path ------------------------------------------------------


def test_rest_cross_references_alone_never_produce_a_usable_candidate() -> None:
    """The consequence of treating a mention as a mention.

    REST's timeline records cross-references, not closures — someone typed
    `#10` in a PR description. The old rule promoted the merged one nearest
    the close timestamp to "the fix"; it now reports `link_unconfirmed`,
    and the API-only path yields nothing until an enrichment pass supplies
    recorded closure (GraphQL `closingIssuesReferences`, or
    `/commits/{sha}/pulls`).

    This is a deliberate yield regression. The old yield was built on an
    inference that a review showed to be unsound.
    """
    api = FakeAPI(
        issues=[issue_payload(10)],
        timelines={10: [xref_event(11, "2026-06-01T11:00:00Z")]},
    )
    report = discover_via_api(REPO, since=SINCE, client=api.client())

    assert report.candidates == []
    assert report.rejections == {str(Rejection.LINK_UNCONFIRMED): 1}


def test_the_report_records_what_the_timeline_path_bought() -> None:
    """The count of PRs that claimed a fix and never merged.

    Every one is a false positive that body-regex mining accepts, so this
    number is the justification for spending a call per issue.
    """
    api = FakeAPI(
        issues=[issue_payload(10)],
        timelines={
            10: [
                xref_event(11, "2026-06-01T11:00:00Z"),
                xref_event(12, None),
                xref_event(13, None),
                {"event": "connected"},
            ]
        },
    )
    report = discover_via_api(REPO, since=SINCE, client=api.client())

    assert report.detail["unmerged_pr_traps"] == 2
    assert report.detail["opaque_connected_events"] == 1


# --- the gates, each with its own name --------------------------------------


@pytest.mark.parametrize(
    ("payload_kwargs", "expected"),
    [
        ({"state_reason": "not_planned"}, Rejection.NOT_COMPLETED),
        ({"user_type": "Bot", "login": "dependabot[bot]"}, Rejection.BOT_AUTHORED),
        ({"labels": ("question",)}, Rejection.LABEL_EXCLUDED),
        ({"body": "broken"}, Rejection.ISSUE_BODY_TOO_THIN),
        ({"closed_at": "2020-01-01T00:00:00Z"}, Rejection.OUTSIDE_WINDOW),
        ({"closed_at": ""}, Rejection.OUTSIDE_WINDOW),
    ],
)
def test_each_gate_rejects_under_its_own_name(payload_kwargs: dict, expected: Rejection) -> None:
    api = FakeAPI(issues=[issue_payload(10, **payload_kwargs)], timelines={10: []})
    report = discover_via_api(REPO, since=SINCE, client=api.client())

    assert report.candidates == []
    assert report.rejections == {str(expected): 1}


def test_search_is_the_primary_query_and_needs_no_pr_filtering() -> None:
    """One request, issues only, all inside the window.

    The point of preferring search: `is:issue` and a closed-date range are
    applied by the server, so the pull requests that dominate the list
    endpoint's output never arrive.
    """
    api = FakeAPI(
        issues=[issue_payload(10), issue_payload(11, is_pr=True)],
        timelines={10: [xref_event(99, "2026-06-01T11:00:00Z")]},
    )
    report = discover_via_api(REPO, since=SINCE, client=api.client())

    assert api.search_calls == 1
    assert api.list_calls == 0
    assert str(Rejection.IS_PULL_REQUEST) not in report.rejections
    # It reached linkage rather than dying on shape — which is what this
    # test is about; the linkage verdict itself is linkage.py's business.
    assert api.timeline_calls == 1


def test_a_broken_search_falls_back_to_the_list_endpoint() -> None:
    """Search has its own budget and its own failure modes.

    On the fallback path pull requests do come back — every PR is an issue
    in GitHub's data model — so the gate that drops them still earns its
    place, and still reports what it dropped.
    """
    api = FakeAPI(
        issues=[issue_payload(10), issue_payload(11, is_pr=True)],
        timelines={10: [xref_event(99, "2026-06-01T11:00:00Z")]},
        search_status=422,
    )
    report = discover_via_api(REPO, since=SINCE, client=api.client())

    assert api.search_calls == 1
    assert api.list_calls == 1
    assert report.rejections[str(Rejection.IS_PULL_REQUEST)] == 1
    assert api.timeline_calls == 1


def test_an_oversized_window_is_bisected_rather_than_truncated() -> None:
    """Search stops paginating past 1,000 results and says nothing about it.

    A window holding more must be split, or its tail vanishes silently —
    the worst failure mode for a mining run, because the output still looks
    plausible.
    """
    api = FakeAPI(
        issues=[issue_payload(10)],
        timelines={10: [xref_event(11, "2026-06-01T11:00:00Z")]},
        search_total=5000,
    )
    report = discover_via_api(
        REPO, since=datetime(2020, 1, 1, tzinfo=UTC), until=datetime(2026, 9, 1, tzinfo=UTC),
        client=api.client(),
    )

    # Bisection recurses to the depth cap, so the window is queried in
    # slices rather than as one truncated request.
    assert api.search_calls > 1
    assert report.detail["issues_scanned"] > 0


def test_free_gates_run_before_the_paid_call() -> None:
    """The ordering that makes a 60-request budget usable.

    Nine of these ten issues fail a free check; only one costs a timeline
    call. Reversing the order would spend the whole hourly budget on
    `wontfix` issues.
    """
    issues = [issue_payload(n, state_reason="not_planned") for n in range(1, 10)]
    issues.append(issue_payload(10))
    api = FakeAPI(issues=issues, timelines={10: [xref_event(11, "2026-06-01T11:00:00Z")]})

    report = discover_via_api(REPO, since=SINCE, client=api.client())

    assert api.timeline_calls == 1
    assert report.detail["issues_scanned"] == 10
    assert report.detail["timelines_fetched"] == 1


def test_no_merged_pr_is_named_as_such() -> None:
    api = FakeAPI(issues=[issue_payload(10)], timelines={10: [xref_event(12, None)]})
    report = discover_via_api(REPO, since=SINCE, client=api.client())
    assert report.rejections == {str(Rejection.NO_MERGED_PR): 1}


def test_the_linkage_gate_now_runs_ahead_of_the_window_check() -> None:
    """A consequence of the linkage fix, recorded rather than hidden.

    `pr_outside_window` sits downstream of linkage, and REST linkage is
    always unconfirmed, so that reason is currently unreachable on the API
    path. It comes back into play once an enrichment pass can produce a
    confirmed link. Asserting the reason that actually fires keeps the test
    honest about where the funnel stops today.
    """
    api = FakeAPI(
        issues=[issue_payload(10, closed_at="2026-06-01T12:00:00Z")],
        timelines={10: [xref_event(11, "2025-03-01T00:00:00Z")]},
    )
    report = discover_via_api(REPO, since=SINCE, client=api.client())
    assert report.rejections == {str(Rejection.LINK_UNCONFIRMED): 1}


def test_the_budget_is_a_cap_on_spend_not_a_quality_gate() -> None:
    issues = [issue_payload(n) for n in (10, 20, 30)]
    timelines = {n: [xref_event(n + 1, "2026-06-01T11:00:00Z")] for n in (10, 20, 30)}
    api = FakeAPI(issues=issues, timelines=timelines)

    report = discover_via_api(
        REPO, since=SINCE, client=api.client(), policy=SelectionPolicy(max_candidates=2)
    )

    # Counted separately from quality rejections, because it says "spend
    # more", not "this repo is unsuitable".
    assert report.rejections[str(Rejection.OVER_BUDGET)] == 1
    assert api.timeline_calls == 2


def test_policy_thresholds_are_adjustable() -> None:
    api = FakeAPI(
        issues=[issue_payload(10, body="parser crashes on empty file")],
        timelines={10: [xref_event(11, "2026-06-01T11:00:00Z")]},
    )
    strict = discover_via_api(
        REPO, since=SINCE, client=api.client(), policy=SelectionPolicy(min_issue_body_words=50)
    )
    assert strict.rejections == {str(Rejection.ISSUE_BODY_TOO_THIN): 1}

    lenient = discover_via_api(
        REPO, since=SINCE, client=api.client(), policy=SelectionPolicy(min_issue_body_words=3)
    )
    # Past the issue gate — it dies later, at linkage, which is the point.
    assert str(Rejection.ISSUE_BODY_TOO_THIN) not in lenient.rejections


def test_issue_payload_parsing_flags_bots_by_login_suffix() -> None:
    parsed = issue_from_payload(issue_payload(1, login="renovate[bot]", user_type="User"))
    assert parsed.author_is_bot is True
    assert parsed.labels == ("bug",)


# --- base commit resolution against a real repo -----------------------------


@pytest.fixture
def mainline(tmp_path: Path) -> tuple[Path, str, str]:
    """A repo where the base branch advanced *after* the PR forked.

    This is the shape that makes the API's `pull_request.base.sha` wrong: a
    PR opened at `forked` and merged after the mainline reached `advanced`
    reports `forked` as its base, while the patch actually applies to
    `advanced`.
    """
    root = init_repo(tmp_path / "repo")
    forked = commit(root, "a.txt", "a", "initial", date="2026-02-01T00:00:00Z")
    git(root, "checkout", "--quiet", "-b", "feature")
    commit_source_fix(root, "fix the crash", date="2026-02-02T00:00:00Z")
    git(root, "checkout", "--quiet", "main")
    advanced = commit(root, "b.txt", "b", "unrelated mainline work", date="2026-02-03T00:00:00Z")
    git(root, "merge", "--quiet", "--no-ff", "feature", "-m", "Fix the crash (#11)")
    return root, forked, advanced


def test_the_base_commit_comes_from_the_merge_point_not_the_stale_api_field(
    mainline: tuple[Path, str, str],
) -> None:
    root, forked, advanced = mainline
    clone = GitRepo.at(root)
    index = MainlineIndex.build(clone.first_parent_points())

    candidate = Candidate(
        repo=REPO,
        issue=Issue(number=10, title="t", body="b", closed_at="", state_reason="completed", url=""),
        pr=MergedPR(number=11, merged_at="2026-02-03T12:00:00Z", url=""),
        evidence=LinkEvidence.RECORDED_CLOSURE,
    )
    resolved = attach_merge_point(candidate, index, clone)

    assert not isinstance(resolved, Rejection)
    assert resolved.base_sha == advanced
    assert resolved.base_sha != forked  # what `pull_request.base.sha` would have said
    assert resolved.merge_style is MergeStyle.MERGE_COMMIT
    assert clone.contains(resolved.base_sha)


def test_a_pr_missing_from_the_window_is_rejected_not_guessed(mainline: tuple[Path, str, str]) -> None:
    root, _, _ = mainline
    clone = GitRepo.at(root)
    index = MainlineIndex.build(clone.first_parent_points())
    candidate = Candidate(
        repo=REPO,
        issue=Issue(number=10, title="t", body="b", closed_at="", state_reason="completed", url=""),
        pr=MergedPR(number=999, merged_at="2026-02-03T12:00:00Z", url=""),
        evidence=LinkEvidence.RECORDED_CLOSURE,
    )
    assert attach_merge_point(candidate, index, clone) is Rejection.MERGE_POINT_NOT_FOUND


def test_api_facts_sharpen_the_merge_style_verdict(mainline: tuple[Path, str, str]) -> None:
    root, _, _ = mainline
    clone = GitRepo.at(root)
    points = clone.first_parent_points()
    head = points[0].parents[1]
    index = MainlineIndex.build(points)
    candidate = Candidate(
        repo=REPO,
        issue=Issue(number=10, title="t", body="b", closed_at="", state_reason="completed", url=""),
        pr=MergedPR(number=11, merged_at="2026-02-03T12:00:00Z", url=""),
        evidence=LinkEvidence.RECORDED_CLOSURE,
    )
    resolved = attach_merge_point(
        candidate, index, clone, pr_facts={"head": {"sha": head}, "commits": 1}
    )
    assert not isinstance(resolved, Rejection)
    assert "parents[1] matches PR head" in resolved.merge.style_evidence


def test_end_to_end_with_a_clone_attached(mainline: tuple[Path, str, str]) -> None:
    root, _, advanced = mainline
    api = FakeAPI(
        issues=[issue_payload(10)],
        timelines={10: [xref_event(11, "2026-02-03T12:00:00Z")]},
    )
    report = discover_via_api(
        REPO, since=SINCE, client=api.client(), clone=GitRepo.at(root)
    )

    # Linkage is unconfirmed on REST evidence alone, so nothing is emitted.
    # The base-commit resolution itself is covered directly, below.
    assert report.candidates == []
    assert report.detail["mainline_points"] == 3


# --- the offline path -------------------------------------------------------


def test_offline_mining_reads_linkage_out_of_commit_messages(tmp_path: Path) -> None:
    """No token, no network: `(#N)` plus `Fixes #M` is enough for a candidate."""
    root = init_repo(tmp_path / "repo")
    base = commit(root, "a.txt", "a", "initial", date="2026-02-01T00:00:00Z")
    commit_source_fix(
        root, "Fix the crash on empty input (#11)\n\nFixes #10", date="2026-02-02T00:00:00Z"
    )

    report = discover_via_git(REPO, GitRepo.at(root), since=SINCE)

    assert len(report.candidates) == 1
    candidate = report.candidates[0]
    assert candidate.pr.number == 11
    assert candidate.issue.number == 10
    assert candidate.base_sha == base
    assert candidate.evidence is LinkEvidence.KEYWORD_CLAIM
    # Honest about the limitation: no API means no problem statement.
    assert candidate.issue.body == ""
    assert "issue text unavailable" in report.detail["linkage"]
    # The root commit closes nothing and is rejected, not silently dropped.
    assert report.rejections == {str(Rejection.NO_MERGED_PR): 1}


def test_offline_says_when_a_repo_keeps_its_linkage_out_of_git(tmp_path: Path) -> None:
    """The real shape of `pallets/click`, and the offline path's hard limit.

    Measured: 22 merges in a 60-day window, every one carrying a `(#N)`
    suffix, not one carrying a closing keyword. Reporting "0 candidates"
    would read as "nothing to mine" when the truth is "the linkage is in
    GitHub's database and this path can't reach it".
    """
    root = init_repo(tmp_path / "repo")
    commit(root, "a.txt", "a", "initial", date="2026-02-01T00:00:00Z")
    commit(root, "b.txt", "b", "Fix the pager (#11)", date="2026-02-02T00:00:00Z")
    commit(root, "c.txt", "c", "Tidy the parser (#12)", date="2026-02-03T00:00:00Z")

    report = discover_via_git(REPO, GitRepo.at(root), since=SINCE)

    assert report.candidates == []
    assert report.detail["merges_with_pr_number"] == 2
    assert report.detail["merges_with_closing_keyword"] == 0
    assert "not in commit messages" in report.detail["offline_limitation"]


def test_no_limitation_notice_when_the_offline_path_actually_works(tmp_path: Path) -> None:
    root = init_repo(tmp_path / "repo")
    commit(root, "a.txt", "a", "initial", date="2026-02-01T00:00:00Z")
    commit_source_fix(root, "Fix the pager (#11)\n\nFixes #10", date="2026-02-02T00:00:00Z")

    report = discover_via_git(REPO, GitRepo.at(root), since=SINCE)

    assert len(report.candidates) == 1
    assert "offline_limitation" not in report.detail


def test_offline_rejects_a_merge_closing_several_issues(tmp_path: Path) -> None:
    root = init_repo(tmp_path / "repo")
    commit(root, "a.txt", "a", "initial\n\nFixes #1", date="2026-02-01T00:00:00Z")
    commit(root, "b.txt", "b", "Batch fix (#12)\n\nFixes #10, closes #11", date="2026-02-02T00:00:00Z")

    report = discover_via_git(REPO, GitRepo.at(root), since=SINCE)

    assert report.candidates == []
    assert report.rejections[str(Rejection.AMBIGUOUS_MULTI_PR)] == 1


# --- the pipeline wrapper ---------------------------------------------------
#
# The pipeline now builds a commit database rather than issue candidates;
# its tests live in test_history_commits.py. The linkage path below stays
# covered because phase 2 will wire it back in.


def test_the_history_pipeline_is_registered_and_conforms() -> None:
    assert PIPELINES["history"] is HistoryPipeline
    assert HistoryPipeline.name == "history"
