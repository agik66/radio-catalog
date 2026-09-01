#!/usr/bin/env python3
"""Hodinový zber toho, čo stanice hrajú → index interpretov.

    ./harvest.py --catalog dist/catalog.json --data data/

Index nepotrebuje úplnosť, len reprezentatívnu vzorku. Pri hodinovom behu
uvidíme ~24 skladieb na stanicu denne; za mesiac ~700, čo bohato stačí na
"táto stanica hráva Nirvanu".

Ukladáme len AGREGOVANÉ počty, nie surovú históriu — inak git repo narastie
do nemožna. Jeden riadok na dvojicu (stanica, interpret).
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
from datetime import datetime, timezone
from pathlib import Path

from cliamp_catalog.nowplaying import normalize_artist, probe


def collect(stations: list[dict], *, workers: int = 12,
            timeout: float = 6.0) -> list[dict]:
    """Jedno kolo: čo hrá každá sledovateľná stanica práve teraz."""
    def check(entry: dict):
        result = probe(entry["url"], timeout=timeout)
        if result is None or not result.is_music:
            return None
        return {
            "uuid": entry["uuid"],
            "artist": result.artist,
            "artist_key": normalize_artist(result.artist),
            "track": result.track,
        }

    seen: list[dict] = []
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for observation in pool.map(check, stations):
            if observation and observation["artist_key"]:
                seen.append(observation)
    return seen


def merge(index: dict, observations: list[dict], *, stamp: str) -> tuple[int, int]:
    """Zlúči pozorovania do agregovaného indexu. Vracia (nové dvojice, spolu)."""
    pairs = index.setdefault("pairs", {})
    artists = index.setdefault("artists", {})
    new = 0

    for obs in observations:
        key = f"{obs['uuid']}|{obs['artist_key']}"
        entry = pairs.get(key)
        if entry is None:
            pairs[key] = {"n": 1, "first": stamp, "last": stamp}
            new += 1
        else:
            # Tá istá skladba dokola v jednom kole sa nesmie počítať dvakrát,
            # ale hodinový odstup je dosť veľký na to, aby to nevadilo.
            entry["n"] += 1
            entry["last"] = stamp
        artists.setdefault(obs["artist_key"], obs["artist"])

    index["updated"] = stamp
    index["rounds"] = index.get("rounds", 0) + 1
    return new, len(pairs)


def build_search_index(index: dict, catalog: list[dict], *,
                       min_plays: int = 1) -> dict:
    """Prevráti agregát na to, čo appka potrebuje: interpret → stanice."""
    by_uuid = {e["uuid"]: e for e in catalog}
    artist_to_stations: dict[str, list[dict]] = {}

    for key, stat in index.get("pairs", {}).items():
        uuid, artist_key = key.split("|", 1)
        if stat["n"] < min_plays or uuid not in by_uuid:
            continue
        artist_to_stations.setdefault(artist_key, []).append({
            "uuid": uuid,
            "n": stat["n"],
            "watchable": by_uuid[uuid].get("watchable", False),
        })

    # Sledovateľné stanice hore — na nich stojí živý lov.
    for stations in artist_to_stations.values():
        stations.sort(key=lambda s: (not s["watchable"], -s["n"]))
        del stations[24:]

    return {
        "updated": index.get("updated"),
        "rounds": index.get("rounds", 0),
        "names": index.get("artists", {}),
        "index": artist_to_stations,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Zber toho, čo stanice hrajú")
    ap.add_argument("--catalog", default="dist/catalog.json")
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="dist")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--only-watchable", action="store_true", default=True)
    args = ap.parse_args()

    catalog = json.loads(Path(args.catalog).read_text("utf-8"))
    stations = [e for e in catalog if e.get("watchable")] if args.only_watchable else catalog
    if not stations:
        print("Žiadne sledovateľné stanice — spusti build.py --watchable")
        return 1

    data_dir = Path(args.data)
    data_dir.mkdir(parents=True, exist_ok=True)
    index_path = data_dir / "plays.json"
    index = json.loads(index_path.read_text("utf-8")) if index_path.exists() else {}

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    print(f"[harvest] {stamp} · {len(stations)} sledovateľných staníc")

    observations = collect(stations, workers=args.workers)
    print(f"[harvest] zachytených skladieb: {len(observations)}/{len(stations)}")

    new, total = merge(index, observations, stamp=stamp)
    print(f"[harvest] nových dvojíc stanica–interpret: {new} · spolu: {total}")
    print(f"[harvest] kôl doteraz: {index['rounds']}")

    index_path.write_text(json.dumps(index, ensure_ascii=False,
                                     separators=(",", ":"), sort_keys=True), "utf-8")

    search = build_search_index(index, catalog)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "artist_index.json").write_text(
        json.dumps(search, ensure_ascii=False, separators=(",", ":")), "utf-8")
    print(f"[harvest] interpretov v indexe: {len(search['index'])}")

    top = sorted(search["index"].items(), key=lambda kv: -len(kv[1]))[:8]
    if top:
        print("[harvest] najrozšírenejší: "
              + ", ".join(f"{search['names'].get(k, k)}({len(v)})" for k, v in top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
