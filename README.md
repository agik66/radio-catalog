# radio-catalog

Builds the station catalog for **Blare** — an internet radio app that finds
stations playing a specific artist.

This is a plain deterministic Python program. **No AI agent, by design** —
verifiability is the entire point of this component.

## Usage

```bash
./build.py --countries SK,CZ,DE --tags rock,jazz --limit 800 --out dist --watchable
./harvest.py
```

Outputs in `dist/`:

| File | Purpose |
|---|---|
| `catalog.json` | the catalog the app consumes |
| `artist_index.json` | artist → stations that play them |
| `stats.json` | quality metrics, tracked over time |

## Verify against reality

The database lies. `lastcheckok` is roughly 95% accurate — in one sample of
60 stations it reported all 60 alive and three were dead. The real test is
playback through an actual AVFoundation stack:

```bash
cd ../Blare_ios
./scripts/triage.sh ../catalog/dist/triage_input.json verified.json 8 20
```

## Architecture

**`net.py` — safety.** Station URLs are attacker-writable strings, not
configuration. Blocks SSRF (loopback, RFC1918, cloud metadata endpoints),
disallowed schemes and ports. Verified against 11 real attack patterns.

**`radiobrowser.py` — source.** Radio Browser client with DNS-based mirror
discovery, rate limiting and the required User-Agent.

**`nowplaying.py` — what's playing.** Reads the current track from Icecast
and Shoutcast status endpoints *without downloading audio*. About 36% of
stations expose one. The rest can only be read from ICY metadata inside the
audio stream, which is expensive and happens in the iOS probe.

**`genres.py` — taxonomy.** Maps free-form multilingual tags to canonical
genres. Without this, "filter by genre" is just full-text search over chaos.
Coverage is measured (`stats.json` → `taxonomy_coverage_pct`); unmapped tags
sorted by frequency are the roadmap for what to add next.

**`curate.py` — quality gate.** Bitrate and codec floors, deduplication by
normalized host and path, and an editorial layer that always wins.

**`watchable.py` — pool selection.** Flags stations that can be polled
cheaply. This is a curation criterion nobody else uses, and it decides which
stations can take part in live artist hunting.

## The harvester

`harvest.py` runs hourly via GitHub Actions and records what each watchable
station is playing. It stores **aggregated counts only**, never raw history,
so the repository stays small.

The index does not need completeness, only a representative sample. At hourly
cadence we observe ~24 tracks per station per day; over a month that is ~700,
which is ample to establish "this station plays Nirvana."

Simulation on measured parameters says the index reaches 88% coverage of
played tracks after 30 days. **That is calendar time no amount of work can
compress**, which is why the harvester runs from day one.

## Why this repository is public

GitHub Actions minutes are unlimited on public repositories. The hourly
harvest is the entire backend, and it costs nothing.
