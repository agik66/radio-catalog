"""Marks the stations that can be watched without pulling audio.

`watchable` is a curatorial criterion nobody else uses: it decides whether a
station may sit in the pool for the live hunt and for MIX. It does not mean a
better station — only a watchable one.
"""

from __future__ import annotations

import concurrent.futures as futures

from .nowplaying import probe


def annotate_watchable(catalog: list[dict], *, workers: int = 16,
                       timeout: float = 6.0) -> int:
    """Adds `watchable` and `nowplaying_endpoint` to every entry.

    Returns the number of watchable stations.
    """
    def check(entry: dict) -> tuple[dict, object]:
        return entry, probe(entry["url"], timeout=timeout)

    count = 0
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for entry, result in pool.map(check, catalog):
            entry["watchable"] = result is not None
            entry["nowplaying_endpoint"] = result.endpoint if result else None
            if result is not None:
                count += 1
    return count
