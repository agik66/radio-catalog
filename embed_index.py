#!/usr/bin/env python3
"""Regenerate the artist-index snapshot embedded in the iOS app.

`ArtistIndexStore.swift` carries `dist/artist_index.json` as a raw-deflate,
base64 blob so FIND works with no network. Nothing regenerated it: the app
shipped an index of 183 artists over 147 stations from a 646-station catalog
while dist/ had grown past it. This is the encoder for the decoder in
`ArtistIndex.init` — the shape is documented there and verified here by
decoding what we wrote.

    root: {u: updated, r: rounds, hunted: stations observed, cat: catalog size,
           s: [[name, country, bitrate, genres, watchable 0/1, url]],
           a: [[key, display, [[stationIndex, plays, watchable 0/1]]]]}
"""
import base64, html, json, re, sys, zlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from blare_catalog.nowplaying import normalize_artist

APP = Path("/Users/michalecerik/Developer/Blare/Blare_ios/Blare")
SWIFT = APP / "Screens/Find/ArtistIndexStore.swift"

search = json.loads(Path("dist/artist_index.json").read_text())
# The station rows embed everything FIND shows, so the index must reference
# the SAME catalog the app bundles — not dist/, which may be ahead of it.
catalog = json.loads((APP / "Resources/catalog.json").read_text())
by_uuid = {e["uuid"]: e for e in catalog}

stations, station_idx = [], {}
def station_index(uuid):
    if uuid in station_idx: return station_idx[uuid]
    e = by_uuid[uuid]
    station_idx[uuid] = len(stations)
    stations.append([e["name"], e.get("country") or "", e.get("bitrate") or 0,
                     "/".join(e.get("genres") or []), 1 if e.get("watchable") else 0, e["url"]])
    return station_idx[uuid]

artists, dropped = [], 0
for key, rows in search["index"].items():
    hits = []
    for row in sorted(rows, key=lambda r: (-r.get("watchable", 0), -r["n"])):
        if row["uuid"] not in by_uuid:      # not in the bundled catalog
            dropped += 1; continue
        hits.append([station_index(row["uuid"]), row["n"], 1 if row.get("watchable") else 0])
    if hits:
        display = search["names"].get(key, key)
        # Entries from harvests older than the html.unescape in nowplaying
        # still carry the entity in BOTH the display and the key
        # ("brat 344 i ebenove"). Decode, re-key, and fold into the clean
        # entry when one already exists.
        if "&" in display:
            decoded = html.unescape(display)
            if decoded != display:
                display, key = decoded, normalize_artist(decoded)
        artists.append([key, display, hits])

# Re-keyed entries may now collide with a clean one; merge their hits.
merged = {}
for key, display, hits in artists:
    if key in merged:
        seen = {h[0]: h for h in merged[key][2]}
        for h in hits:
            if h[0] in seen: seen[h[0]][1] += h[1]
            else: merged[key][2].append(h)
    else:
        merged[key] = [key, display, hits]
artists = list(merged.values())

root = {"u": search["updated"], "r": search["rounds"],
        "hunted": len(stations), "cat": len(catalog), "s": stations, "a": artists}
raw = json.dumps(root, ensure_ascii=False, separators=(",", ":")).encode()
co = zlib.compressobj(9, zlib.DEFLATED, -15)          # raw deflate, as NSData .zlib expects
blob = co.compress(raw) + co.flush()
b64 = base64.b64encode(blob).decode()

# Prove the round trip before touching the Swift file.
assert json.loads(zlib.decompress(base64.b64decode(b64), -15)) == root

src = SWIFT.read_text()
new, n = re.subn(r'(snapshotBase64 = ")[^"]+(")', lambda m: m.group(1) + b64 + m.group(2), src)
assert n == 1
SWIFT.write_text(new)
print(f"[embed] {len(artists)} artists over {len(stations)} stations (catalog {len(catalog)}), "
      f"{dropped} sightings dropped as outside the bundled catalog")
print(f"[embed] {len(raw)/1024:.0f} KB json -> {len(blob)/1024:.0f} KB deflate -> {len(b64)/1024:.0f} KB base64")
