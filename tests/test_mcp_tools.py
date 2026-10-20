"""Tests for the read-only graph tools.

The contract these protect: an agent can look up facts about things already
in its evidence, and cannot do anything else. No writes, no open-ended
traversal, no way to reach beyond the four questions.
"""

from __future__ import annotations

import pytest

from chalkboard.mcp.tools import (
    GET_DERIVED_EDGES,
    GET_PLAYER,
    GET_POSSESSION,
    GET_ZONE_PRESSURE,
    TOOL_SPECS,
    GraphTools,
)


class FakeSession:
    def __init__(self, rows=None) -> None:
        self.rows = rows if rows is not None else []
        self.calls: list[tuple[str, dict]] = []

    def run(self, query: str, **params):
        self.calls.append((query, params))
        return list(self.rows)


ALL_QUERIES = (GET_PLAYER, GET_DERIVED_EDGES, GET_POSSESSION, GET_ZONE_PRESSURE)


def test_every_tool_is_read_only():
    """By construction, not by instruction. There is no write path to reach."""
    for query in ALL_QUERIES:
        upper = query.upper()
        for verb in ("CREATE", "MERGE", "DELETE", "SET ", "REMOVE", "DROP"):
            assert verb not in upper, f"{verb} found in a tool query"


def test_every_tool_is_parameterised():
    """String interpolation into Cypher is how an agent escapes its sandbox."""
    for query in ALL_QUERIES:
        assert "$" in query
        assert "format(" not in query and "%s" not in query


def test_tool_specs_match_the_implementation():
    tools = GraphTools(FakeSession())
    for spec in TOOL_SPECS:
        assert hasattr(tools, spec["name"]), spec["name"]
        assert spec["description"].strip()


def test_get_player_scopes_to_the_match():
    """Position is a fact about a player in a match, not about the player."""
    session = FakeSession([{"name": "Mateo Okonjo", "team": "CFA", "position": "MID",
                            "shirt_number": 4, "starter": True, "team_name": "Castleford"}])
    result = GraphTools(session).get_player("CFA-04", "CBM-49861")
    assert result["name"] == "Mateo Okonjo"
    assert session.calls[0][1] == {"playerId": "CFA-04", "matchId": "CBM-49861"}


def test_get_player_returns_empty_for_an_unknown_id():
    assert GraphTools(FakeSession([])).get_player("NOPE-99", "CBM-49861") == {}


def test_derived_edges_carry_what_a_model_could_not_know():
    """'Fourth time tonight' and 'seven of nine across these fixtures' both
    come from the aggregate layer, and neither is inferable from prose."""
    session = FakeSession([{
        "links": [{"with_player": "Luka Novak", "passes": 23, "progressive": 9,
                   "mean_hold_s": 1.4}],
        "received_share": 0.18,
        "dominates": [{"over": "Tomas Weber", "wins": 7, "losses": 2, "win_rate": 0.778}],
        "beaten_by": [],
    }])
    result = GraphTools(session).get_derived_edges("CFA-04", "CBM-49861")
    assert result["received_share"] == 0.18
    assert result["dominates"][0]["wins"] == 7


def test_possession_returns_the_chain_in_order():
    session = FakeSession([{"chain": [
        {"sequence": 10, "kind": "PASSED_TO", "actor": "A", "target": "B"},
        {"sequence": 11, "kind": "CARRIED_INTO", "actor": "B", "target": "Z5L"},
    ]}])
    chain = GraphTools(session).get_possession("228f546f")
    assert [c["sequence"] for c in chain] == [10, 11]


def test_possession_is_bounded():
    """A passage of play, not the whole match."""
    assert "[..25]" in GET_POSSESSION


def test_zone_pressure_is_ranked_and_capped():
    assert "ORDER BY p.turnovers DESC" in GET_ZONE_PRESSURE
    assert "LIMIT" in GET_ZONE_PRESSURE


def test_zone_pressure_returns_rows():
    session = FakeSession([
        {"zone_id": "Z5L", "zone": "left final third", "half_space": True,
         "turnovers": 6, "mean_pressure": 0.71},
    ])
    rows = GraphTools(session).get_zone_pressure("NGU", "CBM-49861")
    assert rows[0]["turnovers"] == 6
