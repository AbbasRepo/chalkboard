"""Tests for the insight store and the Explainer's three-tier lookup."""

from __future__ import annotations

import pytest

from chalkboard.agents.explainer import Explainer, Insight
from chalkboard.graph.insights import FIND_INSIGHT, LIST_INSIGHTS, InsightStore
from chalkboard.graph.significance import Moment


class FakeSession:
    def __init__(self, rows=None) -> None:
        self.rows = rows if rows is not None else []
        self.calls: list[tuple[str, dict]] = []

    def run(self, query: str, **params):
        self.calls.append((query, params))
        return list(self.rows)


class ScriptedClient:
    name = "scripted"

    def __init__(self, sentence=None) -> None:
        self.sentence = sentence
        self.calls = 0

    def complete(self, prompt, system=None):
        self.calls += 1
        return self.sentence


def moment():
    return Moment(
        match_id="CBM-49861", clock_s=1440.0, score=1.6,
        evidence=[{
            "event_id": "a", "kind": "DISPOSSESSED", "actor": "Tomas Weber",
            "target": "Idris Varela", "zone": "Z4L", "pressure": 0.81,
            "outcome": "SUCCESS", "forced": 0.8, "breaking": 0.0,
        }],
    )


def insight(source="model"):
    return Insight("i1", "CBM-49861", 1440.0, 1.6, "Weber won it back.", source,
                   ["a"], ["Tomas Weber"], ["Z4L"])


# -------------------------------------------------------------- the store

def test_only_model_sentences_are_stored():
    """A graph summary regenerates for free, so caching it adds rows and
    nothing else."""
    session = FakeSession()
    InsightStore(session).save(insight(source="graph"))
    assert session.calls == []


def test_saving_wires_the_insight_to_its_evidence():
    session = FakeSession()
    InsightStore(session).save(insight(), zones=["Z4L"])
    queries = " ".join(q for q, _ in session.calls)
    assert "MERGE (i:Insight" in queries
    assert "EVIDENCED_BY" in queries
    assert "ABOUT_ZONE" in queries


def test_lookup_tolerates_a_nearby_tick():
    """A replay may land on a different tick than the pre-compute run did."""
    assert "abs(i.clock_s - $clockS) <= $tolerance" in FIND_INSIGHT


def test_lookup_only_returns_model_sentences():
    assert "i.source = 'model'" in FIND_INSIGHT


def test_a_lookup_failure_is_treated_as_a_miss():
    """A cache miss and a broken cache are the same thing to the caller."""

    class Broken:
        def run(self, *a, **k):
            raise RuntimeError("neo4j is down")

    assert InsightStore(Broken()).find("CBM-49861", 1440.0) is None


def test_listing_is_ordered_for_an_audit():
    assert "ORDER BY i.clock_s" in LIST_INSIGHTS


# ---------------------------------------------------------- the three tiers

def test_a_stored_sentence_is_used_without_calling_the_model():
    store = InsightStore(FakeSession([{
        "insight_id": "cached1", "sentence": "Weber squeezed the left side.",
        "clock_s": 1440.0, "score": 1.6,
    }]))
    client = ScriptedClient("should never be called")
    result = Explainer(client, store=store).explain(moment())

    assert result.source == "stored"
    assert result.sentence == "Weber squeezed the left side."
    assert client.calls == 0, "a cached sentence must not cost a model call"


def test_a_miss_falls_through_to_the_model():
    store = InsightStore(FakeSession([]))
    client = ScriptedClient("Weber won it back twice.")
    result = Explainer(client, store=store).explain(moment())

    assert result.source == "model"
    assert client.calls == 1


def test_a_miss_and_no_model_falls_through_to_the_graph():
    store = InsightStore(FakeSession([]))
    result = Explainer(ScriptedClient(None), store=store).explain(moment())
    assert result.source == "graph"
    assert result.sentence == moment().summary()


def test_a_store_failure_does_not_stop_the_sentence():
    class Exploding:
        def find(self, *a):
            return None

        def save(self, *a):
            raise RuntimeError("write failed")

    result = Explainer(ScriptedClient("Weber won it back."), store=Exploding()).explain(moment())
    assert result.source == "model"
