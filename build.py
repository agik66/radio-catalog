#!/usr/bin/env python3
"""Builds the station catalog from Radio Browser.

    ./build.py --countries SK,CZ,DE --out dist/
    ./build.py --countries SK --tags rock,jazz --limit 500

Outputs in --out:
    catalog.json        the whole catalog for the app
    triage_input.json   input for StreamTriage (verification on the simulator)
    stats.json          run statistics, for tracking quality over time

This is deliberately a plain deterministic script. No agent — verifiability is
the entire point of this component.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from blare_catalog import country_bounds, genres, siblings
from blare_catalog.curate import Gate, apply_editorial, curate, to_triage_input
from blare_catalog.radiobrowser import RadioBrowser
from blare_catalog.watchable import annotate_watchable


def main() -> int:
    ap = argparse.ArgumentParser(description="Builds the radio station catalog")
    ap.add_argument("--countries", default="SK,CZ",
                    help="comma-separated country codes (e.g. SK,CZ,DE)")
    ap.add_argument("--worldwide", type=int, metavar="N", default=0,
                    help="ALSO take the N most-clicked stations from every "
                         "other country Radio Browser knows, on top of "
                         "--countries and --tags")
    ap.add_argument("--tags", default="",
                    help="extra tags to fetch (e.g. rock,jazz)")
    ap.add_argument("--limit", type=int, default=2000, help="cap per country")
    ap.add_argument("--out", default="dist", help="output directory")
    ap.add_argument("--min-bitrate", type=int, default=96)
    ap.add_argument("--geo-margin", type=float,
                    default=country_bounds.DEFAULT_MARGIN_DEG, metavar="DEG",
                    help="how far outside its own country's bounding box a "
                         "coordinate may sit before it is dropped as false "
                         "(degrees, default %(default)s)")
    ap.add_argument("--allow-unchecked", action="store_true",
                    help="keep stations Radio Browser marked as broken")
    ap.add_argument("--curated", default="curated.json",
                    help="editorial list that goes on top")
    ap.add_argument("--watchable", action="store_true",
                    help="find out which stations can be watched without pulling audio")
    ap.add_argument("--watch-workers", type=int, default=16)
    ap.add_argument("--discover-siblings", action="store_true",
                    help="add the other mounts each known host advertises "
                         "(implies --watchable)")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rb = RadioBrowser()
    print(f"mirror: {rb.servers()[0]}")

    # TWO PASSES, DEEP AND WIDE, BECAUSE ONE CANNOT BE BOTH.
    #
    # `--countries` goes deep: several hundred stations each from the markets
    # with the most listeners. That is where the stations people actually
    # search for live, and it is what the artist index needs to be worth
    # anything.
    #
    # `--worldwide N` then goes wide: the N most-clicked stations from every
    # OTHER country the API knows. Without it the catalog stops at the
    # thirty-odd countries in the deep list, and the globe on LIB has nothing
    # over Africa, most of Asia and the Middle East — which is exactly the
    # complaint that started this ("why do we only have Slovak streams").
    #
    # It has to be per-country. A single "most clicked worldwide" query comes
    # back ~80% US and German, because clickcount is dominated by the big
    # markets; only sampling each country separately gives the small ones a
    # head at all.
    #
    # N is small on purpose. Measured: artist-search success is 66% at 230
    # stations and 74% at 3000, and flat above that — the ceiling is how many
    # stations publish now-playing metadata, not how many exist. Bundling all
    # 48 000 would add megabytes and roughly one percentage point.
    deep = [c.strip().upper() for c in args.countries.split(",") if c.strip()]

    passes: list[tuple[str, int]] = [(cc, args.limit) for cc in deep]
    if args.worldwide:
        try:
            countries = rb.countries()
        except RuntimeError as exc:
            print(f"could not list countries: {exc}", file=sys.stderr)
            return 1
        wide = [code for code, _ in countries if code not in set(deep)]
        passes += [(cc, args.worldwide) for cc in wide]
        print(f"deep: {len(deep)} countries at {args.limit}; "
              f"wide: {len(wide)} more at {args.worldwide}")

    raw = []
    for cc, cap in passes:
        try:
            got = rb.stations_by_country(cc, limit=cap)
        except RuntimeError as exc:
            print(f"  {cc}: ERROR {exc}", file=sys.stderr)
            continue
        if got:
            print(f"  {cc}: {len(got)}")
        raw += got

    for tag in [t.strip() for t in args.tags.split(",") if t.strip()]:
        try:
            got = rb.stations_by_tag(tag, limit=args.limit)
        except RuntimeError as exc:
            print(f"  #{tag}: ERROR {exc}", file=sys.stderr)
            continue
        print(f"  #{tag}: {len(got)}")
        raw += got

    if not raw:
        print("No stations were downloaded.", file=sys.stderr)
        return 1

    gate = Gate(min_bitrate_music=args.min_bitrate,
                require_lastcheckok=not args.allow_unchecked)
    catalog, stats = curate(raw, gate=gate, geo_margin=args.geo_margin)
    catalog = apply_editorial(catalog, Path(args.curated))

    print("\ncuration:")
    print(stats.report())

    want_watchable = args.watchable or args.discover_siblings
    if want_watchable:
        print(f"\nchecking watchability ({len(catalog)} stations)…")
        n_watch = annotate_watchable(catalog, workers=args.watch_workers)
        print(f"  watchable: {n_watch}/{len(catalog)} "
              f"({n_watch / len(catalog) * 100:.0f}%)")

    if args.discover_siblings:
        print("\ndiscovering sibling mounts…")
        found, sib_stats = siblings.discover(catalog, workers=args.watch_workers)
        added = siblings.merge_into(catalog, found)
        print(f"  hosts probed:     {sib_stats['hosts_probed']}")
        print(f"  – alias hosts:    {sib_stats['alias_hosts']}")
        print(f"  mounts listed:    {sib_stats['mounts_seen']}")
        print(f"  – below quality:  {sib_stats['dropped_quality']}")
        print(f"  – same station:   {sib_stats['dropped_variant']}")
        print(f"  – already known:  {sib_stats['already_known']}")
        print(f"  – unsafe URL:     {sib_stats['rejected_unsafe']}")
        print(f"  = new stations:   {added}")
        # Siblings are watchable by construction, but that is an inference from
        # the mount listing rather than a measurement, so it gets measured.
        if added:
            print(f"  verifying the {added} new stations…")
            confirmed = annotate_watchable(found, workers=args.watch_workers)
            print(f"  confirmed watchable: {confirmed}/{added}")

    tag_hist: dict[str, int] = {}
    for s in raw:
        for t in s.tags:
            tag_hist[t] = tag_hist.get(t, 0) + 1
    hit, miss, unmapped = genres.coverage(tag_hist)
    cov = hit / (hit + miss) * 100 if (hit + miss) else 0.0
    print(f"\n  taxonomy coverage: {cov:.1f}%")
    print("  most common unmapped: "
          + ", ".join(f"{t}({c})" for t, c in unmapped[:8]))

    (out / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=1), "utf-8")
    (out / "triage_input.json").write_text(
        json.dumps(to_triage_input(catalog), ensure_ascii=False, indent=1), "utf-8")
    (out / "stats.json").write_text(json.dumps({
        "input": stats.total, "kept": stats.kept,
        "dropped_unsafe": stats.dropped_unsafe,
        "dropped_dead": stats.dropped_dead,
        "dropped_codec": stats.dropped_codec,
        "dropped_bitrate": stats.dropped_bitrate,
        "dropped_duplicate": stats.dropped_duplicate,
        "with_genre": stats.with_genre, "with_geo": stats.with_geo,
        # Coordinates removed because they fell outside the country the station
        # itself claims — see blare_catalog/country_bounds.py. Tracked because
        # it is upstream data quality we do not control: a jump here means
        # Radio Browser has taken on a batch of bad geo, and a fall to zero
        # means the check has quietly stopped running.
        "geo_dropped_wrong_country": stats.geo_dropped_wrong_country,
        "geo_unverifiable_country": stats.geo_unverifiable_country,
        "geo_drop_rate_pct": round(
            stats.geo_dropped_wrong_country
            / max(1, stats.with_geo + stats.geo_dropped_wrong_country) * 100, 2),
        "geo_margin_deg": args.geo_margin,
        "taxonomy_coverage_pct": round(cov, 1),
        "by_genre": stats.by_genre,
        "top_unmapped_tags": unmapped[:40],
    }, ensure_ascii=False, indent=1), "utf-8")

    print(f"\nwritten to {out}/  (catalog.json, triage_input.json, stats.json)")
    print(f"next step: verification on the simulator\n"
          f"  cd ../Blare_ios && ./scripts/triage.sh "
          f"../catalog/{out}/triage_input.json /tmp/verified.json 8 20")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
