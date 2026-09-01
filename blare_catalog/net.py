"""Security layer for handling foreign URLs.

The catalog touches tens of thousands of URLs that anyone at all was able to
write into Radio Browser. That is untrusted data, not configuration. This layer
handles two things:

1. SSRF — without it `http://127.0.0.1:2019/config/` is a valid "station" and
   the probe will dutifully call it. On this machine that is Caddy's admin API.
2. Protocol tricks — ffmpeg ships with `file`, `concat`, `data` and others
   compiled in; without a whitelist `ffprobe <url>` is a local file read.

Deliberately dependency-free — this runs on bare Python.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Protocols ffprobe may use. Everything else (file, concat, data, unix,
# gopher, rtp…) is off — otherwise a URL is a vector for reading the disk.
FFPROBE_PROTOCOL_WHITELIST = "http,https,tcp,tls,crypto"

# Ports not worth visiting even when the IP passes.
BLOCKED_PORTS = frozenset({22, 23, 25, 445, 3306, 5432, 6379, 11211, 27017})


class UnsafeURL(Exception):
    """The URL failed the security check."""


@dataclass(frozen=True)
class ResolvedTarget:
    """A URL whose DNS we resolved and whose target IPs we approved."""

    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def validate_url(raw: str) -> tuple[str, int]:
    """Checks the shape of a URL. Returns (host, port). Does no DNS."""
    if not raw or len(raw) > 2048:
        raise UnsafeURL("empty or disproportionately long URL")

    parts = urlsplit(raw.strip())
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURL(f"scheme not allowed: {parts.scheme!r}")

    host = parts.hostname
    if not host:
        raise UnsafeURL("host missing")

    port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    if port in BLOCKED_PORTS:
        raise UnsafeURL(f"forbidden port: {port}")
    if not (1 <= port <= 65535):
        raise UnsafeURL(f"invalid port: {port}")

    # A host written directly as an IP has to be a public one.
    try:
        if not _is_public(host):
            raise UnsafeURL(f"non-public IP in URL: {host}")
    except ValueError:
        pass  # not an IP literal, it is a domain name — resolve_target handles it

    return host, port


def resolve_target(raw: str, *, timeout: float = 5.0) -> ResolvedTarget:
    """Checks a URL and resolves DNS. Rejects if ANY address is non-public.

    Rejecting on any non-public address, not just the first one — otherwise an
    attacker with several A records gets through by DNS rebinding.
    """
    host, port = validate_url(raw)

    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (OSError, socket.gaierror) as exc:
        raise UnsafeURL(f"DNS failed: {exc}") from exc
    finally:
        socket.setdefaulttimeout(old)

    addresses = tuple(sorted({info[4][0] for info in infos}))
    if not addresses:
        raise UnsafeURL("DNS returned no address")

    for ip in addresses:
        if not _is_public(ip):
            raise UnsafeURL(f"host {host} points at non-public address {ip}")

    return ResolvedTarget(url=raw, host=host, port=port, addresses=addresses)


def is_safe(raw: str) -> bool:
    """Convenience predicate for filtering lists."""
    try:
        resolve_target(raw)
        return True
    except UnsafeURL:
        return False
