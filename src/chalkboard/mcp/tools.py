"""Read-only graph tools, as plain Python.

These are the queries behind the MCP server. They live apart from the server
binding for two reasons: they can be tested with a fake session and no MCP
package installed, and the Explainer can call them directly when running
in-process rather than paying for a protocol round trip.

Every tool is read-only. An agent cannot write to the graph, by construction
rather than by instruction - there is no write path to reach.

Why tools at all, rather than handing the agent the graph
---------------------------------------------------------
The significance gate already hands the Explainer a moment and its evidence.
But evidence is terse: player ids, zone codes, clock values. To say anything
worth reading the agent sometimes needs one more fact - what is this player
called, is this their fourth turnover tonight, who do they usually combine
with. These tools are that lookup, and nothing more.

The agent still cannot go hunting. Each tool answers one narrow question
about something already in the evidence.
"""

from __future__ import annotations

from typing import Any, Protocol


class Session(Protocol):
    def run(self, query: str, **params: Any) -> Any: ...


GET_PLAYER = """
MATCH (p:Player {player_id: $playerId})
OPTIONAL MATCH (p)-[:PLAYS_FOR]->(t:Team)
OPTIONAL MATCH (p)-[a:PLAYED_IN]->(:Match {match_id: $matchId})
RETURN p.name           AS name,
       t.short_name     AS team,
       t.name           AS team_name,
       a.position       AS position,
       a.shirt_number   AS shirt_number,
       a.starter        AS starter
"""

GET_DERIVED_EDGES = """
MATCH (p:Player {player_id: $playerId})
OPTIONAL MATCH (p)-[l:LINKS_WITH {match_id: $matchId}]->(mate:Player)
WITH p, collect({
  with_player: mate.name, passes: l.passes,
  progressive: l.progressive, mean_hold_s: l.mean_hold_s
})[..5] AS links
OPTIONAL MATCH (t:Team)-[c:CIRCULATES_THROUGH {match_id: $matchId}]->(p)
WITH p, links, c.share AS received_share
OPTIONAL MATCH (p)-[n:NEMESIS_OF]->(victim:Player)
WITH p, links, received_share, collect({
  over: victim.name, wins: n.wins, losses: n.losses, win_rate: n.win_rate
})[..3] AS dominates
OPTIONAL MATCH (beater:Player)-[m:NEMESIS_OF]->(p)
RETURN links,
       received_share,
       [x IN dominates WHERE x.over IS NOT NULL] AS dominates,
       collect({
         by: beater.name, wins: m.wins, losses: m.losses, win_rate: m.win_rate
       })[..3] AS beaten_by
"""

GET_POSSESSION = """
MATCH (a:Player)-[r]->(b)
WHERE r.possession_id = $possessionId
  AND type(r) IN ['PASSED_TO', 'CARRIED_INTO', 'DISPOSSESSED', 'PRESSED_IN']
WITH r, a, b ORDER BY r.sequence
RETURN collect({
  sequence: r.sequence,
  kind:     type(r),
  clock_s:  r.clock_s,
  actor:    a.name,
  target:   b.name,
  zone:     r.start_zone,
  outcome:  r.outcome,
  pressure: r.pressure,
  hold_s:   r.hold_time_s
})[..25] AS chain
"""

GET_ZONE_PRESSURE = """
MATCH (t:Team {team_id: $teamId})-[p:PRESSES_IN {match_id: $matchId}]->(z:Zone)
RETURN z.zone_id      AS zone_id,
       z.name         AS zone,
       z.half_space   AS half_space,
       p.turnovers    AS turnovers,
       p.mean_pressure AS mean_pressure
ORDER BY p.turnovers DESC LIMIT 6
"""


class GraphTools:
    """The four questions an agent is allowed to ask the graph."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_player(self, player_id: str, match_id: str) -> dict[str, Any]:
        """Who is this, and what were they doing in this match?"""
        rows = list(self.session.run(GET_PLAYER, playerId=player_id, matchId=match_id))
        return dict(rows[0]) if rows else {}

    def get_derived_edges(self, player_id: str, match_id: str) -> dict[str, Any]:
        """Their partnerships, their share of the ball, their matchups.

        This is where the aggregate layer earns its keep: "fourth time
        tonight" and "seven of nine across these fixtures" both come from
        here, and neither is something a model could know.
        """
        rows = list(
            self.session.run(GET_DERIVED_EDGES, playerId=player_id, matchId=match_id)
        )
        return dict(rows[0]) if rows else {}

    def get_possession(self, possession_id: str) -> list[dict[str, Any]]:
        """The chain of actions in one passage of play, in order.

        Lets the Explainer say how a move was built rather than only how it
        ended.
        """
        rows = list(self.session.run(GET_POSSESSION, possessionId=possession_id))
        return [dict(x) for x in (rows[0]["chain"] if rows else [])]

    def get_zone_pressure(self, team_id: str, match_id: str) -> list[dict[str, Any]]:
        """Where this side is winning the ball back."""
        rows = list(
            self.session.run(GET_ZONE_PRESSURE, teamId=team_id, matchId=match_id)
        )
        return [dict(r) for r in rows]


#: Advertised to the agent. Descriptions are deliberately narrow: each one
#: answers a question about something already in the evidence, so the agent
#: is never invited to go hunting.
TOOL_SPECS = [
    {
        "name": "get_player",
        "description": "Name, team, position and shirt number for one player in one match.",
        "params": {"player_id": "string", "match_id": "string"},
    },
    {
        "name": "get_derived_edges",
        "description": (
            "A player's passing partnerships, share of their team's received "
            "passes, and duel record against specific opponents."
        ),
        "params": {"player_id": "string", "match_id": "string"},
    },
    {
        "name": "get_possession",
        "description": "The ordered chain of actions in one passage of play.",
        "params": {"possession_id": "string"},
    },
    {
        "name": "get_zone_pressure",
        "description": "Where a team is winning the ball back, by pitch zone.",
        "params": {"team_id": "string", "match_id": "string"},
    },
]
