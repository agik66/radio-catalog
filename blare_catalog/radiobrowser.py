"""Klient na Radio Browser — komunitnú databázu rádiových staníc.

Pravidlá, ktoré API očakáva a ktoré tu dodržiavame:
  * server sa hľadá cez DNS (`all.api.radio-browser.info`), nie natvrdo —
    jednotlivé mirrory miznú a striedajú sa
  * povinný User-Agent identifikujúci appku a verziu
  * prekliky sa hlásia na /json/url/{uuid}, inak kazíme ich štatistiky

Bez externých závislostí (žiadny `requests`) — beží to na holom Pythone.
"""

from __future__ import annotations

import gzip
import http.client
import json
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

USER_AGENT = "Blare/1.0 (+https://github.com/agik66/radio-catalog) catalog-builder"
DISCOVERY_HOST = "all.api.radio-browser.info"
FALLBACK_SERVERS = ("de2.api.radio-browser.info", "at1.api.radio-browser.info")


@dataclass
class Station:
    """Surová stanica z Radio Browser, ešte neprefiltrovaná."""

    uuid: str
    name: str
    url: str
    url_resolved: str
    homepage: str = ""
    country: str = ""
    countrycode: str = ""
    language: str = ""
    tags: list[str] = field(default_factory=list)
    codec: str = ""
    bitrate: int = 0
    votes: int = 0
    clickcount: int = 0
    lastcheckok: bool = False
    geo_lat: float | None = None
    geo_long: float | None = None
    favicon: str = ""

    @classmethod
    def from_api(cls, d: dict) -> "Station":
        raw_tags = d.get("tags") or ""
        tags = [t.strip().lower() for t in raw_tags.split(",") if t.strip()]
        return cls(
            uuid=d.get("stationuuid", ""),
            name=(d.get("name") or "").strip(),
            url=d.get("url") or "",
            url_resolved=d.get("url_resolved") or d.get("url") or "",
            homepage=d.get("homepage") or "",
            country=d.get("country") or "",
            countrycode=(d.get("countrycode") or "").upper(),
            language=d.get("language") or "",
            tags=tags,
            codec=(d.get("codec") or "").upper(),
            bitrate=int(d.get("bitrate") or 0),
            votes=int(d.get("votes") or 0),
            clickcount=int(d.get("clickcount") or 0),
            lastcheckok=bool(d.get("lastcheckok")),
            geo_lat=d.get("geo_lat"),
            geo_long=d.get("geo_long"),
            favicon=d.get("favicon") or "",
        )

    def to_dict(self) -> dict:
        return {
            "uuid": self.uuid, "name": self.name, "url": self.url,
            "url_resolved": self.url_resolved, "homepage": self.homepage,
            "country": self.country, "countrycode": self.countrycode,
            "language": self.language, "tags": self.tags, "codec": self.codec,
            "bitrate": self.bitrate, "votes": self.votes,
            "clickcount": self.clickcount, "lastcheckok": self.lastcheckok,
            "geo_lat": self.geo_lat, "geo_long": self.geo_long,
            "favicon": self.favicon,
        }


class RadioBrowser:
    """Tenký klient s rate-limitom a striedaním mirrorov."""

    def __init__(self, *, min_interval: float = 0.35, timeout: float = 20.0):
        self.min_interval = min_interval
        self.timeout = timeout
        self._last_call = 0.0
        self._servers: list[str] = []

    # -- discovery ---------------------------------------------------------

    def servers(self) -> list[str]:
        if self._servers:
            return self._servers
        found: set[str] = set()
        try:
            for info in socket.getaddrinfo(DISCOVERY_HOST, 443, proto=socket.IPPROTO_TCP):
                ip = info[4][0]
                try:
                    host, _, _ = socket.gethostbyaddr(ip)
                    if host.endswith("api.radio-browser.info"):
                        found.add(host)
                except OSError:
                    continue
        except OSError:
            pass
        self._servers = sorted(found) or list(FALLBACK_SERVERS)
        random.shuffle(self._servers)
        return self._servers

    # -- transport ---------------------------------------------------------

    def _throttle(self) -> None:
        delta = time.monotonic() - self._last_call
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        self._last_call = time.monotonic()

    def _get(self, path: str, params: dict | None = None) -> list | dict:
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        last_err: Exception | None = None

        for server in self.servers():
            self._throttle()
            url = f"https://{server}/json/{path.lstrip('/')}{query}"
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            })
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    return json.loads(raw.decode("utf-8"))
            except (urllib.error.URLError, http.client.HTTPException,
                    TimeoutError, json.JSONDecodeError, OSError) as exc:
                last_err = exc
                continue  # skús ďalší mirror

        raise RuntimeError(f"Radio Browser nedostupný: {last_err}")

    # -- API ---------------------------------------------------------------

    def stations_by_country(self, code: str, *, limit: int = 10_000) -> list[Station]:
        rows = self._get("stations/search", {
            "countrycode": code.upper(), "limit": limit,
            "hidebroken": "true", "order": "clickcount", "reverse": "true",
        })
        return [Station.from_api(r) for r in rows]

    def stations_by_tag(self, tag: str, *, limit: int = 5_000) -> list[Station]:
        rows = self._get("stations/search", {
            "tag": tag, "limit": limit,
            "hidebroken": "true", "order": "clickcount", "reverse": "true",
        })
        return [Station.from_api(r) for r in rows]

    def report_click(self, uuid: str) -> None:
        """Nahlási preklik. Zdvorilosť voči prevádzkovateľom API."""
        try:
            self._get(f"url/{uuid}")
        except RuntimeError:
            pass  # štatistika nie je kritická
