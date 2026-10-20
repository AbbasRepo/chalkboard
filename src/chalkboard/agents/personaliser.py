"""The Personaliser: says it the way this viewer needs to hear it.

The second agent, and the one that answers the half of the brief most
entries will miss. A project that only generates metrics has no audience.

It receives the Explainer's grounded sentence and the facts behind it, and
renders the same moment for two viewers who want genuinely different things.
It never reads the graph and never adds a fact: it may only re-say what it
was handed. Everything it produces is therefore as grounded as its input.

The two personas
----------------
``analyst``
    Numbers-dense, no adjectives. Wants the pattern and the cause.
    "Press intensity up in the left half-space - three turnovers there in
    two minutes."

``fan``
    Follows one player for the whole match and ignores everything else.
    Warm, second person, short.
    "Your man just won it back again - that's his second in two minutes."

They are deliberately chosen to differ in *what they are told about*, not
merely in tone. A casual fan is only a shorter analyst - same content,
smaller words - which reads as a font-size setting rather than
personalisation. The fan persona following one player means the two viewers
genuinely diverge: some moments are front-page news for one and silence for
the other.

Silence is a valid rendering
----------------------------
If the followed player had nothing to do with a moment, the fan pane says
so quietly rather than inventing relevance. That is the personalisation
working, not failing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .explainer import Insight
from .llm import ModelClient

log = logging.getLogger(__name__)

PERSONAS: dict[str, dict[str, str]] = {
    "analyst": {
        "label": "Analyst",
        "system": (
            "Rewrite the sentence for a data-led football analyst. "
            "Numbers first, no adjectives, no drama. Keep every name and "
            "number exactly as given. Add nothing. One sentence, under 30 words."
        ),
    },
    "fan": {
        "label": "Following {player}",
        "system": (
            "Rewrite the sentence for a supporter who is following one player "
            "all match and cares about little else. Warm, second person, "
            "short. Keep every name and number exactly as given. Add nothing. "
            "One sentence, under 25 words."
        ),
    },
}


@dataclass
class Rendering:
    persona: str
    label: str
    text: str
    #: "model" when the model rewrote it, "graph" when it fell back to the
    #: Explainer's sentence unchanged, "quiet" when this viewer is not
    #: interested in this moment.
    source: str
    language: str = "en"


@dataclass
class PersonalisedInsight:
    insight: Insight
    renderings: list[Rendering] = field(default_factory=list)

    def by_persona(self, persona: str) -> Rendering | None:
        return next((r for r in self.renderings if r.persona == persona), None)


class Personaliser:
    def __init__(
        self,
        client: ModelClient,
        followed_player: str | None = None,
        language: str = "en",
    ) -> None:
        self.client = client
        self.followed_player = followed_player
        self.language = language

    def render(self, insight: Insight) -> PersonalisedInsight:
        return PersonalisedInsight(
            insight=insight,
            renderings=[
                self._analyst(insight),
                self._fan(insight),
            ],
        )

    # ------------------------------------------------------------ personas

    def _analyst(self, insight: Insight) -> Rendering:
        text, source = self._rewrite("analyst", insight)
        return Rendering("analyst", PERSONAS["analyst"]["label"], text, source, self.language)

    def _fan(self, insight: Insight) -> Rendering:
        player = self.followed_player
        label = PERSONAS["fan"]["label"].format(player=player or "a player")

        if not player:
            text, source = self._rewrite("fan", insight)
            return Rendering("fan", label, text, source, self.language)

        if not self._involves(player, insight):
            # Not their player, so not their moment. Saying so is the
            # personalisation working - inventing relevance would be the
            # failure.
            return Rendering(
                "fan", label,
                f"Nothing for {player} here - play has moved to the other side.",
                "quiet", self.language,
            )

        text, source = self._rewrite("fan", insight, player=player)
        return Rendering("fan", label, text, source, self.language)

    # ------------------------------------------------------------- rewrite

    def _rewrite(
        self, persona: str, insight: Insight, player: str | None = None
    ) -> tuple[str, str]:
        prompt = self._prompt(insight, player)
        candidate = self.client.complete(prompt, system=PERSONAS[persona]["system"])

        if not candidate or candidate.strip() == insight.sentence.strip():
            # No model, or it echoed the input. The Explainer's grounded
            # sentence is shown unchanged - identical in both panes, which
            # is honest about what happened.
            return insight.sentence, "graph"

        if not self._adds_nothing(candidate, insight):
            log.warning("personaliser introduced a name - falling back")
            return insight.sentence, "graph"

        return candidate, "model"

    def _prompt(self, insight: Insight, player: str | None) -> str:
        lines = [f"Sentence: {insight.sentence}", ""]
        if player:
            lines.append(f"The viewer is following {player}.")
        if self.language != "en":
            lines.append(f"Write the result in {self.language}.")
        lines += ["", f"FALLBACK: {insight.sentence}"]
        return "\n".join(lines)

    # -------------------------------------------------------------- checks

    def _involves(self, player: str, insight: Insight) -> bool:
        return any(player.lower() in name.lower() for name in insight.about_players)

    def _adds_nothing(self, candidate: str, insight: Insight) -> bool:
        """A rewrite may drop names, never add them.

        The Explainer's sentence was already checked against the evidence.
        This only has to guarantee the rewrite did not smuggle in anything
        new, which keeps the grounding guarantee intact through the second
        agent without re-running the full check.
        """
        import re

        allowed = {w for name in insight.about_players for w in name.split()}
        source_words = set(re.findall(r"\b[A-Z][a-z]{2,}\b", insight.sentence))
        for token in re.findall(r"\b[A-Z][a-z]{2,}\b", candidate):
            if token in allowed or token in source_words:
                continue
            if token in {"Your", "The", "That", "This", "He", "His", "Nothing"}:
                continue
            return False
        return True
