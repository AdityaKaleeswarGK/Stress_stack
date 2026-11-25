"""The HTTPS helper must not wait on an unreachable address before a working one."""
from __future__ import annotations

import socket
import time

import pytest

from fleet import net


@pytest.fixture
def server():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    yield listener.getsockname()[1]
    listener.close()


def _info(family, host, port):
    return (family, socket.SOCK_STREAM, 6, "", (host, port) if family == socket.AF_INET else (host, port, 0, 0))


def test_ipv4_is_tried_before_an_unreachable_ipv6_address(monkeypatch, server):
    # 100::/64 is the IPv6 discard prefix: never routable, so a connect to it can only hang or fail.
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [_info(socket.AF_INET6, "100::1", server),
                                                               _info(socket.AF_INET, "127.0.0.1", server)])
    started = time.monotonic()
    sock = net.connect(("example.invalid", server), timeout=30)
    try:
        assert sock.family == socket.AF_INET
        assert time.monotonic() - started < 2
        assert sock.gettimeout() == 30  # the request timeout is restored after connecting
    finally:
        sock.close()


def test_a_refused_address_falls_through_to_the_next(monkeypatch, server):
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    unused = closed.getsockname()[1]
    closed.close()
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [_info(socket.AF_INET, "127.0.0.1", unused),
                                                               _info(socket.AF_INET, "127.0.0.1", server)])
    sock = net.connect(("example.invalid", server), timeout=10)
    assert sock.getpeername()[1] == server
    sock.close()


def test_every_address_failing_raises(monkeypatch):
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    unused = closed.getsockname()[1]
    closed.close()
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [_info(socket.AF_INET, "127.0.0.1", unused)])
    with pytest.raises(OSError):
        net.connect(("example.invalid", unused), timeout=5)
