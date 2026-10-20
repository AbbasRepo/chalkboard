"""Drives a match: clock, gate, Explainer, Personaliser.

This is the conductor. It owns the match clock and, on each tick, asks the
significance gate whether anything is worth saying. When the gate fires it
hands the moment to the Explainer, then the resulting insight to the
Personaliser, and emits the whole package.

It replays against a graph that is already loaded, advancing a simulated
match clock in real time. That is a deliberate simplification: the live
pipeline and the replay differ only in where the clock comes from, and the
replay is reliable enough to record a demo against. Nothing about the gate,
the agents or the grounding changes.

Everything it emits is a plain dictionary, so the API layer can serialise it
without knowing anything about the pipeline.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict
from typing import Any, Iterator

from ..agents.explainer import Explainer
from ..agents.personaliser import Personaliser
from ..graph.significance import TICK_S, SignificanceGate

log = logging.getLogger(__name__)

MATCH_HEADER = """
MATCH (m:Match {match_id: $matchId})
OPTIONAL MATCH (m)-[:PLAYED_AT]->(st:Stadium)
OPTIONAL MATCH (m)-[h:HOME_TEAM]->(home:Team)
OPTIONAL MATCH (m)-[a:AWAY_TEAM]->(away:Team)
RETURN m.match_id                AS match_id,
       m.matchweek               AS matchweek,
       st.name                   AS stadium,
       home.short_name           AS home,
       home.name                 AS home_name,
       h.style                   AS home_style,
       away.short_name           AS away,
       away.name                 AS away_name,
       a.style                   AS away_style,
       m.pre_game_win_prob_home  AS p_home,
       m.pre_game_draw_prob      AS p_draw,
       m.pre_game_win_prob_away  AS p_away
"""

SCORE_AT = """
MATCH (p:Player)-[:TOOK]->(s:Shot)
WHERE s.match_id = $matchId AND s.outcome = 'GOAL' AND s.clock_s <= $now
MATCH (p)-[:PLAYS_FOR]->(t:Team)
RETURN t.short_name AS team, count(s) AS goals
"""

SQUAD = """
MATCH (p:Player)-[a:PLAYED_IN]->(:Match {match_id: $matchId})
WHERE a.starter
MATCH (p)-[:PLAYS_FOR]->(t:Team)
RETURN p.player_id AS player_id, p.name AS name,
       a.position AS position, a.shirt_number AS shirt, t.short_name AS team
ORDER BY t.short_name, a.shirt_number
"""


class MatchRunner:
    def __init__(
        self,
        session,
        match_id: str,
        gate: SignificanceGate,
        explainer: Explainer,
        personaliser: Personaliser,
        speed: float = 20.0,
        tick_s: float = TICK_S,
        full_time_s: float = 5400.0,
    ) -> None:
        self.session = session
        self.match_id = match_id
        self.gate = gate
        self.explainer = explainer
        self.personaliser = personaliser
        #: Match seconds per wall-clock second. 20 plays a match in about
        #: four and a half minutes - enough room to narrate without the
        #: clock racing.
        self.speed = speed
        self.tick_s = tick_s
        self.full_time_s = full_time_s

    # ------------------------------------------------------------- context

    def header(self) -> dict[str, Any]:
        rows = list(self.session.run(MATCH_HEADER, matchId=self.match_id))
        return dict(rows[0]) if rows else {"match_id": self.match_id}

    def squad(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.session.run(SQUAD, matchId=self.match_id)]

    def score_at(self, now_s: float) -> dict[str, int]:
        rows = self.session.run(SCORE_AT, matchId=self.match_id, now=now_s)
        return {r["team"]: r["goals"] for r in rows}

    # ---------------------------------------------------------------- run

    def run(self, start_s: float = 0.0) -> Iterator[dict[str, Any]]:
        """Walk the match, yielding clock ticks and insights as they fire."""
        yield {"type": "header", **self.header(), "squad": self.squad()}

        now = start_s
        wall_start = time.monotonic()

        while now <= self.full_time_s:
            yield {
                "type": "clock",
                "clock_s": round(now, 1),
                "minute": int(now // 60),
                "score": self.score_at(now),
            }

            try:
                moment = self.gate.check(self.match_id, now)
            except Exception as error:  # noqa: BLE001
                # A database blip must not end the match. The UI shows the
                # last known state rather than a blank screen.
                log.warning("gate failed at %.0fs: %s", now, error)
                moment = None

            if moment:
                yield self._insight_payload(moment)

            now += self.tick_s
            if self.speed > 0:
                target = wall_start + (now - start_s) / self.speed
                delay = target - time.monotonic()
                if delay > 0:
                    time.sleep(min(delay, 1.0))

        yield {"type": "full_time", "score": self.score_at(self.full_time_s)}

    def _insight_payload(self, moment) -> dict[str, Any]:
        insight = self.explainer.explain(moment)
        personalised = self.personaliser.render(insight)
        return {
            "type": "insight",
            "clock_s": round(moment.clock_s, 1),
            "minute": int(moment.clock_s // 60),
            "score": round(moment.score, 2),
            "insight": asdict(insight),
            "renderings": [asdict(r) for r in personalised.renderings],
            # The evidence goes to the browser so the subgraph can be drawn
            # beside the sentence. Showing the facts next to the claim is
            # the whole groundedness argument, made visible.
            "evidence": moment.evidence,
        }
