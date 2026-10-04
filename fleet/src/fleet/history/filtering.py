"""A bounded initial shortlist from committed diffs, without runtime work.

This stage measures changes. It does not establish that a change fixes a bug,
that a PR number in a commit message is authentic, or that an issue was closed.
Those questions remain explicit downstream review work.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from fleet.history.commits import CommitFilter, build_database
from fleet.history.window import GitRepo


def filter_history(
    repo: str,
    clone: GitRepo,
    *,
    ref: str = "HEAD",
    since: datetime | None = None,
    until: datetime | None = None,
    max_commits: int = 200,
    max_changes: int = 100,
    limit: int = 25,
    max_source_files: int = 10,
) -> dict:
    """Select up to `limit` source-changing mainline diffs, newest first.

    Missing PR/issue metadata and missing test changes do not reject a diff.
    The resulting candidates are commit changes awaiting enrichment, not
    certified complete PRs or runnable tasks. No worktree checkout is changed.
    """
    if max_source_files < 1:
        raise ValueError("Source file limit must be positive")
    if since is not None and until is not None and since > until:
        raise ValueError("History start must not be after its end")
    filters = CommitFilter(max_source_files=max_source_files, require_integration_point=False)
    database = build_database(repo, clone, filters=filters, since=since, until=until,
                              ref=ref, max_points=max_commits, max_changes=max_changes,
                              candidate_limit=limit, screen_python_content=True)
    candidates = []
    for record in database.records:
        candidate = record.to_dict()
        candidate.update(
            id=record.sha,
            repo=repo,
            fixed_sha=record.sha,
            status="initial_filter_passed",
            validation="not_run",
            message=clone.commit_message(record.sha),
            pr_url=f"https://github.com/{repo}/pull/{record.pr_number}" if record.pr_number else None,
            pr_evidence="commit_subject" if record.pr_number else "unavailable",
            pr_verification="not_run",
            issue_evidence="keyword_claim" if record.closes_issues else "unavailable",
            issue_linkage="not_verified",
        )
        candidates.append(candidate)
    payload = database.to_dict()
    return {
        "schema_version": "1.1",
        "stage": "initial_filter",
        "repo": repo,
        "clone": str(clone.path),
        "ref": ref,
        "built_at": database.built_at,
        "limits": {"max_commits": max_commits, "max_changes": max_changes, "candidates": limit},
        "filters": asdict(filters),
        "content_screening": "python_ast_v1",
        "summary": {**payload["summary"], "selected": len(candidates)},
        "candidates": candidates,
        "rejected": payload["rejected"],
        "rejection_reasons": payload["dropped_reasons"],
        "meaning": "Initial diff shortlist only; PR completeness, issue linkage, problem statements and runtime behavior are unverified.",
    }
