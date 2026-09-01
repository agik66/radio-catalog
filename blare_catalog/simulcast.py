"""Detecting simulcast groups — many stream variants carrying one broadcast.

A broadcaster commonly publishes the same programme several times over: bitrate
variants (`/blanikcz128.mp3`, `/blanikcz64.aac`), regional feeds that share a
network programme for most of the day, and mirrors on a second host. To the
catalog those look like separate stations. To a listener they are one.

That matters twice over. MIX hops between stations, and offering a dozen
identical feeds as a dozen choices is not a mix. And "N stations play this
artist" ranks search results, so an artist carried by one broadcaster on a dozen
mounts outranks one carried by ten genuinely different broadcasters.

WHAT IS AND IS NOT EVIDENCE

Host is not evidence, and this module never looks at one. Measured on
ice.abradio.cz: 45 of our stations sit on that single host, among them
Metalománie, Rádio Blaník and Hitrádio Faktor — unrelated broadcasters renting
the same Icecast. Grouping by host would collapse a hosting provider's whole
customer list into one station. It also misses the opposite case: Rádio Blaník
Oldies and Oldies Rádio are one broadcast served from two different hostnames,
and so are Hitradio Zlín and Hitrádio Vysočina.

Repeated agreement on the exact song at the exact moment is evidence. Two rock
stations playing the same hit in the same minute is a coincidence; two streams
agreeing on the song again and again is one encoder.

THE THRESHOLD

A pair is confirmed at MIN_SAME_ROUNDS agreements AND MIN_AGREEMENT of the
rounds where both were observed playing something.

Both halves are needed and they guard different failures. The ratio is what
separates a simulcast from a format twin: two mainstream stations drawing on a
~50-track hot rotation collide on roughly 2 % of rounds, while a genuine
simulcast agrees on essentially 100 % — the gap is enormous and no plausible
pair of independent stations lives near 0.75. The count is what stops a small
sample from reading 1/1 = 100 %. Three agreements at a ~2 % collision rate is a
per-pair probability under 1e-4, and across the ~34 000 pairs a 260-station
catalog can form that is a handful of candidates, all of which the ratio then
has to clear as well.

The evidence accumulates across harvest runs, so groups get stronger rather
than being re-guessed hourly; a pair that stops agreeing decays out on its own
because `both` keeps rising while `same` does not.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

MIN_SAME_ROUNDS = 3
MIN_AGREEMENT = 0.75


@dataclass
class Group:
    """A set of stations carrying one broadcast."""

    representative: str
    members: list[str] = field(default_factory=list)
    # Weakest confirmed edge holding the group together — useful when reviewing
    # whether a group deserves to exist at all.
    weakest_same: int = 0
    weakest_agreement: float = 0.0

    @property
    def size(self) -> int:
        return len(self.members)


def song_key(artist: str, track: str) -> str:
    """Comparable key for "this exact song".

    Deliberately strict about content and forgiving about typography: two feeds
    of one encoder emit byte-identical titles, so the only differences worth
    folding away are case, diacritics and punctuation. Anything looser would
    start matching different recordings of the same song.
    """
    text = unicodedata.normalize("NFKD", f"{artist}\x00{track}".lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9\x00]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _pair_key(a: str, b: str) -> str:
    return f"{a}|{b}" if a < b else f"{b}|{a}"


def load(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text("utf-8"))
    return {"rounds": 0, "co": {}, "groups": []}


def save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False,
                               separators=(",", ":"), sort_keys=True), "utf-8")


def observe(state: dict, observations: list[dict], *, stamp: str) -> int:
    """Fold one harvest round into the co-occurrence evidence.

    Returns the number of station pairs that agreed this round.

    `both` is only counted for pairs already on the books. Tracking it for every
    pair of stations observed would add ~10 000 rows per round to a file that is
    committed on every harvest, for pairs that will never be candidates. The
    cost is that a pair's agreement ratio is measured from its first agreement
    onward, which is why MIN_SAME_ROUNDS exists to backstop it.
    """
    co = state.setdefault("co", {})
    state["rounds"] = state.get("rounds", 0) + 1
    state["updated"] = stamp

    by_song: dict[str, list[str]] = {}
    playing: set[str] = set()
    for obs in observations:
        if not obs.get("artist") or not obs.get("track"):
            continue
        playing.add(obs["uuid"])
        by_song.setdefault(song_key(obs["artist"], obs["track"]), []).append(obs["uuid"])

    agreed: set[str] = set()
    for uuids in by_song.values():
        unique = sorted(set(uuids))
        if len(unique) < 2:
            continue
        for i, a in enumerate(unique):
            for b in unique[i + 1:]:
                key = _pair_key(a, b)
                agreed.add(key)
                entry = co.get(key)
                if entry is None:
                    co[key] = {"same": 1, "both": 1, "first": stamp, "last": stamp}
                else:
                    entry["same"] += 1
                    entry["both"] += 1
                    entry["last"] = stamp

    # Rounds where a known pair was both live but played different songs are the
    # evidence AGAINST it, and matter just as much as the agreements.
    for key, entry in co.items():
        if key in agreed:
            continue
        a, b = key.split("|", 1)
        if a in playing and b in playing:
            entry["both"] += 1

    return len(agreed)


def confirmed_pairs(state: dict, *, min_same: int = MIN_SAME_ROUNDS,
                    min_agreement: float = MIN_AGREEMENT) -> dict[str, dict]:
    out = {}
    for key, entry in state.get("co", {}).items():
        same, both = entry.get("same", 0), entry.get("both", 0)
        if same >= min_same and both and same / both >= min_agreement:
            out[key] = entry
    return out


def _representative(members: list[str], by_uuid: dict[str, dict]) -> str:
    """Pick the feed the app should actually play.

    Highest bitrate first — within one broadcast the variants differ only in
    quality. Popularity breaks ties between equal-quality mirrors, and the uuid
    breaks the rest so the choice does not drift between runs.
    """
    def rank(uuid: str) -> tuple:
        entry = by_uuid.get(uuid) or {}
        return (entry.get("bitrate") or 0, entry.get("popularity") or 0, uuid)

    return max(members, key=rank)


def detect(state: dict, catalog: list[dict], *, min_same: int = MIN_SAME_ROUNDS,
           min_agreement: float = MIN_AGREEMENT) -> list[Group]:
    """Turn the accumulated evidence into groups.

    Confirmed pairs are merged transitively: if A and B are one encoder and B
    and C are one encoder, so are A and C, even in a round where C happened to
    be down. That is sound for simulcasts precisely because the relation being
    detected is "same source", which is an equivalence — it would not be sound
    for a similarity score, which is why the pair test is deliberately strict.
    """
    by_uuid = {e["uuid"]: e for e in catalog}
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    pairs = confirmed_pairs(state, min_same=min_same, min_agreement=min_agreement)
    for key in pairs:
        a, b = key.split("|", 1)
        # A station that has left the catalog cannot represent anything.
        if a in by_uuid and b in by_uuid:
            union(a, b)

    clusters: dict[str, list[str]] = {}
    for uuid in list(parent):
        clusters.setdefault(find(uuid), []).append(uuid)

    groups: list[Group] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        members = sorted(members)
        member_set = set(members)
        inner = [pairs[k] for k in pairs
                 if set(k.split("|", 1)) <= member_set]
        groups.append(Group(
            representative=_representative(members, by_uuid),
            members=members,
            weakest_same=min((e["same"] for e in inner), default=0),
            weakest_agreement=round(
                min((e["same"] / e["both"] for e in inner), default=0.0), 3),
        ))

    groups.sort(key=lambda g: (-g.size, g.representative))
    state["groups"] = [
        {"representative": g.representative, "members": g.members,
         "weakest_same": g.weakest_same, "weakest_agreement": g.weakest_agreement}
        for g in groups
    ]
    return groups


def representative_map(groups: list[Group]) -> dict[str, str]:
    """station uuid → uuid that stands for its group. Ungrouped stations absent."""
    out: dict[str, str] = {}
    for group in groups:
        for uuid in group.members:
            out[uuid] = group.representative
    return out
