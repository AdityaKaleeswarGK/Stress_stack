"""The validator's measurement rules, with Docker faked at the subprocess boundary.

The fake container decides each test's verdict from the files actually mounted
into it, so patch application, hunk reversal and the state sequence are all
real; only the interpreter inside the container is simulated.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

from git_helpers import commit, git, init_repo

from fleet import validation
from fleet.validation import judge, split_hunks

BUGGY = "def add(a, b):\n    return a - b\n" + "\n" * 12 + "def mul(a, b):\n    return a + b\n"
FIXED = "def add(a, b):\n    return a + b\n" + "\n" * 12 + "def mul(a, b):\n    return a * b\n"


def runs(*states):
    return [dict(state) for state in states]


def test_preexisting_base_failure_is_recorded_not_fatal():
    base = {"guard": "pass", "broken": "fail"}
    before = {"guard": "pass", "broken": "fail", "bug": "fail"}
    after = {"guard": "pass", "broken": "fail", "bug": "pass"}
    measured = judge(base, runs(before, before), runs(after, after))
    assert measured["errors"] == []
    assert measured["fail_to_pass"] == ["bug"] and measured["pass_to_pass"] == ["guard"]
    assert measured["preexisting_failures"] == ["broken"] and measured["unresolved"] == ["broken"]


def test_unstable_tests_are_quarantined_and_only_fatal_when_nothing_is_left():
    base = {"guard": "pass", "flaky": "pass"}
    before = runs({"guard": "pass", "flaky": "pass", "bug": "fail"}, {"guard": "pass", "flaky": "fail", "bug": "fail"})
    after = runs({"guard": "pass", "flaky": "pass", "bug": "pass"}, {"guard": "pass", "flaky": "pass", "bug": "pass"})
    measured = judge(base, before, after)
    assert measured["errors"] == [] and measured["quarantined"] == ["flaky"]
    assert "flaky" not in measured["pass_to_pass"]

    shaky = runs({"guard": "pass", "bug": "fail"}, {"guard": "pass", "bug": "pass"})
    steady_after = runs({"guard": "pass", "bug": "pass"}, {"guard": "pass", "bug": "pass"})
    measured = judge({"guard": "pass"}, shaky, steady_after)
    assert measured["fail_to_pass"] == [] and "quarantined" in measured["errors"][0]


def test_a_test_missing_from_one_repeat_is_unstable():
    before = runs({"guard": "pass", "bug": "fail"}, {"guard": "pass"})
    after = runs({"guard": "pass", "bug": "pass"}, {"guard": "pass", "bug": "pass"})
    assert judge({"guard": "pass"}, before, after)["quarantined"] == ["bug"]


def test_could_not_run_before_is_never_fail_to_pass():
    base = {"guard": "pass"}
    before = {"guard": "pass", "tests.test_new": "error"}
    after = {"guard": "pass", "tests.test_new::test_feature": "pass"}
    measured = judge(base, runs(before, before), runs(after, after))
    assert measured["fail_to_pass"] == [] and measured["absent_before"] == ["tests.test_new::test_feature"]
    assert "absent-before" in measured["errors"][0]


def test_regressions_vanishing_and_skipping_guards_are_fatal():
    base = {"a": "pass", "b": "pass", "c": "pass"}
    before = {"a": "pass", "b": "pass", "c": "pass", "bug": "fail"}
    after = {"a": "fail", "c": "skip", "bug": "pass"}
    measured = judge(base, runs(before, before), runs(after, after))
    assert measured["pass_to_fail"] == ["a"] and measured["vanished"] == ["b"] and measured["pass_to_skip"] == ["c"]
    assert len(measured["errors"]) == 4  # three of those, plus no guard left


def test_split_hunks_yields_independently_revertible_patches(tmp_path):
    repo = init_repo(tmp_path / "repo")
    base = commit(repo, "src/pkg/calc.py", BUGGY, "base", date="2024-01-01T00:00:00Z")
    fixed = commit(repo, "src/pkg/calc.py", FIXED, "fix", date="2024-01-02T00:00:00Z")
    patch = subprocess.run(["git", "-C", str(repo), "diff", "--binary", base, fixed], capture_output=True).stdout
    hunks = split_hunks(patch)
    assert [(h["path"], h["index"]) for h in hunks] == [("src/pkg/calc.py", 0), ("src/pkg/calc.py", 1)]
    # Reverting only the second hunk restores `mul` and keeps the `add` fix.
    reverted = subprocess.run(["git", "-C", str(repo), "apply", "-R", "-"], input=hunks[1]["patch"], capture_output=True)
    assert reverted.returncode == 0
    text = (repo / "src/pkg/calc.py").read_text()
    assert "return a + b\n" in text.split("def mul")[0] and "return a + b" in text.split("def mul")[1]


class FakeDocker:
    """Answers the docker CLI; runs everything else (git) for real."""

    def __init__(self):
        self.dockerfiles, self.launches, self.flaky_calls = [], [], 0

    def __call__(self, args, *, timeout=600, **kwargs):
        if args[0] != "docker":
            return subprocess.run(args, capture_output=True, timeout=timeout, **kwargs)
        verb = args[1:3]
        if verb == ["info", "--format"]:
            return SimpleNamespace(returncode=0, stdout=b"27.0", stderr=b"")
        if verb == ["image", "inspect"]:
            if args[3].startswith("fleet-validation:"):
                if "--format" in args:
                    return SimpleNamespace(returncode=0, stdout=b"sha256:" + b"b" * 64, stderr=b"")
                return SimpleNamespace(returncode=1, stdout=b"", stderr=b"")
            data = [{"Id": "sha256:" + "a" * 64, "RepoDigests": ["python@sha256:" + "c" * 64]}]
            return SimpleNamespace(returncode=0, stdout=json.dumps(data).encode(), stderr=b"")
        if args[1] == "build":
            self.dockerfiles.append((Path(args[-1]) / "Dockerfile").read_text())
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if args[1] == "run":
            self.launches.append(args)
            return self.container(args)
        raise AssertionError(f"unexpected docker call: {args}")

    def container(self, args):
        mounts = dict(m.split(",dst=")[::-1] for m in (a.removeprefix("type=bind,src=") for a in args if a.startswith("type=bind")))
        checkout, results = Path(mounts["/workspace"]), Path(mounts["/results"])
        code = (checkout / "src/pkg/calc.py").read_text()
        add_fixed = "return a + b" in code.split("def mul")[0]
        tests = {"tests.test_guard::test_guard": "pass", "tests.test_guard::test_broken": "fail"}
        if (checkout / "tests/test_calc.py").is_file():
            tests["tests.test_calc::test_add"] = "pass" if add_fixed else "fail"
        if (checkout / "tests/test_guard.py").is_file():
            self.flaky_calls += 1
            tests["tests.test_guard::test_flaky"] = "pass" if self.flaky_calls % 3 else "fail"
        cases = []
        for key, status in tests.items():
            classname, name = key.split("::")
            body = '<failure message="AssertionError: assert 1 == 2">E   assert 1 == 2</failure>' if status == "fail" else ""
            cases.append(f'<testcase classname="{classname}" name="{name}">{body}</testcase>')
        (results / "junit.xml").write_text("<testsuite>" + "".join(cases) + "</testsuite>")
        failed = any(status != "pass" for status in tests.values())
        return SimpleNamespace(returncode=1 if failed else 0, stdout=b"", stderr=b"")


def candidate_repo(tmp_path):
    repo = init_repo(tmp_path / "repo")
    commit(repo, "tests/test_guard.py", "def test_guard(): pass\n", "guard", date="2023-12-31T00:00:00Z")
    base = commit(repo, "src/pkg/calc.py", BUGGY, "base", date="2024-01-01T00:00:00Z")
    (repo / "tests/test_calc.py").write_text("from pkg.calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    git(repo, "add", "tests/test_calc.py")
    fixed = commit(repo, "src/pkg/calc.py", FIXED, "fix (#7)", date="2024-01-02T00:00:00Z")
    candidate = {"id": fixed, "repo": "owner/name", "base_sha": base, "fixed_sha": fixed,
                 "status": "ready_for_review", "issues": [{"state": "closed", "body": "add is wrong"}],
                 "patch": {"files": [{"path": "src/pkg/calc.py"}, {"path": "tests/test_calc.py"}]}}
    return repo, candidate


def test_validate_end_to_end_with_repeats_quarantine_pinning_and_hunk_probe(tmp_path, monkeypatch):
    repo, candidate = candidate_repo(tmp_path)
    docker = FakeDocker()
    monkeypatch.setattr(validation, "run", docker)
    recipe = {"image": "python:3.12-slim", "install_command": "pip install -e .", "test_paths": ["tests"]}
    result = validation.validate(candidate, repo, tmp_path / "out", recipe, repeats=3)

    assert result["status"] == "verified_checks", result["errors"]
    assert result["fail_to_pass"] == ["tests.test_calc::test_add"]
    assert result["pass_to_pass"] == ["tests.test_guard::test_guard"]
    assert result["quarantined"] == ["tests.test_guard::test_flaky"]
    assert result["preexisting_failures"] == ["tests.test_guard::test_broken"]
    assert result["fail_to_pass_kinds"] == {"tests.test_calc::test_add": "assertion"}
    # base + 3 before + 3 after + 2 hunk probes.
    assert len(docker.launches) == 9
    assert all("--continue-on-collection-errors" in launch for launch in docker.launches)
    # The `mul` change is in the fix but no test checks it.
    assert result["adequacy"]["killed"] == ["src/pkg/calc.py#0"]
    assert result["adequacy"]["survived"] == ["src/pkg/calc.py#1"]
    assert set(result["labels"]) >= {"quarantined", "preexisting_failures", "untested_hunks"}
    # Dependencies resolve as of the base commit, and only during the install.
    assert result["index_cutoff"] == "2024-01-01T00:00:00Z"
    run_line = next(line for line in docker.dockerfiles[0].splitlines() if line.startswith("RUN "))
    assert "UV_EXCLUDE_NEWER=2024-01-01T00:00:00Z" in run_line and "PIP_UPLOADED_PRIOR_TO=2024-01-01T00:00:00Z" in run_line
    assert json.loads((tmp_path / "out" / "validation.json").read_text())["status"] == "verified_checks"


def test_harness_exit_codes_are_not_test_results(tmp_path, monkeypatch):
    repo, candidate = candidate_repo(tmp_path)
    docker = FakeDocker()
    docker.container = lambda args: SimpleNamespace(returncode=2, stdout=b"", stderr=b"interrupted")
    monkeypatch.setattr(validation, "run", docker)
    recipe = {"image": "python:3.12-slim", "install_command": "pip install -e .", "test_paths": ["tests"]}
    result = validation.validate(candidate, repo, tmp_path / "out", recipe)
    assert result["status"] == "not_verified" and "harness failure" in result["errors"][0]


def test_date_pinning_can_be_switched_off(tmp_path, monkeypatch):
    repo, candidate = candidate_repo(tmp_path)
    docker = FakeDocker()
    monkeypatch.setattr(validation, "run", docker)
    recipe = {"image": "python:3.12-slim", "install_command": "pip install -e .", "test_paths": ["tests"],
              "pin_index_date": False}
    result = validation.validate(candidate, repo, tmp_path / "out", recipe, repeats=2)
    assert result["index_cutoff"] is None and "UV_EXCLUDE_NEWER" not in docker.dockerfiles[0]


def test_gvisor_is_chosen_when_docker_offers_it(monkeypatch):
    offered = lambda runtimes: (lambda args, **kw: SimpleNamespace(
        returncode=0, stdout=json.dumps({name: {} for name in runtimes}).encode(), stderr=b""))
    monkeypatch.delenv("FLEET_SANDBOX_RUNTIME", raising=False)
    monkeypatch.setattr(validation, "run", offered(["runc", "runsc"]))
    assert validation.sandbox_runtime() == "runsc"
    monkeypatch.setattr(validation, "run", offered(["runc"]))
    assert validation.sandbox_runtime() == "runc"
    # An explicit choice wins, including asking for gVisor where it is absent:
    # the launch then fails rather than quietly using the weaker runtime.
    monkeypatch.setenv("FLEET_SANDBOX_RUNTIME", "runsc")
    assert validation.sandbox_runtime() == "runsc"
    monkeypatch.setenv("FLEET_SANDBOX_RUNTIME", "runc")
    monkeypatch.setattr(validation, "run", offered(["runc", "runsc"]))
    assert validation.sandbox_runtime() == "runc"


def test_runs_use_the_chosen_runtime_and_check_out_inside_the_workspace(tmp_path, monkeypatch):
    repo, candidate = candidate_repo(tmp_path)
    docker = FakeDocker()
    monkeypatch.setattr(validation, "run", docker)
    monkeypatch.setenv("FLEET_SANDBOX_RUNTIME", "runsc")
    out = tmp_path / "out"
    recipe = {"image": "python:3.12-slim", "install_command": "pip install -e .", "test_paths": ["tests"]}
    result = validation.validate(candidate, repo, out, recipe, repeats=2)
    assert result["sandbox_runtime"] == "runsc"
    assert all("--runtime=runsc" in launch for launch in docker.launches)
    # Every mounted checkout lives under the evidence folder, never $TMPDIR,
    # because Colima shares only the home folder with its VM.
    sources = [a.split(",dst=")[0].removeprefix("type=bind,src=") for launch in docker.launches
               for a in launch if a.endswith("dst=/workspace")]
    assert sources and all(Path(source).is_relative_to(out) for source in sources)
    assert not list(out.glob(".work-*"))  # cleaned up afterwards
