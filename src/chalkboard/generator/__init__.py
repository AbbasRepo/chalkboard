"""Synthetic football event generation."""

from .models import EventType, MatchEvent, MatchMeta, Outcome, Player, Position, Team
from .simulator import MatchSimulator
from .teams import STYLES, build_team

__all__ = [
    "EventType",
    "MatchEvent",
    "MatchMeta",
    "MatchSimulator",
    "Outcome",
    "Player",
    "Position",
    "STYLES",
    "Team",
    "build_team",
]
