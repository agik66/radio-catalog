"""Označí stanice, ktoré sa dajú sledovať bez sťahovania zvuku.

`watchable` je kurátorské kritérium, ktoré nikto iný nepoužíva: rozhoduje,
či stanica môže byť v bazéne pre živý lov a mix. Neznamená to lepšiu
stanicu — len sledovateľnú.
"""

from __future__ import annotations

import concurrent.futures as futures

from .nowplaying import probe


def annotate_watchable(catalog: list[dict], *, workers: int = 16,
                       timeout: float = 6.0) -> int:
    """Doplní do každej položky `watchable` a `nowplaying_endpoint`.

    Vracia počet sledovateľných staníc.
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
