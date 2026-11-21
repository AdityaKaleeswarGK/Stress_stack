"""Which commits share an environment image, decided from dependency declarations."""
from __future__ import annotations

import json
from types import SimpleNamespace

from git_helpers import commit, git, init_repo

from fleet import validation
from fleet.history.eras import assign, era_key, summarise

PYPROJECT = '[project]\nname = "pkg"\ndependencies = ["attrs>=21"]\n\n[tool.ruff]\nline-length = 88\n'


def head(repo):
    return git(repo, "rev-parse", "HEAD")


def test_tool_settings_do_not_start_an_era_but_dependencies_do(tmp_path):
    repo = init_repo(tmp_path / "repo")
    commit(repo, "pyproject.toml", PYPROJECT, "base", date="2024-01-01T00:00:00Z")
    first = head(repo)
    commit(repo, "pyproject.toml", PYPROJECT.replace("88", "100"), "ruff", date="2024-01-02T00:00:00Z")
    ruff = head(repo)
    commit(repo, "src/pkg/a.py", "A = 1\n", "code", date="2024-01-03T00:00:00Z")
    code = head(repo)
    commit(repo, "pyproject.toml", PYPROJECT.replace("attrs>=21", "attrs>=23"), "bump", date="2024-01-04T00:00:00Z")
    bump = head(repo)
    keys = {sha: era_key(repo, sha)[0] for sha in (first, ruff, code, bump)}
    assert keys[first] == keys[ruff] == keys[code] != keys[bump]


def test_an_era_is_represented_by_its_newest_commit(tmp_path):
    repo = init_repo(tmp_path / "repo")
    commit(repo, "requirements.txt", "attrs==21.4\n", "base", date="2024-01-01T00:00:00Z")
    old = head(repo)
    commit(repo, "src/pkg/a.py", "A = 1\n", "code", date="2024-03-05T12:00:00Z")
    new = head(repo)
    eras = assign(repo, [old, new], resolve_python=False)
    era = eras[old]
    assert eras[new] is era and era["commits"] == [old, new]
    assert era["representative"] == new and era["cutoff"] == "2024-03-05T12:00:00Z"
    assert era["resolution"] == "date_pinned" and era["manifests"] == ["requirements.txt"]


def test_lockfiles_make_an_era_locked_and_side_environments_are_ignored(tmp_path):
    repo = init_repo(tmp_path / "repo")
    commit(repo, "uv.lock", "version = 1\n", "lock", date="2024-01-01T00:00:00Z")
    commit(repo, "requirements/dev.txt", "pytest\n", "dev reqs", date="2024-01-02T00:00:00Z")
    before = head(repo)
    # A requirements file deep in the tree describes a side environment
    # (pytest's testing/plugins_integration/), not the main suite.
    commit(repo, "testing/plugins/requirements.txt", "anyio==4\n", "dependabot", date="2024-01-03T00:00:00Z")
    after = head(repo)
    eras = assign(repo, [before, after], resolve_python=False)
    assert eras[before] is eras[after]
    assert eras[before]["resolution"] == "locked" and eras[before]["lockfiles"] == ["uv.lock"]
    assert eras[before]["manifests"] == ["requirements/dev.txt", "uv.lock"]


def test_reverting_a_dependency_change_returns_to_the_earlier_era(tmp_path):
    repo = init_repo(tmp_path / "repo")
    commit(repo, "requirements.txt", "attrs==21.4\n", "a", date="2024-01-01T00:00:00Z")
    a = head(repo)
    commit(repo, "requirements.txt", "attrs==23.1\n", "b", date="2024-01-02T00:00:00Z")
    b = head(repo)
    commit(repo, "requirements.txt", "attrs==21.4\n", "revert", date="2024-01-03T00:00:00Z")
    c = head(repo)
    eras = assign(repo, [a, b, c], resolve_python=False)
    assert eras[a] is eras[c] is not eras[b]
    assert summarise(eras) == {"commits": 3, "eras": 2, "locked_eras": 0, "largest_era": 2,
                               "single_commit_eras": 1, "pythons": ["None"]}


def test_an_unparseable_pyproject_falls_back_to_the_whole_file(tmp_path):
    repo = init_repo(tmp_path / "repo")
    commit(repo, "pyproject.toml", "[project\nbroken", "a", date="2024-01-01T00:00:00Z")
    a = head(repo)
    commit(repo, "pyproject.toml", "[project\nbroken!", "b", date="2024-01-02T00:00:00Z")
    assert era_key(repo, a)[0] != era_key(repo, head(repo))[0]


class CachingDocker:
    """A docker CLI stand-in that remembers which image tags it has built."""

    def __init__(self):
        self.built, self.builds = set(), 0

    def __call__(self, args, *, timeout=600, **kwargs):
        if args[0] != "docker":
            import subprocess
            return subprocess.run(args, capture_output=True, timeout=timeout, **kwargs)
        if args[1:3] == ["info", "--format"]:
            return SimpleNamespace(returncode=0, stdout=b"27", stderr=b"")
        if args[1:3] == ["image", "inspect"]:
            tag = args[3]
            if tag.startswith("fleet-validation:"):
                if tag not in self.built:
                    return SimpleNamespace(returncode=1, stdout=b"", stderr=b"")
                return SimpleNamespace(returncode=0, stdout=b"sha256:" + b"b" * 64, stderr=b"")
            data = [{"Id": "sha256:" + "a" * 64, "RepoDigests": ["python@sha256:" + "c" * 64]}]
            return SimpleNamespace(returncode=0, stdout=json.dumps(data).encode(), stderr=b"")
        if args[1] == "build":
            self.builds += 1
            self.built.add(args[args.index("--tag") + 1])
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if args[1] == "run":
            results = next(a.split(",dst=")[0].removeprefix("type=bind,src=") for a in args if a.endswith("dst=/results"))
            from pathlib import Path
            Path(results, "junit.xml").write_text('<testsuite><testcase classname="t" name="ok"/></testsuite>')
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        raise AssertionError(args)


def test_candidates_in_one_era_build_one_image(tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "repo")
    commit(repo, "requirements.txt", "attrs\n", "deps", date="2024-01-01T00:00:00Z")
    commit(repo, "tests/test_m.py", "def test_guard(): pass\n", "suite", date="2024-01-01T01:00:00Z")
    candidates = []
    for n in range(2):
        base = commit(repo, f"src/pkg/m{n}.py", "X = 1\n", f"base {n}", date=f"2024-01-0{2 + 2 * n}T00:00:00Z")
        (repo / "tests/test_m.py").write_text(f"def test_guard(): pass\ndef test_{n}(): pass\n")
        git(repo, "add", "tests/test_m.py")
        fixed = commit(repo, f"src/pkg/m{n}.py", "X = 2\n", f"fix {n}", date=f"2024-01-0{3 + 2 * n}T00:00:00Z")
        candidates.append({"id": fixed, "repo": "o/r", "base_sha": base, "fixed_sha": fixed,
                           "status": "ready_for_review", "issues": [{"state": "closed", "body": "x"}],
                           "patch": {"files": [{"path": f"src/pkg/m{n}.py"}, {"path": "tests/test_m.py"}]}})
    docker = CachingDocker()
    monkeypatch.setattr(validation, "run", docker)
    eras = assign(repo, [c["base_sha"] for c in candidates], resolve_python=False)
    recipe = {"image": "python:3.12-slim", "install_command": "pip install -r requirements.txt", "test_paths": ["tests"]}
    results = [validation.validate(c, repo, tmp_path / c["id"], recipe, repeats=2, era=eras[c["base_sha"]])
               for c in candidates]
    assert docker.builds == 1
    assert [r["environment_cache_hit"] for r in results] == [False, True]
    assert results[0]["era"]["key"] == results[1]["era"]["key"] and results[0]["era"]["commits"] == 2


def test_an_era_that_does_not_contain_the_base_is_refused(tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "repo")
    commit(repo, "requirements.txt", "attrs\n", "deps", date="2024-01-01T00:00:00Z")
    commit(repo, "tests/test_m.py", "def test_guard(): pass\n", "suite", date="2024-01-01T01:00:00Z")
    other = assign(repo, [head(repo)], resolve_python=False)[head(repo)]
    base = commit(repo, "src/pkg/m.py", "X = 1\n", "base", date="2024-01-02T00:00:00Z")
    (repo / "tests/test_m.py").write_text("def test_guard(): pass\ndef test(): pass\n")
    git(repo, "add", "tests/test_m.py")
    fixed = commit(repo, "src/pkg/m.py", "X = 2\n", "fix", date="2024-01-04T00:00:00Z")
    candidate = {"id": fixed, "repo": "o/r", "base_sha": base, "fixed_sha": fixed, "status": "ready_for_review",
                 "issues": [{"state": "closed", "body": "x"}],
                 "patch": {"files": [{"path": "src/pkg/m.py"}, {"path": "tests/test_m.py"}]}}
    monkeypatch.setattr(validation, "run", CachingDocker())
    recipe = {"image": "python:3.12-slim", "install_command": "pip install .", "test_paths": ["tests"]}
    result = validation.validate(candidate, repo, tmp_path / "out", recipe, era=other)
    assert "does not contain" in result["errors"][0]
