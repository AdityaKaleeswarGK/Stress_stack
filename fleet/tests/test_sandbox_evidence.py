"""Regressions for what the sandbox measures and what it reports as evidence."""
from __future__ import annotations

from git_helpers import commit, git, init_repo

from fleet.history.catalog import patch_id
from fleet.runtime_review import RuntimeInvestigator, transitions
from fleet.validation import failure_kinds, source_roots


def test_unmeasured_original_does_not_label_every_test_as_new():
    base = {"guard": {"status": "pass"}, "bug": {"status": "fail"}}
    fixed = {key: {"status": "pass"} for key in base}
    result = transitions(None, base, fixed)
    assert result["fail_to_pass"] == ["bug"] and result["pass_to_pass"] == ["guard"]
    # Without a run of the base's own tests, "newly collected" is unknowable.
    assert "newly_collected_pass_on_both" not in result
    assert "newly_collected_fail_to_pass" not in result


def test_investigator_reports_no_new_test_claims_and_uses_native_platform(tmp_path, monkeypatch):
    base, fixed = tmp_path / "base", tmp_path / "fixed"
    base.mkdir(), fixed.mkdir()
    runner = RuntimeInvestigator(base=base, fixed=fixed, image_id="sha256:" + "a" * 64,
                                 directory=tmp_path / "output")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        raise OSError("docker is not needed for this test")

    monkeypatch.setattr("fleet.runtime_review.subprocess.run", fake_run)
    observation = runner({"purpose": "probe", "code": "def test_ok(): pass",
                          "existing_tests": [], "repeat": False})
    assert "newly_collected_pass_on_both" not in observation["comparison"]
    launch = commands[0]
    assert "--platform" not in launch
    assert "--init" in launch and "--memory-swap=1g" in launch and "--network=none" in launch


def test_failure_kinds_separate_missing_code_from_wrong_behaviour(tmp_path):
    report = tmp_path / "junit.xml"
    report.write_text(
        "<testsuite>"
        '<testcase classname="t" name="wrong"><failure message="assert 1 == 2">E   assert 1 == 2</failure></testcase>'
        '<testcase classname="t" name="absent"><failure message="ImportError: cannot import name \'new\'">tb</failure></testcase>'
        '<testcase classname="t" name="raised"><failure message="KeyError: \'x\'">tb</failure></testcase>'
        '<testcase classname="t" name="fine"/>'
        "</testsuite>"
    )
    assert failure_kinds(report) == {"t::wrong": "assertion", "t::absent": "load_error", "t::raised": "exception"}


def test_src_layout_outranks_the_installed_copy(tmp_path):
    assert source_roots(tmp_path) == ["/workspace"]
    (tmp_path / "src").mkdir()
    assert source_roots(tmp_path) == ["/workspace/src", "/workspace"]


def test_squash_onto_a_moved_base_is_the_same_patch(tmp_path):
    repo = init_repo(tmp_path / "repo")
    body = "".join(f"line {n}\n" for n in range(40))
    commit(repo, "src/pkg/thing.py", body, "initial", date="2024-01-01T00:00:00Z")
    fork = git(repo, "rev-parse", "HEAD")

    # The PR branch changes the end of the file.
    git(repo, "checkout", "--quiet", "-b", "feature")
    pr_head = commit(repo, "src/pkg/thing.py", body + "fixed\n", "fix", date="2024-01-02T00:00:00Z")

    # Meanwhile main changes the start of the same file, so the squash lands
    # with different blob hashes and shifted hunk offsets.
    git(repo, "checkout", "--quiet", "main")
    moved = commit(repo, "src/pkg/thing.py", "header\n" + body, "unrelated", date="2024-01-03T00:00:00Z")
    squash = commit(repo, "src/pkg/thing.py", "header\n" + body + "fixed\n", "fix (#1)",
                    date="2024-01-04T00:00:00Z")

    as_text = lambda a, b: git(repo, "diff", "--no-color", a, b)
    assert as_text(fork, pr_head) != as_text(moved, squash)
    assert patch_id(repo, fork, pr_head) == patch_id(repo, moved, squash)
    # A genuinely different change is still told apart.
    assert patch_id(repo, fork, pr_head) != patch_id(repo, fork, moved)
