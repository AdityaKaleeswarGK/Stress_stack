"""The commit database: integration points, measured, capped.

Built on real git repositories throughout — the claims under test are
claims about git's behaviour and about real diff shapes, so a fixture could
only restate the assumption.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from git_helpers import commit, git, init_repo

from fleet.history.commits import CommitFilter, CommitRecord, build_database
from fleet.history.models import MergeStyle, Rejection
from fleet.history.patch import PathKind
from fleet.history.window import GitRepo
from fleet.pipelines import PIPELINES
from fleet.pipelines.history import HistoryPipeline

REPO = "o/r"

# Four lines of application code — clears the default floor of 3.
FIX = "def handle(value):\n    if value is None:\n        return 0\n    return value\n"
BIGGER_FIX = FIX + "".join(f"# line {n}\n" for n in range(30))


_branch_counter = itertools.count()


def landed_via_merge(repo: Path, subject: str, *, files: dict[str, str], date: str) -> str:
    """Land `files` on main through a real merge commit. Returns the base SHA.

    The branch name is counter-derived rather than taken from the subject:
    every subject here starts with "Fix", so a subject-derived name collided
    on the second call.
    """
    branch = f"feature-{next(_branch_counter)}"
    git(repo, "checkout", "--quiet", "-b", branch)
    for index, (path, content) in enumerate(files.items()):
        commit(repo, path, content, f"{subject} part {index}", date=date)
    git(repo, "checkout", "--quiet", "main")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "merge", "--quiet", "--no-ff", branch, "-m", subject)
    return base


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = init_repo(tmp_path / "repo")
    git(root, "remote", "add", "origin", f"https://github.com/{REPO}.git")
    commit(root, "README.md", "start\n", "initial commit", date="2026-01-01T00:00:00Z")
    return root


# --- what gets kept ---------------------------------------------------------


def test_a_merged_pr_is_recorded_with_its_base_and_its_size(repo: Path) -> None:
    base = landed_via_merge(
        repo,
        "Fix the handler (#11)",
        files={"src/pkg/handler.py": FIX},
        date="2026-01-02T00:00:00Z",
    )

    database = build_database(REPO, GitRepo.at(repo))

    assert len(database.records) == 1
    record = database.records[0]
    assert record.pr_number == 11
    assert record.style is MergeStyle.MERGE_COMMIT
    # The base is the mainline commit the work landed on — the commit a
    # patch for this change actually applies against.
    assert record.base_sha == base
    assert record.added == 4
    assert record.deleted == 0
    assert record.patch.source_loc == 4
    assert [change.path for change in record.patch.files] == ["src/pkg/handler.py"]


def test_the_walk_reports_one_entry_per_landed_pr(repo: Path) -> None:
    """Intra-branch commits never appear, however many there were."""
    landed_via_merge(
        repo,
        "Fix one (#11)",
        files={"src/a.py": FIX, "src/b.py": FIX, "src/c.py": FIX},
        date="2026-01-02T00:00:00Z",
    )
    landed_via_merge(
        repo, "Fix two (#12)", files={"src/d.py": FIX}, date="2026-01-03T00:00:00Z"
    )

    database = build_database(REPO, GitRepo.at(repo))

    assert [record.pr_number for record in database.records] == [12, 11]
    assert database.detail["mainline_points"] == 3  # two merges + the initial commit


def test_records_are_newest_first(repo: Path) -> None:
    for number in (11, 12, 13):
        landed_via_merge(
            repo,
            f"Fix {number} (#{number})",
            files={f"src/f{number}.py": FIX},
            date=f"2026-01-{number:02d}T00:00:00Z",
        )

    database = build_database(REPO, GitRepo.at(repo))
    assert [record.pr_number for record in database.records] == [13, 12, 11]


def test_a_squash_merge_is_kept_and_labelled(repo: Path) -> None:
    """One parent plus a `(#N)` suffix — what GitHub's squash button leaves."""
    commit(repo, "src/pkg/thing.py", FIX, "Fix the thing (#77)", date="2026-01-02T00:00:00Z")

    database = build_database(REPO, GitRepo.at(repo))

    assert len(database.records) == 1
    assert database.records[0].style is MergeStyle.SQUASH
    assert database.records[0].pr_number == 77


def test_closing_keywords_are_captured_when_the_message_has_them(repo: Path) -> None:
    commit(
        repo,
        "src/pkg/thing.py",
        FIX,
        "Fix the thing (#77)\n\nFixes #42",
        date="2026-01-02T00:00:00Z",
    )

    database = build_database(REPO, GitRepo.at(repo))
    assert database.records[0].closes_issues == (42,)


def test_no_closing_keyword_leaves_the_field_empty_rather_than_guessing(repo: Path) -> None:
    """The common case, and it is not a failure.

    Measured on `pallets/click`: 22 merges in a 60-day window, every one
    with a `(#N)` suffix and not one with a closing keyword. The issue
    linkage lives in GitHub's database, and recovering it is phase 2's job.
    """
    commit(repo, "src/pkg/thing.py", FIX, "Fix the thing (#77)", date="2026-01-02T00:00:00Z")

    database = build_database(REPO, GitRepo.at(repo))
    assert database.records[0].closes_issues == ()
    assert database.records[0].pr_number == 77


# --- the hard caps ----------------------------------------------------------


def test_a_one_line_edit_is_kept_by_default(repo: Path) -> None:
    """The floor is "nonempty". Small is scope, not proven triviality.

    The previous default of 3 source lines rejected `click#3739` — one line
    of fix, 71 lines of regression test — which is the most valuable shape
    a mined task can have. Recording the size lets ranking demote a version
    bump without a hard cap discarding real one-line fixes alongside it.
    """
    commit(repo, "src/pkg/version.py", "VERSION = 2\n", "Bump (#5)", date="2026-01-02T00:00:00Z")

    database = build_database(REPO, GitRepo.at(repo))

    assert len(database.records) == 1
    assert database.records[0].patch.source_loc == 1
    assert str(Rejection.DIFF_TOO_SMALL) not in database.dropped


def test_a_floor_can_still_be_imposed(repo: Path) -> None:
    commit(repo, "src/pkg/version.py", "VERSION = 2\n", "Bump (#5)", date="2026-01-02T00:00:00Z")

    database = build_database(REPO, GitRepo.at(repo), filters=CommitFilter(min_source_loc=3))

    assert database.records == []
    assert database.dropped[str(Rejection.DIFF_TOO_SMALL)] == 1


def test_a_huge_change_is_dropped_as_too_large(repo: Path) -> None:
    commit(
        repo,
        "src/pkg/big.py",
        "".join(f"line_{n} = {n}\n" for n in range(500)),
        "Rewrite everything (#6)",
        date="2026-01-02T00:00:00Z",
    )

    database = build_database(REPO, GitRepo.at(repo))

    assert database.records == []
    assert database.dropped[str(Rejection.DIFF_TOO_LARGE)] == 1


def test_the_caps_are_adjustable(repo: Path) -> None:
    commit(repo, "src/pkg/v.py", "VERSION = 2\n", "Bump (#5)", date="2026-01-02T00:00:00Z")

    strict = build_database(REPO, GitRepo.at(repo), filters=CommitFilter(min_source_loc=3))
    assert strict.records == []

    default = build_database(REPO, GitRepo.at(repo))
    assert len(default.records) == 1


def test_the_caps_read_source_lines_not_the_diff_total(repo: Path) -> None:
    """The click #3739 shape: a small fix with a large test alongside.

    Judged on the 300-line total this would be thrown out by the max cap;
    judged on the 4-line fix it is exactly the kind of candidate worth
    keeping. The totals are still recorded.
    """
    landed_via_merge(
        repo,
        "Fix the handler (#11)",
        files={
            "src/pkg/handler.py": FIX,
            "tests/test_handler.py": "".join(f"assert {n} == {n}\n" for n in range(300)),
            "CHANGES.md": "- fixed the handler\n",
        },
        date="2026-01-02T00:00:00Z",
    )

    database = build_database(REPO, GitRepo.at(repo), filters=CommitFilter(max_source_loc=50))

    assert len(database.records) == 1
    record = database.records[0]
    assert record.patch.source_loc == 4
    assert record.patch.test_loc == 300
    assert record.added == 305  # the total is recorded, just not what the cap reads
    kinds = {change.path: change.kind for change in record.patch.files}
    assert kinds["src/pkg/handler.py"] is PathKind.SOURCE
    assert kinds["tests/test_handler.py"] is PathKind.TEST
    assert kinds["CHANGES.md"] is PathKind.DOC


def test_a_test_only_change_is_dropped(repo: Path) -> None:
    commit(
        repo,
        "tests/test_thing.py",
        "assert True\nassert True\nassert True\nassert True\n",
        "Add tests (#7)",
        date="2026-01-02T00:00:00Z",
    )

    database = build_database(REPO, GitRepo.at(repo))
    assert database.dropped[str(Rejection.NO_SOURCE_CHANGE)] == 1


def test_a_docs_only_change_is_dropped(repo: Path) -> None:
    commit(repo, "docs/guide.md", "a\nb\nc\nd\n", "Improve docs (#8)", date="2026-01-02T00:00:00Z")

    database = build_database(REPO, GitRepo.at(repo))
    assert database.dropped[str(Rejection.NO_SOURCE_CHANGE)] == 1


def test_a_ci_only_change_is_dropped(repo: Path) -> None:
    commit(
        repo,
        ".github/workflows/ci.yaml",
        "on:\n  push:\n    branches:\n      - main\n",
        "Fix CI (#9)",
        date="2026-01-02T00:00:00Z",
    )

    database = build_database(REPO, GitRepo.at(repo))
    assert database.dropped[str(Rejection.NO_SOURCE_CHANGE)] == 1


def test_a_change_spread_over_many_files_is_dropped(repo: Path) -> None:
    landed_via_merge(
        repo,
        "Sweeping rename (#10)",
        files={f"src/f{n}.py": FIX for n in range(12)},
        date="2026-01-02T00:00:00Z",
    )

    database = build_database(REPO, GitRepo.at(repo))
    assert database.dropped[str(Rejection.TOO_MANY_SOURCE_FILES)] == 1


def test_unresolved_provenance_is_queued_for_enrichment_not_dropped(repo: Path) -> None:
    """One parent, no `(#N)`: a direct push, a rebase-merge, or an edited squash.

    These used to be dropped as `not_an_integration_point`, which silently
    discarded every rebase-merged PR — a rebase-merge preserves the
    author's subject and so never carries `(#N)`. A clone cannot tell the
    three apart (the reflog never travels with one), but
    `/commits/{sha}/pulls` can, so they are deferred rather than thrown
    away.
    """
    commit(repo, "src/pkg/thing.py", FIX, "quick fix, pushed straight to main", date="2026-01-02T00:00:00Z")

    database = build_database(REPO, GitRepo.at(repo))

    assert database.records == []
    assert len(database.needs_enrichment) == 1
    queued = database.needs_enrichment[0]
    assert queued.style is MergeStyle.UNKNOWN
    assert queued.pr_number is None
    # It was measured, so the enrichment pass can rank it without re-walking.
    assert queued.patch.source_loc == 4
    assert database.to_dict()["summary"]["needs_enrichment"] == 1


def test_a_queued_commit_still_has_to_clear_the_size_gates(repo: Path) -> None:
    """A docs-only direct push is not worth enriching either."""
    commit(repo, "docs/notes.md", "a\nb\nc\n", "tidy the docs", date="2026-01-02T00:00:00Z")

    database = build_database(REPO, GitRepo.at(repo))

    assert database.needs_enrichment == []
    assert database.dropped[str(Rejection.NO_SOURCE_CHANGE)] >= 1


def test_direct_pushes_can_be_kept_on_request(repo: Path) -> None:
    commit(repo, "src/pkg/thing.py", FIX, "quick fix straight to main", date="2026-01-02T00:00:00Z")

    database = build_database(
        REPO, GitRepo.at(repo), filters=CommitFilter(require_integration_point=False)
    )

    assert len(database.records) == 1
    assert database.records[0].style is MergeStyle.UNKNOWN
    assert database.records[0].pr_number is None


def test_the_most_fundamental_failure_is_reported_first(repo: Path) -> None:
    """A one-line test-only change reports the missing source, not the size.

    Telling someone to lower the size floor would never help a diff that
    contains no application code at all.
    """
    commit(repo, "tests/test_a.py", "assert True\n", "Tweak a test (#4)", date="2026-01-02T00:00:00Z")

    database = build_database(REPO, GitRepo.at(repo))
    assert database.dropped[str(Rejection.NO_SOURCE_CHANGE)] == 1


# --- walk bounds and bookkeeping --------------------------------------------


def test_the_walk_can_be_bounded_without_truncating_the_clone(repo: Path) -> None:
    from datetime import UTC, datetime

    commit(repo, "src/old.py", FIX, "Old fix (#1)", date="2020-01-02T00:00:00Z")
    commit(repo, "src/new.py", FIX, "New fix (#2)", date="2026-06-01T00:00:00Z")

    database = build_database(
        REPO, GitRepo.at(repo), since=datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert [record.pr_number for record in database.records] == [2]
    # The old commit is still in the clone — it was excluded from the walk,
    # not missing from history, so widening the window needs no re-clone.
    assert GitRepo.at(repo).has_commits()


def test_unresolved_commits_are_measured_so_enrichment_can_rank_them(repo: Path) -> None:
    """Both are measured now — one is kept, one is queued.

    An earlier version skipped the diff for anything without a PR number,
    which meant a queued commit arrived at the enrichment pass with no size
    information and had to be re-walked.
    """
    commit(repo, "src/a.py", FIX, "direct push, no pr number", date="2026-01-02T00:00:00Z")
    commit(repo, "src/b.py", FIX, "Proper fix (#11)", date="2026-01-03T00:00:00Z")

    database = build_database(REPO, GitRepo.at(repo))

    assert [record.pr_number for record in database.records] == [11]
    assert len(database.needs_enrichment) == 1
    assert database.detail["mainline_points"] == 3
    assert database.detail["commits_measured"] == 2


def test_the_summary_counts_styles_and_drops(repo: Path) -> None:
    landed_via_merge(repo, "Fix a (#11)", files={"src/a.py": FIX}, date="2026-01-02T00:00:00Z")
    commit(repo, "src/b.py", FIX, "Fix b (#12)", date="2026-01-03T00:00:00Z")
    commit(repo, "docs/c.md", "x\n", "Docs (#13)", date="2026-01-04T00:00:00Z")

    payload = build_database(REPO, GitRepo.at(repo)).to_dict()

    assert payload["summary"]["kept"] == 2
    assert payload["summary"]["by_style"] == {"squash": 1, "merge_commit": 1}
    assert payload["dropped_reasons"][str(Rejection.NO_SOURCE_CHANGE)] >= 1
    assert payload["repo"] == REPO
    assert payload["built_at"]


def test_a_record_serialises_everything_a_later_phase_needs(repo: Path) -> None:
    landed_via_merge(
        repo,
        "Fix the handler (#11)",
        files={"src/pkg/handler.py": FIX, "tests/test_handler.py": "assert True\n"},
        date="2026-01-02T00:00:00Z",
    )

    payload = build_database(REPO, GitRepo.at(repo)).to_dict()["commits"][0]

    assert set(payload) >= {
        "sha", "base_sha", "subject", "style", "pr_number", "closes_issues",
        "added", "deleted", "source_loc", "test_loc", "difficulty", "files",
    }
    assert payload["difficulty"] == "trivial"
    assert payload["files"][0]["kind"] in {"source", "test", "doc", "config"}
    assert len(payload["sha"]) == 40
    assert len(payload["base_sha"]) == 40


def test_an_empty_repo_produces_an_empty_database(tmp_path: Path) -> None:
    root = init_repo(tmp_path / "repo")
    database = build_database(REPO, GitRepo.at(root))
    assert database.records == []
    assert database.dropped == {}


def test_added_and_deleted_come_apart(repo: Path) -> None:
    """Lines added and lines deleted are reported separately, not summed."""
    base = commit(
        repo, "src/pkg/thing.py", "a = 1\nb = 2\nc = 3\nd = 4\n", "Seed (#1)", date="2026-01-02T00:00:00Z"
    )
    commit(repo, "src/pkg/thing.py", "a = 1\nz = 9\n", "Trim it (#2)", date="2026-01-03T00:00:00Z")

    stats = GitRepo.at(repo).diff_stats(base, "HEAD")

    assert stats is not None
    assert stats.files[0].added == 1  # z = 9
    assert stats.files[0].removed == 3  # b, c, d
    assert stats.source_loc == 4


# --- the pipeline -----------------------------------------------------------


def test_the_pipeline_is_registered(repo: Path) -> None:
    assert PIPELINES["history"] is HistoryPipeline
    assert HistoryPipeline.name == "history"


def test_the_pipeline_writes_the_database_to_disk(repo: Path, tmp_path: Path) -> None:
    landed_via_merge(repo, "Fix it (#11)", files={"src/a.py": FIX}, date="2026-01-02T00:00:00Z")

    pipeline = HistoryPipeline(repo, {"repo": REPO})
    result = pipeline.run(tmp_path / "out")

    payload = json.loads((tmp_path / "out" / "commits.json").read_text())
    assert payload["summary"]["kept"] == 1
    assert payload["commits"][0]["pr_number"] == 11
    # Phase 1 ships measured commits, not tasks, and says so rather than
    # overstating what it produced.
    assert result.tasks_emitted == 0
    assert result.detail["commits"] == 1
    assert result.detail["phase"].startswith("1")


def test_the_pipeline_passes_caps_through(repo: Path, tmp_path: Path) -> None:
    commit(repo, "src/a.py", "X = 1\n", "Bump (#5)", date="2026-01-02T00:00:00Z")

    default = HistoryPipeline(repo, {"repo": REPO})
    assert len(default.build().records) == 1

    strict = HistoryPipeline(repo, {"repo": REPO, "min_source_loc": 3})
    assert strict.build().records == []


def test_the_pipeline_needs_a_clone_or_a_repo_name(tmp_path: Path) -> None:
    pipeline = HistoryPipeline(tmp_path / "not-a-repo", {})
    with pytest.raises(RuntimeError, match="no repository to walk"):
        pipeline.build()


def test_a_record_with_no_files_reports_zero_rather_than_crashing() -> None:
    record = CommitRecord(sha="a" * 40, base_sha="b" * 40, subject="x", style=MergeStyle.SQUASH)
    assert record.added == 0
    assert record.deleted == 0
    assert record.to_dict()["files"] == []
