"""The GitHub client: pagination, caching, auth, rate-limit handling.

Driven through the injectable transport, so none of this touches the
network. The caching tests matter more than they look: an unauthenticated
budget is 60 requests/hour, and a cache that silently misses turns an
iteration loop into a one-shot-per-hour loop.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fleet.history.client import GitHubClient, GitHubError, RateLimited, Response, resolve_token


def make_response(payload: object, *, status: int = 200, link: str = "", remaining: str = "59") -> Response:
    return Response(
        status=status,
        body=json.dumps(payload).encode("utf-8"),
        headers={"Link": link, "X-Ratelimit-Remaining": remaining},
    )


class RecordingTransport:
    """Serves canned responses by URL and records what was asked for."""

    def __init__(self, routes: dict[str, Response]) -> None:
        self.routes = routes
        self.requested: list[str] = []
        self.headers_seen: list[dict[str, str]] = []

    def __call__(self, url: str, headers: dict[str, str]) -> Response:
        self.requested.append(url)
        self.headers_seen.append(headers)
        if url not in self.routes:
            return make_response({"message": "Not Found"}, status=404)
        return self.routes[url]


# --- pagination -------------------------------------------------------------


def test_paginate_follows_the_link_header() -> None:
    root = "https://api.github.com/repos/o/r/issues?page=1"
    second = "https://api.github.com/repos/o/r/issues?page=2"
    transport = RecordingTransport(
        {
            root: make_response([{"number": 1}, {"number": 2}], link=f'<{second}>; rel="next"'),
            second: make_response([{"number": 3}]),
        }
    )
    client = GitHubClient(transport=transport)

    items = list(client.paginate(root))

    assert [item["number"] for item in items] == [1, 2, 3]
    assert transport.requested == [root, second]


def test_paginate_stops_without_a_next_link() -> None:
    root = "https://api.github.com/x"
    transport = RecordingTransport({root: make_response([{"number": 1}], link='<other>; rel="prev"')})
    client = GitHubClient(transport=transport)
    assert len(list(client.paginate(root))) == 1
    assert transport.requested == [root]


def test_paginate_is_guarded_against_a_cursor_loop() -> None:
    """A `next` link pointing at itself must terminate, not spin forever."""
    root = "https://api.github.com/loop"
    transport = RecordingTransport({root: make_response([{"n": 1}], link=f'<{root}>; rel="next"')})
    client = GitHubClient(transport=transport)

    items = list(client.paginate(root, max_pages=3))

    assert len(items) == 3
    assert len(transport.requested) == 3


def test_a_non_array_payload_is_an_error_not_a_crash() -> None:
    root = "https://api.github.com/repos/o/r/issues"
    transport = RecordingTransport({root: make_response({"message": "Moved"})})
    client = GitHubClient(transport=transport)
    with pytest.raises(GitHubError, match="expected a JSON array"):
        list(client.paginate(root))


# --- caching ----------------------------------------------------------------


def test_a_cached_response_costs_no_call(tmp_path: Path) -> None:
    url = "https://api.github.com/repos/o/r"
    transport = RecordingTransport({url: make_response({"full_name": "o/r"})})
    client = GitHubClient(transport=transport, cache_dir=tmp_path / "cache")

    assert client.get(url).json()["full_name"] == "o/r"
    assert client.get(url).json()["full_name"] == "o/r"

    assert client.calls_made == 1
    assert client.cache_hits == 1


def test_the_cache_survives_a_new_client(tmp_path: Path) -> None:
    """What makes re-running a mining pass free rather than another 60 calls."""
    url = "https://api.github.com/repos/o/r"
    cache = tmp_path / "cache"
    first = GitHubClient(transport=RecordingTransport({url: make_response({"n": 1})}), cache_dir=cache)
    first.get(url)

    # A transport that would fail if consulted at all.
    second = GitHubClient(transport=RecordingTransport({}), cache_dir=cache)
    assert second.get(url).json() == {"n": 1}
    assert second.calls_made == 0


def test_pagination_state_is_cached_too(tmp_path: Path) -> None:
    root = "https://api.github.com/a"
    second = "https://api.github.com/b"
    cache = tmp_path / "cache"
    routes = {
        root: make_response([{"n": 1}], link=f'<{second}>; rel="next"'),
        second: make_response([{"n": 2}]),
    }
    list(GitHubClient(transport=RecordingTransport(routes), cache_dir=cache).paginate(root))

    replayed = GitHubClient(transport=RecordingTransport({}), cache_dir=cache)
    assert [item["n"] for item in replayed.paginate(root)] == [1, 2]
    assert replayed.calls_made == 0


def test_failures_are_never_cached(tmp_path: Path) -> None:
    """A transient 403 cached as an answer would poison every later run."""
    url = "https://api.github.com/repos/o/r"
    cache = tmp_path / "cache"
    failing = GitHubClient(
        transport=RecordingTransport({url: make_response({"message": "nope"}, status=403, remaining="30")}),
        cache_dir=cache,
    )
    with pytest.raises(GitHubError):
        failing.get(url)

    recovered = GitHubClient(
        transport=RecordingTransport({url: make_response({"full_name": "o/r"})}), cache_dir=cache
    )
    assert recovered.get(url).json()["full_name"] == "o/r"


# --- auth and limits --------------------------------------------------------


def test_a_token_becomes_a_bearer_header() -> None:
    url = "https://api.github.com/x"
    transport = RecordingTransport({url: make_response({})})
    GitHubClient(token="ghp_secret", transport=transport).get(url)
    assert transport.headers_seen[0]["Authorization"] == "Bearer ghp_secret"


def test_without_a_token_no_auth_header_is_sent() -> None:
    url = "https://api.github.com/x"
    transport = RecordingTransport({url: make_response({})})
    GitHubClient(transport=transport).get(url)
    assert "Authorization" not in transport.headers_seen[0]


def test_exhausted_budget_raises_rate_limited_with_its_reset() -> None:
    url = "https://api.github.com/x"
    response = Response(
        status=403,
        body=b'{"message": "API rate limit exceeded"}',
        headers={"X-Ratelimit-Remaining": "0", "X-Ratelimit-Reset": "1789196940"},
    )
    client = GitHubClient(transport=RecordingTransport({url: response}))

    with pytest.raises(RateLimited) as caught:
        client.get(url)

    assert caught.value.reset_at == 1789196940
    assert "GITHUB_TOKEN" in str(caught.value)  # the message says how to fix it


def test_a_403_with_budget_remaining_is_not_a_rate_limit() -> None:
    """Blocked-by-policy and out-of-budget need different reactions."""
    url = "https://api.github.com/x"
    response = make_response({"message": "Forbidden"}, status=403, remaining="42")
    client = GitHubClient(transport=RecordingTransport({url: response}))

    with pytest.raises(GitHubError) as caught:
        client.get(url)
    assert not isinstance(caught.value, RateLimited)
    assert caught.value.status == 403


def test_remaining_budget_is_tracked_for_reporting() -> None:
    url = "https://api.github.com/x"
    client = GitHubClient(transport=RecordingTransport({url: make_response({}, remaining="17")}))
    client.get(url)
    assert client.rate_remaining == 17


def test_a_relative_path_is_resolved_against_the_api_root() -> None:
    transport = RecordingTransport({"https://api.github.com/repos/o/r": make_response({})})
    GitHubClient(transport=transport).get("/repos/o/r")
    assert transport.requested == ["https://api.github.com/repos/o/r"]


# --- token resolution -------------------------------------------------------


def test_token_resolution_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "from_github_token")
    monkeypatch.setenv("GH_TOKEN", "from_gh_token")
    assert resolve_token("explicit") == "explicit"
    assert resolve_token(None) == "from_github_token"

    monkeypatch.delenv("GITHUB_TOKEN")
    assert resolve_token(None) == "from_gh_token"

    monkeypatch.delenv("GH_TOKEN")
    assert resolve_token(None) is None
    assert resolve_token("   ") is None
