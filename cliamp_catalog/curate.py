"""Kurácia: zo surového výpisu Radio Browser spraví katalóg, ktorý sa dá vydať.

Poradie krokov je zámerné:
  1. bezpečnosť   — nedôveryhodné URL von skôr, než sa ich čohokoľvek spýtame
  2. kvalita      — bitrate/kodek/lastcheckok
  3. deduplikácia — tá istá stanica býva v DB aj 5×
  4. žáner        — kanonická taxonómia namiesto voľných tagov
  5. poradie      — obľúbenosť, aby prvá obrazovka nebola náhoda

Editorský zoznam (curated.json) stojí NAD týmto všetkým a vždy vyhráva —
to je vrstva, ktorá z databázového výpisu robí produkt.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import genres
from .net import UnsafeURL, validate_url
from .radiobrowser import Station

# Kodeky, ktoré AVPlayer aj Media3 zvládnu bez servírovania.
GOOD_CODECS = frozenset({"MP3", "AAC", "AAC+", "AACP", "OGG", "FLAC"})


@dataclass
class Gate:
    """Kvalitatívna brána. Voľnejšia pre reč, prísnejšia pre hudbu."""

    min_bitrate_music: int = 96
    min_bitrate_speech: int = 48
    require_lastcheckok: bool = True
    allowed_codecs: frozenset[str] = GOOD_CODECS
    speech_genres: frozenset[str] = frozenset({"news", "talk", "sport", "culture", "comedy"})


@dataclass
class Stats:
    total: int = 0
    dropped_unsafe: int = 0
    dropped_dead: int = 0
    dropped_codec: int = 0
    dropped_bitrate: int = 0
    dropped_duplicate: int = 0
    kept: int = 0
    with_genre: int = 0
    with_geo: int = 0
    by_genre: dict[str, int] = field(default_factory=dict)

    def report(self) -> str:
        lines = [
            f"  vstup:            {self.total}",
            f"  – nebezpečná URL: {self.dropped_unsafe}",
            f"  – označená mŕtva: {self.dropped_dead}",
            f"  – zlý kodek:      {self.dropped_codec}",
            f"  – nízky bitrate:  {self.dropped_bitrate}",
            f"  – duplicita:      {self.dropped_duplicate}",
            f"  = ponechané:      {self.kept}",
            f"    so žánrom:      {self.with_genre}",
            f"    so súradnicami: {self.with_geo}",
        ]
        top = sorted(self.by_genre.items(), key=lambda kv: -kv[1])[:12]
        if top:
            lines.append("  žánre: " + ", ".join(f"{g}={n}" for g, n in top))
        return "\n".join(lines)


def dedup_key(station: Station) -> str:
    """Kľúč na rozpoznanie tej istej stanice pod viacerými záznamami.

    Normalizuje sa host + cesta; query sa zahadzuje (býva v nej session id),
    rovnako koncové lomítko a veľkosť písmen v hoste.
    """
    url = station.url_resolved or station.url
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    path = re.sub(r"/+$", "", parts.path or "")
    return urlunsplit(("", f"{host}{port}", path, "", ""))


def _is_speech(genre_list: list[str], gate: Gate) -> bool:
    return bool(genre_list) and all(g in gate.speech_genres for g in genre_list)


def curate(
    stations: list[Station],
    *,
    gate: Gate | None = None,
    check_dns: bool = False,
) -> tuple[list[dict], Stats]:
    """Prefiltruje a obohatí stanice. Vracia (katalóg, štatistika).

    `check_dns=False` je default: tvarová kontrola URL je lacná a offline,
    DNS overenie 50 000 hostov patrí až do sondovacej fázy.
    """
    gate = gate or Gate()
    stats = Stats(total=len(stations))
    seen: dict[str, dict] = {}

    for st in stations:
        url = st.url_resolved or st.url
        try:
            validate_url(url)
        except UnsafeURL:
            stats.dropped_unsafe += 1
            continue

        if gate.require_lastcheckok and not st.lastcheckok:
            stats.dropped_dead += 1
            continue

        codec = st.codec.upper().replace("AAC+", "AAC")
        if codec and codec not in {c.replace("AAC+", "AAC") for c in gate.allowed_codecs}:
            stats.dropped_codec += 1
            continue

        genre_list = genres.classify(st.tags, st.name)
        floor = gate.min_bitrate_speech if _is_speech(genre_list, gate) else gate.min_bitrate_music
        # bitrate 0 = neznámy, nie zlý; sonda ho zistí neskôr
        if st.bitrate and st.bitrate < floor:
            stats.dropped_bitrate += 1
            continue

        key = dedup_key(st)
        entry = {
            "uuid": st.uuid,
            "name": st.name,
            "url": url,
            "homepage": st.homepage,
            "country": st.countrycode,
            "language": st.language,
            "genres": genre_list,
            "tags": st.tags[:8],
            "codec": codec,
            "bitrate": st.bitrate,
            "popularity": st.votes + st.clickcount,
            "geo": [st.geo_lat, st.geo_long] if st.geo_lat and st.geo_long else None,
            "favicon": st.favicon,
        }

        prev = seen.get(key)
        if prev is None:
            seen[key] = entry
            continue
        stats.dropped_duplicate += 1
        # Z duplicít si necháme tú s lepším bitrate, pri zhode obľúbenejšiu.
        better = (entry["bitrate"], entry["popularity"]) > (prev["bitrate"], prev["popularity"])
        if better:
            seen[key] = entry

    catalog = sorted(seen.values(), key=lambda e: -e["popularity"])
    stats.kept = len(catalog)
    stats.with_genre = sum(1 for e in catalog if e["genres"])
    stats.with_geo = sum(1 for e in catalog if e["geo"])
    for e in catalog:
        for g in e["genres"]:
            stats.by_genre[g] = stats.by_genre.get(g, 0) + 1
    return catalog, stats


def apply_editorial(catalog: list[dict], curated_path: Path) -> list[dict]:
    """Editorský zoznam ide na vrch a prepíše názov/žánre z databázy.

    Toto je vrstva, ktorá robí rozdiel medzi kurátorovaným rádiom
    a výpisom z databázy.
    """
    if not curated_path.exists():
        return catalog
    picks = json.loads(curated_path.read_text("utf-8"))
    by_url = {dedup_key_from_url(p["url"]): p for p in picks}

    featured, rest = [], []
    for entry in catalog:
        override = by_url.pop(dedup_key_from_url(entry["url"]), None)
        if override:
            merged = {**entry, **{k: v for k, v in override.items() if v}}
            merged["featured"] = True
            featured.append(merged)
        else:
            rest.append(entry)
    # Editorské stanice, ktoré v databáze vôbec nie sú (napr. vlastné streamy).
    for leftover in by_url.values():
        featured.append({**leftover, "featured": True, "genres": leftover.get("genres", [])})
    return featured + rest


def dedup_key_from_url(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    path = re.sub(r"/+$", "", parts.path or "")
    return urlunsplit(("", f"{host}{port}", path, "", ""))


def to_triage_input(catalog: list[dict]) -> list[dict]:
    """Prevod do tvaru, ktorý žerie StreamTriage na simulátore."""
    return [
        {
            "name": e["name"],
            "url": e["url"],
            "group": (e["genres"][0] if e["genres"] else "unknown"),
        }
        for e in catalog
    ]
