"""Tests for the ontology loader.

Runs entirely offline. The fake session records the Cypher and parameters it
is handed, so the whole semantic model can be exercised with no Neo4j
instance. That is a hard requirement: CI has no database and no secrets.
"""

from __future__ import annotations

import json

import pytest

from chalkboard.generator.cli import build_match
from chalkboard.generator.simulator import MatchSimulator
from chalkboard.graph.loader import SPINE, OntologyLoader, load_mapping


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def run(self, query: str, **params):
        self.calls.append((query, params))

    def rows_for(self, fragment: str) -> list[dict]:
        out: list[dict] = []
        for query, params in self.calls:
            if fragment in query:
                out.extend(params.get("rows", []))
        return out


@pytest.fixture(scope="module")
def mapping():
    return load_mapping()


@pytest.fixture(scope="module")
def match_lines():
    meta = build_match(42)
    lines = [json.dumps({"type": "MATCH_META", **meta.to_dict()})]
    lines += [e.to_json() for e in MatchSimulator(meta, seed=42).run()]
    return meta, lines


def load(mapping, lines, batch_size=200, pitch=True):
    session = FakeSession()
    loader = OntologyLoader(mapping, session, batch_size=batch_size)
    if pitch:
        loader.load_pitch()
    counts = loader.load_stream(lines)
    return session, counts


# ------------------------------------------------------------- the model

def test_mapping_is_internally_consistent(mapping):
    labels = set(mapping["entities"]) | set(mapping["reified"])
    for section in ("structure", "actions", "provenance"):
        for edge in mapping[section]:
            assert edge["from"]["node"] in labels, edge["type"]
            assert edge["to"]["node"] in labels, edge["type"]


def test_only_multi_participant_actions_are_reified(mapping):
    """Arity decides shape. Nothing is a node for taste.

    A pass has two participants and must be an edge. Shot and Foul have more
    and must be nodes. Insight is system output, not a football action.
    """
    assert set(mapping["reified"]) == {"Shot", "Foul", "Insight"}
    for rule in mapping["actions"]:
        if rule["type"] == "PASSED_TO":
            assert rule["from"]["node"] == "Player"
            assert rule["to"]["node"] == "Player"


# ------------------------------------------------------------- the pitch

def test_pitch_is_eighteen_zones_with_adjacency(mapping):
    session, _ = load(mapping, [], pitch=True)
    zones = session.rows_for("MERGE (n:Zone")
    assert len(zones) == 18
    assert len(session.rows_for("ADJACENT_TO")) == 54


# ---------------------------------------------------------- entity layer

def test_entity_graph_stays_small(mapping, match_lines):
    """The whole point of the edge-centric model: a readable graph.

    A full match is under 100 nodes. The thousand-odd actions are
    relationships, not nodes.
    """
    _meta, lines = match_lines
    _session, counts = load(mapping, lines)
    nodes = sum(v for k, v in counts.items() if k.startswith("node:"))
    assert nodes < 120, f"entity graph has grown to {nodes} nodes"
    assert counts["node:Player"] == 32
    assert counts["node:Team"] == 2
    assert counts["node:Zone"] == 18


def test_officials_are_four_with_roles_on_the_edge(mapping, match_lines):
    _meta, lines = match_lines
    session, counts = load(mapping, lines)
    assert counts["node:Referee"] == 4
    roles = {r["props"]["role"] for r in session.rows_for("OFFICIATED_BY")}
    assert roles == {"referee", "assistant_1", "assistant_2", "var"}


def test_shirt_and_position_live_on_the_appearance_edge(mapping, match_lines):
    """Position is a fact about a player in a match, not about the player."""
    _meta, lines = match_lines
    session, _ = load(mapping, lines)
    played_in = session.rows_for("PLAYED_IN")
    assert len(played_in) == 32
    assert all("position" in r["props"] and "shirt_number" in r["props"] for r in played_in)
    for row in session.rows_for("MERGE (n:Player"):
        assert "position" not in row["props"]


def test_pre_game_prediction_is_stored_on_the_match(mapping, match_lines):
    _meta, lines = match_lines
    session, _ = load(mapping, lines)
    match = session.rows_for("MERGE (n:Match")[0]["props"]
    total = (
        match["pre_game_win_prob_home"]
        + match["pre_game_draw_prob"]
        + match["pre_game_win_prob_away"]
    )
    assert abs(total - 1.0) < 0.01
    assert match["prediction_model"]


# ---------------------------------------------------------- action layer

def test_passes_become_relationships_between_players(mapping, match_lines):
    _meta, lines = match_lines
    _session, counts = load(mapping, lines)
    assert counts["edge:PASSED_TO"] > 500
    assert counts["edge:PASSED_TO"] == counts["source:PASS"] + counts["source:CROSS"]


def test_every_action_edge_carries_the_temporal_spine(mapping, match_lines):
    """Without clock_s nothing can be ordered; without match_id, aggregates
    over shared Zone nodes silently mix fixtures together."""
    _meta, lines = match_lines
    session, _ = load(mapping, lines)
    for rel in ("PASSED_TO", "CARRIED_INTO", "DISPOSSESSED"):
        rows = session.rows_for(f"r:{rel} ")
        assert rows, rel
        for row in rows[:50]:
            for field in ("clock_s", "period", "match_id", "possession_id", "sequence"):
                assert field in row["props"], f"{rel} missing {field}"


def test_hold_time_is_recorded_on_passes(mapping, match_lines):
    """How long a player kept the ball before releasing it."""
    _meta, lines = match_lines
    session, _ = load(mapping, lines)
    holds = [r["props"].get("hold_time_s") for r in session.rows_for("r:PASSED_TO ")]
    present = [h for h in holds if h is not None]
    assert len(present) > 400
    assert max(present) > min(present), "hold time should vary"


def test_shots_are_nodes_with_zone_and_taker(mapping, match_lines):
    """A shot has shooter, keeper and zone - too many participants for an edge."""
    _meta, lines = match_lines
    _session, counts = load(mapping, lines)
    assert counts["node:Shot"] == counts["source:SHOT"]
    assert counts["edge:TOOK"] == counts["node:Shot"]
    assert counts["edge:FROM_ZONE"] == counts["node:Shot"]


def test_repeated_passes_are_separate_relationships(mapping, match_lines):
    """Two players pass to each other many times, and each pass is its own
    relationship with its own evidence value. Collapsing them would make an
    insight uncitable."""
    _meta, lines = match_lines
    session, _ = load(mapping, lines)
    rows = session.rows_for("r:PASSED_TO ")
    pairs = {(r["a"], r["b"]) for r in rows}
    assert len(rows) > len(pairs) * 2, "passes should not be collapsed per pair"


# --------------------------------------------------------------- safety

def test_is_idempotent_on_replay(mapping, match_lines):
    _meta, lines = match_lines
    once, _ = load(mapping, lines)
    twice, _ = load(mapping, lines + lines)
    for fragment in ("MERGE (n:Player", "MERGE (n:Shot", "MERGE (n:Team"):
        assert {r["key"] for r in once.rows_for(fragment)} == {
            r["key"] for r in twice.rows_for(fragment)
        }


def test_player_names_are_never_blanked_by_later_events(mapping, match_lines):
    _meta, lines = match_lines
    session, _ = load(mapping, lines)
    for row in session.rows_for("MERGE (n:Player"):
        assert "name" not in row["props"] or row["props"]["name"]


def test_rejects_an_unsafe_label(mapping):
    bad = {"entities": {"Player; DROP": {"key": "player_id"}}, "reified": {}, "actions": []}
    with pytest.raises(ValueError, match="node label"):
        OntologyLoader(bad, FakeSession())


def test_constraints_cover_every_node_key(mapping):
    session = FakeSession()
    OntologyLoader(mapping, session).ensure_constraints()
    created = " ".join(q for q, _ in session.calls)
    for label in {**mapping["entities"], **mapping["reified"]}:
        assert f"n:{label}" in created


def test_spine_is_what_the_mapping_expects():
    assert "match_id" in SPINE and "clock_s" in SPINE
