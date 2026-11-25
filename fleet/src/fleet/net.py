"""HTTPS requests that do not stall on a broken IPv6 route.

`urllib` connects to a host's addresses one at a time, in the order DNS
returns them, and waits the operating system's full connect timeout on
each. Hosts such as openrouter.ai list an IPv6 address first; on a network
without working IPv6 the first attempt hangs about 75 seconds on macOS
before IPv4 is tried. Measured here: a one-line request took 1.3 s with
curl (which races both families) and about 78 s through urllib, on every
call. This module tries IPv4 first and gives each address a short connect
timeout, so a dead address costs seconds once, not minutes per request.
"""
from __future__ import annotations

import http.client
import socket
import urllib.request

CONNECT_TIMEOUT = 5.0


def connect(address: tuple[str, int], timeout: float | None = None, source_address=None) -> socket.socket:
    """Open a TCP connection, IPv4 addresses first, each with a short connect timeout."""
    host, port = address
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    infos.sort(key=lambda info: info[0] != socket.AF_INET)  # stable: keeps DNS order within a family
    last: OSError | None = None
    for family, kind, proto, _, target in infos:
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(min(CONNECT_TIMEOUT, timeout) if timeout else CONNECT_TIMEOUT)
            if source_address:
                sock.bind(source_address)
            sock.connect(target)
            sock.settimeout(timeout)  # the caller's timeout governs the request itself
            return sock
        except OSError as exc:
            last = exc
            sock.close()
    raise last or OSError(f"no address for {host}")


class _Connection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = connect


class _Handler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(_Connection, request, context=self._context)


_opener = urllib.request.build_opener(_Handler())


def urlopen(request, timeout: float):
    """Drop-in for `urllib.request.urlopen` on HTTPS URLs."""
    return _opener.open(request, timeout=timeout)
