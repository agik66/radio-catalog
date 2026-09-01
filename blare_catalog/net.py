"""Bezpečnostná vrstva pre prácu s cudzími URL.

Katalóg sa dotýka desiatok tisíc URL, ktoré do Radio Browser zapísal ktokoľvek.
Sú to nedôveryhodné dáta, nie konfigurácia. Táto vrstva rieši dve veci:

1. SSRF — bez nej je `http://127.0.0.1:2019/config/` platná "stanica"
   a sonda ju poslušne zavolá. Na tomto stroji je to admin API Caddy.
2. Protokolové finty — ffmpeg má skompilované `file`, `concat`, `data`
   a ďalšie; bez whitelistu je `ffprobe <url>` čítanie lokálnych súborov.

Zámerne bez závislostí — beží to na holom Pythone.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Protokoly, ktoré smie ffprobe použiť. Všetko ostatné (file, concat, data,
# unix, gopher, rtp…) je vypnuté — inak je URL vektor na čítanie disku.
FFPROBE_PROTOCOL_WHITELIST = "http,https,tcp,tls,crypto"

# Porty, na ktoré sa neoplatí chodiť ani keď IP prejde.
BLOCKED_PORTS = frozenset({22, 23, 25, 445, 3306, 5432, 6379, 11211, 27017})


class UnsafeURL(Exception):
    """URL neprešla bezpečnostnou kontrolou."""


@dataclass(frozen=True)
class ResolvedTarget:
    """URL, ktorej sme rozlúskli DNS a schválili cieľové IP."""

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
    """Overí tvar URL. Vracia (host, port). Nerobí DNS."""
    if not raw or len(raw) > 2048:
        raise UnsafeURL("prázdna alebo neúmerne dlhá URL")

    parts = urlsplit(raw.strip())
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURL(f"nepovolená schéma: {parts.scheme!r}")

    host = parts.hostname
    if not host:
        raise UnsafeURL("chýba host")

    port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    if port in BLOCKED_PORTS:
        raise UnsafeURL(f"zakázaný port: {port}")
    if not (1 <= port <= 65535):
        raise UnsafeURL(f"neplatný port: {port}")

    # Host zapísaný priamo ako IP musí byť verejná.
    try:
        if not _is_public(host):
            raise UnsafeURL(f"neverejná IP v URL: {host}")
    except ValueError:
        pass  # nie je IP literál, je to doménové meno — rieši resolve_target

    return host, port


def resolve_target(raw: str, *, timeout: float = 5.0) -> ResolvedTarget:
    """Overí URL a rozlúskne DNS. Odmietne, ak KTORÁKOĽVEK adresa je neverejná.

    Odmietame pri akejkoľvek neverejnej adrese, nie len pri prvej — inak
    útočník s viacerými A záznamami preleze cez DNS rebinding.
    """
    host, port = validate_url(raw)

    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (OSError, socket.gaierror) as exc:
        raise UnsafeURL(f"DNS zlyhalo: {exc}") from exc
    finally:
        socket.setdefaulttimeout(old)

    addresses = tuple(sorted({info[4][0] for info in infos}))
    if not addresses:
        raise UnsafeURL("DNS nevrátilo žiadnu adresu")

    for ip in addresses:
        if not _is_public(ip):
            raise UnsafeURL(f"host {host} ukazuje na neverejnú adresu {ip}")

    return ResolvedTarget(url=raw, host=host, port=port, addresses=addresses)


def is_safe(raw: str) -> bool:
    """Pohodlný predikát pre filtrovanie zoznamov."""
    try:
        resolve_target(raw)
        return True
    except UnsafeURL:
        return False
