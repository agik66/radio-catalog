#!/usr/bin/env python3
"""Harvest RAW ICY titles as (station, title) pairs, for the iOS orientation corpus.

Unlike harvest.py this keeps the raw line and does NOT parse it — the whole
point is to measure a parser against it, so nothing here may decide which half
is the artist.

    ./harvest_titles.py --out corpus.json --stations 600 --rounds 8 --gap 300
"""
from __future__ import annotations

import argparse, json, random, sys, time
import concurrent.futures as futures
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from blare_catalog.nowplaying import probe


def pick(catalog, n, seed):
    stations = [s for s in catalog if s.get("watchable") and s.get("url")]
    rng = random.Random(seed)
    rng.shuffle(stations)
    return stations[:n]


def round_once(stations, workers=24, timeout=6.0):
    def one(s):
        try:
            r = probe(s["url"], timeout=timeout)
        except Exception:
            return None
        if r is None or not r.raw:
            return None
        return (s["url"], r.raw)
    out = []
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for row in pool.map(one, stations):
            if row:
                out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--catalog", default="dist/catalog.json")
    ap.add_argument("--stations", type=int, default=600)
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--gap", type=float, default=300)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--names-out", default=None)
    a = ap.parse_args()

    catalog = json.load(open(a.catalog))
    stations = pick(catalog, a.stations, a.seed)
    names = {s["url"]: s.get("name", "") for s in stations}
    if a.names_out:
        Path(a.names_out).write_text(json.dumps(names, ensure_ascii=False))
    print(f"[harvest] {len(stations)} stations, {a.rounds} rounds, gap {a.gap}s", flush=True)

    seen, pairs = set(), []
    for i in range(a.rounds):
        t0 = time.time()
        got = round_once(stations)
        new = 0
        for row in got:
            key = (row[0], row[1])
            if key in seen:
                continue
            seen.add(key)
            pairs.append([row[0], row[1]])
            new += 1
        Path(a.out).write_text(json.dumps(pairs, ensure_ascii=False))
        print(f"[harvest] round {i+1}/{a.rounds}: {len(got)} titles, {new} new, "
              f"{len(pairs)} distinct, {time.time()-t0:.0f}s", flush=True)
        if i + 1 < a.rounds:
            time.sleep(max(0, a.gap - (time.time() - t0)))
    print(f"[harvest] done: {len(pairs)} distinct pairs", flush=True)


if __name__ == "__main__":
    main()
