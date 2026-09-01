"""Mapovanie voľných tagov na kanonické žánre.

Tagy v Radio Browser píše ktokoľvek, v akomkoľvek jazyku a pravopise:
`rock`, `rockmusic`, `Rock Music`, `rockova hudba`, `Рок`, `classicrock`.
Bez tejto vrstvy je "filtrovanie podľa žánru" iba fulltext nad chaosom.

Poradie v CANONICAL rozhoduje: prvá zhoda vyhráva, takže špecifickejšie
žánre (classic rock) musia stáť pred všeobecnými (rock).
"""

from __future__ import annotations

import re
import unicodedata

# Kanonické žánre, od najšpecifickejšieho po najvšeobecnejší.
CANONICAL: list[tuple[str, tuple[str, ...]]] = [
    ("classic-rock", (
        "classic rock", "classicrock", "classicalrock", "klassik rock",
        "rock classico", "rock clasico", "classic-rock", "oldies rock",
    )),
    ("hard-rock", (
        "hard rock", "hardrock", "heavy rock", "arena rock",
    )),
    ("metal", (
        "metal", "heavy metal", "heavymetal", "death metal", "black metal",
        "thrash", "thrash metal", "power metal", "metalcore", "doom",
        "metall", "метал",
    )),
    ("punk", ("punk", "punk rock", "punkrock", "hardcore punk", "ska punk")),
    ("alternative", (
        "alternative", "alternativa", "alternativ", "indie", "indie rock",
        "grunge", "britpop", "alt rock", "alternative rock", "альтернатива",
    )),
    ("rock", (
        "rock", "rockmusic", "rock music", "rockova hudba", "rocková hudba",
        "rockowa", "rocken", "roque", "рок", "rock n roll", "rock'n'roll",
        "rocknroll", "classic hits rock",
    )),
    ("soul-funk", (
        "soul", "funk", "funky", "rnb", "r n b", "r&b", "randb", "motown",
        "northern soul", "neo soul", "disco funk", "groove",
    )),
    ("schlager", (
        "schlager", "volksmusik", "deutscher schlager", "volkstumliche",
        "dechovka", "lidovka",
    )),
    ("comedy", ("comedy", "humor", "humour", "satire", "kabarett", "comedy radio")),
    ("jazz", (
        "jazz", "smooth jazz", "jazzy", "bebop", "swing", "dixieland",
        "jazz music", "джаз", "jazzmusik",
    )),
    ("blues", ("blues", "rhythm and blues", "rnb blues", "delta blues", "блюз")),
    ("classical", (
        "classical", "classic", "klassik", "klasika", "klasik", "clasica",
        "classique", "classica", "opera", "symphony", "baroque", "orchestral",
        "классика", "vazna hudba", "vážna hudba",
    )),
    ("electronic", (
        "electronic", "electronica", "elektronik", "edm", "house", "techno",
        "trance", "dubstep", "drum and bass", "dnb", "drum'n'bass", "ambient",
        "chillout", "chill out", "chill", "lounge", "downtempo", "deep house", "electro",
        "электроника", "elektronicka",
    )),
    ("dance", ("dance", "dancefloor", "club", "disco", "eurodance", "танцевальная", "party", "partymusik", "dj sets", "dj remix", "remix")),
    ("hiphop", (
        "hip hop", "hiphop", "hip-hop", "rap", "trap", "urban", "хип-хоп",
        "rapmusic", "rap music",
    )),
    ("reggae", ("reggae", "ska", "dub", "dancehall", "roots reggae")),
    ("country", ("country", "bluegrass", "americana", "country music", "folk country")),
    ("folk", ("folk", "folklore", "folklor", "ludova hudba", "ľudová hudba", "world", "worldmusic", "world music", "ethno")),
    ("latin", ("latin", "latino", "salsa", "bachata", "reggaeton", "cumbia", "merengue", "samba", "bossa nova")),
    ("oldies", (
        "oldies", "60s", "70s", "80s", "90s", "sixties", "seventies",
        "eighties", "nineties", "retro", "golden oldies", "nostalgia",
        "evergreen", "старые песни",
        "60er", "70er", "80er", "90er", "80er jahre", "90er jahre",
        "anos 80", "annees 80", "lata 80", "80 90", "70 80 90",
    )),
    ("pop", (
        "pop", "popmusic", "pop music", "top 40", "top40", "charts", "hits",
        "hit radio", "hitradio", "popular", "adult contemporary", "ac",
        "поп", "popularna",
    )),
    ("news", (
        "news", "nachrichten", "noticias", "spravy", "správy", "info",
        "news talk", "newstalk", "current affairs", "новости",
        "information", "informace", "informacje", "aktuality",
    )),
    ("talk", (
        "talk", "talk radio", "speech", "discussion", "publicistika",
        "podcast", "spoken word", "разговорное",
        "old time radio", "otr", "radio drama", "hoerspiel",
    )),
    ("sport", ("sport", "sports", "sportradio", "football", "soccer", "спорт")),
    ("culture", ("culture", "kultura", "kultúra", "art", "literature", "drama", "kultur")),
    ("religious", ("christian", "gospel", "religious", "catholic", "church", "krestanske", "kresťanské", "religion", "faith", "islamic", "quran", "spiritual")),
    ("kids", ("kids", "children", "detske", "detské", "family", "cartoon")),
    ("relax", ("relax", "meditation", "spa", "easy listening", "easylistening", "instrumental", "new age", "sleep")),
]

# Tagy, ktoré nenesú žánrovú informáciu — nesmú spôsobiť zaradenie.
NOISE = frozenset({
    "music", "radio", "fm", "am", "online", "internet", "internet radio",
    "live", "stream", "streaming", "24/7", "hd", "stereo", "webradio",
    "web radio", "local", "community", "public radio", "commercial",
    "station", "mp3", "aac", "128", "192", "320", "various", "mixed",
    "entertainment", "general", "variety", "eclectic", "local radio",
    "non commercial", "noncommercial", "non-commercial", "college radio",
    "orf", "bbc", "npr", "rtvs", "ard", "regional", "national",
    "dj", "community radio", "adult", "classic hits", "hits music",
    # ES/PT/FR/DE ekvivalenty slov "hudba", "stanica", "zábava", "rádio"
    "musica", "musique", "musik", "estacion", "emisora", "radio online",
    "entretenimiento", "divertissement", "unterhaltung", "espanol",
    "espanola", "portugues", "deutsch", "francais", "english", "italiano",
    "latinoamerica", "norteamerica", "sudamerica", "europa", "mexico",
    "argentina", "colombia", "chile", "brasil", "peru", "venezuela",
})

_SYNONYM_INDEX: dict[str, str] = {}
for _genre, _syns in CANONICAL:
    for _s in _syns:
        _SYNONYM_INDEX.setdefault(_s, _genre)


def normalize(tag: str) -> str:
    """Zjednotí diakritiku, oddeľovače a medzery."""
    t = unicodedata.normalize("NFKD", tag.strip().lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[_\-/|]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


# Dekádové tagy sa píšu v desiatkach variantov: 80s, 80's, 80er, 1980s,
# anos 80, 00s, 2000s… Regex ich pokryje všetky namiesto vymenúvania.
_DECADE_RE = re.compile(r"^(19|20)?[0-9]0\s*(s|'s|er|er jahre|te|ties)?$")


def classify_tag(tag: str) -> str | None:
    """Jeden tag → kanonický žáner, alebo None."""
    t = normalize(tag)
    if not t or t in NOISE:
        return None
    if t in _SYNONYM_INDEX:
        return _SYNONYM_INDEX[t]
    if _DECADE_RE.match(t.replace("'", "'")):
        return "oldies"
    # Voľnejšia zhoda: tag obsahuje synonymum ako celé slovo.
    for genre, syns in CANONICAL:
        for syn in syns:
            if re.search(rf"(^|\s){re.escape(syn)}($|\s)", t):
                return genre
    return None


def classify(tags: list[str], name: str = "") -> list[str]:
    """Zoznam tagov (+ názov ako záloha) → kanonické žánre, bez duplicít.

    Názov sa používa len keď tagy nedali nič — veľa staníc má žáner
    iba v názve ("Rock Antenne", "Jazz FM").
    """
    out: list[str] = []
    for tag in tags:
        g = classify_tag(tag)
        if g and g not in out:
            out.append(g)
    if not out and name:
        g = classify_tag(name)
        if g:
            out.append(g)
    return out


def coverage(all_tags: dict[str, int]) -> tuple[int, int, list[tuple[str, int]]]:
    """Diagnostika: koľko výskytov tagov vieme zaradiť + čo nám uniká.

    Slúži na rast taxonómie — nezaradené tagy zoradené podľa početnosti
    sú presne zoznam, čo doplniť ako ďalšie.
    """
    hit = miss = 0
    unmapped: dict[str, int] = {}
    for tag, count in all_tags.items():
        if normalize(tag) in NOISE:
            continue
        if classify_tag(tag):
            hit += count
        else:
            miss += count
            unmapped[tag] = count
    top = sorted(unmapped.items(), key=lambda kv: -kv[1])
    return hit, miss, top
