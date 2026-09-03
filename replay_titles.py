#!/usr/bin/env python3
"""Fold already-harvested raw titles into the artist index.

The hourly harvest only ever sees what is playing at the minute it runs, and
four rounds of it produced 387 artists. Meanwhile the title-orientation work
captured ~10,500 (station url, raw title) pairs into data/titles_*.json and
never fed them back. This replays them through the orientation resolver (the
lexicon decides which half is the artist, which parse_title's positional
split gets wrong on every track-first station) and merges the result exactly
the way harvest.merge does, then rebuilds dist/artist_index.json.
"""
import glob
import html, json, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, ".")
from blare_catalog.orientation import Lexicon, run as orient
from blare_catalog.nowplaying import normalize_artist
from harvest import merge, build_search_index

RES = Path("/Users/michalecerik/Developer/Blare/Blare_ios/Blare/Resources")
catalog = json.loads(Path("dist/catalog.json").read_text())
by_url = {e["url"]: e for e in catalog}

rows = []
for f in sorted(glob.glob("data/titles_*.json")):
    # The capture files hold titles as the servers sent them, and some
    # servers HTML-escape ("Karel &#352;iktanc"). The live path decodes in
    # nowplaying.normalize; the replay must do the same or the entity ends
    # up inside the artist key ("brat 344 i ebenove").
    rows += [(u, html.unescape(t)) for u, t in json.loads(Path(f).read_text())]
rows = sorted(set(rows))
print(f"[replay] {len(rows)} distinct (url, title) pairs from {len(glob.glob('data/titles_*.json'))} files")

lex = Lexicon.from_files(artists_path=RES / "lexicon_artists.txt",
                         discography=RES / "discography.json")
out = orient(rows, lex)
print(f"[replay] oriented: {out['song']} songs, {out['unsplit']} unsplit, {out['notmusic']} not music "
      f"({out['rate']:.1f}% of musical titles resolved)")

obs, unknown_url = [], 0
for station_url, raw, artist, track, source in out["songs"]:
    e = by_url.get(station_url)
    if e is None:
        unknown_url += 1; continue
    key = normalize_artist(artist)
    if not key: continue
    obs.append({"uuid": e["uuid"], "artist": artist, "artist_key": key, "track": track, "raw": raw})
print(f"[replay] observations: {len(obs)}  (skipped {unknown_url} with a url not in the catalog)")

index_path = Path("data/plays.json")
index = json.loads(index_path.read_text())
before = len(index.get("artists", {}))
stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
new, total = merge(index, obs, stamp=stamp)
index_path.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
print(f"[replay] pairs: +{new} new, {total} total · artists {before} -> {len(index['artists'])} · rounds {index['rounds']}")

search = build_search_index(index, catalog)
Path("dist/artist_index.json").write_text(json.dumps(search, ensure_ascii=False, separators=(",", ":")))
print(f"[replay] dist/artist_index.json: {len(search['index'])} artists")
top = sorted(search["index"].items(), key=lambda kv: -len(kv[1]))[:5]
for k, st in top: print(f"   {search['names'][k]!r}: {len(st)} stations")
