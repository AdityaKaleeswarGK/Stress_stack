"""Recorded CI evidence at exact SHAs; never substitutes for reproduction."""
from __future__ import annotations

from datetime import UTC, datetime

from fleet.history.client import GitHubClient, GitHubError


def summarize(checks: list[dict], statuses: list[dict], complete: bool = True) -> str:
    failures = {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
    if any(c.get("conclusion") in failures for c in checks) or any(s.get("state") in {"failure", "error"} for s in statuses):
        return "reported_failure"
    if not complete:
        return "unknown"
    if not checks and not statuses:
        return "missing"
    if any(c.get("status") != "completed" for c in checks) or any(s.get("state") == "pending" for s in statuses):
        return "pending"
    if all(c.get("conclusion") == "success" for c in checks) and all(s.get("state") == "success" for s in statuses):
        return "reported_success"
    return "unknown"  # skipped/neutral do not prove a test passed


def commit_health(client: GitHubClient, repo: str, sha: str) -> dict:
    checks, statuses, errors = [], [], []
    complete = True
    check_url = f"/repos/{repo}/commits/{sha}/check-runs?per_page=100&filter=latest"
    status_url = f"/repos/{repo}/commits/{sha}/status?per_page=100"
    for kind, start in (("checks", check_url), ("statuses", status_url)):
        url = start
        try:
            for _ in range(10):
                response = client.get(url)
                payload = response.json()
                if kind == "checks":
                    rows = payload.get("check_runs", [])
                    for c in rows:
                        if c.get("head_sha") != sha:
                            complete = False
                            continue
                        checks.append({k: c.get(k) for k in ("id", "name", "head_sha", "status", "conclusion", "html_url", "started_at", "completed_at")})
                else:
                    if payload.get("sha") != sha:
                        complete = False
                    statuses.extend({k: s.get(k) for k in ("context", "state", "target_url", "updated_at")}
                                    for s in payload.get("statuses", []))
                url = response.next_url
                if not url:
                    break
            if url:
                complete = False
                errors.append(f"{kind}: pagination limit reached")
        except (GitHubError, OSError, ValueError) as exc:
            complete = False
            errors.append(f"{kind}: {exc}")
    return {"sha": sha, "state": summarize(checks, statuses, complete),
            "complete": complete, "observed_at": datetime.now(UTC).isoformat(),
            "checks": checks, "statuses": statuses, "errors": errors,
            "meaning": "Recorded CI only; required-check coverage and reproducibility are not established."}
