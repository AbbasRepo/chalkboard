"""Persisting insights, and reading them back.

An Insight node is the system's own record of something it said, wired to
the actions that justified it. Two reasons it exists.

**Provenance.** At full time you can ask the graph: show me everything the
system claimed tonight, and the facts behind each claim. That turns "every
statement traces to the graph" from an assertion into a query.

**Cost.** Writing the sentence is the only expensive step in the pipeline -
seventeen seconds and real money per call through the Copilot CLI, which is
a coding agent rather than a text generator. Persisting the sentence means
a replay reads it instead of writing it again. Pre-compute once, demo as
often as you like, free and instant.

That makes the Explainer's lookup a three-tier affair: a stored sentence
first, a live model call if there is none, and the graph-only summary if
that fails or is switched off. Each tier is correct; they differ only in
how fluent and how expensive they are.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

log = logging.getLogger(__name__)

#: A stored insight matches a moment if it is within this many match seconds.
#: The gate ticks every three, and a replay may land on a slightly different
#: tick than the pre-compute run did.
MATCH_TOLERANCE_S = 4.0

WRITE_INSIGHT = """
MERGE (i:Insight {insight_id: $insightId})
SET i.match_id  = $matchId,
    i.clock_s   = $clockS,
    i.score     = $score,
    i.sentence  = $sentence,
    i.source    = $source
WITH i
MATCH (m:Match {match_id: $matchId})
MERGE (i)-[:IN_MATCH]->(m)
"""

LINK_EVIDENCE = """
MATCH (i:Insight {insight_id: $insightId})
MATCH (a)-[r]->(b)
WHERE r.event_id IN $eventIds
MERGE (i)-[:EVIDENCED_BY {event_id: r.event_id}]->(a)
"""

LINK_SHOT_EVIDENCE = """
MATCH (i:Insight {insight_id: $insightId})
MATCH (s:Shot) WHERE s.event_id IN $eventIds
MERGE (i)-[:EVIDENCED_BY]->(s)
"""

LINK_SUBJECTS = """
MATCH (i:Insight {insight_id: $insightId})
WITH i
UNWIND $zones AS zoneId
MATCH (z:Zone {zone_id: zoneId})
MERGE (i)-[:ABOUT_ZONE]->(z)
"""

FIND_INSIGHT = """
MATCH (i:Insight)
WHERE i.match_id = $matchId
  AND abs(i.clock_s - $clockS) <= $tolerance
  AND i.source = 'model'
RETURN i.insight_id AS insight_id, i.sentence AS sentence,
       i.clock_s AS clock_s, i.score AS score
ORDER BY abs(i.clock_s - $clockS) LIMIT 1
"""

LIST_INSIGHTS = """
MATCH (i:Insight {match_id: $matchId})
OPTIONAL MATCH (i)-[:EVIDENCED_BY]->(e)
RETURN i.clock_s AS clock_s, i.sentence AS sentence, i.source AS source,
       count(e) AS evidence_count
ORDER BY i.clock_s
"""


class Session(Protocol):
    def run(self, query: str, **params: Any) -> Any: ...


class InsightStore:
    """Reads and writes Insight nodes. The Explainer's cache, and the audit
    trail a judge can query."""

    def __init__(self, session: Session, tolerance_s: float = MATCH_TOLERANCE_S) -> None:
        self.session = session
        self.tolerance_s = tolerance_s

    def save(self, insight, zones: list[str] | None = None) -> None:
        """Persist one insight and wire it to its evidence.

        Only model-written sentences are worth storing. A graph summary is
        regenerated for free on every replay, so caching it would add rows
        without adding anything.
        """
        if insight.source != "model":
            return

        self.session.run(
            WRITE_INSIGHT,
            insightId=insight.insight_id,
            matchId=insight.match_id,
            clockS=insight.clock_s,
            score=insight.score,
            sentence=insight.sentence,
            source=insight.source,
        )
        if insight.evidence_ids:
            self.session.run(
                LINK_EVIDENCE,
                insightId=insight.insight_id,
                eventIds=insight.evidence_ids,
            )
            self.session.run(
                LINK_SHOT_EVIDENCE,
                insightId=insight.insight_id,
                eventIds=insight.evidence_ids,
            )
        if zones:
            self.session.run(
                LINK_SUBJECTS, insightId=insight.insight_id, zones=zones
            )

    def find(self, match_id: str, clock_s: float) -> dict[str, Any] | None:
        """A previously written sentence for roughly this moment, if any."""
        try:
            rows = list(
                self.session.run(
                    FIND_INSIGHT,
                    matchId=match_id,
                    clockS=clock_s,
                    tolerance=self.tolerance_s,
                )
            )
        except Exception as error:  # noqa: BLE001
            # A cache miss and a cache failure are the same thing to the
            # caller: write the sentence instead of reading it.
            log.warning("insight lookup failed: %s", error)
            return None
        return dict(rows[0]) if rows else None

    def list_for(self, match_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.session.run(LIST_INSIGHTS, matchId=match_id)]
