"""Tests for the derived-edge layer.

Offline, like everything else: a fake session records the Cypher it is
handed. These tests check the *shape* of what gets run - scoping, parameters
and the statements' own guarantees - because that is what can be verified
without a database.
"""

from __future__ import annotations

import pytest

from chalkboard.graph.derive import (
    DEFAULT_INTERVAL_S,
    Deriver,
    load_statements,
    match_ids,
)


class FakeSession:
    def __init__(self, rows=None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._rows = rows or []

    def run(self, query: str, **params):
        self.calls.append((query, params))
        return self._rows


@pytest.fixture(scope="module")
def statements():
    return load_statements()


def test_all_four_derived_edges_are_defined(statements):
    assert [name for name, _ in statements] == [
        "LINKS_WITH",
        "CIRCULATES_THROUGH",
        "PRESSES_IN",
        "NEMESIS_OF",
    ]


def test_statements_are_split_cleanly(statements):
    for name, body in statements:
        assert not body.endswith(";"), f"{name} kept its terminator"
        assert "--name:" not in body, f"{name} swallowed the next marker"
        assert "MERGE" in body, f"{name} writes nothing"


def test_per_match_edges_are_scoped_to_one_match(statements):
    """A passing network is a fact about one game.

    Without the scope, three loaded matches would be averaged into a single
    meaningless network.
    """
    bodies = dict(statements)
    for name in ("LINKS_WITH", "CIRCULATES_THROUGH", "PRESSES_IN"):
        assert "$matchId" in bodies[name], f"{name} is not scoped"
        assert "match_id: $matchId" in bodies[name], f"{name} is not keyed by match"


def test_nemesis_spans_every_match(statements):
    """A duel record is only worth remarking on once it has recurred.

    So NEMESIS_OF is deliberately the one derived edge that is not scoped to
    a single fixture.
    """
    body = dict(statements)["NEMESIS_OF"]
    assert "MERGE (winner)-[n:NEMESIS_OF]->(loser)" in body
    assert "match_id: $matchId" not in body


def test_derived_edges_are_merged_not_created(statements):
    """Recomputed every few seconds, so a CREATE would multiply them."""
    for name, body in statements:
        assert "CREATE (" not in body, f"{name} would duplicate on recompute"


def test_thresholds_keep_the_network_readable(statements):
    """Below a few passes a link is noise, and the drawn network becomes a
    hairball of one-offs."""
    bodies = dict(statements)
    assert "$minPasses" in bodies["LINKS_WITH"]
    assert "$minDuels" in bodies["NEMESIS_OF"]


def test_run_once_executes_every_statement_with_parameters(statements):
    session = FakeSession()
    ran = Deriver(session, statements).run_once("CBM-00042")

    assert ran == [name for name, _ in statements]
    assert len(session.calls) == len(statements)
    for _query, params in session.calls:
        assert params["matchId"] == "CBM-00042"
        assert params["minPasses"] >= 1
        assert params["minDuels"] >= 1
        assert params["now"] > 0


def test_watch_recomputes_repeatedly(statements):
    session = FakeSession()
    deriver = Deriver(session, statements)
    assert deriver.watch("CBM-00042", interval_s=0, iterations=3) == 3
    assert len(session.calls) == 3 * len(statements)


def test_interval_keeps_up_with_live_play():
    assert DEFAULT_INTERVAL_S <= 5.0


def test_match_ids_reads_the_graph():
    session = FakeSession(rows=[{"id": "CBM-00001"}, {"id": "CBM-00002"}])
    assert match_ids(session) == ["CBM-00001", "CBM-00002"]


def test_no_statement_uses_a_dotted_name_as_a_pattern_endpoint(statements):
    """Cypher will not accept ``row.player`` as a node in a pattern.

    The endpoint has to be a bound variable, so a map has to be unpacked with
    WITH before the MERGE. A fake session cannot parse Cypher, so this is a
    text check - but it catches the whole class of mistake, which surfaced
    only against a live database the first time.
    """
    import re

    for name, body in statements:
        dotted = re.findall(r"(?:MERGE|MATCH)\s*\(\s*\w+\.\w+", body)
        assert not dotted, f"{name} uses a dotted name as a pattern endpoint: {dotted}"
