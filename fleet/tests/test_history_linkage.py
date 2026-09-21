"""Issue -> PR linkage rules.

The fixtures mirror payload shapes taken from a live probe of
`pallets/click#3822`, which happens to contain every interesting case on one
issue: a merged PR that fixed it, an unmerged PR that claimed to, an open
PR, a `connected` event whose target REST refuses to name, and `referenced`
commits.
"""

from __future__ import annotations

from fleet.history.linkage import (
    LinkResolution,
    closed_issue_numbers,
    parse_timeline,
    pr_number_from_subject,
    rejection_for,
    resolve_fix_pr,
)
from fleet.history.models import Issue, LinkConfidence, LinkEvidence, MergedPR, Rejection

REPO = "pallets/click"


def xref(number: int, *, is_pr: bool = True, merged_at: str | None = None, state: str = "closed",
         repo: str = REPO, title: str = "") -> dict:
    issue: dict = {
        "number": number,
        "state": state,
        "title": title,
        "html_url": f"https://github.com/{repo}/pull/{number}",
        "repository": {"full_name": repo},
    }
    if is_pr:
        issue["pull_request"] = {"merged_at": merged_at}
    return {"event": "cross-referenced", "source": {"issue": issue}}


def an_issue(*, number: int = 3822, closed_at: str = "2026-09-08T20:00:00Z", body: str = "x " * 30) -> Issue:
    return Issue(
        number=number,
        title="`click.Path` should be generic on `path_type`",
        body=body,
        closed_at=closed_at,
        state_reason="completed",
        url=f"https://github.com/{REPO}/issues/{number}",
    )


# --- parse_timeline ---------------------------------------------------------


def test_sorts_the_real_click_3822_timeline_into_buckets() -> None:
    events = [
        xref(10292, merged_at=None, state="open"),
        {"event": "referenced", "commit_id": "edaea2ca2089330e32e83cfd94967bc395d4d9b2"},
        xref(3835, merged_at=None, state="closed"),
        xref(3858, merged_at="2026-09-08T19:48:10Z", state="closed"),
        {"event": "connected"},
        {"event": "closed", "commit_id": None},
    ]
    links = parse_timeline(events, repo=REPO)

    assert [pr.number for pr in links.merged_prs] == [3858]
    # The trap: #3835 claimed the fix and never merged. A body regex over all
    # PRs accepts it; the timeline knows better.
    assert links.unmerged_pr_numbers == [3835]
    assert links.open_pr_numbers == [10292]
    assert links.referenced_commits == ["edaea2ca2089330e32e83cfd94967bc395d4d9b2"]
    # REST gives us no target for `connected` — counted so a run can report
    # what a token would recover.
    assert links.opaque_connected == 1
    assert links.closed_by_commit is None


def test_a_plain_issue_reference_is_not_a_fix() -> None:
    links = parse_timeline([xref(99, is_pr=False)], repo=REPO)
    assert links.merged_prs == []
    assert links.unmerged_pr_numbers == []


def test_a_merged_pr_in_another_repo_is_recorded_but_rejected() -> None:
    events = [xref(7, merged_at="2026-09-01T00:00:00Z", repo="other/project")]
    links = parse_timeline(events, repo=REPO)
    assert links.merged_prs == []
    assert links.cross_repo_pr_numbers == [7]


def test_closed_by_commit_is_captured() -> None:
    links = parse_timeline([{"event": "closed", "commit_id": "abc1234"}], repo=REPO)
    assert links.closed_by_commit == "abc1234"


# --- resolve_fix_pr ----------------------------------------------------------
#
# The rules changed after a review found three defects in the old
# `select_fix_pr`. Each is pinned by a test below.


def test_a_lone_merged_mention_is_not_treated_as_a_closure() -> None:
    """DEFECT 1: proximity was mistaken for causation.

    The old rule picked the merged PR nearest the issue's close timestamp
    and returned it as the fix. A cross-reference is a *mention* — someone
    typed `#3822` somewhere — and a mention that happens to merge near a
    manual close is not evidence of anything.
    """
    links = parse_timeline([xref(3858, merged_at="2026-09-08T19:48:10Z")], repo=REPO)
    resolution = resolve_fix_pr(an_issue(), links)

    assert resolution.pr is not None and resolution.pr.number == 3858
    assert resolution.evidence is LinkEvidence.MENTION_ONLY
    assert resolution.confidence is LinkConfidence.UNKNOWN
    # The point of the change: it is not usable on this evidence alone.
    assert resolution.usable is False
    assert rejection_for(resolution) is Rejection.LINK_UNCONFIRMED


def test_an_unrelated_pr_merging_a_minute_before_a_manual_close_does_not_win() -> None:
    """The review's probe, verbatim: mention-only, merged one minute before."""
    links = parse_timeline([xref(999, merged_at="2026-09-08T19:59:00Z")], repo=REPO)
    resolution = resolve_fix_pr(an_issue(closed_at="2026-09-08T20:00:00Z"), links)

    assert resolution.usable is False
    assert resolution.evidence is LinkEvidence.MENTION_ONLY


def test_an_unrelated_closing_sha_no_longer_strengthens_the_chosen_pr() -> None:
    """DEFECT 2: any non-empty `closed_by_commit` upgraded the evidence.

    The commit was never checked against the selected PR, so a closure by
    some other commit silently promoted an unrelated mention.
    """
    links = parse_timeline(
        [xref(3858, merged_at="2026-09-08T19:48:10Z"), {"event": "closed", "commit_id": "deadbeef"}],
        repo=REPO,
    )
    resolution = resolve_fix_pr(an_issue(), links)

    assert resolution.evidence is LinkEvidence.MENTION_ONLY  # was CLOSED_BY_COMMIT
    # The SHA is carried for the enrichment pass instead of being consumed
    # as if it proved something.
    assert resolution.unresolved_closing_commit == "deadbeef"
    assert resolution.usable is False


def test_a_closing_commit_with_no_cross_reference_is_deferred_not_rejected() -> None:
    """DEFECT 3: this returned `no_merged_pr` and the candidate was lost.

    It is exactly the case the API can still resolve —
    `/commits/{sha}/pulls` maps the SHA to its PR — so throwing it away
    discarded recoverable candidates.
    """
    links = parse_timeline([{"event": "closed", "commit_id": "abc1234"}], repo=REPO)
    resolution = resolve_fix_pr(an_issue(), links)

    assert resolution.pr is None
    assert resolution.evidence is LinkEvidence.CLOSING_REFERENCE
    assert resolution.confidence is LinkConfidence.UNKNOWN
    assert resolution.unresolved_closing_commit == "abc1234"
    assert rejection_for(resolution) is Rejection.LINK_UNCONFIRMED
    assert "commits/{sha}/pulls" in resolution.notes


def test_nothing_at_all_is_no_merged_pr() -> None:
    resolution = resolve_fix_pr(an_issue(), parse_timeline([], repo=REPO))
    assert resolution.pr is None
    assert rejection_for(resolution) is Rejection.NO_MERGED_PR


def test_a_ui_link_rest_cannot_name_is_reported_as_such() -> None:
    """A `connected` event means a link exists that REST will not resolve."""
    resolution = resolve_fix_pr(an_issue(), parse_timeline([{"event": "connected"}], repo=REPO))

    assert resolution.confidence is LinkConfidence.UNKNOWN
    assert "GraphQL" in resolution.notes


def test_a_pr_merged_after_the_issue_closed_did_not_close_it() -> None:
    links = parse_timeline([xref(1, merged_at="2026-09-20T00:00:00Z")], repo=REPO)
    resolution = resolve_fix_pr(an_issue(closed_at="2026-09-08T20:00:00Z"), links)

    assert resolution.pr is None
    assert "after the issue closed" in resolution.notes


def test_competing_prs_are_all_recorded_for_later_review() -> None:
    """Ambiguity is preserved rather than resolved by guessing."""
    links = parse_timeline(
        [
            xref(3857, merged_at="2026-09-08T18:00:00Z"),
            xref(3858, merged_at="2026-09-08T19:48:10Z"),
        ],
        repo=REPO,
    )
    resolution = resolve_fix_pr(an_issue(closed_at="2026-09-08T20:00:00Z"), links)

    assert resolution.pr is None
    assert resolution.confidence is LinkConfidence.NEEDS_REVIEW
    assert set(resolution.competing) == {3857, 3858}
    assert rejection_for(resolution) is Rejection.AMBIGUOUS_MULTI_PR


def test_missing_close_timestamp_never_produces_a_usable_link() -> None:
    one = parse_timeline([xref(1, merged_at="2026-01-01T00:00:00Z")], repo=REPO)
    assert resolve_fix_pr(an_issue(closed_at=""), one).usable is False

    two = parse_timeline(
        [xref(1, merged_at="2026-01-01T00:00:00Z"), xref(2, merged_at="2026-02-01T00:00:00Z")],
        repo=REPO,
    )
    assert resolve_fix_pr(an_issue(closed_at=""), two).confidence is LinkConfidence.NEEDS_REVIEW


def test_usable_requires_recorded_closure_not_a_mention() -> None:
    """What an enrichment pass has to produce before a candidate is usable."""
    mention = LinkResolution(
        pr=MergedPR(number=1, merged_at="", url=""),
        evidence=LinkEvidence.MENTION_ONLY,
        confidence=LinkConfidence.RESOLVED,
    )
    assert mention.usable is False

    recorded = LinkResolution(
        pr=MergedPR(number=1, merged_at="", url=""),
        evidence=LinkEvidence.RECORDED_CLOSURE,
        confidence=LinkConfidence.RESOLVED,
    )
    assert recorded.usable is True
    assert rejection_for(recorded) is None


# --- text-derived helpers (the git-local path) -------------------------------


def test_pr_number_comes_out_of_a_squash_subject() -> None:
    assert pr_number_from_subject("Make `click.Path` generic on `path_type` (#3858)") == 3858
    assert pr_number_from_subject("Stable (#3851)") == 3851
    # A rebase-merge keeps the author's subject, so there is nothing to read —
    # which is exactly what makes the missing suffix informative.
    assert pr_number_from_subject("fix the pager on Windows") is None
    # A trailing reference is required; a mid-subject mention is not a suffix.
    assert pr_number_from_subject("Revert (#123) because it broke CI") is None


def test_closing_keywords_are_read_out_of_a_commit_message() -> None:
    assert closed_issue_numbers("Fix the thing\n\nFixes #42") == [42]
    assert closed_issue_numbers("closes #1 and resolved #2") == [1, 2]
    assert closed_issue_numbers("Closes: #7") == [7]
    assert closed_issue_numbers("see #9") == []  # a mention, not a closure
    assert closed_issue_numbers("duplicated #5 #5 closes #5") == [5]
