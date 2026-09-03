"""Python mirror of the iOS `StreamTitle` orientation resolver.

WHY IT EXISTS: the Swift resolver is the shipping code, but tuning it needs
numbers over tens of thousands of titles and a lexicon that does not fit in a
simulator. This module is a line-for-line port of the decision logic in
`Blare/Screens/PlayerScreen.swift` so an experiment run here predicts what the
app will do. When one changes, change the other.

The junk/mojibake filter is ported too, because a coverage number that counts
junk differently from the app is not a number about the app.

`measure_orientation.py` is the entry point that uses this.
"""
from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------- fold

_BRACKETED = re.compile(r"[\(\[].*?[\)\]]")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def fold(text: str) -> str:
    """Mirror of `StreamTitle.fold`."""
    s = text.lower()
    if not s.isascii():
        s = "".join(c for c in unicodedata.normalize("NFD", s)
                    if unicodedata.category(c) != "Mn")
    s = _BRACKETED.sub(" ", s)
    s = _NON_ALNUM.sub(" ", s)
    s = " ".join(s.split())
    if s.startswith("the "):
        s = s[4:]
    return s


# ------------------------------------------------------- title splitting

SEPARATOR = re.compile(r"\s+[-–—]\s+")
EDGE_JUNK = " -–—\"'"

VERSION_SUFFIX = re.compile(
    r"[\(\[][^\)\]]*\b(mix|remix|edit|version|live|acoustic|instrumental|radio"
    r"|remaster|remastered|cover|prod|feat|ft|featuring|extended|club|original"
    r"|single|album|demo|bonus|explicit|clean|mono|stereo|reprise|intro|outro"
    r"|part|pt|vocal|dub)\b", re.I)

BARE_COLLABORATION = re.compile(
    r"(?<![\(\[])\b(feat\.?|ft\.?|featuring|vs\.?|pres\.?|presents)\b(?![^\(\[]*[\)\]])",
    re.I)

ARTIST_FIRST = 0
TRACK_FIRST = 1


def split_halves(text: str):
    """(left, right) or None, using the app's separator rule."""
    m = SEPARATOR.search(text)
    if not m:
        return None
    left = text[:m.start()].strip(EDGE_JUNK)
    right = text[m.end():].strip(EDGE_JUNK)
    if not left or not right or len(left) > 120 or len(right) > 120:
        return None
    return left, right


def _annotation_end(half: str, match: re.Match, bracketed: bool) -> int:
    """Where the cue's annotation stops inside `half`."""
    if not bracketed:
        # "feat. X" runs to the end of the half: the featured name is part of it.
        return len(half.rstrip())
    close = re.search(r"[\)\]]", half[match.start():])
    return match.start() + close.end() if close else len(half.rstrip())


def _is_bound(half: str, match: re.Match, *, bracketed: bool, is_left: bool) -> bool:
    """Can this cue be explained as an annotation appended to the whole LINE?

    A station that annotates a line appends the annotation at the end, where it
    lands textually inside whichever half comes last — saying nothing about that
    half. This is the one way both cues are known to lie: Europa 2 SK's
    "She - Karin Ann (Remix Benny Benassi)" is track-first with the remix credit
    hanging off the ARTIST, and "Calvin Harris - Feels feat. Pharrell" is
    artist-first with the feature credit hanging off the TRACK.

    A cue that does not reach the end of the line cannot have arrived that way,
    so it is bound to the half it sits in. A cue in the left half always
    qualifies: the separator and the other half come after it.
    """
    if is_left:
        return True
    return _annotation_end(half, match, bracketed) < len(half.rstrip())


def structural_evidence(left: str, right: str, whole: str | None = None):
    """(orientation, bound) for the strongest structural cue, or (None, False).

    `bound` is whether the cue is safe to act on for THIS title; an unbound cue
    is still real evidence in aggregate and still trains the station tally.
    """
    lv, rv = VERSION_SUFFIX.search(left), VERSION_SUFFIX.search(right)
    if rv and not lv:                       # a version credit marks a TRACK
        return ARTIST_FIRST, _is_bound(right, rv, bracketed=True, is_left=False)
    if lv and not rv:
        return TRACK_FIRST, _is_bound(left, lv, bracketed=True, is_left=True)

    lc, rc = BARE_COLLABORATION.search(left), BARE_COLLABORATION.search(right)
    if lc and not rc:                       # a bare feat. joins PERFORMERS
        return ARTIST_FIRST, _is_bound(left, lc, bracketed=False, is_left=True)
    if rc and not lc:
        return TRACK_FIRST, _is_bound(right, rc, bracketed=False, is_left=False)
    return None, False


# --------------------------------------------------- the rest of the pipeline
#
# Everything below mirrors `StreamTitle.reading(from:station:)` and
# `StationConventions`. It is here rather than in a scratch file because the
# accuracy figures quoted in the Swift docstring are claims, and a claim nobody
# can re-run is a claim nobody can check.

JUNK = re.compile("|".join([
    r"^unknown artist", r"^unknown$", r"^\s*-\s*$",
    r"^advert", r"^reklama", r"^werbung", r"^commercial",
    r"^jingle", r"^id\s*$", r"^station id",
    r"^live stream", r"^untitled", r"^no title", r"^\d+\s*$",
]), re.I)

MOJIBAKE = re.compile(
    "[\u0080-\u009F]"
    "|[\u00C3\u00C2\u00C5\u00D0\u00D1][A-Z]"
    "|\u00C3[\u00A1-\u00FF]"
    "|\u00C5[\u00A1\u00BE]")

HAS_WORD = re.compile(r"[^\W\d_]{2,}", re.U)


class Conventions:
    """Port of `StationConventions`.

    NOTE that the app's copy is backed by `UserDefaults`, which in the simulator
    survives between test runs and across checkouts. This copy always starts
    empty, which is the honest baseline — see the `--clean` note in
    `measure_orientation.py`.
    """

    MIN_STRONG = 2

    def __init__(self, min_weak: int = 3):
        self.min_weak = min_weak
        self.counts: dict[str, list[int]] = {}
        self.counted: set[tuple[str, str]] = set()

    def verdict(self, station: str):
        t = self.counts.get(station)
        if not t:
            return None
        hi, lo = max(t[0], t[1]), min(t[0], t[1])
        if hi >= self.MIN_STRONG and hi > lo * 2:
            return ARTIST_FIRST if t[0] > t[1] else TRACK_FIRST
        if hi:
            return None
        if t[2] >= self.min_weak and t[3] == 0:
            return ARTIST_FIRST
        if t[3] >= self.min_weak and t[2] == 0:
            return TRACK_FIRST
        return None

    def record(self, orient, strength: int, station: str, title: str) -> None:
        if strength < 1 or not station:
            return
        fingerprint = (station, title)
        if fingerprint in self.counted:      # one title, one vote
            return
        self.counted.add(fingerprint)
        t = self.counts.setdefault(station, [0, 0, 0, 0])
        i = (0 if strength >= 2 else 2) + (0 if orient == ARTIST_FIRST else 1)
        if t[i] < 64:
            t[i] += 1


class Lexicon:
    """Folded artist names, and `artist \x01 track` pair keys."""

    def __init__(self, artists=None, pairs=None):
        self.artists = artists or set()
        self.pairs = pairs or set()

    def is_artist(self, folded: str) -> bool:
        return folded in self.artists

    def is_pair(self, artist: str, track: str) -> bool:
        return (artist + "\x01" + track) in self.pairs

    @classmethod
    def from_files(cls, artists_path=None, pairs_path=None, discography=None):
        import json
        artists, pairs = set(), set()
        if artists_path:
            artists |= {ln for ln in
                        open(artists_path).read().split("\n") if ln}
        if pairs_path:
            pairs |= {ln for ln in open(pairs_path).read().split("\n") if ln}
        if discography:
            for name, albums in json.loads(open(discography).read()).items():
                key = fold(name)
                if not key:
                    continue
                artists.add(key)
                for album in (albums if isinstance(albums, list) else []):
                    for entry in (album.get("tracks") or []):
                        title = fold(entry.get("title") or "")
                        if title:
                            pairs.add(key + "\x01" + title)
        return cls(artists, pairs)


def evidence(left: str, right: str, whole: str, lex: Lexicon, *, bound_decides=True):
    """(orientation, strength, source). 3 pair, 2 name or bound cue, 1 trailing."""
    l, r = fold(left), fold(right)
    lp, rp = lex.is_pair(l, r), lex.is_pair(r, l)
    if lp and not rp:
        return ARTIST_FIRST, 3, "pair"
    if rp and not lp:
        return TRACK_FIRST, 3, "pair"
    la, ra = lex.is_artist(l), lex.is_artist(r)
    if la and not ra:
        return ARTIST_FIRST, 2, "name"
    if ra and not la:
        return TRACK_FIRST, 2, "name"
    o, bound = structural_evidence(left, right, whole)
    if o is None:
        return None, 0, "none"
    return o, (2 if (bound and bound_decides) else 1), ("bound" if bound else "trailing")


def reading(raw: str, station, lex: Lexicon, conv: Conventions, *,
            bound_decides=True):
    """('song', artist, track, source) | ('unsplit', text) | ('notmusic',)."""
    text = " ".join(raw.split())
    if len(text) < 4 or JUNK.search(text) or MOJIBAKE.search(text):
        return ("notmusic",)
    halves = split_halves(text)
    if not halves:
        return ("unsplit", text)
    left, right = halves
    if JUNK.search(left) or JUNK.search(right):
        return ("notmusic",)

    o, strength, source = evidence(left, right, text, lex,
                                   bound_decides=bound_decides)
    decision = o if strength >= 2 else None
    if station:
        if o is not None:
            conv.record(o, strength, station, text)
        if decision is None:
            decision, source = conv.verdict(station), "station"
    if decision is None:
        return ("unsplit", text)
    artist, track = (left, right) if decision == ARTIST_FIRST else (right, left)
    if not HAS_WORD.search(artist) or len(artist) > 90:
        return ("unsplit", text)
    return ("song", artist, track, source)


def run(rows, lex: Lexicon, *, min_weak=3, **kw):
    """Play a whole corpus of (station, raw) through the pipeline."""
    conv = Conventions(min_weak=min_weak)
    out = {"song": 0, "unsplit": 0, "notmusic": 0, "songs": [], "unsplits": []}
    for station, raw in rows:
        result = reading(raw, station, lex, conv, **kw)
        out[result[0]] += 1
        if result[0] == "song":
            out["songs"].append((station, raw, result[1], result[2], result[3]))
        elif result[0] == "unsplit":
            out["unsplits"].append((station, raw))
    musical = out["song"] + out["unsplit"]
    out["rate"] = 100.0 * out["song"] / musical if musical else 0.0
    out["conv"] = conv
    return out
