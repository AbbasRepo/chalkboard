"""MCP server exposing the read-only graph tools.

A thin binding. All the logic is in ``tools.py``, which is testable without
the MCP package installed; this file only advertises those four functions
over the Model Context Protocol and routes calls to them.

Run it standalone over stdio:

    python -m chalkboard.mcp.server

Why MCP rather than in-process calls
------------------------------------
The Explainer could import ``GraphTools`` directly, and in the running demo
it does. MCP exists here because it makes the boundary explicit and
inspectable: the agent's access to the graph is a declared, read-only
surface of four named tools, not an open database handle. That is worth more
to a reader of this repo than the round trip costs.

It is also a named hero technology for the hackathon category, which is a
smaller reason but an honest one.
"""

from __future__ import annotations

import os
import sys

from .tools import TOOL_SPECS, GraphTools


def build_tools() -> GraphTools:
    from dotenv import load_dotenv
    from neo4j import GraphDatabase

    load_dotenv()
    uri, password = os.environ.get("NEO4J_URI"), os.environ.get("NEO4J_PASSWORD")
    if not uri or not password:
        raise SystemExit("NEO4J_URI and NEO4J_PASSWORD must be set in .env")

    driver = GraphDatabase.driver(
        uri, auth=(os.environ.get("NEO4J_USER", "neo4j"), password)
    )
    return GraphTools(driver.session())


def main() -> int:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print(
            "The mcp package is required to run the server.\n"
            "  pip install mcp\n"
            "The tools themselves are importable without it - see tools.py.",
            file=sys.stderr,
        )
        return 2

    tools = build_tools()
    server = FastMCP("chalkboard-graph")

    @server.tool(description=TOOL_SPECS[0]["description"])
    def get_player(player_id: str, match_id: str) -> dict:
        return tools.get_player(player_id, match_id)

    @server.tool(description=TOOL_SPECS[1]["description"])
    def get_derived_edges(player_id: str, match_id: str) -> dict:
        return tools.get_derived_edges(player_id, match_id)

    @server.tool(description=TOOL_SPECS[2]["description"])
    def get_possession(possession_id: str) -> list:
        return tools.get_possession(possession_id)

    @server.tool(description=TOOL_SPECS[3]["description"])
    def get_zone_pressure(team_id: str, match_id: str) -> list:
        return tools.get_zone_pressure(team_id, match_id)

    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
