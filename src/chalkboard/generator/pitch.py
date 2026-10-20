"""Pitch geometry: zones, and the vocabulary used to talk about space.

Zones are the nouns the Explainer uses for place. "The left half-space in the
final third" is only sayable if space is modelled, so the pitch is divided
into 18 zones: six bands along the pitch by three channels across it.

Coordinates are attack-relative - always from the perspective of the team in
possession - so zone Z6C is "in front of the goal they are attacking",
whichever way the teams are kicking. That removes end-swapping from every
downstream query.
"""

from __future__ import annotations

#: Six bands from own goal line (1) to opposition goal line (6).
BANDS = 6
#: Three channels across the pitch, looking towards the attacking goal.
CHANNELS = ("L", "C", "R")

#: Human names, so the narrative layer has football words rather than codes.
BAND_NAMES = {
    1: "own box",
    2: "defensive third",
    3: "own half",
    4: "opposition half",
    5: "final third",
    6: "penalty area",
}
CHANNEL_NAMES = {"L": "left", "C": "central", "R": "right"}


def band_of(x: float) -> int:
    """1-6, from the team's own goal line to the one they attack."""
    return min(BANDS, max(1, int(x // (100.0 / BANDS)) + 1))


def channel_of(y: float) -> str:
    if y < 100.0 / 3:
        return "L"
    if y < 200.0 / 3:
        return "C"
    return "R"


def zone_id(x: float, y: float) -> str:
    """The zone code for a point, e.g. ``Z5L`` - final third, left channel."""
    return f"Z{band_of(x)}{channel_of(y)}"


def zone_name(code: str) -> str:
    band = int(code[1])
    channel = code[2]
    if band in (1, 6):
        return BAND_NAMES[band]
    return f"{CHANNEL_NAMES[channel]} {BAND_NAMES[band]}"


def half_space(code: str) -> bool:
    """True for the channels between centre and touchline in advanced areas.

    Not a separate zone - a property of one. Keeps the zone grid regular
    while still letting the narrative use the term coaches actually use.
    """
    return code[2] in ("L", "R") and int(code[1]) >= 4


def all_zones() -> list[dict[str, object]]:
    """Every zone, for loading as nodes once per graph."""
    return [
        {
            "zone_id": f"Z{band}{channel}",
            "band": band,
            "channel": channel,
            "name": zone_name(f"Z{band}{channel}"),
            "half_space": half_space(f"Z{band}{channel}"),
        }
        for band in range(1, BANDS + 1)
        for channel in CHANNELS
    ]


def adjacent(a: str, b: str) -> bool:
    """Zones sharing an edge. Static pitch topology, loaded once.

    Lets "the attack shifted channels" be a traversal rather than arithmetic
    on coordinates.
    """
    if a == b:
        return False
    band_a, band_b = int(a[1]), int(b[1])
    ch_a, ch_b = CHANNELS.index(a[2]), CHANNELS.index(b[2])
    return abs(band_a - band_b) + abs(ch_a - ch_b) == 1
