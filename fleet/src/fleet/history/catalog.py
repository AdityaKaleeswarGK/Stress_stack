"""Enrich a bounded batch without losing the unprocessed history inventory."""
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import json
import urllib.request

from fleet.history.client import GitHubClient, GitHubError
from fleet.history.health import commit_health
from fleet.history.linkage import closed_issue_numbers
from fleet.workspace import git


def closing_issues(client: GitHubClient, repo: str, number: int) -> list[dict] | None:
    """Structured closing references, including UI links, when authenticated.

    These describe intended closing relationships, not necessarily the causal
    closure event. Preserve that distinction in the candidate record.
    """
    if not client.token:
        return None
    owner, name = repo.split("/")
    query = """query($owner:String!, $name:String!, $number:Int!, $after:String) {
      repository(owner:$owner,name:$name) { pullRequest(number:$number) {
        closingIssuesReferences(first:100,after:$after) {
          nodes { number title body url state closedAt createdAt updatedAt repository { nameWithOwner } }
          pageInfo { hasNextPage endCursor }
        }
      }}
    }"""
    rows, after = [], None
    for _ in range(10):
        payload = {"query": query, "variables": {"owner": owner, "name": name, "number": number, "after": after}}
        request = urllib.request.Request("https://api.github.com/graphql", json.dumps(payload).encode(),
                    {"Authorization": f"Bearer {client.token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=40) as response:
            data = json.load(response)
        if data.get("errors"):
            raise GitHubError("GitHub GraphQL could not return closing references")
        connection = data["data"]["repository"]["pullRequest"]["closingIssuesReferences"]
        rows.extend(connection["nodes"])
        page = connection["pageInfo"]
        if not page["hasNextPage"]:
            return rows
        after = page["endCursor"]
    raise GitHubError("Closing-reference pagination incomplete")


def build_catalog(database: dict, root, client: GitHubClient | None, *, limit: int = 5,
                  health: bool = True, only_pr: int | None = None) -> dict:
    repo = database["repo"]
    records = database["commits"] + database.get("needs_enrichment", [])
    # Explicit audit targeting is separate from the ordinary ranking budget.
    if only_pr is not None:
        records = [r for r in records if r.get("pr_number") == only_pr]
    records = sorted(records, key=lambda r: (r.get("test_loc", 0) > 0, r.get("committer_date", "")), reverse=True)
    candidates, health_cache = [], {}
    for r in records[:limit]:
        candidate = {"id": r["sha"], "repo": repo, "base_sha": r["base_sha"], "fixed_sha": r["sha"],
                     "subject": r["subject"], "patch": r, "pr": None, "issues": [], "health": {},
                     "status": "needs_enrichment", "notes": []}
        candidates.append(candidate)
        if client is None:
            candidate["notes"].append("Offline: GitHub evidence has not been fetched")
            continue
        try:
            number = r.get("pr_number")
            if not number:
                associated = list(client.paginate(f"/repos/{repo}/commits/{r['sha']}/pulls?per_page=100", max_pages=2))
                matches = [p for p in associated if p.get("merged_at") and p.get("merge_commit_sha") == r["sha"]]
                if len(matches) != 1:
                    candidate["notes"].append("Commit-to-PR mapping is absent or ambiguous; retain for review")
                    continue
                number = matches[0]["number"]
            pr = client.pull_request(repo, number)
            if not pr.get("merged") or pr.get("merge_commit_sha") != r["sha"]:
                candidate["notes"].append("PR is not merged at this recorded integration point")
                continue
            parents = git(root, "show", "-s", "--format=%P", r["sha"]).split()
            # A single-parent multi-commit PR might have been rebased. Do not
            # certify the final commit as its complete patch based on its title.
            if len(parents) == 1 and pr.get("commits", 1) > 1:
                original = list(client.paginate(f"/repos/{repo}/pulls/{number}/commits?per_page=100", max_pages=3))
                original_head = original[-1]["sha"] if original else ""
                if not original_head:
                    candidate["notes"].append("PR commit list unavailable")
                    continue
                try:
                    patch_base = git(root, "merge-base", r["base_sha"], original_head)
                    source_diff = git(root, "diff", "--no-color", patch_base, original_head)
                    landed_diff = git(root, "diff", "--no-color", r["base_sha"], r["sha"])
                except RuntimeError:
                    candidate["notes"].append("Cannot prove complete squash/rebase patch from local objects")
                    continue
                if source_diff != landed_diff:
                    candidate["notes"].append("Multi-commit integration needs grouping; final-commit diff not assumed complete")
                    continue
            candidate["pr"] = {k: pr.get(k) for k in ("number", "title", "body", "html_url", "merged_at", "merge_commit_sha")}
            candidate["pr"]["head_sha"] = pr["head"]["sha"]
            linked = None
            try:
                linked = closing_issues(client, repo, number)
            except (OSError, ValueError, GitHubError, KeyError) as exc:
                candidate["notes"].append(f"Structured linkage unavailable: {exc}")
            if linked is not None:
                for issue in linked:
                    if issue["repository"]["nameWithOwner"].lower() != repo.lower():
                        continue
                    candidate["issues"].append({"number": issue["number"], "title": issue["title"],
                        "body": issue["body"], "url": issue["url"], "state": issue["state"].lower(),
                        "created_at": issue["createdAt"], "updated_at": issue["updatedAt"],
                        "closed_at": issue["closedAt"], "evidence": "closing_reference"})
            else:
                for issue_number in closed_issue_numbers(pr.get("body") or "")[:10]:
                    issue = client.get(f"/repos/{repo}/issues/{issue_number}").json()
                    if "pull_request" in issue:
                        continue
                    candidate["issues"].append({**{k: issue.get(k) for k in
                        ("number", "title", "body", "state", "created_at", "updated_at", "closed_at")},
                        "url": issue.get("html_url"), "evidence": "keyword_claim"})
            candidate["status"] = "ready_for_review" if candidate["issues"] else "needs_issue"
            if health:
                for role, sha in {"base": r["base_sha"], "fixed": r["sha"], "pr_head": pr["head"]["sha"]}.items():
                    if sha not in health_cache:
                        health_cache[sha] = commit_health(client, repo, sha)
                    candidate["health"][role] = health_cache[sha]
        except (GitHubError, OSError, ValueError, KeyError, RuntimeError) as exc:
            candidate["notes"].append(f"Enrichment incomplete: {exc}")
    return {"schema_version": "1.0", "repo": repo, "clone": str(root), "head_sha": database["ref"],
            "built_at": datetime.now(UTC).isoformat(), "inventory_count": len(records),
            "deferred_by_budget": max(0, len(records) - limit), "candidates": candidates,
            "meaning": "Review candidates, not verified tasks; CI and runtime validation are separate."}
