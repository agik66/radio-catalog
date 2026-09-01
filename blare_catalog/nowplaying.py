"""Finding out what a station is playing right now — without pulling audio.

Icecast and Shoutcast servers commonly expose a status endpoint carrying the
current title. A query costs a few kilobytes instead of hundreds, so it can be
polled often and from a phone. Measured on 150 stations: ~34 % are watchable
this way.

The rest can only be read from ICY metadata inside the audio stream — that is
expensive and is done by the simulator probe (`StreamTriage`).

MOUNT ATTRIBUTION. A status endpoint describes EVERY mount on the server, not
just ours. Taking the first title in the document attributes one mount's song
to every station sharing the host — measured on ice.abradio.cz, where 26 of our
stations were all reporting whatever `/blanikcz128.mp3` happened to be playing.
So the mount is matched by URL path, and a title is only accepted when it comes
from our own mount, or from a document that names no mount at all (SHOUTcast).
When our mount is not in the listing the station reports nothing, because a
neighbour's song is worse than silence: it is silently wrong.
"""

from __future__ import annotations

import html
import http.client
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

from .net import UnsafeURL, validate_url

USER_AGENT = "Blare/1.0 catalog-builder"

# Ordered by measured success rate: status-json.xsl wins by a wide margin.
STATUS_PATHS = ("/status-json.xsl", "/stats?json=1", "/status.xsl", "/7.html")

# Keys under which servers report the current title.
TITLE_KEYS = frozenset({"title", "songtitle", "yp_currently_playing", "song"})

# Titles that are not a song — station ID, filler, ad break.
JUNK_PATTERNS = (
    r"^unknown artist",
    r"^unknown$",
    r"^\s*-\s*$",
    r"^advert", r"^reklama", r"^werbung", r"^commercial",
    r"^jingle", r"^id\s*$", r"^station id",
    r"^live stream", r"^untitled", r"^no title",
    r"^\d+\s*$",
)
_JUNK_RE = re.compile("|".join(JUNK_PATTERNS), re.I)

# Mojibake: UTF-8 read as Latin-1. Verified against real data — "NOVÁK" arrives
# as "NOVÃK", i.e. Ã (0xC3) followed by an ASCII letter. In sound text Ã/Â/Å may
# be followed by a lowercase letter (São, Ålborg), never by an uppercase one.
_MOJIBAKE_RE = re.compile(
    "[\u0080-\u009f]"           # control characters do not occur in clean text
    "|[\u00c3\u00c2\u00c5\u00d0\u00d1][A-Z]"   # Ã + uppercase = broken encoding
    "|\u00c3[\u00a1-\u00ff]"    # Ã¡ Ã© Ã³ … the classic pairs
    "|\u00c5[\u00a1\u00be]"     # Å¡ Å¾
)

# An artist has to contain at least one run of two letters — this catches
# station IDs of the "- 0 N - 2000s on Radio" kind.
_HAS_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)

# Playlist wrappers a listen URL may carry; they name the same mount.
_PLAYLIST_SUFFIXES = (".m3u", ".m3u8", ".pls", ".xspf", ".asx")


@dataclass(frozen=True)
class Mount:
    """One mount point as the status endpoint describes it.

    `path` is the normalized URL path, which is the only field that reliably
    identifies a mount across sources — servers behind a CDN or an alias report
    `listen_url` on a different hostname than the one we asked (measured:
    ice.abradio.cz answers with listen URLs on ice.radia.cz).
    """

    path: str
    listen_url: str
    # Mount exactly as the server spelled it. `path` is folded for comparison,
    # but Icecast mount names are case-sensitive (dr.dk serves /A/A05H.mp3), so
    # anything that builds a URL has to use this instead.
    raw_path: str = ""
    title: str | None = None
    name: str = ""
    genre: str = ""
    codec: str = ""
    bitrate: int = 0
    listeners: int = 0


@dataclass(frozen=True)
class NowPlaying:
    raw: str
    artist: str | None
    track: str | None
    endpoint: str

    @property
    def is_music(self) -> bool:
        return bool(self.artist and self.track)


def mount_path(url_or_path: str) -> str:
    """Normalize a mount to a comparable form.

    Playlist wrappers and the SHOUTcast `/;` suffix name the same mount as the
    bare path. Case is folded for matching only, because a catalog URL and a
    status listing routinely disagree about it; `Mount.raw_path` keeps the real
    spelling for anything that has to build a URL back.
    """
    path = urlsplit(url_or_path).path if "//" in url_or_path else url_or_path
    path = (path or "/").split("?", 1)[0].strip().lower()
    path = path.rstrip(";")
    for suffix in _PLAYLIST_SUFFIXES:
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


def _raw_mount_path(url_or_path: str) -> str:
    """The mount as written, with only the URL wrapper removed."""
    path = urlsplit(url_or_path).path if "//" in url_or_path else url_or_path
    path = (path or "").split("?", 1)[0].strip()
    if path and not path.startswith("/"):
        path = "/" + path
    return path


def _codec_from_content_type(content_type: str) -> str:
    ct = (content_type or "").lower()
    if "aac" in ct:
        return "AAC"
    if "mpeg" in ct or "mp3" in ct:
        return "MP3"
    if "ogg" in ct or "vorbis" in ct:
        return "OGG"
    if "flac" in ct:
        return "FLAC"
    return ""


def _clean(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _icecast_mounts(data: dict) -> list[Mount]:
    stats = data.get("icestats")
    if not isinstance(stats, dict):
        return []
    sources = stats.get("source")
    if isinstance(sources, dict):      # a server with a single mount
        sources = [sources]
    if not isinstance(sources, list):
        return []

    out: list[Mount] = []
    for src in sources:
        if not isinstance(src, dict):
            continue
        listen = _clean(src.get("listenurl"))
        raw_mount = _clean(src.get("mount")) or listen
        if not raw_mount:
            continue
        title = _clean(src.get("title")) or _clean(src.get("yp_currently_playing"))
        try:
            bitrate = int(src.get("bitrate") or src.get("ice-bitrate") or 0)
        except (TypeError, ValueError):
            bitrate = 0
        try:
            listeners = int(src.get("listeners") or 0)
        except (TypeError, ValueError):
            listeners = 0
        out.append(Mount(
            path=mount_path(raw_mount),
            listen_url=listen,
            raw_path=_raw_mount_path(raw_mount),
            title=title or None,
            name=_clean(src.get("server_name")),
            genre=_clean(src.get("genre")),
            codec=_codec_from_content_type(_clean(src.get("server_type"))),
            bitrate=bitrate,
            listeners=listeners,
        ))
    return out


def _shoutcast_mounts(data: dict) -> list[Mount]:
    """SHOUTcast v2 `/stats?json=1` — one stream per port, flat object.

    The mount is left unnamed on purpose. There is only ever one stream behind
    this endpoint, and its `streampath` is usually "/" where the public URL says
    "/stream" or "/;" — naming it would only create a disagreement to resolve.
    """
    if "songtitle" not in data and "servertitle" not in data:
        return []
    try:
        bitrate = int(data.get("bitrate") or 0)
    except (TypeError, ValueError):
        bitrate = 0
    try:
        listeners = int(data.get("currentlisteners") or 0)
    except (TypeError, ValueError):
        listeners = 0
    return [Mount(
        path="",
        listen_url="",
        raw_path=_raw_mount_path(_clean(data.get("streampath")) or "/"),
        title=_clean(data.get("songtitle")) or None,
        name=_clean(data.get("servertitle")),
        genre=_clean(data.get("servergenre")),
        codec=_codec_from_content_type(_clean(data.get("content"))),
        bitrate=bitrate,
        listeners=listeners,
    )]


def _generic_json_mounts(data) -> list[Mount]:
    """Last resort for JSON we do not recognize: harvest any title we can see.

    The result carries no mount path, so it is only trusted when it is the sole
    candidate — see `select_mount`.
    """
    titles: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in TITLE_KEYS and isinstance(value, str) and value.strip():
                    titles.append(value.strip())
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return [Mount(path="", listen_url="", title=t) for t in titles]


# Icecast's HTML status page renders one block per mount, headed by
# "Mount Point /name". Icecast itself warns against parsing this page, and
# rightly so — but for the ~25 % of watchable stations whose host answers only
# status.xsl it is that or nothing, and the alternative (first title on the page)
# is the cross-mount bleed this module exists to avoid. The heading markup and
# the row labels differ between 2.3 and 2.4, hence the label alternatives below.
_MOUNT_BLOCK_RE = re.compile(r"<h3[^>]*>\s*Mount Point\s*([^<\s]+)", re.I)
_ROW_RE = re.compile(r"<td[^>]*>([^<]{1,60}):</td>\s*<td[^>]*>([^<]{0,200})</td>", re.I)

_TITLE_LABELS = ("currently playing", "current song", "now playing", "title")
_NAME_LABELS = ("stream name", "stream title", "name")
_LISTENER_LABELS = ("listeners (current)", "current listeners", "listeners")


def _first_label(fields: dict[str, str], labels: tuple[str, ...]) -> str:
    for label in labels:
        value = fields.get(label)
        if value:
            return value
    return ""


def _html_mounts(body: str) -> list[Mount]:
    starts = [(m.start(), m.group(1)) for m in _MOUNT_BLOCK_RE.finditer(body)]
    out: list[Mount] = []
    for i, (pos, raw_mount) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(body)
        fields = {k.strip().lower(): v.strip() for k, v in _ROW_RE.findall(body[pos:end])}
        try:
            bitrate = int(fields.get("bitrate") or 0)
        except ValueError:
            bitrate = 0
        try:
            listeners = int(_first_label(fields, _LISTENER_LABELS) or 0)
        except ValueError:
            listeners = 0
        out.append(Mount(
            path=mount_path(raw_mount),
            listen_url="",
            raw_path=_raw_mount_path(raw_mount),
            title=_first_label(fields, _TITLE_LABELS) or None,
            name=_first_label(fields, _NAME_LABELS),
            genre=fields.get("genre", ""),
            codec=_codec_from_content_type(fields.get("content type", "")),
            bitrate=bitrate,
            listeners=listeners,
        ))
    if out:
        return out

    # SHOUTcast v1 /7.html and unknown table layouts: no mount names anywhere,
    # so the best we can do is the un-attributed title.
    return [
        Mount(path="", listen_url="", title=text)
        for text in (m.group(1).strip() for m in re.finditer(r"<td[^>]*>([^<]{4,150})</td>", body))
        if " - " in text
    ]


def parse_mounts(body: str, path: str) -> list[Mount]:
    """Turn a status endpoint's body into the mounts it describes."""
    if path.endswith(("json=1", "json.xsl")):
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return []
        if isinstance(data, dict):
            return (_icecast_mounts(data)
                    or _shoutcast_mounts(data)
                    or _generic_json_mounts(data))
        return _generic_json_mounts(data)
    return _html_mounts(body)


def select_mount(mounts: list[Mount], stream_url: str) -> Mount | None:
    """Pick the mount that belongs to `stream_url`.

    Returns None rather than guessing: on a multi-mount server an unmatched
    stream means our mount is currently offline, and reporting a neighbour's
    song would be worse than reporting nothing.
    """
    if not mounts:
        return None
    wanted = mount_path(stream_url)
    for mount in mounts:
        if mount.path and mount.path == wanted:
            return mount

    # AzuraCast and friends publish a station at /kiks_hq.mp3 but report it as
    # /listen/radio_kiks/kiks_hq.mp3 — same mount behind a proxy prefix. Only
    # accepted when the basename is unique on the server, so a prefix guess can
    # never attribute the wrong stream.
    base = wanted.rsplit("/", 1)[-1]
    if base:
        same_base = [m for m in mounts if m.path.rsplit("/", 1)[-1] == base]
        if len(same_base) == 1:
            return same_base[0]

    # A lone mount is accepted only when it does not contradict us: SHOUTcast
    # and the HTML fallback report no usable mount name ("" or "/"), so there is
    # nothing to disagree with. A lone mount that names some OTHER stream is a
    # server listing only what is currently live — measured on
    # icecast.stv.livebox.sk, where one live mount briefly made nine RTVS
    # stations, Rádio Devín included, all report the same pop song.
    if len(mounts) == 1 and mounts[0].path in ("", "/"):
        return mounts[0]
    return None


def parse_title(raw: str) -> tuple[str | None, str | None]:
    """Split "Artist - Track". Returns (None, None) when it is not a song."""
    # Some servers emit HTML-escaped titles ("Karel &#352;iktanc"). Decode
    # before anything else, or the entity ends up in the artist index.
    text = re.sub(r"\s+", " ", html.unescape(raw)).strip()
    if len(text) < 4 or _JUNK_RE.search(text) or _MOJIBAKE_RE.search(text):
        return None, None

    # The separator must be surrounded by spaces, otherwise we break "AC-DC"
    # and "Jay-Z".
    parts = re.split(r"\s+[-–—]\s+", text, maxsplit=1)
    if len(parts) != 2:
        return None, None

    artist, track = (p.strip(" -–—\"'") for p in parts)
    if not artist or not track or len(artist) > 90 or len(track) > 120:
        return None, None
    if not _HAS_WORD_RE.search(artist):
        return None, None
    if _JUNK_RE.search(artist) or _JUNK_RE.search(track):
        return None, None
    return artist, track


def normalize_artist(name: str) -> str:
    """Key for comparing artists across stations.

    Only handles the cheap and safe things: case, diacritics, "feat.", square
    brackets, a leading "The". First-name/surname order is deliberately left
    alone — that belongs to MusicBrainz matching.
    """
    import unicodedata

    text = unicodedata.normalize("NFKD", name.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.split(r"\s+(?:feat\.?|ft\.?|featuring|vs\.?|&|with)\s+", text)[0]
    text = re.sub(r"[\(\[].*?[\)\]]", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"^the ", "", text)


def status_base(stream_url: str) -> str | None:
    """Base URL of the status server, derived from the stream URL."""
    try:
        validate_url(stream_url)
    except UnsafeURL:
        return None
    parts = urlsplit(stream_url)
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


def fetch_status(stream_url: str, *, timeout: float = 6.0) -> tuple[str, list[Mount]] | None:
    """Fetch the first status endpoint that answers with mounts.

    Returns (endpoint path, mounts) or None. This is the single place that
    talks to a status server; everything else (probe, watchability, sibling
    discovery) goes through it so the transport quirks are handled once.
    """
    base = status_base(stream_url)
    if not base:
        return None

    for path in STATUS_PATHS:
        try:
            request = urllib.request.Request(
                base + path, headers={"User-Agent": USER_AGENT}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(400_000).decode("utf-8", "ignore")
        # BadStatusLine: old SHOUTcast v1 answers "ICY 200 OK" instead of a
        # valid HTTP status line. It derives from HTTPException, not OSError.
        except (urllib.error.URLError, http.client.HTTPException,
                TimeoutError, OSError, ValueError):
            continue

        mounts = parse_mounts(body, path)
        if mounts:
            return path, mounts

    return None


def probe(stream_url: str, *, timeout: float = 6.0) -> NowPlaying | None:
    """Try to read the current title. None = station is not watchable."""
    found = fetch_status(stream_url, timeout=timeout)
    if not found:
        return None
    endpoint, mounts = found

    mine = select_mount(mounts, stream_url)
    if mine is None:
        return None

    if mine.title:
        artist, track = parse_title(mine.title)
        # The endpoint answered but the title is not a song (ad, station ID).
        # The station IS watchable — it just is not playing music right now.
        return NowPlaying(raw=mine.title, artist=artist, track=track, endpoint=endpoint)

    # Mount is up and listed but reports no metadata at all. Still watchable.
    return NowPlaying(raw="", artist=None, track=None, endpoint=endpoint)


def is_watchable(stream_url: str, *, timeout: float = 6.0) -> bool:
    """Can this station be polled without downloading audio?"""
    return probe(stream_url, timeout=timeout) is not None
