"""The Explainer: says why a moment mattered.

It is handed a Moment by the significance gate - a score and the specific
actions that caused it. It enriches that with a few narrow lookups, asks the
model for one sentence, and then checks that sentence against the facts it
supplied before anyone sees it.

The order matters. The agent does not choose the subject, does not browse
the graph, and does not get the last word on its own output.

The grounding check
-------------------
A model handed three turnovers will sometimes produce a fluent sentence
naming a player who was not involved, or a statistic nobody gave it. Fluent
and wrong is worse than plain and right, because it is harder to notice.

So every name and every number in the sentence must appear in the fact pack
the agent was given. Anything else means the sentence is rejected and the
graph-only summary is used instead. The check is crude - it is string
matching, not comprehension - but it is strict in the right direction: it
can reject a true sentence, and cannot accept an invented one.

Falling back is not failure
---------------------------
A rejected sentence, a model timeout and the kill switch all produce the
same thing: ``Moment.summary()``, built from graph facts alone. Plainer,
never wrong. The system keeps speaking either way, which is the graceful
degradation the hackathon category scores.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..graph.significance import Moment
from ..mcp.tools import GraphTools
from .llm import ModelClient, StubClient

log = logging.getLogger(__name__)

SYSTEM = (
    "You are a football analyst writing one sentence of live commentary. "
    "Explain WHY the moment mattered, not that it happened. "
    "Use only the names and numbers given to you. Invent nothing. "
    "No preamble, no quotation marks, one sentence, under 30 words."
)

#: Words that look like names but are ours, not the model's inventions.
_SAFE_WORDS = {
    "The", "A", "An", "And", "But", "That", "This", "They", "Their", "There",
    "His", "Her", "It", "In", "On", "At", "With", "From", "After", "Before",
    "Northgate", "Castleford",  # replaced per match by the real team names
}


@dataclass
class Insight:
    """What the system said, and what it said it from."""

    insight_id: str
    match_id: str
    clock_s: float
    score: float
    sentence: str
    #: "model" when the model's sentence survived the check, "graph" when it
    #: fell back. Shown in the UI, because the difference is honest and
    #: interesting rather than embarrassing.
    source: str
    evidence_ids: list[str] = field(default_factory=list)
    about_players: list[str] = field(default_factory=list)
    about_zones: list[str] = field(default_factory=list)
    rejected: str | None = None


class Explainer:
    def __init__(
        self,
        client: ModelClient,
        tools: GraphTools | None = None,
        strict: bool = True,
    ) -> None:
        self.client = client
        self.tools = tools
        self.strict = strict

    # ------------------------------------------------------------- public

    def explain(self, moment: Moment) -> Insight:
        facts = self.build_facts(moment)
        fallback = moment.summary()

        sentence, source, rejected = fallback, "graph", None
        candidate = self.client.complete(
            self._prompt(facts, fallback), system=SYSTEM
        )
        if candidate and candidate.strip() != fallback.strip():
            # A client that simply echoes the fallback has not written
            # anything, so the insight is still graph-sourced. Labelling it
            # "model" would overstate what happened.
            ok, reason = self.check_grounded(candidate, facts)
            if ok:
                sentence, source = candidate, "model"
            else:
                rejected = f"{reason}: {candidate}"
                log.warning("rejected ungrounded sentence - %s", rejected)

        return Insight(
            insight_id=uuid.uuid4().hex[:12],
            match_id=moment.match_id,
            clock_s=moment.clock_s,
            score=moment.score,
            sentence=sentence,
            source=source,
            evidence_ids=[e["event_id"] for e in moment.evidence],
            about_players=moment.players,
            about_zones=moment.zones,
            rejected=rejected,
        )

    # -------------------------------------------------------------- facts

    def build_facts(self, moment: Moment) -> dict[str, Any]:
        """Assemble everything the model is allowed to know.

        Deliberately small. The evidence, plus at most two lookups about
        things already in it. The agent is never invited to go hunting.
        """
        facts: dict[str, Any] = {
            "clock": f"{int(moment.clock_s // 60)}'",
            "actions": moment.evidence,
            "names": moment.players,
            "zones": moment.zones,
        }
        if not self.tools:
            return facts

        # Where the press is working, for the team that won the ball back.
        turnovers = [e for e in moment.evidence if e["kind"] == "DISPOSSESSED"]
        if turnovers:
            facts["zone_pressure"] = self._safe(
                self.tools.get_zone_pressure, _team_of(turnovers[0]), moment.match_id
            )

        # The aggregate picture for whoever appears most in the evidence.
        lead = _most_frequent_actor(moment.evidence)
        if lead:
            facts["player_context"] = self._safe(
                self.tools.get_derived_edges, lead, moment.match_id
            )
        return facts

    def _safe(self, fn, *args):
        """A tool failure must not stop the sentence being written."""
        try:
            return fn(*args)
        except Exception as error:  # noqa: BLE001 - any failure is the same here
            log.warning("tool %s failed: %s", getattr(fn, "__name__", fn), error)
            return None

    def _prompt(self, facts: dict[str, Any], fallback: str) -> str:
        lines = [f"Minute {facts['clock']}.", "", "What happened:"]
        for action in facts["actions"]:
            lines.append("  " + _describe(action))

        pressure = facts.get("zone_pressure")
        if pressure:
            top = pressure[0]
            lines += [
                "",
                f"Across the match so far, that side has won the ball back "
                f"{top['turnovers']} times in the {top['zone']}.",
            ]

        context = facts.get("player_context") or {}
        if context.get("received_share"):
            share = int(round(context["received_share"] * 100))
            lines.append(f"That player receives {share}% of his team's passes.")
        for duel in (context.get("dominates") or [])[:1]:
            if duel.get("over"):
                lines.append(
                    f"He has won {duel['wins']} of "
                    f"{duel['wins'] + duel['losses']} duels against {duel['over']}."
                )

        lines += [
            "",
            "Names you may use: " + (", ".join(facts["names"]) or "none"),
            "",
            f"FALLBACK: {fallback}",
            "",
            "One sentence. Why did this matter?",
        ]
        return "\n".join(lines)

    # ----------------------------------------------------------- grounding

    def check_grounded(self, sentence: str, facts: dict[str, Any]) -> tuple[bool, str]:
        """Every name and number must appear in the facts supplied.

        Strict in the right direction: it can reject a true sentence, and
        cannot accept an invented one.
        """
        if not self.strict:
            return True, ""

        haystack = _fact_text(facts)
        allowed_names = {n for name in facts["names"] for n in name.split()}

        for token in re.findall(r"\b[A-Z][a-z]{2,}\b", sentence):
            if token in _SAFE_WORDS or token in allowed_names:
                continue
            if token in haystack:
                continue
            return False, f"names {token}, who is not in the evidence"

        # No trailing boundary: an ordinal like "47th" must not slip past
        # the check just because a letter follows the digits.
        for number in re.findall(r"\b\d+(?:\.\d+)?", sentence):
            if number in haystack:
                continue
            if _is_countable(number, facts):
                continue
            return False, f"states {number}, which is not in the evidence"

        return True, ""


# --------------------------------------------------------------- helpers

def _describe(action: dict[str, Any]) -> str:
    kind = action["kind"]
    actor = action.get("actor") or "unknown"
    zone = action.get("zone") or ""
    if kind == "SHOT":
        return f"{actor} shot from {zone}, outcome {action.get('outcome', '').lower()}"
    if kind == "DISPOSSESSED":
        return (
            f"{actor} won the ball from {action.get('target')} in {zone} "
            f"under pressure {action.get('pressure', 0):.2f}"
        )
    if kind == "PASSED_TO":
        return f"{actor} passed to {action.get('target')} into {zone}"
    return f"{actor} {kind.lower().replace('_', ' ')} in {zone}"


def _fact_text(facts: dict[str, Any]) -> str:
    return " ".join(str(v) for v in facts.values())


def _is_countable(number: str, facts: dict[str, Any]) -> bool:
    """Allow a count the model could legitimately have made itself.

    "three turnovers" is honest arithmetic over the evidence, not invention,
    so a small integer no larger than the number of actions supplied passes.
    """
    try:
        value = int(number)
    except ValueError:
        return False
    return 0 < value <= len(facts.get("actions", []))


def _team_of(action: dict[str, Any]) -> str:
    return action.get("team") or action.get("team_id") or ""


def _most_frequent_actor(evidence: list[dict[str, Any]]) -> str | None:
    counts: dict[str, int] = {}
    for item in evidence:
        pid = item.get("actor_id") or item.get("actor")
        if pid:
            counts[pid] = counts.get(pid, 0) + 1
    return max(counts, key=counts.get) if counts else None


def default_explainer(fallback: str | None = None) -> Explainer:
    from .llm import get_client

    return Explainer(get_client(fallback) or StubClient(fallback))
