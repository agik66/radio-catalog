"""Zisťovanie, čo stanica práve hrá — bez sťahovania zvuku.

Icecast a Shoutcast servery bežne vystavujú status endpoint s aktuálnym
titulkom. Dopyt stojí pár kilobajtov namiesto stoviek, takže sa dá pollovať
často a z telefónu. Meranie na 150 staniciach: ~34 % je takto sledovateľných.

Zvyšok sa dá čítať len z ICY metadát v audio streame — to je drahé a robí to
až sonda na simulátore (`StreamTriage`).
"""

from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

from .net import UnsafeURL, validate_url

USER_AGENT = "Cliamp/1.0 catalog-builder"

# Poradie podľa nameranej úspešnosti: status-json.xsl drvivo vedie.
STATUS_PATHS = ("/status-json.xsl", "/stats?json=1", "/status.xsl", "/7.html")

# Kľúče, pod ktorými servery hlásia aktuálny titulok.
TITLE_KEYS = frozenset({"title", "songtitle", "yp_currently_playing", "song"})

# Titulky, ktoré nie sú skladba — ID stanice, výplň, reklamný blok.
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

# Mojibake: UTF-8 prečítané ako Latin-1. Overené na reálnych dátach —
# "NOVÁK" príde ako "NOVÃK", teda Ã (0xC3) nasledované ASCII písmenom.
# Za Ã/Â/Å smie v poriadnom texte stáť malé písmeno (São, Ålborg), nie veľké.
_MOJIBAKE_RE = re.compile(
    "[\u0080-\u009f]"           # riadiace znaky sa v čistom texte nevyskytujú
    "|[\u00c3\u00c2\u00c5\u00d0\u00d1][A-Z]"   # Ã + veľké písmeno = rozbité kódovanie
    "|\u00c3[\u00a1-\u00ff]"    # Ã¡ Ã© Ã³ … klasické dvojice
    "|\u00c5[\u00a1\u00be]"     # Å¡ Å¾
)

# Interpret musí obsahovať aspoň jedno súvislé slovo z dvoch písmen —
# zachytáva ID staníc typu "- 0 N - 2000s on Radio".
_HAS_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


@dataclass(frozen=True)
class NowPlaying:
    raw: str
    artist: str | None
    track: str | None
    endpoint: str

    @property
    def is_music(self) -> bool:
        return bool(self.artist and self.track)


def _extract_titles(body: str, path: str) -> list[str]:
    out: list[str] = []
    if path.endswith(("json=1", "json.xsl")):
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return out

        def walk(node) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in TITLE_KEYS and isinstance(value, str) and value.strip():
                        out.append(value.strip())
                    else:
                        walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(data)
    else:
        # status.xsl / 7.html sú HTML tabuľky — titulok má oddeľovač " - ".
        for match in re.finditer(r"<td[^>]*>([^<]{4,150})</td>", body):
            text = match.group(1).strip()
            if " - " in text:
                out.append(text)
    return out


def parse_title(raw: str) -> tuple[str | None, str | None]:
    """Rozdelí "Interpret - Skladba". Vracia (None, None) ak to nie je skladba."""
    text = re.sub(r"\s+", " ", raw).strip()
    if len(text) < 4 or _JUNK_RE.search(text) or _MOJIBAKE_RE.search(text):
        return None, None

    # Oddeľovač musí mať okolo seba medzery, inak rozbijeme "AC-DC" či "Jay-Z".
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
    """Kľúč na porovnávanie interpretov naprieč stanicami.

    Rieši len lacné a bezpečné veci: veľkosť písmen, diakritiku, "feat.",
    hranaté zátvorky, "The" na začiatku. Poradie meno/priezvisko zámerne
    nerieši — to patrí až spárovaniu s MusicBrainz.
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
    """Základ URL statusového servera, odvodený od streamu."""
    try:
        validate_url(stream_url)
    except UnsafeURL:
        return None
    parts = urlsplit(stream_url)
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


def probe(stream_url: str, *, timeout: float = 6.0) -> NowPlaying | None:
    """Skúsi zistiť aktuálny titulok. None = stanica nie je sledovateľná."""
    base = status_base(stream_url)
    if not base:
        return None

    for path in STATUS_PATHS:
        try:
            request = urllib.request.Request(
                base + path, headers={"User-Agent": USER_AGENT}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(80_000).decode("utf-8", "ignore")
        # BadStatusLine: starý SHOUTcast v1 odpovedá "ICY 200 OK" namiesto
        # platného HTTP statusu. Dedí z HTTPException, nie z OSError.
        except (urllib.error.URLError, http.client.HTTPException,
                TimeoutError, OSError, ValueError):
            continue

        for title in _extract_titles(body, path):
            artist, track = parse_title(title)
            if artist:
                return NowPlaying(raw=title, artist=artist, track=track, endpoint=path)
        # Endpoint odpovedal, ale titulok nie je skladba (reklama, ID stanice).
        # Stanica JE sledovateľná — len práve nehrá hudbu.
        if _extract_titles(body, path):
            first = _extract_titles(body, path)[0]
            return NowPlaying(raw=first, artist=None, track=None, endpoint=path)

    return None


def is_watchable(stream_url: str, *, timeout: float = 6.0) -> bool:
    """Dá sa táto stanica pollovať bez sťahovania zvuku?"""
    return probe(stream_url, timeout=timeout) is not None
