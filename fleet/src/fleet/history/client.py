"""A small GitHub REST client: stdlib only, token optional, disk cached.

Three deliberate departures from Repo2RLEnv, which shells out to `gh` for
every call (src/repo2rlenv/github.py):

1. **No `gh` dependency.** `gh` is a separate binary the user has to install
   and authenticate; it isn't present on every machine that wants to mine a
   repo (it is not present on this one). `urllib` is in the stdlib, so this
   adds no dependency to `pyproject.toml` either.
2. **Works unauthenticated.** The endpoints we need — closed issues and the
   issue timeline — are readable without a token at 60 requests/hour. A
   token raises that to 5,000 and is used when present, but its absence
   degrades the yield rather than failing the run.
3. **Cached to disk.** At 60 requests/hour, re-running a mining pass while
   iterating on filters would burn the entire budget in one go. Responses
   are cached by URL so only the first pass over a window costs anything.

`transport` is injectable so tests exercise pagination, caching, and
rate-limit handling against recorded payloads instead of the network.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"
_USER_AGENT = "fleet-history/0.1 (+https://github.com/harbor-framework/harbor)"
# `rel="next"` out of a Link header. GitHub paginates every list endpoint
# this way, and the URL carries opaque cursor state on some endpoints — so
# follow the header rather than incrementing a `page=` parameter ourselves.
_LINK_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _midpoint_iso(since: str, until: str) -> str | None:
    """The halfway point between two ISO timestamps, for window bisection."""
    try:
        start = datetime.fromisoformat(since.replace("Z", "+00:00"))
        end = datetime.fromisoformat(until.replace("Z", "+00:00"))
    except ValueError:
        return None
    if end <= start:
        return None
    return (start + (end - start) / 2).isoformat()


class GitHubError(RuntimeError):
    """Any failure talking to the API, with the status code when there was one."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class RateLimited(GitHubError):
    """Budget exhausted. Carries the reset epoch so the caller can report it."""

    def __init__(self, message: str, *, reset_at: int | None = None) -> None:
        super().__init__(message, status=403)
        self.reset_at = reset_at


@dataclass(frozen=True, slots=True)
class Response:
    """Just the parts of an HTTP response this client cares about."""

    status: int
    body: bytes
    headers: dict[str, str]

    def json(self) -> Any:
        return json.loads(self.body) if self.body else None

    @property
    def next_url(self) -> str | None:
        match = _LINK_NEXT_RE.search(self.headers.get("Link", ""))
        return match.group(1) if match else None


Transport = Callable[[str, dict[str, str]], Response]


def _urllib_transport(url: str, headers: dict[str, str]) -> Response:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as raw:
            return Response(
                status=raw.status,
                body=raw.read(),
                headers={key.title(): value for key, value in raw.headers.items()},
            )
    except urllib.error.HTTPError as exc:
        # An HTTPError *is* the response for 403/404/422 — read it rather than
        # raising, so the caller can distinguish "rate limited" (403 with a
        # zero remaining header) from "private repo" (404) from a real fault.
        return Response(
            status=exc.code,
            body=exc.read(),
            headers={key.title(): value for key, value in (exc.headers or {}).items()},
        )
    except urllib.error.URLError as exc:
        raise GitHubError(f"network error for {url}: {exc.reason}") from exc


def resolve_token(explicit: str | None = None) -> str | None:
    """First of: explicit argument, `GITHUB_TOKEN`, `GH_TOKEN`.

    No `gh auth token` shell-out — see the module docstring. A token is
    optional; without one the caller gets the 60/hour unauthenticated budget.
    """
    for value in (explicit, os.environ.get("GITHUB_TOKEN"), os.environ.get("GH_TOKEN")):
        if value and value.strip():
            return value.strip()
    return None


class GitHubClient:
    """Paginating, caching, rate-limit-aware reader for the endpoints we need."""

    def __init__(
        self,
        *,
        token: str | None = None,
        cache_dir: Path | None = None,
        transport: Transport | None = None,
        api_root: str = API_ROOT,
        max_cache_age: float | None = None,
    ) -> None:
        self.token = token
        self.cache_dir = cache_dir
        self.api_root = api_root.rstrip("/")
        self.max_cache_age = max_cache_age
        self._transport = transport or _urllib_transport
        # Observability for the run report: how much budget a pass actually
        # cost, and how much the cache saved.
        self.calls_made = 0
        self.cache_hits = 0
        self.rate_remaining: int | None = None
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    # --- caching ---------------------------------------------------------

    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def _cached(self, url: str) -> Response | None:
        path = self._cache_path(url)
        if path is None or not path.is_file():
            return None
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if self.max_cache_age is not None and time.time() - stored.get("cached_at", 0) >= self.max_cache_age:
            return None
        return Response(
            status=stored["status"],
            body=stored["body"].encode("utf-8"),
            headers=stored.get("headers", {}),
        )

    def _store(self, url: str, response: Response) -> None:
        path = self._cache_path(url)
        # Only success is cacheable. Caching a 403 would persist a transient
        # rate-limit as a permanent answer.
        if path is None or response.status != 200:
            return
        payload = {
            "url": url,
            "status": response.status,
            "body": response.body.decode("utf-8", errors="replace"),
            # Link is the only header worth keeping — pagination depends on it.
            "headers": {"Link": response.headers.get("Link", "")},
            "cached_at": int(time.time()),
        }
        try:
            path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError as exc:  # a full/read-only cache dir must not kill a run
            logger.debug("cache write failed for %s: %s", url, exc)

    # --- requests --------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": _USER_AGENT,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def get(self, url_or_path: str) -> Response:
        """GET one URL, cache-first. Raises on anything but 200."""
        url = url_or_path if url_or_path.startswith("http") else f"{self.api_root}/{url_or_path.lstrip('/')}"

        cached = self._cached(url)
        if cached is not None:
            self.cache_hits += 1
            return cached

        response = self._transport(url, self._headers())
        self.calls_made += 1

        remaining = response.headers.get("X-Ratelimit-Remaining")
        if remaining is not None and remaining.isdigit():
            self.rate_remaining = int(remaining)

        if response.status == 200:
            self._store(url, response)
            return response

        if response.status in (403, 429) and self.rate_remaining == 0:
            reset = response.headers.get("X-Ratelimit-Reset", "")
            raise RateLimited(
                f"GitHub rate limit exhausted ({'authenticated' if self.token else 'unauthenticated, 60/hour'}). "
                f"Set GITHUB_TOKEN to raise the budget to 5,000/hour.",
                reset_at=int(reset) if reset.isdigit() else None,
            )
        raise GitHubError(f"GET {url} -> HTTP {response.status}", status=response.status)

    def paginate(self, url_or_path: str, *, max_pages: int = 50) -> Iterator[dict[str, Any]]:
        """Yield items across pages by following `Link: rel="next"`.

        `max_pages` is a guard against a cursor loop, not a result limit —
        callers bound results by their own window and budget.
        """
        url = url_or_path
        for _ in range(max_pages):
            response = self.get(url)
            payload = response.json()
            if not isinstance(payload, list):
                raise GitHubError(f"expected a JSON array from {url}, got {type(payload).__name__}")
            yield from payload
            following = response.next_url
            if not following:
                return
            url = following
        logger.warning("pagination stopped at max_pages=%d for %s", max_pages, url_or_path)

    # --- endpoints -------------------------------------------------------

    def closed_issues(
        self, repo: str, *, since: str, per_page: int = 100, max_pages: int = 50
    ) -> Iterator[dict[str, Any]]:
        """Closed issues in `repo` updated at or after `since` (ISO 8601).

        The endpoint mixes pull requests into "issues" — every PR is an issue
        in GitHub's data model. Filtering them out is the caller's job (see
        `Rejection.IS_PULL_REQUEST`) so the count shows up in the report
        rather than vanishing.

        `since` filters on *updated* time, not closed time: an issue closed
        inside the window was necessarily updated inside it, so this is a
        superset, and the precise closed-time gate runs client-side.
        """
        query = urllib.parse.urlencode(
            {
                "state": "closed",
                "since": since,
                "per_page": str(per_page),
                "sort": "updated",
                "direction": "desc",
            }
        )
        yield from self.paginate(f"/repos/{repo}/issues?{query}", max_pages=max_pages)

    def search_closed_issues(
        self,
        repo: str,
        *,
        since: str,
        until: str | None = None,
        max_pages: int = 10,
        _depth: int = 0,
    ) -> Iterator[dict[str, Any]]:
        """Issues (never PRs) closed inside the window, filtered server-side.

        Strictly better than `closed_issues` where it works, and the
        difference is not marginal. Measured against `pallets/click` over a
        60-day window: the list endpoint returns 3,300 items across 33
        requests, of which 1,703 are pull requests and 1,528 are issues
        closed long before the window (it filters on *updated* time, the
        only thing it offers). This returns 33 items in one request, all of
        them issues, all closed inside the window.

        Two limits come with it. Search is capped at 1,000 results per
        query, so a window holding more is bisected by date and the halves
        are queried separately — which is why the window bounds are part of
        the query rather than applied afterwards. And search bills to its
        own, much smaller budget (10 requests/minute unauthenticated), so
        this is a cheap primary path, not a cheap loop.
        """
        until = until or _now_iso()
        query = (
            f"repo:{repo} is:issue is:closed "
            f"closed:{since[:10]}..{until[:10]}"
        )
        url = f"/search/issues?{urllib.parse.urlencode({'q': query, 'per_page': '100', 'advanced_search': 'true'})}"

        first = self.get(url)
        payload = first.json() or {}
        total = payload.get("total_count", 0)

        if total > 1000 and _depth < 6:
            # Beyond 1,000 the API simply stops paginating, so a wider window
            # would silently lose its tail. Bisect instead.
            midpoint = _midpoint_iso(since, until)
            if midpoint and midpoint[:10] not in (since[:10], until[:10]):
                logger.info("search window %s..%s holds %d issues; bisecting", since[:10], until[:10], total)
                yield from self.search_closed_issues(
                    repo, since=since, until=midpoint, max_pages=max_pages, _depth=_depth + 1
                )
                yield from self.search_closed_issues(
                    repo, since=midpoint, until=until, max_pages=max_pages, _depth=_depth + 1
                )
                return
            logger.warning(
                "search window %s..%s holds %d issues and cannot be bisected further; "
                "results are truncated at 1000",
                since[:10],
                until[:10],
                total,
            )

        yield from payload.get("items", [])
        following = first.next_url
        for _ in range(max_pages - 1):
            if not following:
                return
            response = self.get(following)
            page = response.json() or {}
            yield from page.get("items", [])
            following = response.next_url

    def issues_closed_in_window(
        self, repo: str, *, since: str, until: str | None = None
    ) -> Iterator[dict[str, Any]]:
        """The candidate pool: search first, list endpoint as a fallback.

        Search is the better query but has its own failure modes — a
        separate per-minute budget, and occasional 422s on an unusual query
        — so a failure there falls back to the list endpoint rather than
        ending the run. The fallback returns pull requests and
        out-of-window issues; the caller's gates count both.
        """
        try:
            yield from self.search_closed_issues(repo, since=since, until=until)
            return
        except RateLimited:
            raise
        except GitHubError as exc:
            logger.warning("search unavailable (%s); falling back to the issues endpoint", exc)
        yield from self.closed_issues(repo, since=since)

    def issue_timeline(self, repo: str, number: int, *, max_pages: int = 10) -> list[dict[str, Any]]:
        """Every timeline event on an issue — the issue→PR ground truth.

        This is the expensive call: one per issue, no batching available on
        REST. It is also the only place GitHub tells us which *merged* PR
        closed an issue, including links made through the UI that leave no
        closing keyword in any body.
        """
        return list(
            self.paginate(f"/repos/{repo}/issues/{number}/timeline?per_page=100", max_pages=max_pages)
        )

    def pull_request(self, repo: str, number: int) -> dict[str, Any]:
        return self.get(f"/repos/{repo}/pulls/{number}").json()

    def commit(self, repo: str, sha: str) -> dict[str, Any]:
        """One commit, including its parents — used to derive the true base.

        `parents[0]` of a PR's merge commit is the mainline commit the work
        landed on. That is the commit a patch applies against; the REST API's
        `pull_request.base.sha` is the base branch tip at last sync and is
        stale whenever the base advanced during review.
        """
        return self.get(f"/repos/{repo}/commits/{sha}").json()

    def repository(self, repo: str) -> dict[str, Any]:
        return self.get(f"/repos/{repo}").json()
