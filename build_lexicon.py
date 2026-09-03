#!/usr/bin/env python3
"""Build the app's orientation lexicon from MusicBrainz.

    ./build_lexicon.py --dump musicbrainz-canonical-dump-*.tar.zst \
                       --titles data/titles.json --out dist/

WHAT THIS IS FOR. `StreamTitle` in the iOS app decides which half of an ICY
title is the artist. Its strongest evidence is a name it recognises, so its
recall is capped by how much of the world's repertoire it knows: on the shipped
99-artist discography it could orient 2 % of live titles.

WHY IT IS NOT BUILT FROM `dist/artist_index.json`. That index is the obvious
source and it is poison. It was produced by `nowplaying.parse_title`, which
splits by POSITION — left half is the artist — and roughly a third of stations
send the other order, so the index lists "hotel california" and "show must go
on" as artists. Feeding it into the resolver would teach the resolver to make
exactly the error it exists to catch, and would do it invisibly, because the
wrong names would then be "recognised names" — the resolver's own top-strength
evidence.

THE RULE HERE IS THAT THE PARSER NEVER GETS A VOTE. Every name admitted below
is admitted by MusicBrainz, which knows artists and recordings apart because
they are different tables. The harvest is allowed to say "this string appeared
somewhere in a title on some station"; it is never allowed to say which half it
was. A string the harvest proposes and MusicBrainz does not confirm is dropped.

TWO TIERS.

  A. GLOBAL. Artists with at least one recording that MusicBrainz has seen
     released more than `--pop` times. Re-release count is the closest thing in
     the dump to "how much radio would play this", it is computed from
     `canonical_recording_redirect`, and it has nothing to do with any station.

  B. OBSERVED. Folded halves seen in the raw-title harvest, admitted only if
     MusicBrainz lists them as an artist. This is what carries the obscure
     local repertoire the global tier cuts: Billy Barman and Karin Ann are real
     artists with almost no re-releases, and they are what the Slovak stations
     actually play. Position in the title is not consulted, so a track title
     that happens to be a real artist name elsewhere is admitted as an artist
     name — which is true, and which tier P below is there to disambiguate.

  P. PAIRS, OPT-IN (`--pairs`). `artist \\x01 track` keys, for the case the name
     tiers cannot settle: BOTH halves are known artist names. It is off by
     default because it turned out not to earn its bundle size. Before the
     ambiguity test above existed, pair evidence rescued 45 of 960 measured
     titles, because half the "known names" were song titles; with the
     ambiguity test in place only 3 of 801 split titles have both halves known
     at all. Five megabytes for three titles is the wrong trade, and the rung
     is not lost: the curated `discography.json` still supplies pairs for the
     artists most likely to collide. Turn this on if the tie rate ever grows.

OUTPUT is two sorted, LF-separated, pure-ASCII files, byte order == sort order,
which is what `StreamTitle.SortedKeyFile` binary-searches without parsing.
"""
from __future__ import annotations

import argparse
import csv
import json
import pickle
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from blare_catalog.orientation import fold

csv.field_size_limit(10 ** 9)

MEMBER = "canonical/canonical_musicbrainz_data.csv"
REDIRECT = "canonical/canonical_recording_redirect.csv"


def stream(dump: Path, member_suffix: str):
    """Yield the decompressed bytes of one member of the canonical dump."""
    zstd = subprocess.Popen(["zstd", "-dc", str(dump)], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    # bsdtar and GNU tar both glob a member pattern; GNU needs --wildcards told
    # to it only for --exclude, not for extraction, so one spelling serves both.
    tar = subprocess.Popen(["tar", "-xOf", "-", f"*{member_suffix}"],
                           stdin=zstd.stdout, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL)
    zstd.stdout.close()
    return tar


def redirect_counts(dump: Path):
    """canonical recording mbid (top 64 bits) -> how many mbids redirect to it.

    A song released once appears once; a standard that has been put on four
    hundred compilations appears four hundred times. That is the popularity
    signal, and it is a property of MusicBrainz, not of any station.
    """
    proc = stream(dump, REDIRECT)
    buf = proc.stdout
    buf.readline()                                   # header
    cols = list(range(37, 45)) + list(range(46, 50)) + list(range(51, 55))
    chunks, carry = [], b""
    while True:
        blk = buf.read(1 << 26)
        if not blk and not carry:
            break
        blk, carry = carry + blk, b""
        n = len(blk) // 111                          # uuid,uuid,uuid + LF
        carry = blk[n * 111:]
        if n == 0:
            break
        a = np.frombuffer(blk[:n * 111], dtype=np.uint8).reshape(n, 111)
        v = np.zeros(n, dtype=np.uint64)
        for i in cols:
            c = a[:, i].astype(np.uint64)
            d = np.where(c >= 97, c - np.uint64(87), c - np.uint64(48)).astype(np.uint64)
            v = (v << np.uint64(4)) | d
        chunks.append(v)
    proc.wait()
    keys, counts = np.unique(np.concatenate(chunks), return_counts=True)
    return keys, counts.astype(np.int32)


def artist_evidence(dump: Path, keys, counts):
    """folded string -> [max re-release, times credited as artist, times a title].

    All three come out of one pass because the third is what makes the first two
    safe to use. See `admissible`.
    """
    proc = stream(dump, MEMBER)
    r = csv.reader((line.decode("utf-8", "replace") for line in proc.stdout))
    next(r)
    cache, tcache, best = {}, {}, {}
    while True:
        rows = []
        for row in r:
            rows.append((row[3], row[6], row[7]))
            if len(rows) >= 500_000:
                break
        if not rows:
            break
        v = np.fromiter((int(m[:8] + m[9:13] + m[14:18], 16) for _, m, _ in rows),
                        dtype=np.uint64, count=len(rows))
        i = np.clip(np.searchsorted(keys, v), 0, len(keys) - 1)
        pop = np.where(keys[i] == v, counts[i], 0)
        for (name, _, title), p in zip(rows, pop):
            f = cache.get(name)
            if f is None:
                f = cache[name] = fold(name)
            if f:
                e = best.get(f)
                p = int(p)
                if e is None:
                    best[f] = [p, 1, 0]
                else:
                    if p > e[0]:
                        e[0] = p
                    e[1] += 1
            g = tcache.get(title)
            if g is None:
                g = fold(title)
                if len(tcache) > 3_000_000:
                    tcache.clear()
                tcache[title] = g
            if g:
                e = best.get(g)
                if e is None:
                    best[g] = [0, 0, 1]
                else:
                    e[2] += 1
    proc.wait()
    return best


def admissible(e, pop_min: int, ratio: float) -> bool:
    """Is this string safe to treat as "a name that means an artist"?

    THE TRAP THIS CLOSES. MusicBrainz has an artist called Celebration, one
    called Boomerang and one called Smile. Admitting every name it lists turns
    "exactly one half is a known artist" — the resolver's second-strongest
    evidence — into a confident reversal of "Kool & The Gang - Celebration",
    because the obscure band IS in the index and Kool & The Gang's misspelling
    is not. Measured on the first run of this builder: 167k names lifted
    resolution from 5 % to 49 % and reversed roughly a third of the calls it
    added, which is worse than resolving nothing.

    What separates them is in the dump: `Celebration` is credited on 120
    recordings and is the TITLE of 1,342; `The Beatles` is credited on 16,899
    and titles 86. A name that is used as a song title more than it is used as a
    credit is not evidence about which half of a title is the artist — it is
    evidence that the string is ambiguous, and ambiguity is what tier P is for.
    """
    pop, as_artist, as_title = e
    if pop < pop_min or as_artist == 0:
        return False
    return as_artist >= ratio * as_title


def observed_halves(titles_path: Path | None) -> set[str]:
    """Folded halves of harvested titles — CANDIDATES ONLY, never verdicts.

    Both halves of every title are proposed. Which one was the artist is the
    question the app is trying to answer, so nothing here may assume it.
    """
    if not titles_path or not titles_path.exists():
        return set()
    from blare_catalog.orientation import split_halves
    out = set()
    for row in json.loads(titles_path.read_text()):
        if len(row) != 2:
            continue
        halves = split_halves(" ".join(row[1].split()))
        if not halves:
            continue
        for half in halves:
            f = fold(half)
            if f:
                out.add(f)
    return out


def emit_pairs(dump: Path, artists: set[str], out: Path):
    """`artist \\x01 track` for tracks whose own title is a known artist name."""
    proc = stream(dump, MEMBER)
    r = csv.reader((line.decode("utf-8", "replace") for line in proc.stdout))
    next(r)
    fc, tc, keys = {}, {}, set()
    for row in r:
        a = row[3]
        f = fc.get(a)
        if f is None:
            f = fc[a] = fold(a)
        if f not in artists:
            continue
        t = row[7]
        g = tc.get(t)
        if g is None:
            g = fold(t)
            if len(tc) > 2_000_000:
                tc.clear()
            tc[t] = g
        # A pair only ever breaks a tie between two known artist names, so a
        # track whose title is not one of those names can never be consulted.
        if g and g in artists:
            keys.add(f + "\x01" + g)
    proc.wait()
    out.write_text("\n".join(sorted(keys)))
    return len(keys)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True, type=Path)
    ap.add_argument("--titles", type=Path, default=None,
                    help="raw-title harvest, for tier B candidates")
    ap.add_argument("--out", type=Path, default=Path("dist"))
    ap.add_argument("--pop", type=int, default=2,
                    help="tier A cut: minimum re-release count")
    ap.add_argument("--pairs", action="store_true",
                    help="also emit lexicon_pairs.txt (see tier P in the module "
                         "docstring; it does not currently pay for its size)")
    ap.add_argument("--ratio", type=float, default=2.0,
                    help="a name must be credited as an artist this many times "
                         "more often than it is used as a song title")
    ap.add_argument("--cache", type=Path, default=None,
                    help="pickle of the artist popularity table, reused if present")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    if a.cache and a.cache.exists():
        best = pickle.loads(a.cache.read_bytes())
        print(f"[lexicon] evidence table from cache: {len(best)} strings", flush=True)
    else:
        keys, counts = redirect_counts(a.dump)
        print(f"[lexicon] redirect table: {len(keys)} canonical recordings "
              f"({time.time()-t0:.0f}s)", flush=True)
        best = artist_evidence(a.dump, keys, counts)
        print(f"[lexicon] evidence table: {len(best)} strings "
              f"({time.time()-t0:.0f}s)", flush=True)
        if a.cache:
            a.cache.write_bytes(pickle.dumps(best, 4))

    tier_a = {f for f, e in best.items() if admissible(e, a.pop, a.ratio)}
    proposed = observed_halves(a.titles)
    # Tier B lowers the popularity bar, never the ambiguity bar: an obscure local
    # band is exactly what this is for, an obscure band NAMED "Friends" is not.
    tier_b = {f for f in proposed
              if f in best and admissible(best[f], 0, a.ratio)} - tier_a
    artists = tier_a | tier_b
    print(f"[lexicon] tier A (re-released >= {a.pop}): {len(tier_a)}", flush=True)
    print(f"[lexicon] tier B (harvest proposed {len(proposed)}, "
          f"MusicBrainz confirmed {len(tier_b)} new)", flush=True)

    (a.out / "lexicon_artists.txt").write_text("\n".join(sorted(artists)))
    written = ["lexicon_artists.txt"]
    if a.pairs:
        n = emit_pairs(a.dump, artists, a.out / "lexicon_pairs.txt")
        written.append("lexicon_pairs.txt")
        print(f"[lexicon] pairs {n}", flush=True)
    print(f"[lexicon] artists {len(artists)} ({time.time()-t0:.0f}s)", flush=True)
    for f in written:
        print(f"[lexicon] {f}: {(a.out / f).stat().st_size/1e6:.2f} MB", flush=True)


if __name__ == "__main__":
    main()
