"""Tests for the pipeline conductor.

The runner is where the pieces meet, so these check the wiring rather than
the components: that a fired moment becomes a payload with both renderings
and its evidence attached, and that a database blip does not end the match.
"""

from __future__ import annotations

import pytest

from chalkboard.agents.explainer import Explainer
from chalkboard.agents.llm import StubClient
from chalkboard.agents.personaliser import Personaliser
from chalkboard.app.pipeline import MatchRunner
from chalkboard.graph.significance import Moment


def turnover(event_id="a", actor="Tomas Weber", target="Idris Varela"):
    return {
        "event_id": event_id, "kind": "DISPOSSESSED", "clock_s": 1440.0,
        "actor": actor, "target": target, "zone": "Z4L",
        "pressure": 0.81, "outcome": "SUCCESS", "forced": 0.8, "breaking": 0.0,
    }


class FakeSession:
    """Answers the runner's three context queries and nothing else."""

    def run(self, query: str, **params):
        if "HOME_TEAM" in query and "matchweek" in query:
            return [{
                "match_id": "CBM-49861", "matchweek": 1, "stadium": "The Northgate",
                "home": "NGU", "home_name": "Northgate United", "home_style": "high_press",
                "away": "CFA", "away_name": "Castleford Athletic", "away_style": "low_block",
                "p_home": 0.4, "p_draw": 0.27, "p_away": 0.33,
            }]
        if "PLAYED_IN" in query:
            return [{"player_id": "NGU-06", "name": "Tomas Weber",
                     "position": "MID", "shirt": 6, "team": "NGU"}]
        if "outcome = 'GOAL'" in query:
            return [{"team": "NGU", "goals": 1}]
        return []


class OneMomentGate:
    """Fires once, then stays quiet - like the real gate under cooldown."""

    def __init__(self) -> None:
        self.fired = False

    def check(self, match_id, now_s):
        if self.fired:
            return None
        self.fired = True
        return Moment(match_id=match_id, clock_s=now_s, score=1.6,
                      evidence=[turnover("a"), turnover("b", target="Luka Novak")])


def runner(gate=None, **kwargs):
    session = FakeSession()
    return MatchRunner(
        session=session,
        match_id="CBM-49861",
        gate=gate or OneMomentGate(),
        explainer=Explainer(StubClient()),
        personaliser=Personaliser(StubClient(), "Tomas Weber"),
        speed=0,  # no sleeping in tests
        full_time_s=kwargs.pop("full_time_s", 30.0),
        **kwargs,
    )


def test_header_comes_first():
    payloads = list(runner().run())
    assert payloads[0]["type"] == "header"
    assert payloads[0]["home"] == "NGU"
    assert payloads[0]["squad"][0]["name"] == "Tomas Weber"


def test_clock_ticks_and_carries_the_score():
    payloads = list(runner().run())
    clocks = [p for p in payloads if p["type"] == "clock"]
    assert len(clocks) > 1
    assert clocks[0]["score"] == {"NGU": 1}
    assert [c["clock_s"] for c in clocks] == sorted(c["clock_s"] for c in clocks)


def test_a_fired_moment_becomes_a_full_payload():
    payloads = list(runner().run())
    insights = [p for p in payloads if p["type"] == "insight"]
    assert len(insights) == 1

    payload = insights[0]
    assert {r["persona"] for r in payload["renderings"]} == {"analyst", "fan"}
    assert payload["insight"]["sentence"]
    # The evidence travels to the browser so the subgraph can be drawn
    # beside the sentence - the groundedness argument, made visible.
    assert [e["event_id"] for e in payload["evidence"]] == ["a", "b"]


def test_the_match_ends_with_full_time():
    assert list(runner().run())[-1]["type"] == "full_time"


def test_a_gate_failure_does_not_end_the_match():
    """A database blip shows the last known state, not a blank screen."""

    class BrokenGate:
        def check(self, *_a):
            raise RuntimeError("neo4j went away")

    payloads = list(runner(gate=BrokenGate()).run())
    assert payloads[-1]["type"] == "full_time"
    assert not [p for p in payloads if p["type"] == "insight"]


def test_speed_zero_does_not_sleep():
    """Used by the tests and by an offline rehearsal sweep."""
    import time

    start = time.monotonic()
    list(runner(full_time_s=120.0).run())
    assert time.monotonic() - start < 2.0
