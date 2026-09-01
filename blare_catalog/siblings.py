"""Discovering the other mounts on a host we already know.

An Icecast or Shoutcast status endpoint describes EVERY mount on that server.
One known station therefore hands us its whole host for free: measured on
ice.abradio.cz we knew 45 mounts and the endpoint listed 149.

These siblings are the best possible candidates for the live-hunt pool, because
watchability is what the pool is short of and a sibling is watchable by
construction — it was found by reading the very endpoint the hunt would poll.

The whole conversation with the status server goes through `nowplaying`. That
module already handles the transport quirks, most importantly the SHOUTcast v1
`ICY 200 OK` status line that raises BadStatusLine out of http.client rather
than a normal URLError; duplicating that here would mean duplicating the bug.

Everything discovered is attacker-influenced: mount names and listen URLs are
strings a station operator writes, and the listen URL may point at a completely
different host than the one we asked. So every candidate is put back through
`net` validation, including DNS, before it is allowed into the catalog.

Yield is not the goal — usable stations are. An ungated run over the real
catalog turned 646 stations into 4 308, and most of the growth was noise: four
DNS aliases of one radionetz.de box each contributing the same 147 mounts, and
mounts like /0n-2000s.mp3, /0n-2000s_app.mp3, /0n-2000s_alexa.mp3 that are one
station wearing seven delivery labels. Both are filtered here, at discovery,
because the cheapest place to not add a duplicate is before adding it.
"""

from __future__ import annotations

import concurrent.futures as futures
import re
from urllib.parse import urlsplit, urlunsplit

from . import genres
from .curate import GOOD_CODECS, dedup_key_from_url
from .net import UnsafeURL, resolve_target
from .nowplaying import Mount, fetch_status, status_base

# Mounts that are not a listenable programme.
_SKIP_MOUNT_SUFFIXES = (".xspf", ".m3u", ".pls", ".asx")
_SKIP_MOUNT_NAMES = frozenset({"/", "/admin", "/status", "/stream.nsv"})

# A host advertising thousands of mounts is a mass hosting panel, not a
# broadcaster; pulling all of it would swamp the catalog with unrelated feeds.
MAX_MOUNTS_PER_HOST = 400

# Two hostnames that advertise nearly the same mounts are one machine behind
# two DNS names. Measured: 0n-60s, 0n-classicrock, 0n-country and 0n-relax
# .radionetz.de share 146 of 147 mounts. Not 1.0, because a mount going up or
# down between two fetches is normal.
HOST_ALIAS_OVERLAP = 0.9

_AUDIO_EXT_RE = re.compile(r"\.(mp3|aac|aacp|ogg|oga|opus|flac|m4a|nsv)$")
# Only real bitrate values are stripped as a bitrate suffix. Stripping any
# trailing digits would fold /hit80128.mp3 and /hit90128.mp3 — Hitrádio
# Osmdesátka and Devadesátka — into one station, which they are not.
_BITRATE_SUFFIX_RE = re.compile(r"[_-]?(32|48|56|64|96|112|128|160|192|224|256|320)$")
_DELIVERY_SUFFIX_RE = re.compile(
    r"[_-](alexa|app|web|tunein|mobile|player|site|hd|sd|hi|lo|high|low|"
    r"aac|mp3|ogg|flac|opus)$")


def _candidate_url(base: str, mount: Mount) -> str | None:
    """Absolute stream URL for a mount: the host we queried plus the mount path.

    Not the server's own `listenurl`, even though it looks more authoritative.
    Measured on ice.abradio.cz, which answers with listen URLs on ice.radia.cz:
    following those would re-discover the 45 stations we already hold under a
    second hostname, and none of them would dedup against the catalog. The host
    we just fetched status from is the one known to serve, and it is the form
    the rest of the catalog already uses.
    """
    raw = mount.raw_path or mount.path
    if not raw or raw == "/":
        return None
    base_parts = urlsplit(base)
    if not base_parts.netloc:
        return None
    return urlunsplit((base_parts.scheme, base_parts.netloc, raw, "", ""))


def _is_listenable(mount: Mount) -> bool:
    if not mount.path or mount.path in _SKIP_MOUNT_NAMES:
        return False
    return not mount.path.endswith(_SKIP_MOUNT_SUFFIXES)


def _passes_gate(mount: Mount, *, min_bitrate: int) -> bool:
    """Same bar the catalog itself applies, so siblings are not second class.

    An unknown codec or bitrate is not a failure — the status endpoint often
    omits both, and `curate` treats 0 as unknown rather than bad for the same
    reason.
    """
    if mount.codec and mount.codec not in GOOD_CODECS:
        return False
    return not (mount.bitrate and mount.bitrate < min_bitrate)


def station_stem(path: str) -> str:
    """Strip the parts of a mount name that only describe how it is delivered.

    /0n-2000s.mp3, /0n-2000s_app.mp3 and /0n-2000s_alexa.mp3 are one station in
    three wrappers, and /HitFMDp with /HitFMDp_HD is one station at two
    qualities. This is a naming heuristic, so it is only ever applied WITHIN a
    single host and only to decide which of several near-identical mounts to
    ADD — never to merge stations already in the catalog. Genuine simulcast
    detection is evidence-based and lives in `simulcast.py`.
    """
    stem = path.lower().lstrip("/")
    stem = _AUDIO_EXT_RE.sub("", stem)
    for _ in range(3):     # "_app_128", "_tunein_aac" — a couple of layers deep
        shorter = _DELIVERY_SUFFIX_RE.sub("", stem)
        shorter = _BITRATE_SUFFIX_RE.sub("", shorter)
        if shorter == stem or not shorter:
            break
        stem = shorter
    return stem or path.lower()


def _entry_from_mount(url: str, mount: Mount, seed: dict) -> dict:
    """Shape a discovered mount like a catalog entry.

    Country and language are inherited from the seed station: the mounts live on
    the same server, run by the same operator, so the seed's metadata is a far
    better guess than nothing. The genre goes through the same taxonomy as every
    other station — siblings arrive after `curate` has run, so without this they
    would all land in triage as "unknown".
    """
    name = mount.name.strip() or mount.path.lstrip("/")
    tags = [t for t in [mount.genre.strip().lower()] if t]
    return {
        "uuid": f"sibling:{dedup_key_from_url(url)}",
        "name": name,
        "url": url,
        "homepage": "",
        "country": seed.get("country", ""),
        "language": seed.get("language", ""),
        "genres": genres.classify(tags, name),
        "tags": tags,
        "codec": mount.codec,
        "bitrate": mount.bitrate,
        # Unknown to Radio Browser, so it has no vote history. Zero keeps it
        # below every curated station instead of inventing a rank for it.
        "popularity": 0,
        "geo": None,
        "favicon": "",
        "watchable": True,
        "discovered_from": seed.get("uuid", ""),
    }


def discover_on_host(seed: dict, *, timeout: float = 8.0) -> list[tuple[str, Mount]]:
    """Every listenable mount the seed station's status endpoint reports."""
    base = status_base(seed["url"])
    if not base:
        return []
    found = fetch_status(seed["url"], timeout=timeout)
    if not found:
        return []
    _, mounts = found
    if len(mounts) > MAX_MOUNTS_PER_HOST:
        return []

    out: list[tuple[str, Mount]] = []
    for mount in mounts:
        if not _is_listenable(mount):
            continue
        url = _candidate_url(base, mount)
        if url:
            out.append((url, mount))
    return out


def _drop_alias_hosts(per_host: dict[str, list[tuple[str, Mount]]]) -> list[str]:
    """Host names that are a duplicate view of another host. See HOST_ALIAS_OVERLAP.

    The host keeping the mounts is the one with more of them, ties going to the
    alphabetically first name so a rebuild does not pick a different winner and
    churn the whole catalog.
    """
    paths = {host: {mount.path for _, mount in items} for host, items in per_host.items()}
    order = sorted(per_host, key=lambda h: (-len(paths[h]), h))
    dropped: list[str] = []
    kept: list[str] = []
    for host in order:
        mine = paths[host]
        if not mine:
            continue
        if any(len(mine & paths[other]) / len(mine | paths[other]) >= HOST_ALIAS_OVERLAP
               for other in kept):
            dropped.append(host)
        else:
            kept.append(host)
    return dropped


def _pick_variants(items: list[tuple[str, Mount]]) -> list[tuple[str, Mount]]:
    """One mount per station stem on a host — the best-sounding one."""
    best: dict[str, tuple[str, Mount]] = {}
    for url, mount in items:
        stem = station_stem(mount.path)
        current = best.get(stem)
        if current is None or (mount.bitrate, mount.listeners) > (
                current[1].bitrate, current[1].listeners):
            best[stem] = (url, mount)
    return list(best.values())


def discover(catalog: list[dict], *, workers: int = 12, timeout: float = 8.0,
             min_bitrate: int = 96,
             max_hosts: int | None = None) -> tuple[list[dict], dict[str, int]]:
    """Find stations on hosts we already know. Returns (new entries, stats).

    Only one seed per host is probed — every station on a host reports the same
    mount list, so the other seeds would be identical requests.
    """
    known = {dedup_key_from_url(e["url"]) for e in catalog}
    stats = {"hosts_probed": 0, "mounts_seen": 0, "alias_hosts": 0,
             "dropped_quality": 0, "dropped_variant": 0, "already_known": 0,
             "rejected_unsafe": 0, "new": 0}

    seeds: dict[str, dict] = {}
    for entry in catalog:
        if not entry.get("watchable"):
            continue
        host = urlsplit(entry["url"]).netloc.lower()
        if host and host not in seeds:
            seeds[host] = entry
    seed_list = list(seeds.values())[:max_hosts] if max_hosts else list(seeds.values())

    def probe_host(seed: dict) -> tuple[dict, list[tuple[str, Mount]]]:
        return seed, discover_on_host(seed, timeout=timeout)

    per_host: dict[str, list[tuple[str, Mount]]] = {}
    seed_of: dict[str, dict] = {}
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for seed, items in pool.map(probe_host, seed_list):
            stats["hosts_probed"] += 1
            stats["mounts_seen"] += len(items)
            if not items:
                continue
            host = urlsplit(seed["url"]).netloc.lower()
            per_host[host] = items
            seed_of[host] = seed

    for host in _drop_alias_hosts(per_host):
        stats["alias_hosts"] += 1
        stats["mounts_seen"] -= len(per_host.pop(host))

    candidates: list[tuple[str, Mount, dict]] = []
    for host, items in per_host.items():
        good = [pair for pair in items if _passes_gate(pair[1], min_bitrate=min_bitrate)]
        stats["dropped_quality"] += len(items) - len(good)
        picked = _pick_variants(good)
        stats["dropped_variant"] += len(good) - len(picked)
        candidates += [(url, mount, seed_of[host]) for url, mount in picked]

    # DNS is the expensive half of validation and a host resolves the same way
    # for all of its mounts, so the verdict is cached per host.
    host_ok: dict[str, bool] = {}
    new: list[dict] = []
    for url, mount, seed in candidates:
        key = dedup_key_from_url(url)
        if key in known:
            stats["already_known"] += 1
            continue
        host = urlsplit(url).netloc.lower()
        if host not in host_ok:
            try:
                resolve_target(url, timeout=5.0)
                host_ok[host] = True
            except UnsafeURL:
                host_ok[host] = False
        if not host_ok[host]:
            stats["rejected_unsafe"] += 1
            continue
        known.add(key)
        new.append(_entry_from_mount(url, mount, seed))

    stats["new"] = len(new)
    return new, stats


def merge_into(catalog: list[dict], discovered: list[dict]) -> int:
    """Append discovered stations that are not already present. Returns count."""
    known = {dedup_key_from_url(e["url"]) for e in catalog}
    added = 0
    for entry in discovered:
        key = dedup_key_from_url(entry["url"])
        if key in known:
            continue
        known.add(key)
        catalog.append(entry)
        added += 1
    return added


__all__ = ["discover", "discover_on_host", "merge_into", "station_stem",
           "MAX_MOUNTS_PER_HOST", "HOST_ALIAS_OVERLAP"]
