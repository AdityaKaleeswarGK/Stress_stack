"""Initial filtering over real Git diffs; no sandbox or provider calls."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from click.testing import CliRunner
from git_helpers import commit, git, init_repo

from fleet.cli import app
from fleet.history.filtering import filter_history
from fleet.history.window import GitRepo


@pytest.fixture
def repo(tmp_path):
    root = init_repo(tmp_path / "repo")
    git(root, "remote", "add", "origin", "https://github.com/o/r.git")
    commit(root, "README.md", "Project\n", "Initial", date="2026-01-01T00:00:00Z")
    return root


def change(repo, path, text, message):
    return commit(repo, path, text, message, date="2026-02-01T00:00:00Z")


def test_source_only_and_source_with_tests_survive_noise(repo):
    # A deletion/replacement counts both sides, rather than the net growth.
    change(repo, "src/app.py", "old = 1\nold_again = 2\n", "Seed (#1)")
    change(repo, "src/app.py", "fixed = 3\n", "Fix (#2)\n\nFixes #42")
    for path, text in {
        "src/model.py": "X = 1\n",
        "tests/test_model.py": "assert True\nassert True\n",
        "CHANGES.md": "Fixed model\n",
        ".github/workflows/ci.yml": "name: ci\n",
    }.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Fix model (#3)")
    change(repo, "tests/test_extra.py", "assert True\n", "Tests only (#4)")
    change(repo, "docs/test_examples.py", "example = 1\n", "Docs only (#5)")
    change(repo, ".github/workflows/ci.yml", "name: new-ci\n", "CI only (#6)")

    result = filter_history("o/r", GitRepo.at(repo), limit=2)

    assert [c["pr_number"] for c in result["candidates"]] == [3, 2]
    assert result["summary"]["stop_reason"] == "candidate_limit"
    assert result["summary"]["commits_measured"] == 5
    assert result["rejection_reasons"] == {"no_source_change": 3}
    assert {r["pr_number"] for r in result["rejected"]} == {4, 5, 6}
    mixed, source_only = result["candidates"]
    assert mixed["file_count"] == 4 and mixed["loc_changed"] == 5
    assert mixed["by_kind"]["test"]["loc_changed"] == 2
    assert mixed["by_kind"]["source"]["file_count"] == 1
    assert mixed["by_kind"]["doc"]["file_count"] == 1
    assert mixed["by_kind"]["config"]["file_count"] == 1
    assert {f["kind"] for f in mixed["files"]} == {"source", "test", "doc", "config"}
    assert source_only["added"] == 1 and source_only["deleted"] == 2
    assert source_only["loc_changed"] == 3 and not source_only["has_test_change"]
    assert source_only["closes_issues"] == [42]
    assert source_only["issue_linkage"] == "not_verified"
    assert source_only["pr_verification"] == "not_run"
    assert all(c["validation"] == "not_run" for c in result["candidates"])


def test_source_filename_containing_test_is_not_discarded(repo):
    change(repo, "src/click/testing.py", "X = 1\n", "Fix helper")
    result = filter_history("o/r", GitRepo.at(repo))
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["pr_number"] is None
    assert result["candidates"][0]["files"][0]["kind"] == "source"


def test_no_line_cap_or_required_test_patch(repo):
    change(repo, "src/app.py", "".join(f"X{i} = {i}\n" for i in range(501)), "Fix (#1)")
    result = filter_history("o/r", GitRepo.at(repo))
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["source_loc"] == 501
    assert result["filters"]["max_source_loc"] is None


@pytest.mark.parametrize("options,expected,measured", [
    ({"max_commits": 2}, "commit_limit", 2),
    ({"max_changes": 2}, "change_limit", 2),
    ({"limit": 2}, "candidate_limit", 2),
    ({}, "history_exhausted", 4),
])
def test_stopping_limits_are_separate(repo, options, expected, measured):
    for i in range(4):
        change(repo, f"src/app{i}.py", "X = 1\n", f"Fix (#{i + 1})")
    result = filter_history("o/r", GitRepo.at(repo), **options)
    assert result["summary"]["stop_reason"] == expected
    assert result["summary"]["commits_measured"] == measured
    assert len(result["candidates"]) == measured


def test_rejected_changes_consume_inspection_budget_not_candidate_slots(repo):
    change(repo, "src/app.py", "X = 1\n", "Older fix (#1)")
    for i in range(3):
        change(repo, f"tests/test_{i}.py", "assert True\n", f"Tests (#{i + 2})")
    result = filter_history("o/r", GitRepo.at(repo), max_changes=2, limit=1)
    assert result["candidates"] == []
    assert result["summary"]["stop_reason"] == "change_limit"
    assert result["summary"]["dropped"] == 2
    assert result["summary"]["remaining_in_window"] == 3


def test_file_cap_remains_independent_of_line_counts(repo):
    for i in range(11):
        (repo / f"app{i}.py").write_text("X = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Broad fix (#1)")
    result = filter_history("o/r", GitRepo.at(repo))
    assert result["candidates"] == []
    assert result["rejection_reasons"]["too_many_source_files"] == 1
    assert len(filter_history("o/r", GitRepo.at(repo), max_source_files=11)["candidates"]) == 1


def test_unavailable_base_is_reported_without_fabricating_a_diff(tmp_path):
    root = init_repo(tmp_path / "root-only")
    change(root, "app.py", "X = 1\n", "Root fix (#1)")
    result = filter_history("o/r", GitRepo.at(root))
    assert result["candidates"] == []
    assert result["rejected"][0]["reason"] == "base_before_graft"
    assert result["rejected"][0]["patch"] is None


@pytest.mark.parametrize("options", [{"max_commits": 0}, {"max_changes": 0}, {"limit": 0}, {"max_source_files": 0},
    {"since": datetime(2026, 2, 1, tzinfo=UTC), "until": datetime(2026, 1, 1, tzinfo=UTC)}])
def test_invalid_limits_fail_explicitly(repo, options):
    with pytest.raises(ValueError):
        filter_history("o/r", GitRepo.at(repo), **options)


def test_cli_saves_initial_report_and_preserves_checkout(repo, tmp_path, monkeypatch):
    head = change(repo, "app.py", "X = 1\n", "Fix (#1)")
    (repo / "app.py").write_text("uncommitted content\n")
    before = git(repo, "status", "--porcelain")

    def forbidden(*args, **kwargs):
        raise AssertionError("Initial filtering must not enrich or investigate runtime")

    for name in ("GitHubClient", "build_catalog", "run_review", "validate"):
        monkeypatch.setattr(f"fleet.commands.{name}", forbidden)
    directory = tmp_path / "filtered"
    output = tmp_path / "copy.json"
    result = CliRunner().invoke(app, ["filter", "o/r", "--repo-path", str(repo),
        "--work-dir", str(directory), "--out", str(output)])
    assert result.exit_code == 0, result.output
    report = json.loads((directory / "filtered_candidates.json").read_text())
    assert report == json.loads(output.read_text())
    assert report["ref"] == head
    assert report["limits"] == {"max_commits": 200, "max_changes": 100, "candidates": 25}
    assert report["candidates"][0]["added"] == 1
    assert "runtime validation not run" in result.output
    assert not (directory / "candidates.json").exists()
    assert git(repo, "status", "--porcelain") == before
    assert git(repo, "rev-parse", "HEAD") == head


def test_mine_default_no_longer_rejects_large_changes(repo, tmp_path):
    change(repo, "app.py", "".join(f"X{i} = {i}\n" for i in range(501)), "Fix (#1)")
    directory = tmp_path / "mined"
    result = CliRunner().invoke(app, ["mine", "o/r", "--repo-path", str(repo),
        "--work-dir", str(directory), "--offline"])
    assert result.exit_code == 0, result.output
    report = json.loads((directory / "commits.json").read_text())
    assert report["summary"]["kept"] == 1


def test_static_noise_excluded_but_code_and_unknown_edits_retained(repo):
    change(repo, 'app.py', '"old docs"\nx = 1\n', 'Seed')
    doc = change(repo, 'app.py', '"new docs"\nx = 1\n', 'Docstring')
    comment = change(repo, 'app.py', '"new docs"\nx = 1  # note\n', 'Comment')
    fix = change(repo, 'app.py', '"new docs"\nx = 2\n', 'Real fix')
    change(repo, '_version.py', '__version__ = "1"\n', 'Seed version')
    version = change(repo, '_version.py', '__version__ = "2"\n', 'Release')
    unknown = change(repo, 'app.js', 'throw new Error("not executed");\n', 'Unsupported language')
    result = filter_history('o/r', GitRepo.at(repo))
    selected = {c['sha'] for c in result['candidates']}
    rejected = {c['sha']: c['reason'] for c in result['rejected']}
    assert fix in selected and unknown in selected
    assert rejected[doc] == rejected[comment] == 'no_executable_source_change'
    assert rejected[version] == 'version_only_source_change'
