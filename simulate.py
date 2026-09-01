#!/usr/bin/env python3
"""Is artist search worth building? Monte Carlo over measured data.

The parameters are NOT estimates — they come from measurements on live
stations:
  * a track change every ~233 s  (23 changes / 51 stations / 105 s)
  * 36 % of stations watchable without pulling audio
  * hourly harvesting = 24 samples per station per day
"""
import random, statistics

SONG_SECONDS = 233.0
SONGS_PER_DAY = 86400 / SONG_SECONDS          # ~371
SAMPLES_PER_DAY = 24                           # hourly harvesting
SAMPLE_RATE = SAMPLES_PER_DAY / SONGS_PER_DAY  # ~6.5 %

def zipf_rotation(k=300, exponent=1.0):
    """A station's rotation: k artists, Zipf-distributed airplay."""
    w = [1.0 / (i ** exponent) for i in range(1, k + 1)]
    total = sum(w)
    return [x / total for x in w]

print("=" * 62)
print("SIMULATION 1 — how fast the index matures")
print("=" * 62)
print(f"sampling rate: {SAMPLE_RATE*100:.1f} % of tracks played\n")
print(f"{'plays/day':>10} {'7 days':>9} {'30 days':>9} {'90 days':>9}")
for plays_per_day in (10, 5, 3, 2, 1, 0.5, 0.14):
    row = []
    for days in (7, 30, 90):
        p_miss = (1 - SAMPLE_RATE) ** (plays_per_day * days)
        row.append(f"{(1-p_miss)*100:>8.0f}%")
    label = f"{plays_per_day:g}" if plays_per_day >= 1 else f"{plays_per_day:.2f}"
    print(f"{label:>10} {row[0]} {row[1]} {row[2]}")

print("\n" + "=" * 62)
print("SIMULATION 2 — coverage of a station's rotation over time")
print("=" * 62)
random.seed(11)
probs = zipf_rotation()
def coverage_after(days, trials=300):
    """What share of tracks PLAYED comes from artists already discovered."""
    res = []
    for _ in range(trials):
        seen = set()
        for _ in range(int(SAMPLES_PER_DAY * days)):
            seen.add(random.choices(range(len(probs)), weights=probs)[0])
        res.append(sum(probs[i] for i in seen))
    return statistics.mean(res)
for d in (1, 3, 7, 14, 30, 60, 90):
    print(f"  after {d:>2} days: {coverage_after(d)*100:>5.1f} % of airplay covered")

print("\n" + "=" * 62)
print("SIMULATION 3 — how long to wait for a live hit (the hunt)")
print("=" * 62)
print("Pool of stations that play the given artist:\n")
print(f"{'stations':>8} {'3×/day':>12} {'1×/day':>12} {'1×/week':>12}")
for pool in (5, 10, 25, 50):
    row = []
    for rate in (3, 1, 1/7):
        hits_per_hour = pool * rate / 24
        wait_min = 60 / hits_per_hour if hits_per_hour else float('inf')
        row.append(f"{wait_min:>9.0f} min" if wait_min < 600 else f"{wait_min/60:>9.1f} h")
    print(f"{pool:>8} {row[0]} {row[1]} {row[2]}")

print("\n" + "=" * 62)
print("SIMULATION 4 — search success rate")
print("=" * 62)
print("User queries and rotations are both Zipf. The question: what share")
print("of searches finds something, as a function of catalog size.\n")
random.seed(7)
UNIVERSE = 20000
query_w = [1.0/(i**0.8) for i in range(1, UNIVERSE+1)]
qs = sum(query_w); query_w = [x/qs for x in query_w]

def hit_rate(n_stations, days=30, trials=4000):
    """An artist is findable if at least one station plays them often enough."""
    covered = set()
    for _ in range(n_stations):
        depth = random.randint(150, 500)
        start = random.choices(range(UNIVERSE), weights=query_w)[0] // 4
        for rank in range(depth):
            idx = min(start + rank, UNIVERSE - 1)
            plays = 3.0 / (rank + 1) ** 0.7
            if random.random() < 1 - (1 - SAMPLE_RATE) ** (plays * days):
                covered.add(idx)
    hits = sum(1 for _ in range(trials)
               if random.choices(range(UNIVERSE), weights=query_w)[0] in covered)
    return hits / trials

for n in (230, 500, 1000, 3000):
    print(f"  {n:>5} watchable stations → {hit_rate(n)*100:>5.1f} % of searches succeed")
