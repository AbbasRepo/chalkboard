"""Data contracts for Chalkboard synthetic football events.

Every event emitted by the simulator conforms to :class:`MatchEvent`. The schema
is deliberately flat and JSON-serialisable so it can travel over Event Hubs
without a schema registry, and be landed directly into graph tables.

Pitch coordinates are normalised to a 0-100 grid where (0, 50) is the centre of
the attacking team's own goal line and (100, 50) is the centre of the goal it is
attacking. Coordinates are always expressed from the perspective of the team in
possession, which removes end-swapping from every downstream consumer.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

PITCH_LENGTH_M = 105.0
PITCH_WIDTH_M = 68.0


class EventType(str, Enum):
    """Event vocabulary. Kept small on purpose: every type here is either
    required by the hackathon brief or needed to keep possession coherent."""

    PERIOD_START = "PERIOD_START"
    PERIOD_END = "PERIOD_END"
    KICK_OFF = "KICK_OFF"
    PASS = "PASS"
    CARRY = "CARRY"
    CROSS = "CROSS"
    SHOT = "SHOT"
    SAVE = "SAVE"
    GOAL = "GOAL"
    TACKLE = "TACKLE"
    INTERCEPTION = "INTERCEPTION"
    CLEARANCE = "CLEARANCE"
    PRESSURE = "PRESSURE"
    FOUL = "FOUL"
    THROW_IN = "THROW_IN"
    CORNER = "CORNER"
    GOAL_KICK = "GOAL_KICK"
    OFFSIDE = "OFFSIDE"
    SUBSTITUTION = "SUBSTITUTION"


class Outcome(str, Enum):
    SUCCESS = "SUCCESS"
    FAIL = "FAIL"
    GOAL = "GOAL"
    SAVED = "SAVED"
    OFF_TARGET = "OFF_TARGET"
    BLOCKED = "BLOCKED"
    NEUTRAL = "NEUTRAL"


class Position(str, Enum):
    GK = "GK"
    DEF = "DEF"
    MID = "MID"
    FWD = "FWD"


@dataclass(frozen=True)
class Player:
    player_id: str
    name: str
    shirt_number: int
    position: Position
    team_id: str
    #: 0-1 skill weights, used to bias outcome probabilities so that the
    #: generated data has believable per-player signal for the agents to find.
    passing: float = 0.75
    finishing: float = 0.5
    pace: float = 0.6
    defending: float = 0.5


@dataclass(frozen=True)
class Team:
    team_id: str
    name: str
    short_name: str
    formation: str
    style: str
    players: tuple[Player, ...]

    def starters(self) -> tuple[Player, ...]:
        return self.players[:11]

    def by_position(self, position: Position) -> list[Player]:
        return [p for p in self.starters() if p.position is position]


@dataclass(frozen=True)
class MatchMeta:
    match_id: str
    competition: str
    venue: str
    kickoff_utc: str
    home: Team
    away: Team
    period_length_s: int = 2700
    seed: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


@dataclass
class MatchEvent:
    """A single synthetic match event.

    The derived metrics (``pass_difficulty``, ``shot_xg``, ``pressure``) are
    produced by the generator rather than inferred downstream, so the dataset is
    self-describing and the Interpreter agent has something concrete to
    aggregate from the first event onward.
    """

    event_id: str
    match_id: str
    sequence: int
    period: int
    clock_s: float
    timestamp_utc: str
    type: EventType
    outcome: Outcome
    team_id: str
    possession_id: str
    possession_index: int

    player_id: str | None = None
    related_player_id: str | None = None

    start_x: float | None = None
    start_y: float | None = None
    end_x: float | None = None
    end_y: float | None = None

    # Pass quality (brief: pass distance, accuracy, difficulty rating)
    pass_distance_m: float | None = None
    pass_angle_deg: float | None = None
    pass_progressive: bool | None = None
    pass_difficulty: float | None = None

    # Speed metrics (brief: ball speed and shot speed)
    ball_speed_ms: float | None = None
    shot_speed_ms: float | None = None

    # Shot context
    shot_xg: float | None = None
    body_part: str | None = None

    # Pressure context (brief: pressure events)
    pressure: float | None = None
    opponents_within_5m: int | None = None

    # Running score after this event, so any single event is self-sufficient
    score_home: int = 0
    score_away: int = 0

    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))


def _clean(payload: Any) -> Any:
    """Recursively convert enums to values and drop ``None`` fields.

    Dropping nulls keeps the JSONL output roughly 40% smaller, which matters
    when the stream is replayed at speed.
    """
    if isinstance(payload, dict):
        return {k: _clean(v) for k, v in payload.items() if v is not None}
    if isinstance(payload, (list, tuple)):
        return [_clean(v) for v in payload]
    if isinstance(payload, Enum):
        return payload.value
    return payload
