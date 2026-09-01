#!/usr/bin/env python3
"""Postaví katalóg staníc z Radio Browser.

    ./build.py --countries SK,CZ,DE --out dist/
    ./build.py --countries SK --tags rock,jazz --limit 500

Výstupy v --out:
    catalog.json        celý katalóg pre appku
    triage_input.json   vstup pre StreamTriage (overenie na simulátore)
    stats.json          štatistika behu, na sledovanie kvality v čase

Toto je zámerne obyčajný deterministický skript. Žiadny agent —
overiteľnosť je celý zmysel tohto komponentu.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cliamp_catalog import genres
from cliamp_catalog.curate import Gate, apply_editorial, curate, to_triage_input
from cliamp_catalog.radiobrowser import RadioBrowser
from cliamp_catalog.watchable import annotate_watchable


def main() -> int:
    ap = argparse.ArgumentParser(description="Postaví katalóg rádiových staníc")
    ap.add_argument("--countries", default="SK,CZ",
                    help="kódy krajín oddelené čiarkou (napr. SK,CZ,DE)")
    ap.add_argument("--tags", default="",
                    help="doplnkové tagy na stiahnutie (napr. rock,jazz)")
    ap.add_argument("--limit", type=int, default=2000, help="strop na krajinu")
    ap.add_argument("--out", default="dist", help="výstupný adresár")
    ap.add_argument("--min-bitrate", type=int, default=96)
    ap.add_argument("--allow-unchecked", action="store_true",
                    help="ponechať aj stanice, ktoré RB označil ako nefunkčné")
    ap.add_argument("--curated", default="curated.json",
                    help="editorský zoznam, ktorý ide na vrch")
    ap.add_argument("--watchable", action="store_true",
                    help="zistiť, ktoré stanice sa dajú sledovať bez sťahovania zvuku")
    ap.add_argument("--watch-workers", type=int, default=16)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rb = RadioBrowser()
    print(f"mirror: {rb.servers()[0]}")

    raw = []
    for cc in [c.strip().upper() for c in args.countries.split(",") if c.strip()]:
        try:
            got = rb.stations_by_country(cc, limit=args.limit)
        except RuntimeError as exc:
            print(f"  {cc}: CHYBA {exc}", file=sys.stderr)
            continue
        print(f"  {cc}: {len(got)}")
        raw += got

    for tag in [t.strip() for t in args.tags.split(",") if t.strip()]:
        try:
            got = rb.stations_by_tag(tag, limit=args.limit)
        except RuntimeError as exc:
            print(f"  #{tag}: CHYBA {exc}", file=sys.stderr)
            continue
        print(f"  #{tag}: {len(got)}")
        raw += got

    if not raw:
        print("Nestiahli sa žiadne stanice.", file=sys.stderr)
        return 1

    gate = Gate(min_bitrate_music=args.min_bitrate,
                require_lastcheckok=not args.allow_unchecked)
    catalog, stats = curate(raw, gate=gate)
    catalog = apply_editorial(catalog, Path(args.curated))

    print("\nkurácia:")
    print(stats.report())

    if args.watchable:
        print(f"\nzisťujem sledovateľnosť ({len(catalog)} staníc)…")
        n_watch = annotate_watchable(catalog, workers=args.watch_workers)
        print(f"  sledovateľných: {n_watch}/{len(catalog)} "
              f"({n_watch / len(catalog) * 100:.0f}%)")

    tag_hist: dict[str, int] = {}
    for s in raw:
        for t in s.tags:
            tag_hist[t] = tag_hist.get(t, 0) + 1
    hit, miss, unmapped = genres.coverage(tag_hist)
    cov = hit / (hit + miss) * 100 if (hit + miss) else 0.0
    print(f"\n  pokrytie taxonómie: {cov:.1f}%")
    print("  najčastejšie nezaradené: "
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
        "taxonomy_coverage_pct": round(cov, 1),
        "by_genre": stats.by_genre,
        "top_unmapped_tags": unmapped[:40],
    }, ensure_ascii=False, indent=1), "utf-8")

    print(f"\nzapísané do {out}/  (catalog.json, triage_input.json, stats.json)")
    print(f"ďalší krok: overenie na simulátore\n"
          f"  cd ../CliampIOS && ./scripts/triage.sh "
          f"../cliamp-catalog/{out}/triage_input.json /tmp/verified.json 8 20")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
