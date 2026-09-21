"""Merge-style classification and the first-parent walk, against real git.

Built on actual repositories rather than fixtures, because the claims being
tested are claims about git's behaviour: that `--first-parent` steps over
the inside of a merged branch, that a squash leaves one parent and a `(#N)`
suffix while a rebase leaves neither, and that `patch-id` survives a replay.
A fixture could only restate the assumption.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from git_helpers import commit, git, init_repo

from fleet.history.merge_style import classify, patch_ids_match
from fleet.history.models import MergePoint, MergeStyle
from fleet.history.window import GitRepo, parse_since


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return init_repo(tmp_path / "repo")


# --- the first-parent walk --------------------------------------------------


def test_first_parent_steps_over_the_inside_of_a_merged_branch(repo: Path) -> None:
    """One entry per landed PR — the inside of the branch never appears.

    This is what "only take merges" means in git terms, and it is why the
    walk uses `--first-parent` rather than `--merges`: `--merges` would drop
    every squash-merged repository on the floor.
    """
    commit(repo, "a.txt", "a", "initial", date="2026-01-01T00:00:00Z")
    git(repo, "checkout", "--quiet", "-b", "feature")
    commit(repo, "c.txt", "c", "work in progress", date="2026-01-02T00:00:00Z")
    commit(repo, "c.txt", "c2", "more wip", date="2026-01-03T00:00:00Z")
    git(repo, "checkout", "--quiet", "main")
    base = commit(repo, "b.txt", "b", "mainline moves on", date="2026-01-04T00:00:00Z")
    git(repo, "merge", "--quiet", "--no-ff", "feature", "-m", "Add the feature (#12)")

    points = GitRepo.at(repo).first_parent_points()
    subjects = [point.subject for point in points]

    assert subjects == ["Add the feature (#12)", "mainline moves on", "initial"]
    assert "work in progress" not in subjects
    assert "more wip" not in subjects

    merge_point = points[0]
    assert len(merge_point.parents) == 2
    # The true base is the mainline side, and it is the commit the mainline
    # had advanced to — not anything the branch knew about when it forked.
    assert merge_point.base_sha == base
    assert merge_point.pr_number == 12


def test_since_bounds_the_window(repo: Path) -> None:
    commit(repo, "old.txt", "old", "ancient history", date="2020-01-01T00:00:00Z")
    commit(repo, "new.txt", "new", "recent work (#5)", date="2026-06-01T00:00:00Z")

    points = GitRepo.at(repo).first_parent_points(since=datetime(2026, 1, 1, tzinfo=UTC))

    assert [point.subject for point in points] == ["recent work (#5)"]


def test_a_repo_with_no_commits_is_an_empty_window_not_an_error(repo: Path) -> None:
    """A freshly `git init`-ed repo has an unborn HEAD.

    `git log HEAD` there fails with a bare "ambiguous argument", which reads
    as a caller bug rather than "no history yet" — hit for real when mining
    a repo whose first commit hadn't been made.
    """
    opened = GitRepo.at(repo)
    assert opened.has_commits() is False
    assert opened.first_parent_points() == []

    commit(repo, "a.txt", "a", "initial", date="2026-01-01T00:00:00Z")
    assert opened.has_commits() is True


def test_commit_message_returns_the_full_body(repo: Path) -> None:
    commit(repo, "a.txt", "a", "Fix the thing (#9)\n\nFixes #42", date="2026-01-01T00:00:00Z")
    message = GitRepo.at(repo).commit_message("HEAD")
    assert "Fixes #42" in message


def test_a_full_clone_reports_no_graft(repo: Path) -> None:
    commit(repo, "a.txt", "a", "initial", date="2026-01-01T00:00:00Z")
    opened = GitRepo.at(repo)
    assert opened.is_shallow is False
    assert opened.graft_points() == []


def test_contains_distinguishes_ancestors_from_strangers(repo: Path) -> None:
    first = commit(repo, "a.txt", "a", "initial", date="2026-01-01T00:00:00Z")
    git(repo, "checkout", "--quiet", "-b", "sidetrack")
    stray = commit(repo, "s.txt", "s", "never merged", date="2026-01-02T00:00:00Z")
    git(repo, "checkout", "--quiet", "main")

    opened = GitRepo.at(repo)
    assert opened.contains(first) is True
    assert opened.contains(stray) is False  # present in the object db, not an ancestor
    assert opened.has_object(stray) is True  # …which is why presence alone is not enough
    assert opened.contains("") is False


# --- merge-style classification ---------------------------------------------


def test_two_parents_is_a_merge_commit(repo: Path) -> None:
    commit(repo, "a.txt", "a", "initial", date="2026-01-01T00:00:00Z")
    git(repo, "checkout", "--quiet", "-b", "feature")
    head = commit(repo, "c.txt", "c", "the work", date="2026-01-02T00:00:00Z")
    git(repo, "checkout", "--quiet", "main")
    git(repo, "merge", "--quiet", "--no-ff", "feature", "-m", "Land it (#3)")

    point = GitRepo.at(repo).first_parent_points()[0]
    style, evidence = classify(point, head_sha=head)

    assert style is MergeStyle.MERGE_COMMIT
    assert "parents[1] matches PR head" in evidence


def test_single_parent_with_a_pr_suffix_is_a_squash(repo: Path) -> None:
    """GitHub appends `(#N)` when squashing and when merging, never when rebasing."""
    commit(repo, "a.txt", "a", "initial", date="2026-01-01T00:00:00Z")
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "b.txt", "b", "Fix the pager (#77)", date="2026-01-02T00:00:00Z")

    point = GitRepo.at(repo).first_parent_points()[0]
    style, evidence = classify(point, pr_commit_count=4)

    assert style is MergeStyle.SQUASH
    assert point.base_sha == base
    assert "4 commits collapsed" in evidence


def test_a_real_rebase_leaves_no_suffix_and_a_matching_patch_id(repo: Path) -> None:
    """The rebase case, end to end, on a genuinely rebased commit.

    A rebase-merge preserves the author's subject verbatim, so there is no
    `(#N)` to read — which is the local signal. It also rewrites the SHA
    while leaving the diff alone, so `patch-id` still matches the original.
    """
    commit(repo, "a.txt", "a", "initial", date="2026-01-01T00:00:00Z")
    git(repo, "checkout", "--quiet", "-b", "feature")
    original = commit(repo, "fix.txt", "fixed", "fix the pager on Windows", date="2026-01-02T00:00:00Z")
    # Keep the pre-rebase commit reachable so we can compare patch ids.
    git(repo, "tag", "before-rebase", original)

    git(repo, "checkout", "--quiet", "main")
    mainline = commit(repo, "b.txt", "b", "unrelated mainline work", date="2026-01-03T00:00:00Z")
    git(repo, "checkout", "--quiet", "feature")
    git(repo, "rebase", "--quiet", "main")
    replayed = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "--quiet", "main")
    git(repo, "merge", "--quiet", "--ff-only", "feature")

    opened = GitRepo.at(repo)
    point = opened.first_parent_points()[0]

    assert point.sha == replayed
    assert replayed != original  # the SHA was rewritten
    assert len(point.parents) == 1
    assert point.parents[0] == mainline
    assert point.pr_number is None  # no suffix: the subject came through untouched

    # Without PR metadata, a rebase-merge and a direct push to the mainline
    # are indistinguishable from a clone. Say UNKNOWN rather than guess.
    style, evidence = classify(point)
    assert style is MergeStyle.UNKNOWN
    assert "no `(#N)` suffix" in evidence

    # Told that a PR existed, the verdict resolves.
    style, evidence = classify(point, pr_commit_count=1)
    assert style is MergeStyle.REBASE
    assert "replayed with new SHAs" in evidence

    # And the diff survived the replay, which is the strongest evidence a
    # clone can offer that these two commits are the same change.
    assert patch_ids_match(opened.patch_id(original), opened.patch_id(replayed))


def test_head_reachable_from_mainline_is_a_fast_forward(repo: Path) -> None:
    commit(repo, "a.txt", "a", "initial", date="2026-01-01T00:00:00Z")
    git(repo, "checkout", "--quiet", "-b", "feature")
    head = commit(repo, "c.txt", "c", "the work", date="2026-01-02T00:00:00Z")
    git(repo, "checkout", "--quiet", "main")
    git(repo, "merge", "--quiet", "--ff-only", "feature")

    point = GitRepo.at(repo).first_parent_points()[0]
    style, _ = classify(point, head_sha=head)
    assert style is MergeStyle.FAST_FORWARD


def test_a_parentless_point_has_no_base_to_offer() -> None:
    """The graft boundary of a shallow clone, or a root commit.

    Either way there is nothing beneath it to check out, so the candidate is
    unusable rather than merely unclassified.
    """
    point = MergePoint(sha="a" * 40, subject="initial (#1)", parents=())
    style, evidence = classify(point)
    assert style is MergeStyle.UNKNOWN
    assert "graft boundary" in evidence
    assert point.base_sha == ""


def test_author_committer_divergence_is_a_hint_not_a_verdict() -> None:
    """Deliberately inert: cherry-picks and `--amend` produce it too."""
    replayed = MergePoint(
        sha="b" * 40,
        subject="fix something",
        parents=("c" * 40,),
        author_date="2026-01-02T00:00:00Z",
        committer_date="2026-01-09T00:00:00Z",
    )
    assert replayed.dates_diverge is True
    # The classifier does not consult it — with no PR facts the answer stays
    # UNKNOWN rather than being upgraded to REBASE on a weak signal.
    assert classify(replayed)[0] is MergeStyle.UNKNOWN


# --- window parsing ---------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected_date"),
    [
        ("90d", "2026-06-14"),
        ("12w", "2026-06-20"),
        ("6mo", "2026-03-13"),
        ("2y", "2024-09-11"),
        ("2026-01-01", "2026-01-01"),
        ("2026-01-01T12:00:00Z", "2026-01-01"),
    ],
)
def test_since_accepts_relative_and_absolute_windows(value: str, expected_date: str) -> None:
    now = datetime(2026, 9, 12, tzinfo=UTC)
    assert parse_since(value, now=now).date().isoformat() == expected_date


@pytest.mark.parametrize("value", ["", "banana", "0d", "-5d", "6 fortnights"])
def test_since_rejects_what_it_cannot_parse(value: str) -> None:
    with pytest.raises(ValueError):
        parse_since(value)


def test_parsed_since_is_always_timezone_aware() -> None:
    # A naive datetime compared against GitHub's aware timestamps raises at
    # runtime, deep inside the window filter.
    assert parse_since("2026-01-01").tzinfo is not None
    assert parse_since("30d").tzinfo is not None
