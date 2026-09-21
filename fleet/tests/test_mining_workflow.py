from __future__ import annotations

import json
from pathlib import Path
import subprocess

from click.testing import CliRunner
import pytest

from fleet.agent import OpenRouter, review
from fleet.cli import app
from fleet.history.client import GitHubClient, Response
from fleet.history.health import commit_health, summarize
from fleet.history.linkage import pr_number_from_subject
from fleet.history.patch import PathKind, classify_path
from fleet.validation import compare, junit
from fleet.workspace import check_identity, git, prepare, repository_name


@pytest.mark.parametrize("value", ["mahmoud/glom", "https://github.com/mahmoud/glom.git", "https://github.com/mahmoud/glom/", "git@github.com:mahmoud/glom.git"])
def test_repository_url_identity(value):
    assert repository_name(value) == "mahmoud/glom"


@pytest.mark.parametrize("value", ["https://evil.example/o/r", "https://github.com/o/r/pull/1", "o/../r", "--upload-pack=x/r", "https://token@github.com/o/r", "https://github.com/o/r?x=y"])
def test_reject_ambiguous_or_non_github_targets(value):
    with pytest.raises(ValueError):
        repository_name(value)


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    git(root, "config", "user.name", "Fleet test")
    git(root, "config", "user.email", "fleet@example.invalid")
    git(root, "remote", "add", "origin", "https://github.com/o/r.git")
    (root / "a.py").write_text("X = 1\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "initial")
    base = git(root, "rev-parse", "HEAD")
    (root / "a.py").write_text("X = 2\n")
    git(root, "commit", "-qam", "Fix (#1)")
    return root, base, git(root, "rev-parse", "HEAD")


def test_wrong_local_repository_is_rejected(repository, tmp_path):
    with pytest.raises(ValueError, match="origin o/r"):
        prepare("mahmoud/glom", tmp_path / "out", repository[0])


def test_cli_uses_requested_workspace_not_current_git(repository, monkeypatch, tmp_path):
    root, _, _ = repository
    calls = []
    def prepared(repo, directory, local, **kwargs):
        calls.append((repo, directory, local))
        return root
    monkeypatch.setattr("fleet.commands.prepare", prepared)
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        subprocess.run(["git", "init", "-q"], check=True)
        result = runner.invoke(app, ["mine", "https://github.com/o/r.git", "--offline"])
        assert result.exit_code == 0, result.output
        assert calls[0][0] == "o/r" and calls[0][2] is None
        assert calls[0][1] == Path.cwd() / ".fleet/o--r"
        manifest = json.loads(Path(".fleet/o--r/commits.json").read_text())
        assert manifest["summary"]["kept"] == 1


def test_standard_merge_and_noise_classification():
    assert pr_number_from_subject("Merge pull request #196 from mahmoud/arg-mode") == 196
    assert classify_path("LICENSE") == PathKind.DOC
    assert classify_path("codecov.yml") == PathKind.CONFIG
    assert classify_path("glom/core.py") == PathKind.SOURCE


@pytest.mark.parametrize("checks,statuses,complete,expected", [
    ([], [], True, "missing"),
    ([], [], False, "unknown"),
    ([{"status": "completed", "conclusion": "success"}], [], True, "reported_success"),
    ([{"status": "completed", "conclusion": "skipped"}], [], True, "unknown"),
    ([{"status": "in_progress", "conclusion": None}], [], True, "pending"),
    ([], [{"state": "failure"}], False, "reported_failure"),
    ([{"status": "completed", "conclusion": "success"}], [], False, "unknown"),
])
def test_ci_evidence_is_not_assumed_green(checks, statuses, complete, expected):
    assert summarize(checks, statuses, complete) == expected


def test_health_preserves_exact_sha_and_partial_api_failure():
    def transport(url, headers):
        if "check-runs" in url:
            return Response(200, json.dumps({"check_runs": [{"head_sha": "wrong", "status": "completed", "conclusion": "success"}]}).encode(), {})
        return Response(404, b"{}", {})
    report = commit_health(GitHubClient(transport=transport), "o/r", "a" * 40)
    assert report["state"] == "unknown"
    assert not report["complete"] and report["errors"]


def test_ci_cache_can_be_explicitly_refreshed(tmp_path):
    calls = []
    def transport(url, headers):
        calls.append(url)
        return Response(200, b"{}", {})
    old = GitHubClient(cache_dir=tmp_path, transport=transport)
    old.get("/example")
    old.get("/example")
    GitHubClient(cache_dir=tmp_path, transport=transport, max_cache_age=0).get("/example")
    assert len(calls) == 2


@pytest.mark.parametrize("after", [{"bug": "pass", "guard": "skip"}, {"bug": "pass"}, {"bug": "skip", "guard": "pass"}])
def test_skipped_or_disappearing_tests_cannot_validate(after):
    assert compare({"bug": "fail", "guard": "pass"}, after)["errors"]


def test_collection_error_is_not_fail_to_pass():
    measured = compare({"bug": "error", "guard": "pass"}, {"bug": "pass", "guard": "pass"})
    assert measured["errors"] and not measured["fail_to_pass"]


def test_real_f2p_and_regression_guard():
    measured = compare({"bug": "fail", "guard": "pass"}, {"bug": "pass", "guard": "pass"})
    assert measured == {"fail_to_pass": ["bug"], "pass_to_pass": ["guard"], "errors": []}


def test_junit_rejects_missing_duplicate_and_symlink_reports(tmp_path):
    with pytest.raises(ValueError):
        junit(tmp_path / "missing.xml")
    target = tmp_path / "report.xml"
    target.write_text('<testsuite><testcase name="x"/><testcase name="x"/></testsuite>')
    with pytest.raises(ValueError, match="Duplicate"):
        junit(target)
    link = tmp_path / "link.xml"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="unsafe"):
        junit(link)


def completion(name, args):
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}], "usage": {"total_tokens": 3}}


def test_agent_observation_loop_and_transcript(repository, tmp_path):
    root, base, fixed = repository
    candidate = {"id": fixed, "base_sha": base, "fixed_sha": fixed, "status": "ready_for_review", "subject": "Fix", "issues": []}
    outputs = iter([completion("inspect_candidate", {"id": fixed}), completion("read_patch", {"id": fixed}),
                    completion("finish", {"decisions": [{"id": fixed, "decision": "select", "reason": "Reviewed the supplied patch and evidence"}]})])
    class FakeProvider:
        model = "test/tool-model"
        def complete(self, messages, tools):
            if len(messages) > 2:
                assert messages[-1]["role"] == "tool"
            return next(outputs)
    result = review({"clone": str(root), "candidates": [candidate]}, tmp_path, FakeProvider())
    assert result["status"] == "reviewed"
    assert not result["validations"]  # selection does not manufacture runtime evidence
    assert json.loads((tmp_path / "agent.json").read_text())["steps"][1]["actions"][0]["tool"] == "read_patch"


def test_agent_cannot_read_outside_snapshot(repository, tmp_path):
    root, base, fixed = repository
    class FakeProvider:
        model = "test/tool-model"
        def complete(self, messages, tools):
            return completion("read_file", {"id": fixed, "snapshot": "base", "path": "../../secret"})
    report = review({"clone": str(root), "candidates": [{"id": fixed, "base_sha": base, "fixed_sha": fixed, "subject": "x", "status": "needs_enrichment"}]},
                    tmp_path, FakeProvider(), max_steps=1)
    assert report["status"] == "incomplete"
    assert "repository-relative" in report["steps"][0]["actions"][0]["observation"]


def test_openrouter_protocol_and_key_not_in_payload(monkeypatch):
    observed = {}
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"}}]}).encode()
    def urlopen(request, timeout):
        observed["payload"] = json.loads(request.data)
        observed["authorization"] = request.headers["Authorization"]
        return Reply()
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    OpenRouter("test/model", key="test-secret").complete([{"role": "user", "content": "hi"}], [{"type": "function"}])
    assert observed["authorization"] == "Bearer test-secret"
    assert "test-secret" not in json.dumps(observed["payload"])
    assert observed["payload"]["tools"]
