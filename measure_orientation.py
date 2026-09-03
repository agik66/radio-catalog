#!/usr/bin/env python3
"""Grade the title-orientation resolver: how many titles, and how many right.

    ./measure_orientation.py --titles data/titles_2026-09-02_holdout.json \
        --artists ../Blare_ios/Blare/Resources/lexicon_artists.txt \
        --discography ../Blare_ios/Blare/Resources/discography.json \
        --dump musicbrainz-canonical-dump-*.tar.zst --oracle-cache /tmp/mb_oracle.npy

WHY BOTH NUMBERS, ALWAYS. Resolution rate is trivial to raise: guess. The first
build of `lexicon_artists.txt` raised it from 5 % to 49 % and got 8 of 304
gradable titles BACKWARDS, because MusicBrainz has bands called Celebration and
Boomerang and the list admitted them. That is a worse parser with a better
number, and only the second column shows it. Never quote a rate from this tool
without the accuracy beside it.

THE ORACLE is every (artist, recording) pair in the MusicBrainz canonical dump —
31.8 M of them, far broader than anything shipped. A title is graded when
MusicBrainz confirms one direction as a real credit and not the other. It is an
independent judge for decisions the parser made from NAMES or from the station
tally; for decisions it made from a pair that is also in the shipped lexicon the
agreement is partly circular, which is why the report breaks the verdicts down
by which rung of the ladder decided.

Anything it cannot grade is reported as ungraded rather than as correct.

`--dump` is the canonical dump from
https://data.metabrainz.org/pub/musicbrainz/canonical_data/ (~2 GB). It is only
needed to build `--oracle-cache`; after that the cache alone is enough.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from blare_catalog.orientation import (ARTIST_FIRST, TRACK_FIRST, Lexicon,
                                       fold, run, split_halves)

csv.field_size_limit(10 ** 9)


def key64(text: str) -> int:
    """Stable 64-bit key. Not `hash()`: that is salted per process."""
    return int.from_bytes(hashlib.blake2b(text.encode(), digest_size=8).digest(), "big")


def build_oracle(dump: Path, cache: Path) -> np.ndarray:
    """Sorted 64-bit keys of every `artist \\x01 recording` pair MusicBrainz has."""
    zstd = subprocess.Popen(["zstd", "-dc", str(dump)], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    tar = subprocess.Popen(["tar", "-xOf", "-", "*canonical_musicbrainz_data.csv"],
                           stdin=zstd.stdout, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL)
    zstd.stdout.close()
    reader = csv.reader((line.decode("utf-8", "replace") for line in tar.stdout))
    next(reader)
    artist_fold, track_fold, keys = {}, {}, []
    t0, n = time.time(), 0
    for row in reader:
        n += 1
        a = artist_fold.get(row[3])
        if a is None:
            a = artist_fold[row[3]] = fold(row[3])
        if not a:
            continue
        t = track_fold.get(row[7])
        if t is None:
            t = fold(row[7])
            if len(track_fold) > 3_000_000:
                track_fold.clear()
            track_fold[row[7]] = t
        if t:
            keys.append(key64(a + "\x01" + t))
    tar.wait()
    out = np.unique(np.array(keys, dtype=np.uint64))
    np.save(cache, out)
    print(f"[oracle] {n} rows -> {len(out)} distinct pairs "
          f"({time.time()-t0:.0f}s), cached in {cache}", flush=True)
    return out


class Oracle:
    def __init__(self, pairs: np.ndarray):
        self.pairs = pairs

    def _known(self, artist: str, track: str) -> bool:
        v = np.uint64(key64(artist + "\x01" + track))
        i = int(np.searchsorted(self.pairs, v))
        return i < len(self.pairs) and self.pairs[i] == v

    def label(self, raw: str):
        """ARTIST_FIRST / TRACK_FIRST, or None when MusicBrainz cannot say."""
        halves = split_halves(" ".join(raw.split()))
        if not halves:
            return None
        l, r = fold(halves[0]), fold(halves[1])
        if not l or not r:
            return None
        lp, rp = self._known(l, r), self._known(r, l)
        if lp and not rp:
            return ARTIST_FIRST
        if rp and not lp:
            return TRACK_FIRST
        return None


def report(tag: str, rows, lex: Lexicon, oracle: Oracle | None, **kw):
    result = run(rows, lex, **kw)
    labels = {}
    for _, raw in rows:
        if raw not in labels:
            labels[raw] = oracle.label(raw) if oracle else None

    right = wrong = 0
    by_rung, misses = Counter(), []
    for station, raw, artist, _track, source in result["songs"]:
        want = labels.get(raw)
        if want is None:
            by_rung[source + ":ungraded"] += 1
            continue
        halves = split_halves(" ".join(raw.split()))
        got = ARTIST_FIRST if halves and artist == halves[0] else TRACK_FIRST
        if got == want:
            right += 1
            by_rung[source + ":ok"] += 1
        else:
            wrong += 1
            by_rung[source + ":WRONG"] += 1
            misses.append((source, raw, artist))

    graded = right + wrong
    print(f"\n=== {tag} ===")
    print(f"  sampled {len(rows)}   junk {result['notmusic']}   "
          f"resolved {result['song']}   unsplit {result['unsplit']}")
    if graded:
        print(f"  RESOLUTION {result['rate']:.1f}%   "
              f"ACCURACY {100.0*right/graded:.1f}% over {graded} graded ({wrong} wrong)")
    else:
        print(f"  RESOLUTION {result['rate']:.1f}%   ACCURACY not measured "
              f"(no oracle — pass --dump or --oracle-cache)")
    for rung, count in sorted(by_rung.items()):
        print(f"    {rung:<22} {count}")
    for source, raw, artist in misses:
        print(f"    WRONG [{source}] {raw}  ->  artist={artist!r}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--titles", required=True, type=Path,
                    help="[[station, raw title], ...] as harvest_titles.py writes")
    ap.add_argument("--artists", type=Path, help="lexicon_artists.txt")
    ap.add_argument("--pairs", type=Path, help="lexicon_pairs.txt, if shipped")
    ap.add_argument("--discography", type=Path, help="discography.json")
    ap.add_argument("--dump", type=Path, help="MusicBrainz canonical dump")
    ap.add_argument("--oracle-cache", type=Path, default=Path("/tmp/mb_oracle.npy"))
    ap.add_argument("--baseline", action="store_true",
                    help="also report the discography-only lexicon, for a before/after")
    a = ap.parse_args()

    rows = [r for r in json.loads(a.titles.read_text()) if len(r) == 2]
    stations = len({r[0] for r in rows})
    print(f"corpus: {len(rows)} (station, title) pairs from {stations} stations")

    if a.oracle_cache.exists():
        pairs = np.load(a.oracle_cache)
        print(f"[oracle] {len(pairs)} pairs from cache")
    elif a.dump:
        pairs = build_oracle(a.dump, a.oracle_cache)
    else:
        # Deliberately not fatal. The rate is worth having on its own — but it
        # is half a measurement, so say so loudly rather than printing a bare
        # percentage that reads like a result.
        print("[oracle] NOT AVAILABLE — resolution only, accuracy UNVERIFIED.\n"
              "         Pass --dump (canonical dump, ~2 GB) once to build the cache.",
              file=sys.stderr)
        pairs = None
    oracle = Oracle(pairs) if pairs is not None else None

    if a.baseline:
        # The parser AS IT SHIPPED on 2026-09-01, which is the only honest
        # "before": the 98-artist discography AND no bound-structure rung.
        # Leave `bound_decides` on here and this prints 13.4 % rather than
        # 10 %, because it is then the new parser wearing the old lexicon.
        report("before: discography only, structure only votes", rows,
               Lexicon.from_files(discography=a.discography), oracle,
               bound_decides=False)
    report("after: shipped lexicon", rows,
           Lexicon.from_files(a.artists, a.pairs, a.discography), oracle)

    # The Swift is the authority and this is a mirror of it. On the 2026-09-02
    # holdout corpus the two agree to within 9 titles of 1,698 (0.5 %); if that
    # gap ever widens, this file has drifted from PlayerScreen.swift and the
    # number to trust is the one `TitleCoverageTests` prints. Run it with the
    # app UNINSTALLED from the simulator — `StationConventions` persists its
    # tally in UserDefaults and a stale container reads high.
    print("\ncross-check: TEST_RUNNER_TITLES=<corpus> xcodebuild test "
          "-only-testing:StreamTriage/TitleCoverageTests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
