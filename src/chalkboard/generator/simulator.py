"""Possession-based synthetic match simulator.

The model is a Markov chain over possession states. Each possession belongs to
one team and advances through a chain of actions (pass, carry, cross, shot)
until it ends in a turnover, a shot, or a set-piece restart. Action selection
and outcome probabilities are conditioned on:

* pitch position, in thirds,
* the style profile of the team in possession,
* the press profile of the team out of possession, and
* per-player skill weights.

That conditioning is what makes the output *football-realistic* rather than
merely random: a low block concedes territory but few clear chances, a high
press forces turnovers in dangerous areas, and a direct side generates more
shots of lower quality. Those are exactly the patterns the downstream agents are
asked to detect and explain.

The simulator is deterministic for a given seed, which matters for demos and for
reproducible evaluation runs.
"""

from __future__ import annotations

import math
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterator

from .models import EventType, MatchEvent, MatchMeta, Outcome, Player, Position, Team
from .teams import STYLES, StyleProfile

#: Mean seconds of match clock consumed by a single event, before tempo scaling.
BASE_EVENT_SECONDS = 4.2

#: A possession is abandoned after this many actions to avoid pathological runs.
#: Set high enough that a patient side can actually complete a long build-up.
MAX_CHAIN = 24


class MatchSimulator:
    """Generates a full match as an ordered stream of :class:`MatchEvent`."""

    def __init__(self, meta: MatchMeta, seed: int | None = None) -> None:
        self.meta = meta
        self.rng = random.Random(seed if seed is not None else meta.seed)
        self.kickoff = datetime.fromisoformat(meta.kickoff_utc.replace("Z", "+00:00"))

        self._sequence = 0
        self._score = {meta.home.team_id: 0, meta.away.team_id: 0}
        self._clock = 0.0
        self._period = 1

    # ------------------------------------------------------------------ public

    def run(self) -> Iterator[MatchEvent]:
        """Yield every event of the match, in chronological order."""
        for period in (1, 2):
            self._period = period
            self._clock = 0.0
            yield self._event(
                EventType.PERIOD_START,
                Outcome.NEUTRAL,
                team=self.meta.home,
                player=None,
                possession_id="-",
                possession_index=0,
            )
            yield from self._play_period()
            yield self._event(
                EventType.PERIOD_END,
                Outcome.NEUTRAL,
                team=self.meta.home,
                player=None,
                possession_id="-",
                possession_index=0,
            )

    @property
    def score(self) -> dict[str, int]:
        return dict(self._score)

    # ----------------------------------------------------------------- periods

    def _play_period(self) -> Iterator[MatchEvent]:
        attacking = self.meta.home if self._period == 1 else self.meta.away
        restart = EventType.KICK_OFF

        while self._clock < self.meta.period_length_s:
            defending = self._opponent(attacking)
            possession_id = uuid.uuid4().hex[:12]
            events, turnover_to = self._play_possession(
                attacking, defending, possession_id, restart
            )
            # Yield the whole possession. The clock is advanced when events are
            # generated, so truncating here would drop events whose effects
            # (notably goals) have already been applied to the score. Play runs
            # to the end of the passage, exactly as it does in a real match.
            yield from events
            attacking = turnover_to
            restart = EventType.PASS

    def _play_possession(
        self,
        attacking: Team,
        defending: Team,
        possession_id: str,
        restart: EventType,
    ) -> tuple[list[MatchEvent], Team]:
        """Play one possession. Returns its events and who gets the ball next."""
        style = STYLES[attacking.style]
        press = STYLES[defending.style]
        events: list[MatchEvent] = []

        x, y = self._start_location(restart, press)
        carrier = self._pick_starter(attacking, x)

        for index in range(MAX_CHAIN):
            pressure, nearby = self._pressure_at(x, press)

            if restart is EventType.KICK_OFF and index == 0:
                events.append(
                    self._event(
                        EventType.KICK_OFF,
                        Outcome.SUCCESS,
                        attacking,
                        carrier,
                        possession_id,
                        index,
                        start_x=50.0,
                        start_y=50.0,
                        pressure=0.0,
                        opponents_within_5m=0,
                    )
                )
                x, y = 50.0, 50.0
                continue

            action = self._choose_action(x, style, pressure)

            if action is EventType.SHOT:
                shot = self._resolve_shot(
                    attacking, defending, carrier, x, y, pressure, nearby,
                    possession_id, index, style,
                )
                events.extend(shot.events)
                return events, shot.next_team

            if self.rng.random() < press.press_intensity * pressure * 0.22:
                events.append(
                    self._event(
                        EventType.PRESSURE,
                        Outcome.NEUTRAL,
                        defending,
                        self._nearest_defender(defending, x),
                        possession_id,
                        index,
                        start_x=100.0 - x,
                        start_y=100.0 - y,
                        pressure=round(pressure, 3),
                        opponents_within_5m=nearby,
                        tags=["press_trigger"],
                    )
                )

            target = self._pick_receiver(attacking, carrier, x, style)
            end_x, end_y = self._destination(x, y, style, action)
            event = self._build_move(
                action, attacking, carrier, target, x, y, end_x, end_y,
                pressure, nearby, possession_id, index, style,
            )
            events.append(event)

            if event.outcome is Outcome.FAIL:
                recovery = self._nearest_defender(defending, end_x)
                events.append(
                    self._event(
                        EventType.INTERCEPTION
                        if action is not EventType.CARRY
                        else EventType.TACKLE,
                        Outcome.SUCCESS,
                        defending,
                        recovery,
                        possession_id,
                        index,
                        start_x=100.0 - end_x,
                        start_y=100.0 - end_y,
                        pressure=round(pressure, 3),
                        opponents_within_5m=nearby,
                        tags=["turnover", self._zone(end_x)],
                    )
                )
                return events, defending

            x, y, carrier = end_x, end_y, target

        return events, defending

    # ----------------------------------------------------------------- actions

    def _choose_action(self, x: float, style: StyleProfile, pressure: float) -> EventType:
        shot_chance = 0.0
        if x > 70.0:
            # The exponent encodes shot selection: a side with little appetite
            # for shooting waits until it is close to goal, so it takes fewer
            # shots but each one is worth more. A high-appetite side flattens
            # the curve and shoots from anywhere in the final third.
            selectivity = 4.0 - 2.8 * style.shot_appetite
            shot_chance = ((x - 70.0) / 30.0) ** selectivity * 0.17
        if x > 60.0 and style.shot_appetite > 0.55:
            shot_chance += 0.015

        if self.rng.random() < shot_chance:
            return EventType.SHOT
        if x > 65.0 and self.rng.random() < style.width * 0.35:
            return EventType.CROSS
        if self.rng.random() < 0.18 + 0.15 * (1.0 - pressure):
            return EventType.CARRY
        return EventType.PASS

    def _build_move(
        self,
        action: EventType,
        team: Team,
        carrier: Player,
        target: Player,
        x: float,
        y: float,
        end_x: float,
        end_y: float,
        pressure: float,
        nearby: int,
        possession_id: str,
        index: int,
        style: StyleProfile,
    ) -> MatchEvent:
        distance = _metres(x, y, end_x, end_y)
        angle = math.degrees(math.atan2(end_y - y, end_x - x))
        progressive = (end_x - x) > 8.0
        difficulty = self._pass_difficulty(distance, angle, pressure, progressive, action)

        skill = carrier.passing if action is not EventType.CARRY else carrier.pace
        success_p = _clamp(0.98 - difficulty * 0.72 + (skill - 0.7) * 0.35, 0.15, 0.99)
        success = self.rng.random() < success_p

        speed = _clamp(distance / max(0.45, (1.6 - 0.7 * style.tempo)), 4.0, 32.0)

        return self._event(
            action,
            Outcome.SUCCESS if success else Outcome.FAIL,
            team,
            carrier,
            possession_id,
            index,
            related_player_id=target.player_id if action is not EventType.CARRY else None,
            start_x=round(x, 2),
            start_y=round(y, 2),
            end_x=round(end_x, 2),
            end_y=round(end_y, 2),
            pass_distance_m=round(distance, 2),
            pass_angle_deg=round(angle, 1),
            pass_progressive=progressive,
            pass_difficulty=round(difficulty, 3),
            ball_speed_ms=round(speed, 2),
            pressure=round(pressure, 3),
            opponents_within_5m=nearby,
            tags=[self._zone(x)] + (["progressive"] if progressive else []),
        )

    def _resolve_shot(
        self,
        attacking: Team,
        defending: Team,
        shooter: Player,
        x: float,
        y: float,
        pressure: float,
        nearby: int,
        possession_id: str,
        index: int,
        style: StyleProfile,
    ) -> "_ShotResult":
        xg = _expected_goals(x, y, pressure)
        # Centred on 1.0 so that, across a season, goals track xG and a
        # clinical finisher outperforms it by a believable margin.
        quality = _clamp(xg * (0.70 + 0.35 * shooter.finishing), 0.005, 0.92)
        roll = self.rng.random()
        speed = round(self.rng.uniform(18.0, 31.0) * (0.8 + 0.4 * shooter.finishing), 2)
        body = "head" if x > 88.0 and self.rng.random() < 0.25 else "foot"

        if roll < quality:
            outcome, follow = Outcome.GOAL, EventType.GOAL
        elif roll < quality + 0.30:
            outcome, follow = Outcome.SAVED, EventType.SAVE
        elif roll < quality + 0.45:
            outcome, follow = Outcome.BLOCKED, None
        else:
            outcome, follow = Outcome.OFF_TARGET, None

        shot = self._event(
            EventType.SHOT,
            outcome,
            attacking,
            shooter,
            possession_id,
            index,
            start_x=round(x, 2),
            start_y=round(y, 2),
            shot_xg=round(xg, 4),
            shot_speed_ms=speed,
            body_part=body,
            pressure=round(pressure, 3),
            opponents_within_5m=nearby,
            tags=["shot", self._zone(x)],
        )
        events = [shot]

        if follow is EventType.GOAL:
            self._score[attacking.team_id] += 1
            shot.score_home = self._score[self.meta.home.team_id]
            shot.score_away = self._score[self.meta.away.team_id]
            events.append(
                self._event(
                    EventType.GOAL,
                    Outcome.GOAL,
                    attacking,
                    shooter,
                    possession_id,
                    index + 1,
                    start_x=round(x, 2),
                    start_y=round(y, 2),
                    shot_xg=round(xg, 4),
                    tags=["goal"],
                )
            )
            return _ShotResult(events, defending)

        if follow is EventType.SAVE:
            keeper = defending.by_position(Position.GK)[0]
            events.append(
                self._event(
                    EventType.SAVE,
                    Outcome.SUCCESS,
                    defending,
                    keeper,
                    possession_id,
                    index + 1,
                    start_x=round(100.0 - x, 2),
                    start_y=round(100.0 - y, 2),
                    tags=["save"],
                )
            )
        return _ShotResult(events, defending)

    # ------------------------------------------------------------- positioning

    def _start_location(self, restart: EventType, press: StyleProfile) -> tuple[float, float]:
        if restart is EventType.KICK_OFF:
            return 50.0, 50.0
        # A high press forces play to begin deeper for the team in possession.
        base = 34.0 - 10.0 * press.press_height
        return (
            _clamp(self.rng.gauss(base, 11.0), 4.0, 70.0),
            _clamp(self.rng.gauss(50.0, 20.0), 3.0, 97.0),
        )

    def _destination(
        self, x: float, y: float, style: StyleProfile, action: EventType
    ) -> tuple[float, float]:
        if action is EventType.CARRY:
            gain = self.rng.gauss(5.0, 4.0)
            lateral = self.rng.gauss(0.0, 5.0)
        elif action is EventType.CROSS:
            return _clamp(self.rng.gauss(90.0, 4.0), 72.0, 99.0), _clamp(
                self.rng.gauss(50.0, 9.0), 25.0, 75.0
            )
        else:
            long_ball = self.rng.random() < style.directness
            mean = 22.0 if long_ball else 7.5
            gain = self.rng.gauss(mean * (1.0 - 0.30 * style.patience), 9.0)
            lateral = self.rng.gauss(0.0, 14.0 * (0.5 + style.width))

        return _clamp(x + gain, 2.0, 99.0), _clamp(y + lateral, 2.0, 98.0)

    def _pressure_at(self, x: float, press: StyleProfile) -> tuple[float, int]:
        """Pressure on the ball, 0-1, plus a count of nearby opponents.

        Pressure peaks where the pressing team wants to win the ball: close to
        the press height. This is what lets the Explainer agent describe a
        pressure trap rather than just a turnover.
        """
        target_x = 100.0 - press.press_height * 75.0
        proximity = math.exp(-((x - target_x) ** 2) / 620.0)
        value = _clamp(
            proximity * press.press_intensity + self.rng.gauss(0.0, 0.09), 0.0, 1.0
        )
        return value, int(round(value * 3.4 + self.rng.random()))

    def _pick_starter(self, team: Team, x: float) -> Player:
        pool = team.by_position(Position.DEF if x < 40.0 else Position.MID)
        return self.rng.choice(pool or list(team.starters()))

    def _pick_receiver(
        self, team: Team, carrier: Player, x: float, style: StyleProfile
    ) -> Player:
        if x > 70.0:
            weights = {Position.FWD: 5, Position.MID: 3, Position.DEF: 1, Position.GK: 0}
        elif x > 40.0:
            weights = {Position.FWD: 3, Position.MID: 5, Position.DEF: 2, Position.GK: 0}
        else:
            weights = {Position.FWD: 1, Position.MID: 4, Position.DEF: 5, Position.GK: 1}

        candidates = [p for p in team.starters() if p.player_id != carrier.player_id]
        population = [p for p in candidates if weights[p.position] > 0]
        return self.rng.choices(
            population, weights=[weights[p.position] for p in population], k=1
        )[0]

    def _nearest_defender(self, team: Team, x: float) -> Player:
        pool = team.by_position(Position.MID if x > 45.0 else Position.DEF)
        return self.rng.choice(pool or list(team.starters()))

    def _opponent(self, team: Team) -> Team:
        return self.meta.away if team.team_id == self.meta.home.team_id else self.meta.home

    # --------------------------------------------------------------- utilities

    @staticmethod
    def _pass_difficulty(
        distance: float,
        angle: float,
        pressure: float,
        progressive: bool,
        action: EventType,
    ) -> float:
        """0-1 difficulty rating, as called for in the brief.

        Distance dominates, pressure amplifies, backwards passes are easier, and
        crosses carry a fixed penalty.
        """
        base = _clamp(distance / 55.0, 0.0, 1.0) * 0.55
        base += pressure * 0.28
        base += 0.10 if progressive else 0.0
        base += 0.12 if action is EventType.CROSS else 0.0
        base -= 0.10 if abs(angle) > 120.0 else 0.0
        return _clamp(base, 0.02, 0.98)

    @staticmethod
    def _zone(x: float) -> str:
        if x < 33.3:
            return "defensive_third"
        if x < 66.6:
            return "middle_third"
        return "final_third"

    def _event(
        self,
        type_: EventType,
        outcome: Outcome,
        team: Team,
        player: Player | None,
        possession_id: str,
        possession_index: int,
        **fields,
    ) -> MatchEvent:
        self._sequence += 1
        self._clock += max(0.4, self.rng.gauss(BASE_EVENT_SECONDS, 1.6))
        offset = (self._period - 1) * self.meta.period_length_s + self._clock
        return MatchEvent(
            event_id=uuid.uuid4().hex,
            match_id=self.meta.match_id,
            sequence=self._sequence,
            period=self._period,
            clock_s=round(self._clock, 1),
            timestamp_utc=(
                self.kickoff + timedelta(seconds=offset)
            ).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
            type=type_,
            outcome=outcome,
            team_id=team.team_id,
            possession_id=possession_id,
            possession_index=possession_index,
            player_id=player.player_id if player else None,
            score_home=self._score[self.meta.home.team_id],
            score_away=self._score[self.meta.away.team_id],
            **fields,
        )


class _ShotResult:
    __slots__ = ("events", "next_team")

    def __init__(self, events: list[MatchEvent], next_team: Team) -> None:
        self.events = events
        self.next_team = next_team


def _expected_goals(x: float, y: float, pressure: float) -> float:
    """A compact xG model: logistic over distance to goal and shooting angle.

    Not calibrated against real data — it cannot be, since the data is
    synthetic — but it behaves correctly: close and central is good, wide and
    far is poor, and pressure degrades both.
    """
    dx = (100.0 - x) / 100.0 * 105.0
    dy = abs(y - 50.0) / 100.0 * 68.0
    distance = math.hypot(dx, dy)
    goal_half_width = 3.66
    angle = math.atan2(2 * goal_half_width * max(dx, 0.5), dx**2 + dy**2 - goal_half_width**2)
    if angle < 0:
        angle += math.pi

    # Intercept calibrated so the mean value of a generated shot sits near 0.10,
    # which is the long-run open-play figure in professional football.
    logit = -3.17 - 0.105 * distance + 1.95 * angle - 0.55 * pressure
    return _clamp(1.0 / (1.0 + math.exp(-logit)), 0.005, 0.95)


def _metres(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot((x2 - x1) / 100.0 * 105.0, (y2 - y1) / 100.0 * 68.0)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
