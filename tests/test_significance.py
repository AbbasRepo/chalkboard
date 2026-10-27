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
    DEFAULT_THRESHOLD,
    MAX_EVIDENCE,
    THRESHOLD_MULTIPLIER,
    WINDOW_S,
    Moment,
    SignificanceGate,
    ThresholdStats,
    estimate_threshold,
    load_queries,
    load_query,
    load_threshold_query,
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
        self.queries: list[str] = []

    def run(self, query: str, **params):
        self.queries.append(query)
        self.calls.append(params)
        return list(self.rows)


class RoutingFakeSession:
    """Returns different rows for ESTIMATE_THRESHOLD vs SCORE_WINDOW queries."""

    def __init__(self, threshold_rows=None, score_rows=None) -> None:
        self.threshold_rows = threshold_rows if threshold_rows is not None else []
        self.score_rows = score_rows if score_rows is not None else []
        self.calls: list[tuple[str, dict]] = []

    def run(self, query: str, **params):
        self.calls.append((query, params))
        if "expected_window_score" in query or "defaultThreshold" in params:
            return list(self.threshold_rows)
        return list(self.score_rows)


@pytest.fixture
def query():
    return load_query()


@pytest.fixture
def threshold_query():
    return load_threshold_query()


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


# ------------------------------------------------ dynamic threshold query

def test_both_named_queries_are_loaded_cleanly():
    queries = load_queries()
    assert set(queries) == {"ESTIMATE_THRESHOLD", "SCORE_WINDOW"}
    for name, body in queries.items():
        assert not body.endswith(";"), f"{name} kept its terminator"
        assert "--name:" not in body, f"{name} swallowed the next marker"


def test_threshold_query_aggregates_global_event_rates(threshold_query):
    """The dynamic threshold query must inspect all scored event types
    globally across matches without scoping to a single $matchId."""
    assert "MATCH (m:Match)" in threshold_query
    assert "$matchId" not in threshold_query
    for fragment in ("DISPOSSESSED", "PASSED_TO", "Shot", "shot_xg"):
        assert fragment in threshold_query, fragment
    for param in ("$window", "$multiplier", "$defaultThreshold"):
        assert param in threshold_query, param


def test_threshold_query_is_read_only(threshold_query):
    """Statistical parameter extraction must never mutate the graph."""
    for keyword in ("MERGE", "CREATE", "DELETE", "SET"):
        assert keyword not in threshold_query, keyword


def test_estimate_threshold_extracts_stats_from_graph():
    session = FakeSession(rows=[{
        "threshold": 1.42,
        "expected_window_score": 0.947,
        "matches": 3,
        "action_count": 3890,
        "forced_turnovers": 42,
        "breaking_passes": 28,
        "shot_count": 81,
        "total_xg": 8.6,
    }])
    stats = estimate_threshold(session, window_s=90.0, multiplier=1.5)
    assert isinstance(stats, ThresholdStats)
    assert stats.threshold == 1.42
    assert stats.expected_window_score == 0.947
    assert stats.matches == 3
    assert stats.action_count == 3890
    assert stats.forced_turnovers == 42
    assert stats.breaking_passes == 28
    assert stats.shot_count == 81
    assert stats.total_xg == 8.6
    assert session.calls[0] == {
        "window": 90.0,
        "multiplier": 1.5,
        "defaultThreshold": DEFAULT_THRESHOLD,
    }


def test_estimate_threshold_falls_back_when_graph_is_empty():
    session = FakeSession(rows=[])
    stats = estimate_threshold(session)
    assert stats.threshold == DEFAULT_THRESHOLD
    assert stats.matches == 0


def test_gate_derives_threshold_dynamically_on_init(monkeypatch):
    monkeypatch.delenv("SIGNIFICANCE_THRESHOLD", raising=False)
    monkeypatch.delenv("SIGNIFICANCE_MULTIPLIER", raising=False)
    session = RoutingFakeSession(
        threshold_rows=[{
            "threshold": 1.35,
            "expected_window_score": 0.9,
            "matches": 2,
            "action_count": 2600,
            "forced_turnovers": 25,
            "breaking_passes": 18,
            "shot_count": 50,
            "total_xg": 5.2,
        }],
        score_rows=[{"score": 1.9, "evidence": [action("e1", chance=0.9)], "window_size": 35}],
    )
    gate = SignificanceGate(session)
    assert gate.threshold == 1.35
    assert gate.threshold_stats is not None
    assert gate.threshold_stats.matches == 2

    moment = gate.check("CBM-00042", 1200.0)
    assert moment is not None
    assert session.calls[-1][1]["threshold"] == 1.35


def test_gate_respects_numeric_env_override_without_querying(monkeypatch):
    monkeypatch.setenv("SIGNIFICANCE_THRESHOLD", "2.1")
    session = RoutingFakeSession(threshold_rows=[{"threshold": 0.9}])
    gate = SignificanceGate(session)
    assert gate.threshold == 2.1
    assert gate.threshold_stats is None
    assert len(session.calls) == 0


def test_gate_runs_dynamic_query_when_env_is_auto(monkeypatch):
    monkeypatch.setenv("SIGNIFICANCE_THRESHOLD", "auto")
    monkeypatch.setenv("SIGNIFICANCE_MULTIPLIER", "1.75")
    session = RoutingFakeSession(threshold_rows=[{"threshold": 1.65, "matches": 1}])
    gate = SignificanceGate(session)
    assert gate.threshold == 1.65
    assert gate.threshold_stats is not None
    assert session.calls[0][1]["multiplier"] == 1.75


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
