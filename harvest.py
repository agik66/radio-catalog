#!/usr/bin/env python3
"""Hourly harvest of what stations are playing → artist index.

    ./harvest.py --catalog dist/catalog.json --data data/

The index does not need completeness, only a representative sample. At an
hourly cadence we see ~24 tracks per station per day; over a month ~700, which
is plenty for "this station plays Nirvana".

Only AGGREGATED counts are stored, never the raw history — otherwise the git
repo grows without bound. One row per (station, artist) pair.

The one thing kept per round rather than aggregated is which stations agreed on
the exact song at the exact moment: that is the evidence simulcast detection
runs on, and it is folded into a running total the same way (see
`blare_catalog/simulcast.py`).
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
from datetime import datetime, timezone
from pathlib import Path

from blare_catalog import simulcast
from blare_catalog.nowplaying import normalize_artist, probe


def collect(stations: list[dict], *, workers: int = 12,
            timeout: float = 6.0) -> list[dict]:
    """One round: what every watchable station is playing right now."""
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
    """Fold observations into the aggregated index. Returns (new pairs, total)."""
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
            # The same track seen twice within one round must not count twice,
            # but an hour apart is a wide enough gap for that not to matter.
            entry["n"] += 1
            entry["last"] = stamp
        artists.setdefault(obs["artist_key"], obs["artist"])

    index["updated"] = stamp
    index["rounds"] = index.get("rounds", 0) + 1
    return new, len(pairs)


def build_search_index(index: dict, catalog: list[dict], *,
                       min_plays: int = 1,
                       groups: list[simulcast.Group] | None = None) -> dict:
    """Invert the aggregate into what the app needs: artist → stations.

    Counts GROUPS, not raw stations. A broadcaster carrying one programme on a
    dozen mounts is one place to hear the artist, and both things this index
    feeds — the MIX hop list and the ranking signal behind search — are wrong if
    it counts as a dozen.
    """
    by_uuid = {e["uuid"]: e for e in catalog}
    rep_of = simulcast.representative_map(groups or [])
    artist_to_stations: dict[str, dict[str, dict]] = {}

    for key, stat in index.get("pairs", {}).items():
        uuid, artist_key = key.split("|", 1)
        if stat["n"] < min_plays or uuid not in by_uuid:
            continue
        canonical = rep_of.get(uuid, uuid)
        if canonical not in by_uuid:
            canonical = uuid
        slot = artist_to_stations.setdefault(artist_key, {})
        row = slot.get(canonical)
        if row is None:
            slot[canonical] = {
                "uuid": canonical,
                "n": stat["n"],
                "watchable": by_uuid[canonical].get("watchable", False),
                "variants": 1,
            }
        else:
            # Plays observed on any feed of a group are plays of that broadcast.
            row["n"] += stat["n"]
            row["variants"] += 1

    out: dict[str, list[dict]] = {}
    for artist_key, slot in artist_to_stations.items():
        stations = list(slot.values())
        # Watchable stations first — the live hunt rests on them.
        stations.sort(key=lambda s: (not s["watchable"], -s["n"]))
        del stations[24:]
        for station in stations:
            if station["variants"] == 1:
                del station["variants"]
        out[artist_key] = stations

    result = {
        "updated": index.get("updated"),
        "rounds": index.get("rounds", 0),
        "names": index.get("artists", {}),
        "index": out,
    }
    if groups:
        # The app needs the mapping too: a station it holds may be a member of
        # a group the index only ever names by its representative.
        result["simulcast"] = {g.representative: g.members for g in groups}
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Harvest what stations are playing")
    ap.add_argument("--catalog", default="dist/catalog.json")
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="dist")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--only-watchable", action="store_true", default=True)
    # Opt-in, not opt-out. Review found the co-occurrence state grows
    # quadratically and is never pruned, and harvest runs hourly in CI and
    # commits its output -- so an unfinished detector must not be the default.
    ap.add_argument("--simulcast", action="store_true",
                    help="enable simulcast detection (experimental, see simulcast.py)")
    args = ap.parse_args()

    catalog = json.loads(Path(args.catalog).read_text("utf-8"))
    stations = [e for e in catalog if e.get("watchable")] if args.only_watchable else catalog
    if not stations:
        print("No watchable stations — run build.py --watchable")
        return 1

    data_dir = Path(args.data)
    data_dir.mkdir(parents=True, exist_ok=True)
    index_path = data_dir / "plays.json"
    index = json.loads(index_path.read_text("utf-8")) if index_path.exists() else {}

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    print(f"[harvest] {stamp} · {len(stations)} watchable stations")

    observations = collect(stations, workers=args.workers)
    print(f"[harvest] tracks captured: {len(observations)}/{len(stations)}")

    new, total = merge(index, observations, stamp=stamp)
    print(f"[harvest] new station–artist pairs: {new} · total: {total}")
    print(f"[harvest] rounds so far: {index['rounds']}")

    index_path.write_text(json.dumps(index, ensure_ascii=False,
                                     separators=(",", ":"), sort_keys=True), "utf-8")

    groups: list[simulcast.Group] = []
    if args.simulcast:
        sim_path = data_dir / "simulcast.json"
        state = simulcast.load(sim_path)
        agreed = simulcast.observe(state, observations, stamp=stamp)
        groups = simulcast.detect(state, catalog)
        simulcast.save(sim_path, state)
        collapsed = sum(g.size for g in groups) - len(groups)
        print(f"[harvest] simulcast: {agreed} agreeing pairs this round · "
              f"{len(groups)} groups · {collapsed} stations collapsed")

    search = build_search_index(index, catalog, groups=groups)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "artist_index.json").write_text(
        json.dumps(search, ensure_ascii=False, separators=(",", ":")), "utf-8")
    print(f"[harvest] artists in index: {len(search['index'])}")

    top = sorted(search["index"].items(), key=lambda kv: -len(kv[1]))[:8]
    if top:
        print("[harvest] most widespread: "
              + ", ".join(f"{search['names'].get(k, k)}({len(v)})" for k, v in top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
