"""Tests for the two agents.

The guarantee these protect: nothing reaches a viewer that is not supported
by the evidence the gate supplied. Fluent and wrong is worse than plain and
right, because it is harder to notice.
"""

from __future__ import annotations

import pytest

from chalkboard.agents.explainer import Explainer, Insight
from chalkboard.agents.llm import StubClient
from chalkboard.agents.personaliser import Personaliser
from chalkboard.graph.significance import Moment


class ScriptedClient:
    """Returns a fixed sentence, whatever it is asked."""

    name = "scripted"

    def __init__(self, sentence: str | None) -> None:
        self.sentence = sentence
        self.prompts: list[str] = []

    def complete(self, prompt: str, system: str | None = None) -> str | None:
        self.prompts.append(prompt)
        return self.sentence


def turnover(event_id, actor="Tomas Weber", target="Idris Varela", zone="Z4L"):
    return {
        "event_id": event_id, "kind": "DISPOSSESSED", "clock_s": 1440.0,
        "actor": actor, "target": target, "zone": zone,
        "pressure": 0.81, "outcome": "SUCCESS", "forced": 0.8, "breaking": 0.0,
    }


@pytest.fixture
def moment():
    return Moment(
        match_id="CBM-49861", clock_s=1440.0, score=1.6,
        evidence=[turnover("a"), turnover("b", target="Luka Novak")],
    )


# ------------------------------------------------------- grounding check

def test_accepts_a_sentence_built_from_the_evidence(moment):
    explainer = Explainer(ScriptedClient("Weber won it back twice in the left channel."))
    insight = explainer.explain(moment)
    assert insight.source == "model"
    assert insight.rejected is None


def test_rejects_a_player_who_was_not_involved(moment):
    """The failure mode that matters: fluent, confident, and about someone
    who was nowhere near the ball."""
    explainer = Explainer(ScriptedClient("Marchetti broke the press superbly."))
    insight = explainer.explain(moment)
    assert insight.source == "graph"
    assert "Marchetti" in insight.rejected
    assert "Marchetti" not in insight.sentence


def test_rejects_an_invented_statistic(moment):
    explainer = Explainer(ScriptedClient("That is Weber's 47th turnover this season."))
    insight = explainer.explain(moment)
    assert insight.source == "graph"
    assert "47" in insight.rejected


def test_an_ordinal_does_not_slip_past_the_number_check(moment):
    """"47th" must not pass merely because a letter follows the digits."""
    explainer = Explainer(ScriptedClient("His 19th recovery tonight."))
    assert explainer.explain(moment).source == "graph"


def test_allows_counting_the_evidence_itself(moment):
    """"two turnovers" is arithmetic over what was supplied, not invention."""
    explainer = Explainer(ScriptedClient("Weber has 2 recoveries in that zone."))
    assert explainer.explain(moment).source == "model"


def test_falls_back_when_there_is_no_model(moment):
    insight = Explainer(ScriptedClient(None)).explain(moment)
    assert insight.source == "graph"
    assert insight.sentence == moment.summary()


def test_echoing_the_fallback_is_not_model_output(moment):
    """A client that returns the fallback has written nothing. Labelling it
    model output would overstate what happened."""
    explainer = Explainer(StubClient())
    assert explainer.explain(moment).source == "graph"


def test_insight_cites_its_evidence(moment):
    insight = Explainer(ScriptedClient("Weber won it twice.")).explain(moment)
    assert insight.evidence_ids == ["a", "b"]
    assert "Tomas Weber" in insight.about_players
    assert insight.about_zones == ["Z4L"]


def test_a_tool_failure_does_not_stop_the_sentence(moment):
    class BrokenTools:
        def get_zone_pressure(self, *_a):
            raise RuntimeError("neo4j is down")

        def get_derived_edges(self, *_a):
            raise RuntimeError("neo4j is down")

    explainer = Explainer(ScriptedClient("Weber won it twice."), tools=BrokenTools())
    assert explainer.explain(moment).source == "model"


# ---------------------------------------------------------- personaliser

@pytest.fixture
def insight():
    return Insight(
        "i1", "CBM-49861", 1440.0, 1.6,
        "Weber won it back twice in the left opposition half.", "model",
        ["a", "b"], ["Tomas Weber", "Idris Varela"], ["Z4L"],
    )


def test_both_personas_are_rendered(insight):
    result = Personaliser(StubClient(), "Tomas Weber").render(insight)
    assert {r.persona for r in result.renderings} == {"analyst", "fan"}


def test_a_moment_without_your_player_is_quiet(insight):
    """The two viewers genuinely diverge. Inventing relevance would be the
    failure; saying nothing happened is the personalisation working."""
    fan = Personaliser(StubClient(), "Luka Novak").render(insight).by_persona("fan")
    assert fan.source == "quiet"
    assert "Luka Novak" in fan.text


def test_a_moment_with_your_player_is_rendered(insight):
    fan = Personaliser(ScriptedClient("Your man won it back again."), "Tomas Weber")
    rendering = fan.render(insight).by_persona("fan")
    assert rendering.source == "model"
    assert rendering.text == "Your man won it back again."


def test_a_rewrite_may_not_introduce_a_name(insight):
    """The Explainer's sentence was checked. The rewrite must not smuggle
    anything new past that check."""
    p = Personaliser(ScriptedClient("Weber and Lindqvist combined to win it."))
    analyst = p.render(insight).by_persona("analyst")
    assert analyst.source == "graph"
    assert "Lindqvist" not in analyst.text


def test_the_followed_player_reaches_the_prompt(insight):
    client = ScriptedClient("Your man won it back.")
    Personaliser(client, "Tomas Weber").render(insight)
    assert any("following Tomas Weber" in p for p in client.prompts)


def test_language_reaches_the_prompt(insight):
    client = ScriptedClient("Weber la recuperó dos veces.")
    Personaliser(client, "Tomas Weber", language="Spanish").render(insight)
    assert any("Spanish" in p for p in client.prompts)


def test_rejects_a_refusal(moment):
    """A model handed a mangled prompt replies asking for the details it was
    already given. Such a reply invents no names and no numbers, so the other
    checks pass it happily - and it would then be stored as an insight.
    """
    explainer = Explainer(ScriptedClient(
        "I can't explain why the moment mattered without details of the event."
    ))
    insight = explainer.explain(moment)
    assert insight.source == "graph"
    assert "refusal" in insight.rejected


def test_rejects_a_sentence_naming_nobody_from_the_evidence(moment):
    """An explanation of a moment must name someone who was in it."""
    explainer = Explainer(ScriptedClient("A fine passage of play in midfield."))
    assert explainer.explain(moment).source == "graph"


def test_still_accepts_a_proper_explanation(moment):
    explainer = Explainer(ScriptedClient("Weber won it back twice under pressure."))
    assert explainer.explain(moment).source == "model"


def test_a_capitalised_opening_word_is_not_treated_as_a_name(moment):
    """"Two early shots..." is a well-formed sentence, not a player called
    Two. Rejecting it would fire on most decent prose."""
    explainer = Explainer(ScriptedClient(
        "Two turnovers put Weber in control of that zone."
    ))
    assert explainer.explain(moment).source == "model"


def test_a_name_mid_sentence_is_still_caught(moment):
    explainer = Explainer(ScriptedClient("Weber won it, then Marchetti cleared."))
    insight = explainer.explain(moment)
    assert insight.source == "graph"
    assert "Marchetti" in insight.rejected


def test_a_rewrite_opening_with_a_capital_is_not_rejected(insight):
    """English capitalises the first word of every sentence. Treating those
    as names rejects most well-formed rewrites, and both panes then show
    identical text - hiding the personalisation entirely.
    """
    p = Personaliser(ScriptedClient("Your man won it back twice in two minutes."))
    analyst = p.render(insight).by_persona("analyst")
    assert analyst.source == "model"
    assert analyst.text.startswith("Your man")


def test_a_rewrite_still_cannot_smuggle_a_name_in(insight):
    p = Personaliser(ScriptedClient("Weber won it, then Lindqvist cleared."))
    analyst = p.render(insight).by_persona("analyst")
    assert analyst.source == "graph"
    assert "Lindqvist" not in analyst.text


def test_a_sentence_anchored_to_a_zone_is_accepted(moment):
    """Some moments are about space rather than people. "Two line-breaking
    passes carried play into Z4L" is traceable without naming anyone."""
    explainer = Explainer(ScriptedClient(
        "Two line-breaking passes carried play into Z4L."
    ))
    assert explainer.explain(moment).source == "model"


def test_a_sentence_anchored_to_nothing_is_rejected(moment):
    explainer = Explainer(ScriptedClient("It was an important passage of play."))
    insight = explainer.explain(moment)
    assert insight.source == "graph"
    assert "anchored to nothing" in insight.rejected
