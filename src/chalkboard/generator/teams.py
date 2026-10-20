"""Fictional clubs, squads and tactical style profiles.

All club and player names here are invented. The hackathon requires synthetic,
football-realistic data, and the submission rules bar third-party trademarks, so
nothing in this module should ever be replaced with a real club or player.

A :class:`StyleProfile` is the knob that makes two generated matches tactically
distinct. The Significance and Explainer agents have nothing interesting to say
about a match where both sides play identically, so style is the single most
important input to the quality of the dataset.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .models import Player, Position, Team


@dataclass(frozen=True)
class StyleProfile:
    """Tactical parameters that shape possession behaviour.

    All values are 0-1 unless noted. They are intentionally few: each one has a
    visible, explainable effect on the event stream, which is what lets the
    Explainer agent talk about control versus chaos rather than guessing.
    """

    name: str
    #: Preference for short retention over progression. High = patient build-up.
    patience: float
    #: Defensive line height. High = aggressive press high up the pitch.
    press_height: float
    #: Intensity of pressing once engaged. Drives the ``pressure`` metric.
    press_intensity: float
    #: Share of progressive attempts that go long rather than through midfield.
    directness: float
    #: Willingness to shoot from distance.
    shot_appetite: float
    #: Share of final-third entries that arrive as crosses.
    width: float

    @property
    def tempo(self) -> float:
        """Derived: how quickly the ball moves. Used for event spacing."""
        return 0.3 + 0.5 * self.directness + 0.2 * (1.0 - self.patience)


STYLES: dict[str, StyleProfile] = {
    "possession": StyleProfile(
        name="possession",
        patience=0.85,
        press_height=0.65,
        press_intensity=0.55,
        directness=0.15,
        shot_appetite=0.40,
        width=0.35,
    ),
    "high_press": StyleProfile(
        name="high_press",
        patience=0.45,
        press_height=0.90,
        press_intensity=0.90,
        directness=0.45,
        shot_appetite=0.55,
        width=0.45,
    ),
    "low_block": StyleProfile(
        name="low_block",
        patience=0.40,
        press_height=0.20,
        press_intensity=0.35,
        directness=0.70,
        shot_appetite=0.35,
        width=0.30,
    ),
    "direct": StyleProfile(
        name="direct",
        patience=0.20,
        press_height=0.50,
        press_intensity=0.60,
        directness=0.85,
        shot_appetite=0.65,
        width=0.70,
    ),
}

FORMATIONS: dict[str, tuple[int, int, int]] = {
    "4-3-3": (4, 3, 3),
    "4-4-2": (4, 4, 2),
    "3-5-2": (3, 5, 2),
    "5-3-2": (5, 3, 2),
}

CLUBS: list[tuple[str, str, str]] = [
    ("Ashcombe Rovers", "ASH", "Ashcombe Park"),
    ("Northgate United", "NGU", "The Northgate"),
    ("Castleford Athletic", "CFA", "Weir Lane"),
    ("Brindley Town", "BRT", "Brindley Fields"),
    ("Halewood City", "HWC", "The Crucible Ground"),
    ("Everdon Wanderers", "EVW", "Everdon Vale"),
    ("Kingsmere Albion", "KMA", "Kingsmere Road"),
    ("Thornbury Forest", "THF", "The Thicket"),
]

_FIRST = [
    "Mateo", "Idris", "Tobias", "Emeka", "Luka", "Rafael", "Sami", "Noah",
    "Kwame", "Adrien", "Viktor", "Nikhil", "Oscar", "Tariq", "Jonas", "Elias",
    "Dmitri", "Santiago", "Yusei", "Callum", "Amir", "Felix", "Marek", "Diogo",
]
_LAST = [
    "Варела", "Okonjo", "Lindqvist", "Marchetti", "Bakare", "Novak", "Fontaine",
    "Delgado", "Haraldsen", "Mwangi", "Sørensen", "Petrov", "Rahman", "Costa",
    "Virtanen", "Adeyemi", "Kovač", "Moreau", "Basaran", "Lindholm", "Duarte",
    "Nakamura", "Olsen", "Ferreira",
]
# Guard against a non-Latin name slipping in via copy-paste.
_LAST = [n for n in _LAST if n.isascii()]


def _make_player(rng: random.Random, team_id: str, number: int, position: Position) -> Player:
    name = f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
    base = rng.uniform(0.55, 0.9)
    return Player(
        player_id=f"{team_id}-{number:02d}",
        name=name,
        shirt_number=number,
        position=position,
        team_id=team_id,
        passing=_clamp(base + rng.uniform(-0.1, 0.1) + (0.08 if position is Position.MID else 0.0)),
        finishing=_clamp(base + rng.uniform(-0.2, 0.1) + (0.15 if position is Position.FWD else -0.15)),
        pace=_clamp(rng.uniform(0.45, 0.95)),
        defending=_clamp(base + (0.12 if position is Position.DEF else -0.12)),
    )


def _clamp(value: float, lo: float = 0.05, hi: float = 0.98) -> float:
    return round(max(lo, min(hi, value)), 3)


def build_team(rng: random.Random, club_index: int, style: str, formation: str) -> Team:
    """Assemble a 16-player squad (11 starters plus 5 substitutes)."""
    name, short, _venue = CLUBS[club_index % len(CLUBS)]
    defenders, midfielders, forwards = FORMATIONS[formation]

    players: list[Player] = [_make_player(rng, short, 1, Position.GK)]
    number = 2
    for position, count in (
        (Position.DEF, defenders),
        (Position.MID, midfielders),
        (Position.FWD, forwards),
    ):
        for _ in range(count):
            players.append(_make_player(rng, short, number, position))
            number += 1

    # Bench: one keeper, two defenders, one midfielder, one forward.
    for position in (Position.GK, Position.DEF, Position.DEF, Position.MID, Position.FWD):
        players.append(_make_player(rng, short, number, position))
        number += 1

    return Team(
        team_id=short,
        name=name,
        short_name=short,
        formation=formation,
        style=style,
        players=tuple(players),
    )


def venue_for(club_index: int) -> str:
    return CLUBS[club_index % len(CLUBS)][2]
