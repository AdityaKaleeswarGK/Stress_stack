"""The post-sandbox review: free triage, the packet, the code checks and the agent loop."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fleet import review
from git_helpers import git, init_repo

JUNIT = """<?xml version="1.0"?><testsuites><testsuite>
<testcase classname="tests.test_thing" name="test_parse_strict">
<failure message="AttributeError: module has no attribute 'parse_strict'">def test_parse_strict():
&gt;       assert thing.parse_strict("1") == 1
E   AttributeError: module 'pkg.thing' has no attribute 'parse_strict'
tests/test_thing.py:4: AttributeError</failure></testcase>
<testcase classname="tests.test_thing" name="test_old" />
</testsuite></testsuites>"""


@pytest.fixture
def change(tmp_path: Path):
    root = init_repo(tmp_path / "repo")
    (root / "src/pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "README.md").write_text("Thing parses values.\n")
    (root / "src/pkg/thing.py").write_text("def parse(value):\n    return int(value)\n")
    (root / "tests/test_thing.py").write_text("from pkg import thing\n\ndef test_old():\n    assert thing.parse('1') == 1\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "base", date="2024-01-01T00:00:00")
    base = git(root, "rev-parse", "HEAD")
    (root / "src/pkg/thing.py").write_text(
        "def parse(value):\n    return int(value)\n\n\ndef parse_strict(value, *, allow_sign=False):\n"
        "    if not allow_sign and value.startswith(('-', '+')):\n        raise ValueError(value)\n    return int(value)\n")
    (root / "tests/test_thing.py").write_text(
        "from pkg import thing\n\ndef test_old():\n    assert thing.parse('1') == 1\n\n"
        "def test_parse_strict():\n    assert thing.parse_strict('1') == 1\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Add a strict parser that rejects signs", date="2024-01-02T00:00:00")
    fixed = git(root, "rev-parse", "HEAD")
    evidence = tmp_path / "evidence"
    (evidence / "before-1").mkdir(parents=True)
    (evidence / "before-1" / "junit.xml").write_text(JUNIT)
    validation = {"fail_to_pass": ["tests.test_thing::test_parse_strict"], "pass_to_pass": ["tests.test_thing::test_old"],
                  "pass_to_fail": [], "vanished": [], "pass_to_skip": [], "labels": [],
                  "fail_to_pass_kinds": {"tests.test_thing::test_parse_strict": "exception"}, "errors": []}
    candidate = {"id": fixed, "base_sha": base, "fixed_sha": fixed}
    return root, candidate, validation, evidence


def test_triage_decides_obvious_cases_without_a_model():
    assert review.triage({"errors": ["Environment build failed"]})["route"] == "environment"
    assert review.triage({"fail_to_pass": [], "errors": ["no stable F2P"]})["route"] == "drop"
    broken = review.triage({"fail_to_pass": ["a"], "pass_to_fail": ["b"]})
    assert broken == {"route": "drop", "reason": "fix_breaks_other_tests", "tests": ["b"]}
    assert review.triage({"fail_to_pass": ["a"], "pass_to_fail": [], "vanished": []})["route"] == "review"


def test_packet_keeps_error_lines_and_code_facts(change):
    root, candidate, validation, evidence = change
    packet, facts = review.build_packet(candidate, validation, root, evidence)
    shown = packet["sandbox"]["fail_to_pass"][0]
    assert shown["error_lines"] == ["AttributeError: module 'pkg.thing' has no attribute 'parse_strict'"]
    # The traceback is left out of the packet but kept for the test_failure tool.
    assert "tests/test_thing.py:4" in facts["details"][shown["test"]]["full"]
    assert "parse_strict" in packet["code_facts"]["names_created_by_fix"]
    assert "allow_sign" in packet["code_facts"]["names_created_by_fix"]
    assert packet["code_facts"]["created_names_used_by_tests"] == ["parse_strict"]
    assert packet["commit_message"] == "Add a strict parser that rejects signs"


def _verdict(**overrides):
    verdict = {"decision": "keep", "reason": "The tests call a new strict parser.", "kind": "feature",
               "failure_cause": "missing_interface",
               "required_interface": [{"name": "parse_strict", "description": "parse_strict(value, *, allow_sign=False) -> int"}],
               "tests_check_whole_fix": "partly", "unchecked_changes": "allow_sign is not tested",
               "title": "Add a strict integer parser",
               "statement": "The package has no way to parse an integer while rejecting a leading sign. Add "
                            "pkg.thing.parse_strict(value, *, allow_sign=False) that returns the integer and raises "
                            "ValueError for '-1' or '+1' unless allow_sign is true."}
    verdict.update(overrides)
    return verdict


def test_checks_accept_a_clean_verdict(change):
    root, candidate, validation, evidence = change
    _, facts = review.build_packet(candidate, validation, root, evidence)
    facts["code_facts"] = review.code_facts(root, candidate["base_sha"], candidate["fixed_sha"], facts["source"], facts["test_diff"])
    assert review.check_verdict(_verdict(), facts) == []


def test_checks_catch_leaks_and_invented_names(change):
    root, candidate, validation, evidence = change
    _, facts = review.build_packet(candidate, validation, root, evidence)
    facts["code_facts"] = review.code_facts(root, candidate["base_sha"], candidate["fixed_sha"], facts["source"], facts["test_diff"])
    leaky = _verdict(statement=_verdict()["statement"] + " See test_parse_strict in tests/test_thing.py: "
                     "if not allow_sign and value.startswith(('-', '+')):")
    problems = review.check_verdict(leaky, facts)
    assert any("names fail-to-pass tests" in p for p in problems)
    assert any("test files" in p for p in problems)
    assert any("quotes 1 line" in p for p in problems)
    invented = review.check_verdict(_verdict(required_interface=[{"name": "parse_loose", "description": "x"}]), facts)
    assert any("'parse_loose' is not created by the fix" in p for p in invented)
    unstated = review.check_verdict(_verdict(statement="Parsing should reject signs. " * 5), facts)
    assert any("must mention the required name 'parse_strict'" in p for p in unstated)
    empty = review.check_verdict(_verdict(required_interface=[]), facts)
    assert any("required_interface is empty" in p for p in empty)


class ScriptedClient:
    """Plays back tool calls, so the loop is tested without a model."""

    model = "scripted"

    def __init__(self, calls):
        self.calls, self.seen = list(calls), []

    def complete(self, messages, tools, max_tokens=0):
        self.seen.append(messages[-1])
        name, args = self.calls.pop(0)
        return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{len(self.seen)}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 60}, "cost": 0.001}}


def test_loop_explores_and_retries_a_rejected_verdict(change, tmp_path):
    root, candidate, validation, evidence = change
    knowledge = review.Knowledge(root, tmp_path / "cache")
    client = ScriptedClient([
        ("outline", {"version": "fixed", "path": "src/pkg/thing.py"}),
        ("read_lines", {"version": "base", "path": "src/pkg/thing.py", "start": 1, "end": 500}),
        ("finish", _verdict(statement=_verdict()["statement"] + " Like test_parse_strict expects.")),
        ("finish", _verdict()),
    ])
    record = review.review_candidate(candidate, validation, root, evidence, client, knowledge,
                                     knowledge.summary(candidate["base_sha"]))
    assert record["status"] == "reviewed"
    assert record["verdict"]["kind"] == "feature"
    assert len(record["check_failures"]) == 1
    outline_result = client.seen[1]["content"]
    assert "5-8 def parse_strict" in outline_result
    assert client.seen[2]["content"].startswith("src/pkg/thing.py lines 1-2 of 2")
    assert "Verdict not accepted" in client.seen[3]["content"]
    assert record["usage"] == {"prompt_tokens": 400, "completion_tokens": 40, "cached_tokens": 240,
                               "cost": pytest.approx(0.004), "calls": 4, "reasoning_tokens": 0}
    # Outlines are cached by blob id, so the same file version is outlined once.
    assert len(list((tmp_path / "cache" / "outline").glob("*.json"))) == 1


def test_tools_refuse_paths_outside_the_repository(change, tmp_path):
    root, candidate, validation, evidence = change
    knowledge = review.Knowledge(root, tmp_path / "cache")
    with pytest.raises(ValueError):
        review._run_tool("read_lines", {"version": "base", "path": "../etc/passwd", "start": 1, "end": 2},
                         candidate, root, knowledge, {})


def test_cache_marks_the_fixed_prefix_only_when_asked(change, tmp_path):
    root, candidate, validation, evidence = change
    knowledge = review.Knowledge(root, tmp_path / "cache")
    summary = knowledge.summary(candidate["base_sha"])
    for cache in (False, True):
        client = ScriptedClient([("finish", _verdict())])
        sent = []
        original = client.complete
        client.complete = lambda messages, tools, max_tokens=0: (sent.append([dict(m) for m in messages]), original(messages, tools, max_tokens))[1]
        review.review_candidate(candidate, validation, root, evidence, client, knowledge, summary, cache=cache)
        first = sent[0]
        marked = [m for m in first if isinstance(m["content"], list) and any("cache_control" in p for p in m["content"])]
        assert len(marked) == (2 if cache else 0)
        assert first[0]["role"] == "system"
