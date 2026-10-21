"""Tests for the MCP client wrapper.

Two things matter here. The client must present the same four methods as the
direct tools, so the Explainer neither knows nor cares which it holds. And
nothing about the protocol may be fatal - if the server will not start, the
caller falls back rather than the demo failing.
"""

from __future__ import annotations

import inspect

import pytest

from chalkboard.mcp.client import MCPGraphTools, _unwrap, connect_tools
from chalkboard.mcp.tools import TOOL_SPECS, GraphTools


class FakeSession:
    def run(self, query, **params):
        return []


# ------------------------------------------------------- interface parity

def test_the_client_offers_the_same_four_tools_as_the_direct_version():
    """The Explainer holds one or the other and must not be able to tell."""
    for spec in TOOL_SPECS:
        name = spec["name"]
        assert hasattr(MCPGraphTools, name), name
        assert hasattr(GraphTools, name), name


def test_the_signatures_match():
    for spec in TOOL_SPECS:
        name = spec["name"]
        mcp_args = list(inspect.signature(getattr(MCPGraphTools, name)).parameters)
        direct_args = list(inspect.signature(getattr(GraphTools, name)).parameters)
        assert mcp_args == direct_args, name


# ------------------------------------------------------------- never fatal

def test_a_client_that_never_connected_returns_empties_not_errors():
    client = MCPGraphTools()
    assert client.get_player("CFA-04", "CBM-49861") == {}
    assert client.get_derived_edges("CFA-04", "CBM-49861") == {}
    assert client.get_possession("228f") == []
    assert client.get_zone_pressure("NGU", "CBM-49861") == []


def test_falling_back_gives_direct_tools():
    tools, transport = connect_tools(FakeSession(), prefer_mcp=False)
    assert isinstance(tools, GraphTools)
    assert transport == "direct"


def test_a_failed_server_falls_back_rather_than_raising(monkeypatch):
    """A protocol boundary that takes the demo down is a poor trade for
    architectural tidiness."""
    monkeypatch.setattr(MCPGraphTools, "start", lambda self: False)
    tools, transport = connect_tools(FakeSession(), prefer_mcp=True)
    assert isinstance(tools, GraphTools)
    assert transport == "direct"


def test_closing_a_client_that_never_started_is_safe():
    MCPGraphTools().close()


# --------------------------------------------------------- result unwrapping

def test_unwraps_structured_content():
    class Result:
        structuredContent = {"result": {"name": "Tomas Weber"}}

    assert _unwrap(Result()) == {"name": "Tomas Weber"}


def test_unwraps_a_json_text_block():
    """Which shape the SDK returns has moved between versions, so both are
    handled rather than pinning to one."""

    class Block:
        text = '{"name": "Idris Osei"}'

    class Result:
        structuredContent = None
        content = [Block()]

    assert _unwrap(Result()) == {"name": "Idris Osei"}


def test_unwraps_plain_text():
    class Block:
        text = "left opposition half"

    class Result:
        structuredContent = None
        content = [Block()]

    assert _unwrap(Result()) == "left opposition half"


def test_unwrapping_nothing_is_none():
    class Result:
        structuredContent = None
        content = []

    assert _unwrap(Result()) is None
