"""Tests for the significance gate.

The gate decides what the system talks about, so both directions matter and
the second is the one people forget: it must stay quiet on a dull passage.
A gate that fires on everything is the same failure as one that never fires.

Offline, like everything else. A fake session returns canned rows, which is
enough to test scoring behaviour, cooldown and the evidence contract.
"""

from __future__ import annotations

import pytest

from chalkboard.graph.significance import (
    COOLDOWN_S,
    MAX_EVIDENCE,
    WINDOW_S,
    Moment,
    SignificanceGate,
    load_query,
)


def action(event_id, kind="PASSED_TO", actor="Mateo Okonjo", target="Luka Novak",
           zone="Z5L", forced=0.0, breaking=0.0, chance=None, outcome="SUCCESS"):
    row = {
        "event_id": event_id, "kind": kind, "clock_s": 1200.0,
        "actor": actor, "target": target, "zone": zone,
        "pressure": 0.7, "outcome": outcome,
        "forced": forced, "breaking": breaking,
    }
    if chance is not None:
        row["chance"] = chance
    return row


class FakeSession:
    """Returns whatever rows it is given, and records the parameters used."""

    def __init__(self, rows=None) -> None:
        self.rows = rows if rows is not None else []
        self.calls: list[dict] = []

    def run(self, query: str, **params):
        self.calls.append(params)
        return list(self.rows)


@pytest.fixture
def query():
    return load_query()


# ----------------------------------------------------------------- the query

def test_query_covers_both_halves_of_the_model(query):
    """Actions are relationships and shots are nodes, so the window has to
    be assembled in two parts. Missing either silently halves the gate."""
    for fragment in ("PASSED_TO", "CARRIED_INTO", "DISPOSSESSED", "PRESSED_IN"):
        assert fragment in query, fragment
    assert ":Shot)" in query
    assert "TOOK" in query


def test_query_is_parameterised_not_interpolated(query):
    for param in ("$matchId", "$now", "$window", "$threshold", "$maxEvidence"):
        assert param in query, param


def test_query_returns_evidence_with_citable_ids(query):
    """An Insight must point at specific actions. Without event_id on the
    evidence, provenance is a claim rather than a link."""
    assert "event_id: r.event_id" in query
    assert "RETURN score" in query and "evidence" in query


# --------------------------------------------------------------- firing

def test_fires_when_a_window_scores_above_threshold():
    rows = [{"score": 2.9, "evidence": [action("e1", chance=0.9)], "window_size": 40}]
    gate = SignificanceGate(FakeSession(rows), query="Q", threshold=1.8)
    moment = gate.check("CBM-00042", 1200.0)
    assert moment is not None
    assert moment.score == 2.9
    assert moment.evidence[0]["event_id"] == "e1"


def test_stays_quiet_on_a_dull_passage():
    """Twenty sideways passes should score nothing. This is the half of the
    gate's job that is easy to forget to test."""
    gate = SignificanceGate(FakeSession(rows=[]), query="Q", threshold=1.8)
    assert gate.check("CBM-00042", 1200.0) is None


def test_stays_quiet_when_nothing_is_citable():
    """Scoring above threshold with no evidence means staying silent.

    An unevidenced claim is the one thing this system must never make, so
    the gate would rather say nothing.
    """
    rows = [{"score": 9.0, "evidence": [], "window_size": 40}]
    gate = SignificanceGate(FakeSession(rows), query="Q", threshold=1.8)
    assert gate.check("CBM-00042", 1200.0) is None


def test_threshold_is_passed_to_the_query():
    session = FakeSession(rows=[])
    SignificanceGate(session, query="Q", threshold=2.5).check("CBM-00042", 900.0)
    assert session.calls[0]["threshold"] == 2.5
    assert session.calls[0]["window"] == WINDOW_S
    assert session.calls[0]["maxEvidence"] == MAX_EVIDENCE


# -------------------------------------------------------------- cooldown

def test_cooldown_prevents_repeating_the_same_passage():
    """The window is 90s and the gate runs every 3s, so one good passage
    would otherwise fire thirty times saying much the same thing."""
    rows = [{"score": 3.0, "evidence": [action("e1", chance=1.0)], "window_size": 40}]
    gate = SignificanceGate(FakeSession(rows), query="Q", threshold=1.8, cooldown_s=75)

    assert gate.check("CBM-00042", 1200.0) is not None
    assert gate.check("CBM-00042", 1210.0) is None
    assert gate.check("CBM-00042", 1260.0) is None
    assert gate.check("CBM-00042", 1280.0) is not None


def test_cooldown_gives_commentary_rhythm():
    """Roughly one remark every three minutes across a 90 minute match."""
    assert 45 <= COOLDOWN_S <= 120


def test_sweep_walks_a_whole_match():
    rows = [{"score": 3.0, "evidence": [action("e1", chance=1.0)], "window_size": 40}]
    gate = SignificanceGate(FakeSession(rows), query="Q", threshold=1.8, cooldown_s=75)
    moments = list(gate.sweep("CBM-00042", until_s=900.0, tick_s=3.0))
    # Every window scores, so cooldown alone sets the pace.
    assert 8 <= len(moments) <= 12


# ------------------------------------------------------- the evidence contract

def test_moment_lists_only_names_from_its_evidence():
    """The Explainer may use these names and no others. Anything else in its
    sentence is invented and gets the sentence rejected."""
    moment = Moment(
        match_id="CBM-00042", clock_s=1200.0, score=2.4,
        evidence=[action("e1", actor="Mateo Okonjo", target="Luka Novak")],
    )
    assert moment.players == ["Mateo Okonjo", "Luka Novak"]
    assert "Tomas Weber" not in moment.players


def test_moment_summarises_from_graph_facts_alone():
    """The fallback when inference is unavailable: plainer, never wrong."""
    moment = Moment(
        match_id="CBM-00042", clock_s=1200.0, score=3.1,
        evidence=[
            action("e1", kind="SHOT", chance=0.9, outcome="SAVED", actor="Idris Costa"),
            action("e2", kind="DISPOSSESSED", forced=0.8),
            action("e3", breaking=0.5),
        ],
    )
    text = moment.summary()
    assert "Idris Costa" in text
    assert "turnover" in text and "line-breaking" in text
    assert "Z5L" in text


def test_window_is_a_passage_of_play():
    """Shorter and you see a single pass; longer and a goal is averaged in
    with the dull minutes either side."""
    assert 60 <= WINDOW_S <= 120
