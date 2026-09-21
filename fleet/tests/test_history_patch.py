"""Patch shape: path classification, numstat parsing, and the size gates.

The path classifier gets the most attention here because it is the part
that quietly decides everything downstream: a source file misfiled as a
test makes a candidate look like it has no fix in it, and a test misfiled
as source makes a test-only PR look like a real one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from git_helpers import commit, init_repo

from fleet.history.discover import SelectionPolicy
from fleet.history.models import Rejection
from fleet.history.patch import (
    FileChange,
    PathKind,
    PatchStats,
    classify_path,
    difficulty_bucket,
    parse_numstat,
)
from fleet.history.window import GitRepo


# --- path classification ----------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # source
        ("src/click/core.py", PathKind.SOURCE),
        ("lib/parser.go", PathKind.SOURCE),
        ("main.py", PathKind.SOURCE),
        # tests, by directory
        ("tests/test_core.py", PathKind.TEST),
        ("test/helpers.py", PathKind.TEST),
        ("src/__tests__/button.tsx", PathKind.TEST),
        ("e2e/checkout.ts", PathKind.TEST),
        # tests, by filename
        ("pkg/thing_test.go", PathKind.TEST),
        ("src/button.test.tsx", PathKind.TEST),
        ("src/button.spec.js", PathKind.TEST),
        ("conftest.py", PathKind.TEST),
        # docs
        ("README.md", PathKind.DOC),
        ("CHANGES.md", PathKind.DOC),
        ("docs/api.rst", PathKind.DOC),
        ("examples/complete/app.py", PathKind.DOC),
        # config
        (".github/workflows/ci.yaml", PathKind.CONFIG),
        ("pyproject.toml", PathKind.CONFIG),
        ("uv.lock", PathKind.CONFIG),
        ("Dockerfile", PathKind.CONFIG),
    ],
)
def test_paths_are_classified_by_role(path: str, expected: PathKind) -> None:
    assert classify_path(path) is expected


@pytest.mark.parametrize(
    "path",
    [
        "src/click/testing.py",  # a *testing helper*, shipped as library code
        "src/latest/loader.py",  # contains "test" as a substring of "latest"
        "src/contest/rules.py",  # ditto, "contest"
    ],
)
def test_source_files_whose_names_merely_contain_test_are_not_tests(path: str) -> None:
    """The bug substring matching causes, and the reason this matches components.

    SWE-bench classifies on a substring of the whole path, and Repo2RLEnv
    inherits it — so `src/click/testing.py` becomes a test file, its change
    lands in `test_patch` instead of `patch`, and a real one-file fix looks
    like a PR with no source change at all.
    """
    assert classify_path(path) is PathKind.SOURCE


def test_docs_win_over_tests() -> None:
    """`docs/testing.md` is documentation, whatever its name suggests."""
    assert classify_path("docs/testing.md") is PathKind.DOC
    assert classify_path("docs/test_examples.py") is PathKind.DOC


def test_classification_is_case_insensitive_and_survives_odd_input() -> None:
    assert classify_path("Tests/Test_Core.py") is PathKind.TEST
    assert classify_path("DOCS/Api.md") is PathKind.DOC
    assert classify_path("") is PathKind.SOURCE
    assert classify_path("/") is PathKind.SOURCE


# --- numstat parsing --------------------------------------------------------


def test_parses_the_real_click_3739_diff() -> None:
    """The shape that motivates measuring source lines rather than the total.

    One line of fix, 71 lines of test, 5 lines of changelog. Judged on its
    78-line total this looks medium-sized; it is a one-line fix, which is
    the most valuable shape a mined task can have.
    """
    stats = parse_numstat(
        "5\t0\tCHANGES.md\n"
        "1\t1\tsrc/click/_termui_impl.py\n"
        "71\t0\ttests/test_termui.py\n"
    )

    assert stats.file_count == 3
    assert stats.loc_changed == 78
    assert stats.source_loc == 2  # 1 added + 1 removed
    assert stats.test_loc == 71
    assert [change.path for change in stats.source_files] == ["src/click/_termui_impl.py"]
    assert stats.has_test_change is True
    assert difficulty_bucket(stats) == "trivial"


def test_binary_files_are_recorded_not_dropped() -> None:
    """A dash means binary. Dropping the row would read as an empty diff."""
    stats = parse_numstat("-\t-\tassets/logo.png\n4\t2\tsrc/app.py\n")

    assert stats.file_count == 2
    assert stats.touches_binary is True
    assert stats.loc_changed == 6  # the binary contributes no lines
    assert stats.source_loc == 6


def test_renames_keep_the_destination_path() -> None:
    """Git compresses renames; the destination is where the change lands."""
    stats = parse_numstat("3\t1\tsrc/{old => new}/thing.py\n")
    assert [change.path for change in stats.files] == ["src/new/thing.py"]

    flat = parse_numstat("3\t1\tsrc/old.py => src/new.py\n")
    assert [change.path for change in flat.files] == ["src/new.py"]


def test_empty_and_malformed_output_parse_to_nothing() -> None:
    assert parse_numstat("").files == ()
    assert parse_numstat("\n\n").files == ()
    assert parse_numstat("not a numstat line").files == ()


@pytest.mark.parametrize(
    ("source_loc", "expected"),
    [(1, "trivial"), (5, "trivial"), (6, "small"), (20, "small"), (21, "medium"), (80, "medium"), (81, "large")],
)
def test_difficulty_buckets_read_the_fix_not_the_diff(source_loc: int, expected: str) -> None:
    stats = PatchStats(
        files=(
            FileChange(path="src/a.py", added=source_loc, removed=0, kind=PathKind.SOURCE),
            # A huge test alongside must not inflate the bucket.
            FileChange(path="tests/test_a.py", added=500, removed=0, kind=PathKind.TEST),
        )
    )
    assert difficulty_bucket(stats) == expected


# --- the size gates ---------------------------------------------------------


def source(added: int, removed: int = 0, path: str = "src/a.py") -> FileChange:
    return FileChange(path=path, added=added, removed=removed, kind=PathKind.SOURCE)


def test_a_one_line_fix_is_accepted_by_default() -> None:
    """The floor is "nonempty", not 3.

    A floor of 3 rejected `click#3739` — a one-line fix with a 71-line
    regression test, which is the best shape a mined task can have. A
    one-line *replacement* measures 2 (one added, one removed), so even a
    floor of 3 caught it. Size is scope; ranking can use it, but a hard cap
    should not discard a fix for being small.
    """
    policy = SelectionPolicy()
    assert policy.patch_rejection(PatchStats(files=(source(1),))) is None
    assert policy.patch_rejection(PatchStats(files=(source(1, 1),))) is None
    # An empty source change is still nothing to fix.
    assert policy.patch_rejection(PatchStats(files=())) is Rejection.NO_SOURCE_CHANGE


def test_the_floor_is_still_configurable_upward() -> None:
    strict = SelectionPolicy(min_source_loc=5)
    assert strict.patch_rejection(PatchStats(files=(source(2),))) is Rejection.DIFF_TOO_SMALL
    assert strict.patch_rejection(PatchStats(files=(source(5),))) is None


def test_a_huge_diff_is_rejected_as_too_large() -> None:
    policy = SelectionPolicy()
    assert policy.patch_rejection(PatchStats(files=(source(401),))) is Rejection.DIFF_TOO_LARGE
    assert policy.patch_rejection(PatchStats(files=(source(400),))) is None


def test_the_size_gates_ignore_test_and_doc_lines() -> None:
    """The click #3739 shape again, this time through the gates.

    A 2-line fix with a 71-line test must pass the floor on its own merit
    and not be pushed over it by the test — and a 2-line fix must not be
    *saved* by a large test either. Here the source is 4 lines, so it
    passes; the 900 lines of test and changelog alongside are irrelevant.
    """
    stats = PatchStats(
        files=(
            source(2, 2),
            FileChange(path="tests/test_a.py", added=800, removed=0, kind=PathKind.TEST),
            FileChange(path="CHANGES.md", added=100, removed=0, kind=PathKind.DOC),
        )
    )
    assert stats.loc_changed == 904
    assert stats.source_loc == 4
    assert SelectionPolicy().patch_rejection(stats) is None


def test_a_test_only_diff_has_nothing_to_fix() -> None:
    stats = PatchStats(
        files=(FileChange(path="tests/test_a.py", added=40, removed=0, kind=PathKind.TEST),)
    )
    assert SelectionPolicy().patch_rejection(stats) is Rejection.NO_SOURCE_CHANGE


def test_a_docs_only_diff_has_nothing_to_fix() -> None:
    stats = PatchStats(files=(FileChange(path="README.md", added=40, removed=0, kind=PathKind.DOC),))
    assert SelectionPolicy().patch_rejection(stats) is Rejection.NO_SOURCE_CHANGE


def test_a_ci_only_diff_has_nothing_to_fix() -> None:
    stats = PatchStats(
        files=(
            FileChange(path=".github/workflows/ci.yaml", added=40, removed=0, kind=PathKind.CONFIG),
        )
    )
    assert SelectionPolicy().patch_rejection(stats) is Rejection.NO_SOURCE_CHANGE


def test_a_fix_spread_over_many_files_is_rejected() -> None:
    stats = PatchStats(files=tuple(source(5, path=f"src/f{n}.py") for n in range(11)))
    assert SelectionPolicy().patch_rejection(stats) is Rejection.TOO_MANY_SOURCE_FILES
    assert SelectionPolicy(max_source_files=20).patch_rejection(stats) is None


def test_requiring_a_test_change_is_available_but_off_by_default() -> None:
    """Phase B's gate, exposed now and recorded now, enforced later.

    A fail-to-pass oracle needs a test change; a diff-similarity task does
    not. Phase A shouldn't pre-judge which kind of task a candidate becomes,
    so the fact is recorded on every candidate and the gate stays optional.
    """
    stats = PatchStats(files=(source(10),))
    assert stats.has_test_change is False
    assert SelectionPolicy().patch_rejection(stats) is None
    assert SelectionPolicy(require_test_change=True).patch_rejection(stats) is Rejection.NO_TEST_CHANGE


def test_gate_order_reports_the_most_fundamental_failure_first() -> None:
    """A test-only diff that is also tiny reports the missing source change.

    `diff_too_small` on a diff with no source at all would send someone to
    lower the size floor, which would never help.
    """
    stats = PatchStats(
        files=(FileChange(path="tests/test_a.py", added=1, removed=0, kind=PathKind.TEST),)
    )
    assert SelectionPolicy().patch_rejection(stats) is Rejection.NO_SOURCE_CHANGE


# --- against a real clone ---------------------------------------------------


def test_diff_stats_come_off_a_real_repo(tmp_path: Path) -> None:
    root = init_repo(tmp_path / "repo")
    base = commit(root, "src/app.py", "a = 1\n", "initial", date="2026-01-01T00:00:00Z")
    head = commit(
        root,
        "src/app.py",
        "a = 1\nb = 2\nc = 3\n",
        "extend it",
        date="2026-01-02T00:00:00Z",
    )
    commit(root, "tests/test_app.py", "assert True\n", "test it", date="2026-01-03T00:00:00Z")

    stats = GitRepo.at(root).diff_stats(base, head)

    assert stats is not None
    assert stats.source_loc == 2
    assert stats.file_count == 1
    assert stats.has_test_change is False


def test_an_unresolvable_diff_returns_none_rather_than_empty(tmp_path: Path) -> None:
    """None and "no changes" must stay distinguishable.

    An empty `PatchStats` would be indistinguishable from a real empty
    diff, and the caller needs to reject with `diff_unavailable` instead of
    silently treating a missing base commit as a no-op change.
    """
    root = init_repo(tmp_path / "repo")
    commit(root, "src/app.py", "a = 1\n", "initial", date="2026-01-01T00:00:00Z")
    assert GitRepo.at(root).diff_stats("0" * 40, "HEAD") is None
