"""Bounding boxes per country, for catching coordinates that are a lie.

Radio Browser's geo_lat/geo_long are typed in by station operators, and a
non-trivial number of them are wrong in ways nobody notices in a list view but
that are glaring on a globe: Schwarzwaldradio (country DE) carries
[18.81, -38.92], which is open Atlantic about 1 500 km west of Guinea-Bissau.
The app plots those, so a bad coordinate is a visible false claim about where a
station is.

WHY BOXES AND NOT AN OUTLIER TEST
A first attempt scored each station by its distance from the median coordinate
of its own country and flagged anything beyond ~20°. It flagged 7.8% of
stations and was almost all false positives, because the premise is wrong:
the US, Russia, Canada, Australia and Brazil legitimately span far more than
20° from their own centre, so the test punished exactly the countries whose
coordinates are most likely to be fine. Distance from a centre says nothing
without the country's actual shape. Hence real extents.

WHAT A BOX CAN AND CANNOT DO
A box is not a polygon, so it passes things it should not: a coordinate in the
Bay of Biscay reads as French, one in the Gulf of Mexico as Mexican, and any
point in a neighbour that happens to sit inside the rectangle survives. That is
accepted. The failures that are actually visible on the globe — mid-ocean, and
wrong continent — are all far outside the box, and those are the ones this
catches. Cheap, offline, deterministic, no polygon dependency.

The numbers are true extents including offshore islands that share the
country's ISO code; the caller adds the safety margin. Overseas territories
that hold their OWN alpha-2 code are NOT folded into the parent, which is what
keeps France a rectangle over Europe instead of one spanning from Guyane to
Réunion: GF, GP, MQ, RE, YT, PM, NC, PF, WF, BL, MF and TF are separate rows,
as are the British, Dutch, US and Danish equivalents.

Two deliberate omissions, both cases where a box would mean nothing:
  * UM (US Minor Outlying Islands) is scattered from Wake to Navassa across
    240° of longitude and both hemispheres.
  * AQ (Antarctica) is bounded by latitude only, so it gets the full lon range.
An unknown code is never a failure — `contains` says "cannot tell" and the
coordinate is kept. Dropping a coordinate needs positive evidence it is wrong.
"""

from __future__ import annotations

# code -> (min_lat, max_lat, min_lon, max_lon)
#
# min_lon > max_lon means the box crosses the antimeridian and is read as
# "lon >= min_lon OR lon <= max_lon". Only RU, FJ, KI and NZ need it.
BOUNDS: dict[str, tuple[float, float, float, float]] = {
    "AD": (42.43, 42.66, 1.41, 1.79),
    "AE": (22.60, 26.10, 51.50, 56.40),
    "AF": (29.35, 38.50, 60.50, 74.90),
    "AG": (16.90, 17.75, -62.00, -61.65),
    "AI": (18.15, 18.30, -63.20, -62.95),
    "AL": (39.60, 42.70, 19.25, 21.10),
    "AM": (38.80, 41.35, 43.40, 46.65),
    "AO": (-18.10, -4.35, 11.65, 24.10),
    "AQ": (-90.00, -60.00, -180.00, 180.00),
    "AR": (-55.10, -21.75, -73.60, -53.60),
    "AS": (-14.60, -14.15, -171.10, -169.40),
    "AT": (46.35, 49.05, 9.45, 17.20),
    "AU": (-43.70, -9.95, 112.85, 159.20),
    "AW": (12.40, 12.65, -70.10, -69.85),
    "AX": (59.85, 60.55, 19.30, 21.05),
    "AZ": (38.35, 41.95, 44.75, 50.40),
    "BA": (42.50, 45.30, 15.70, 19.65),
    "BB": (13.00, 13.35, -59.70, -59.40),
    "BD": (20.55, 26.65, 88.00, 92.70),
    "BE": (49.45, 51.55, 2.50, 6.45),
    "BF": (9.35, 15.10, -5.55, 2.45),
    "BG": (41.20, 44.25, 22.30, 28.70),
    "BH": (25.70, 26.35, 50.40, 50.85),
    "BI": (-4.50, -2.30, 28.95, 30.90),
    "BJ": (6.15, 12.45, 0.75, 3.90),
    "BL": (17.85, 18.00, -62.95, -62.75),
    "BM": (32.20, 32.42, -64.90, -64.60),
    "BN": (4.00, 5.10, 114.00, 115.40),
    "BO": (-22.95, -9.65, -69.70, -57.45),
    "BQ": (11.95, 17.60, -68.50, -62.90),
    "BR": (-33.80, 5.30, -74.05, -34.75),
    "BS": (20.85, 27.30, -79.05, -72.65),
    "BT": (26.70, 28.40, 88.70, 92.20),
    "BV": (-54.50, -54.35, 3.20, 3.50),
    "BW": (-26.95, -17.75, 19.95, 29.40),
    "BY": (51.20, 56.25, 23.15, 32.80),
    "BZ": (15.85, 18.55, -89.30, -87.70),
    "CA": (41.60, 83.20, -141.10, -52.55),
    "CC": (-12.30, -11.80, 96.75, 96.95),
    "CD": (-13.50, 5.40, 12.15, 31.35),
    "CF": (2.20, 11.05, 14.40, 27.50),
    "CG": (-5.05, 3.75, 11.10, 18.70),
    "CH": (45.80, 47.85, 5.90, 10.55),
    "CI": (4.30, 10.75, -8.65, -2.45),
    "CK": (-22.00, -8.85, -166.00, -157.25),
    "CL": (-56.00, -17.45, -75.75, -66.35),
    "CM": (1.60, 13.10, 8.40, 16.25),
    "CN": (18.10, 53.60, 73.45, 134.80),
    "CO": (-4.30, 13.45, -79.05, -66.80),
    "CR": (5.45, 11.25, -87.20, -82.50),
    "CU": (19.80, 23.30, -85.00, -74.10),
    "CV": (14.75, 17.25, -25.40, -22.65),
    "CW": (11.95, 12.45, -69.20, -68.70),
    "CX": (-10.60, -10.40, 105.50, 105.80),
    "CY": (34.50, 35.75, 32.20, 34.65),
    "CZ": (48.50, 51.10, 12.05, 18.90),
    "DE": (47.20, 55.10, 5.85, 15.05),
    "DJ": (10.90, 12.75, 41.70, 43.50),
    "DK": (54.50, 57.80, 8.00, 15.25),
    "DM": (15.15, 15.65, -61.50, -61.20),
    "DO": (17.45, 19.95, -72.05, -68.30),
    "DZ": (18.90, 37.15, -8.70, 12.00),
    "EC": (-5.05, 1.70, -92.10, -75.15),
    "EE": (57.50, 59.75, 21.75, 28.25),
    "EG": (21.95, 31.70, 24.65, 36.95),
    "EH": (20.70, 27.70, -17.15, -8.65),
    "ER": (12.35, 18.05, 36.40, 43.20),
    "ES": (27.60, 43.85, -18.20, 4.40),
    "ET": (3.35, 14.95, 32.90, 48.05),
    "FI": (59.70, 70.15, 20.50, 31.60),
    "FJ": (-21.05, -12.40, 176.80, -178.15),
    "FK": (-52.50, -50.95, -61.40, -57.70),
    "FM": (0.95, 10.05, 137.20, 163.10),
    "FO": (61.30, 62.45, -7.75, -6.25),
    "FR": (41.30, 51.15, -5.25, 9.60),
    "GA": (-4.00, 2.35, 8.65, 14.55),
    "GB": (49.80, 60.95, -8.70, 1.80),
    "GD": (11.95, 12.55, -61.85, -61.35),
    "GE": (41.00, 43.60, 39.90, 46.75),
    "GF": (2.10, 5.80, -54.60, -51.60),
    "GG": (49.40, 49.75, -2.70, -2.15),
    "GH": (4.70, 11.20, -3.30, 1.25),
    "GI": (36.10, 36.20, -5.40, -5.30),
    "GL": (59.70, 83.70, -73.30, -11.30),
    "GM": (13.00, 13.90, -17.05, -13.75),
    "GN": (7.15, 12.70, -15.10, -7.60),
    "GP": (15.80, 16.55, -61.85, -61.00),
    "GQ": (-1.55, 3.80, 5.60, 11.40),
    "GR": (34.75, 41.80, 19.30, 29.70),
    "GS": (-59.50, -53.95, -38.10, -26.20),
    "GT": (13.70, 17.85, -92.30, -88.20),
    "GU": (13.20, 13.70, 144.60, 145.05),
    "GW": (10.85, 12.70, -16.75, -13.60),
    "GY": (1.15, 8.60, -61.40, -56.45),
    "HK": (22.15, 22.60, 113.80, 114.45),
    "HM": (-53.25, -52.90, 72.50, 74.00),
    "HN": (12.95, 16.55, -89.40, -83.10),
    "HR": (42.35, 46.60, 13.45, 19.45),
    "HT": (18.00, 20.10, -74.50, -71.60),
    "HU": (45.70, 48.60, 16.10, 22.95),
    "ID": (-11.05, 6.10, 94.95, 141.05),
    "IE": (51.40, 55.45, -10.55, -5.90),
    "IL": (29.45, 33.35, 34.25, 35.90),
    "IM": (54.00, 54.45, -4.90, -4.30),
    "IN": (6.70, 35.55, 68.10, 97.45),
    "IO": (-7.50, -5.20, 71.20, 72.55),
    "IQ": (29.05, 37.40, 38.75, 48.60),
    "IR": (25.00, 39.80, 44.00, 63.35),
    "IS": (63.30, 66.60, -24.60, -13.45),
    "IT": (35.45, 47.10, 6.60, 18.55),
    "JE": (49.15, 49.30, -2.30, -2.00),
    "JM": (17.65, 18.60, -78.40, -76.15),
    "JO": (29.15, 33.40, 34.90, 39.35),
    "JP": (24.00, 45.60, 122.85, 153.05),
    "KE": (-4.75, 5.05, 33.85, 41.95),
    "KG": (39.15, 43.30, 69.20, 80.30),
    "KH": (10.35, 14.75, 102.30, 107.65),
    "KI": (-11.50, 4.75, 169.50, -150.15),
    "KM": (-12.45, -11.30, 43.20, 44.60),
    "KN": (17.05, 17.45, -62.90, -62.50),
    "KP": (37.60, 43.05, 124.15, 130.70),
    "KR": (33.10, 38.65, 125.00, 129.65),
    "KW": (28.50, 30.10, 46.50, 48.50),
    "KY": (19.20, 19.80, -81.50, -79.70),
    "KZ": (40.50, 55.50, 46.45, 87.40),
    "LA": (13.85, 22.55, 100.05, 107.70),
    "LB": (33.00, 34.70, 35.05, 36.65),
    "LC": (13.65, 14.15, -61.10, -60.85),
    "LI": (47.00, 47.30, 9.45, 9.65),
    "LK": (5.85, 9.90, 79.60, 81.95),
    "LR": (4.30, 8.60, -11.50, -7.35),
    "LS": (-30.70, -28.55, 27.00, 29.50),
    "LT": (53.85, 56.50, 20.90, 26.90),
    "LU": (49.40, 50.20, 5.70, 6.55),
    "LV": (55.65, 58.10, 20.90, 28.25),
    "LY": (19.45, 33.20, 9.30, 25.20),
    "MA": (27.60, 35.95, -13.20, -0.95),
    "MC": (43.70, 43.80, 7.35, 7.50),
    "MD": (45.40, 48.50, 26.60, 30.20),
    "ME": (41.80, 43.60, 18.40, 20.40),
    "MF": (18.00, 18.15, -63.20, -62.95),
    "MG": (-25.65, -11.90, 43.15, 50.55),
    "MH": (4.50, 14.70, 160.70, 172.20),
    "MK": (40.80, 42.40, 20.40, 23.05),
    "ML": (10.10, 25.05, -12.30, 4.30),
    "MM": (9.70, 28.60, 92.15, 101.20),
    "MN": (41.50, 52.20, 87.70, 119.95),
    "MO": (22.05, 22.25, 113.50, 113.65),
    "MP": (14.05, 20.60, 144.85, 146.10),
    "MQ": (14.35, 14.95, -61.30, -60.75),
    "MR": (14.70, 27.35, -17.10, -4.80),
    "MS": (16.65, 16.85, -62.30, -62.10),
    "MT": (35.75, 36.15, 14.15, 14.60),
    "MU": (-20.60, -19.90, 57.25, 63.55),
    "MV": (-0.75, 7.15, 72.55, 73.80),
    "MW": (-17.20, -9.35, 32.65, 35.95),
    "MX": (14.50, 32.80, -118.50, -86.65),
    "MY": (0.80, 7.40, 99.60, 119.30),
    "MZ": (-26.90, -10.45, 30.20, 40.90),
    "NA": (-29.00, -16.95, 11.65, 25.30),
    "NC": (-22.75, -19.50, 163.50, 168.20),
    "NE": (11.65, 23.55, 0.15, 16.00),
    "NF": (-29.15, -28.95, 167.90, 168.05),
    "NG": (4.20, 13.90, 2.65, 14.70),
    "NI": (10.70, 15.05, -87.70, -82.60),
    "NL": (50.70, 53.60, 3.30, 7.25),
    "NO": (57.90, 71.25, 4.60, 31.15),
    "NP": (26.35, 30.50, 80.00, 88.25),
    "NR": (-0.60, -0.45, 166.85, 167.00),
    "NU": (-19.20, -18.90, -170.00, -169.75),
    "NZ": (-47.40, -34.00, 166.35, -176.00),
    "OM": (16.60, 26.45, 51.95, 59.90),
    "PA": (7.15, 9.70, -83.10, -77.10),
    "PE": (-18.40, 0.00, -81.40, -68.60),
    "PF": (-27.70, -7.80, -155.00, -134.40),
    "PG": (-11.70, -0.95, 140.80, 156.05),
    "PH": (4.55, 21.25, 116.85, 126.65),
    "PK": (23.60, 37.15, 60.85, 77.90),
    "PL": (49.00, 55.05, 14.10, 24.20),
    "PM": (46.70, 47.15, -56.50, -56.10),
    "PN": (-25.10, -23.85, -130.80, -124.70),
    "PR": (17.85, 18.55, -67.30, -65.20),
    "PS": (31.20, 32.60, 34.20, 35.60),
    "PT": (32.35, 42.20, -31.30, -6.15),
    "PW": (6.80, 8.15, 134.10, 134.75),
    "PY": (-27.60, -19.25, -62.70, -54.20),
    "QA": (24.45, 26.20, 50.70, 51.70),
    "RE": (-21.40, -20.85, 55.20, 55.85),
    "RO": (43.60, 48.30, 20.20, 29.75),
    "RS": (42.20, 46.20, 18.80, 23.05),
    "RU": (41.15, 81.90, 19.60, -169.00),
    "RW": (-2.90, -1.05, 28.85, 30.90),
    "SA": (16.30, 32.20, 34.50, 55.70),
    "SB": (-12.30, -6.55, 155.45, 167.00),
    "SC": (-10.05, -3.70, 46.15, 56.30),
    "SD": (8.65, 22.25, 21.75, 38.60),
    "SE": (55.30, 69.10, 10.95, 24.20),
    "SG": (1.15, 1.50, 103.60, 104.10),
    "SH": (-37.20, -7.85, -14.50, -5.60),
    "SI": (45.40, 46.90, 13.35, 16.65),
    "SJ": (70.60, 80.80, 10.45, 33.55),
    "SK": (47.70, 49.65, 16.80, 22.60),
    "SL": (6.85, 10.00, -13.35, -10.25),
    "SM": (43.85, 44.00, 12.35, 12.55),
    "SN": (12.25, 16.70, -17.60, -11.30),
    "SO": (-1.70, 12.00, 40.90, 51.45),
    "SR": (1.80, 6.05, -58.15, -53.90),
    "SS": (3.45, 12.30, 24.10, 36.00),
    "ST": (0.00, 1.75, 6.45, 7.50),
    "SV": (13.10, 14.50, -90.15, -87.65),
    "SX": (17.95, 18.10, -63.20, -62.95),
    "SY": (32.30, 37.35, 35.70, 42.40),
    "SZ": (-27.40, -25.70, 30.75, 32.15),
    "TC": (21.00, 22.00, -72.50, -71.10),
    "TD": (7.40, 23.50, 13.40, 24.05),
    "TF": (-49.80, -11.50, 50.15, 70.60),
    "TG": (6.05, 11.15, -0.20, 1.85),
    "TH": (5.60, 20.50, 97.30, 105.70),
    "TJ": (36.65, 41.10, 67.30, 75.20),
    "TK": (-9.50, -8.50, -172.60, -171.10),
    "TL": (-9.55, -8.10, 124.00, 127.40),
    "TM": (35.10, 42.85, 52.40, 66.75),
    "TN": (30.20, 37.60, 7.45, 11.65),
    "TO": (-22.40, -15.50, -176.30, -173.65),
    "TR": (35.80, 42.20, 25.60, 44.85),
    "TT": (10.00, 11.40, -62.00, -60.45),
    "TV": (-10.80, -5.60, 176.00, 179.95),
    "TW": (21.85, 25.40, 118.10, 122.10),
    "TZ": (-11.80, -0.95, 29.30, 40.50),
    "UA": (44.20, 52.40, 22.10, 40.30),
    "UG": (-1.50, 4.25, 29.50, 35.05),
    "US": (18.85, 71.45, -179.20, -66.85),
    "UY": (-35.10, -30.00, -58.50, -53.00),
    "UZ": (37.15, 45.65, 55.90, 73.20),
    "VA": (41.89, 41.92, 12.43, 12.47),
    "VC": (12.50, 13.40, -61.50, -61.10),
    "VE": (0.60, 12.25, -73.40, -59.75),
    "VG": (18.30, 18.80, -64.85, -64.25),
    "VI": (17.60, 18.45, -65.10, -64.50),
    "VN": (8.15, 23.40, 102.10, 109.55),
    "VU": (-20.30, -13.05, 166.50, 170.30),
    "WF": (-14.40, -13.15, -178.25, -176.10),
    "WS": (-14.10, -13.40, -172.85, -171.35),
    "XK": (41.85, 43.30, 20.00, 21.80),
    "YE": (12.10, 19.00, 42.50, 54.60),
    "YT": (-13.10, -12.60, 44.95, 45.35),
    "ZA": (-34.90, -22.10, 16.40, 32.95),
    "ZM": (-18.10, -8.20, 21.90, 33.75),
    "ZW": (-22.45, -15.60, 25.20, 33.10),
}

# Territories that travel on the parent's passport in Radio Browser's data.
# RCI Martinique is filed as country FR and sits at 14.6N/61.0W, which is
# Martinique and is entirely correct — Martinique IS France, and the operators
# who fill this database in are as likely to write FR as MQ. The same holds for
# a BBC relay in Gibraltar, a Bonaire station under NL and a Guam station under
# US. Without this the check would delete perfectly good coordinates from
# exactly the places least able to spare them, so a parent is accepted anywhere
# its dependencies are, by reusing their boxes above.
DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "AU": ("CX", "CC", "NF", "HM"),
    "DK": ("GL", "FO"),
    "FI": ("AX",),
    "FR": ("GF", "GP", "MQ", "RE", "YT", "PM", "NC", "PF", "WF", "BL", "MF", "TF"),
    "GB": ("AI", "BM", "FK", "GI", "GG", "IM", "JE", "KY", "MS", "PN", "SH",
           "TC", "VG", "GS", "IO"),
    "MA": ("EH",),
    "NL": ("AW", "CW", "SX", "BQ"),
    "NO": ("SJ", "BV"),
    "NZ": ("CK", "NU", "TK"),
    "US": ("PR", "VI", "GU", "AS", "MP"),
}


# How far outside its own box a coordinate may sit and still be believed, in
# degrees (2° ≈ 220 km of latitude). Generous on purpose. The extents above are
# hand-entered and a tight fit would turn every small error in this table into
# a wrongly deleted coordinate, so the margin covers the table's own slop as
# well as the honest border cases — a transmitter across the frontier, a
# station registered to the capital of the country it broadcasts INTO. The
# errors worth catching miss by tens of degrees, not by two.
DEFAULT_MARGIN_DEG = 2.0

# Null Island. A sentinel for "the form was left empty", never a station.
# Belt and braces: `curate` never even builds a geo pair with a zero component,
# so today nothing reaches here with it. But that is a truthiness test on a
# float, the kind of thing a later reader corrects on sight, and no box on this
# table admits (0, 0) at the default margin while three West African ones do by
# margin 5. Rather than have the guarantee rest on two unrelated accidents,
# it is stated.
_NULL_ISLAND = (0.0, 0.0)


def contains(
    country: str,
    lat: float,
    lon: float,
    *,
    margin: float = DEFAULT_MARGIN_DEG,
) -> bool | None:
    """Is (lat, lon) inside `country`'s box, plus the margin?

    True  — plausible, keep the coordinate.
    False — outside the country it claims, or not a coordinate at all.
    None  — no box for this code, so there is nothing to say. The caller keeps
            the coordinate: silence is not evidence.
    """
    if lat is None or lon is None:
        return False
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return False
    if (lat, lon) == _NULL_ISLAND:
        return False

    code = (country or "").strip().upper()
    box = BOUNDS.get(code)
    if box is None:
        return None

    if _in_box(box, lat, lon, margin):
        return True
    return any(
        _in_box(BOUNDS[dep], lat, lon, margin)
        for dep in DEPENDENCIES.get(code, ())
        if dep in BOUNDS
    )


def _in_box(
    box: tuple[float, float, float, float],
    lat: float,
    lon: float,
    margin: float,
) -> bool:
    min_lat, max_lat, min_lon, max_lon = box
    if not (min_lat - margin <= lat <= max_lat + margin):
        return False
    lo, hi = min_lon - margin, max_lon + margin
    if min_lon > max_lon:          # box crosses the antimeridian
        return lon >= lo or lon <= hi
    return lo <= lon <= hi
