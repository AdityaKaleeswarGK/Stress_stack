"""Reproduce the glom history audit; leaves Fleet's production code unchanged."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from fleet.history.client import GitHubClient
from fleet.history.discover import SelectionPolicy, discover_via_api, issue_from_payload
from fleet.history.linkage import parse_timeline, resolve_fix_pr
from fleet.history.window import GitRepo

HERE = Path(__file__).resolve().parent


def save(name, value):
    (HERE / name).write_text(json.dumps(value, indent=2, default=str) + "\n")


def git(clone, *args, **kwargs):
    return subprocess.run(["git", "-C", str(clone), *args], capture_output=True,
                          check=True, **kwargs).stdout


def local(clone):
    database = json.loads((HERE / "commits.json").read_text())
    records = database["commits"] + database["needs_enrichment"]
    head = git(clone, "rev-parse", "HEAD").decode().strip()
    failures = []
    for item in records:
        sha, base = item["sha"], item["base_sha"]
        parents = git(clone, "show", "-s", "--format=%P", sha).decode().split()
        if not parents or base != parents[0]:
            failures.append({"sha": sha, "error": "base is not first parent"})
        git(clone, "merge-base", "--is-ancestor", sha, head)
        rows = git(clone, "diff", "--numstat", base, sha).decode().splitlines()
        added = removed = 0
        for row in rows:
            a, r, _ = row.split("\t", 2)
            if a != "-":
                added += int(a)
                removed += int(r)
        if (added, removed) != (item["added"], item["deleted"]):
            failures.append({"sha": sha, "error": "diff totals mismatch"})

    # Take diverse deterministic cases: recent and old, merge and squash,
    # small edits and deferred provenance. Reconstruct full trees in a private
    # Git index, without changing the clone's checkout or its normal index.
    samples = database["commits"][:4] + database["commits"][-3:]
    samples += [r for r in database["commits"] if r["style"] == "merge_commit"][:3]
    samples += [r for r in database["commits"] if r["source_loc"] <= 2][:2]
    samples += database["needs_enrichment"][:3]
    samples = list({r["sha"]: r for r in samples}.values())
    replay = []
    with tempfile.TemporaryDirectory(prefix="fleet-glom-index-") as tmp:
        for n, item in enumerate(samples):
            sha, base = item["sha"], item["base_sha"]
            env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / f"index-{n}")}
            patch = git(clone, "diff", "--binary", "--full-index", base, sha)
            git(clone, "read-tree", base, env=env)
            git(clone, "apply", "--cached", "--binary", "-", input=patch, env=env)
            actual = git(clone, "write-tree", env=env).decode().strip()
            expected = git(clone, "rev-parse", f"{sha}^{{tree}}").decode().strip()
            replay.append({"sha": sha, "base_sha": base, "subject": item["subject"],
                           "tree_matches": actual == expected,
                           "patch_sha256": hashlib.sha256(patch).hexdigest()})

    missing_prs = []
    for r in records:
        match = re.match(r"Merge pull request #(\d+)\b", r["subject"])
        if match and r["pr_number"] is None:
            missing_prs.append({"sha": r["sha"], "expected_pr": int(match[1]),
                                "subject": r["subject"]})
    report = {
        "repo": "mahmoud/glom", "head_sha": head,
        "recorded_at": datetime.now(UTC).isoformat(),
        "total_reachable_commits": int(git(clone, "rev-list", "--count", head)),
        "first_parent_commits": int(git(clone, "rev-list", "--first-parent", "--count", head)),
        "database_summary": database["summary"],
        "accounting_matches": sum(database["summary"][k] for k in ("kept", "dropped", "needs_enrichment")) == database["summary"]["mainline_points"],
        "records_checked": len(records), "record_failures": failures,
        "patch_replays": replay, "missing_standard_merge_pr_numbers": missing_prs,
        "tiny_kept": [{"sha": r["sha"], "pr": r["pr_number"], "source_loc": r["source_loc"]}
                      for r in database["commits"] if r["source_loc"] <= 2],
        "test_change_counts": {key: sum(r["test_loc"] > 0 for r in database[key])
                               for key in ("commits", "needs_enrichment")},
        "production_sha256": {str(p.relative_to(HERE.parents[1])): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in (HERE.parents[1] / "fleet/src/fleet/history").glob("*.py")},
    }
    save("local_verification.json", report)
    print(json.dumps({k: report[k] for k in ("head_sha", "records_checked", "accounting_matches", "record_failures", "test_change_counts")}, indent=2))
    print(f"Patch replay: {sum(x['tree_matches'] for x in replay)}/{len(replay)} exact tree matches")
    print(f"Standard merge messages with missing PR numbers: {len(missing_prs)}")


def api(clone):
    client = GitHubClient(cache_dir=HERE / "api_cache")
    report = discover_via_api(
        "mahmoud/glom", since=datetime(2023, 1, 1, tzinfo=UTC),
        until=datetime(2026, 9, 13, tzinfo=UTC), client=client,
        policy=SelectionPolicy(max_candidates=8), clone=GitRepo.at(clone),
    )
    save("api_discovery.json", report.to_dict())
    print(json.dumps(report.to_dict()["summary"], indent=2))
    print("Rejections:", report.rejections)

    focused = discover_via_api(
        "mahmoud/glom", since=datetime(2026, 6, 1, tzinfo=UTC),
        until=datetime(2026, 7, 1, tzinfo=UTC), client=client,
        policy=SelectionPolicy(max_candidates=8), clone=GitRepo.at(clone),
    )
    save("api_discovery_focused.json", focused.to_dict())
    print("June 2026 focused discovery:", focused.to_dict()["summary"], focused.rejections)

    examples = []
    for number in (249, 299):
        issue = client.get(f"/repos/mahmoud/glom/issues/{number}").json()
        events = client.issue_timeline("mahmoud/glom", number)
        links = parse_timeline(events, repo="mahmoud/glom")
        resolution = resolve_fix_pr(issue_from_payload(issue), links)
        closing_prs = None
        if links.closed_by_commit:
            closing_prs = client.get(f"/repos/mahmoud/glom/commits/{links.closed_by_commit}/pulls").json()
        examples.append({"issue": issue, "events": events, "parsed": asdict(links),
                         "resolution": asdict(resolution), "usable": resolution.usable,
                         "closing_commit_prs": closing_prs})
    save("linkage_examples.json", examples)
    for number in (298, 196, 281):
        save(f"pr_{number}.json", client.pull_request("mahmoud/glom", number))
    print(f"Public API calls: {client.calls_made}, cache hits: {client.cache_hits}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("local", "api"))
    parser.add_argument("clone", type=Path)
    args = parser.parse_args()
    (local if args.mode == "local" else api)(args.clone)
