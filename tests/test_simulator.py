"""Tests for the synthetic match generator.

The realism tests are deliberately loose-bounded. They are not asserting that
the simulator reproduces any particular league; they guard against the failure
mode that actually matters, which is a regression that silently turns the data
into noise (forty goals a match, or ten) and quietly destroys every downstream
agent's ability to say anything sensible.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict

import pytest

from chalkboard.generator.cli import build_match
from chalkboard.generator.models import EventType, Outcome
from chalkboard.generator.simulator import MatchSimulator, _expected_goals
from chalkboard.generator.teams import STYLES


def simulate(seed: int):
    meta = build_match(seed)
    return meta, list(MatchSimulator(meta, seed=seed).run())


@pytest.fixture(scope="module")
def corpus():
    """Thirty matches, reused across the realism tests."""
    return [simulate(seed) for seed in range(1, 31)]


def test_is_deterministic_for_a_seed():
    _, first = simulate(7)
    _, second = simulate(7)
    mask = {"event_id": "", "possession_id": ""}
    assert [e.to_dict() | mask for e in first] == [
        e.to_dict() | mask for e in second
    ]


def test_every_event_is_json_serialisable_and_well_formed(corpus):
    meta, events = corpus[0]
    for event in events:
        payload = json.loads(event.to_json())
        assert payload["match_id"] == meta.match_id
        assert payload["period"] in (1, 2)
        assert payload["clock_s"] >= 0
        assert "None" not in event.to_json()


def test_sequence_and_clock_are_monotonic(corpus):
    for _meta, events in corpus[:5]:
        assert [e.sequence for e in events] == sorted(e.sequence for e in events)
        for period in (1, 2):
            clocks = [e.clock_s for e in events if e.period == period]
            assert clocks == sorted(clocks)


def test_coordinates_stay_on_the_pitch(corpus):
    for _meta, events in corpus[:5]:
        for event in events:
            for value in (event.start_x, event.start_y, event.end_x, event.end_y):
                if value is not None:
                    assert 0.0 <= value <= 100.0


def test_score_is_consistent_with_goal_events(corpus):
    for meta, events in corpus:
        goals = Counter(e.team_id for e in events if e.type is EventType.GOAL)
        final = events[-1]
        assert final.score_home == goals.get(meta.home.team_id, 0)
        assert final.score_away == goals.get(meta.away.team_id, 0)


def test_xg_is_monotonic_in_distance_and_angle():
    close_central = _expected_goals(92.0, 50.0, 0.0)
    far_central = _expected_goals(70.0, 50.0, 0.0)
    close_wide = _expected_goals(92.0, 12.0, 0.0)
    assert close_central > far_central
    assert close_central > close_wide
    assert _expected_goals(92.0, 50.0, 0.9) < close_central


def test_match_volumes_are_football_realistic(corpus):
    goals = [sum(1 for e in ev if e.type is EventType.GOAL) for _m, ev in corpus]
    shots = [sum(1 for e in ev if e.type is EventType.SHOT) for _m, ev in corpus]
    assert 1.5 <= statistics.mean(goals) <= 4.5, "goals per match outside plausible range"
    assert 18 <= statistics.mean(shots) <= 38, "shots per match outside plausible range"


def test_pass_accuracy_is_football_realistic(corpus):
    attempted = completed = 0
    for _m, events in corpus:
        for event in events:
            if event.type is EventType.PASS:
                attempted += 1
                completed += event.outcome is Outcome.SUCCESS
    assert 0.68 <= completed / attempted <= 0.90


def test_shot_quality_responds_to_style():
    """A side with low appetite for shooting should take better chances.

    This is the property that gives the Explainer agent something true to say,
    so it is worth asserting rather than assuming.
    """
    quality: dict[str, list[float]] = defaultdict(list)
    for seed in range(1, 41):
        meta, events = simulate(seed)
        styles = {meta.home.team_id: meta.home.style, meta.away.team_id: meta.away.style}
        for event in events:
            if event.type is EventType.SHOT and event.shot_xg is not None:
                quality[styles[event.team_id]].append(event.shot_xg)

    means = {k: statistics.mean(v) for k, v in quality.items() if len(v) > 30}
    assert means["direct"] < means["low_block"], (
        "a direct side should shoot from worse positions than a counter-attacking one"
    )


def test_all_styles_are_playable():
    for style in STYLES:
        meta = build_match(99)
        home = type(meta.home)(**{**meta.home.__dict__, "style": style})
        patched = type(meta)(**{**meta.__dict__, "home": home})
        events = list(MatchSimulator(patched, seed=99).run())
        assert len(events) > 500
