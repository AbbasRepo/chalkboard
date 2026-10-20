# Chalkboard

**The tactics board that answers back.** Explainable football match
intelligence from synthetic events, personalised per viewer.

Entry for the Microsoft × Premier League *Inside the Game* hackathon,
category **Best Multi-Agent System**. Submission closes 27 October 2026.

## The one idea

**The graph reasons. The model narrates.**

Significance is computed by a Cypher query over a live match graph, not
guessed by a model. The agents only narrate what the graph has already
proven. Every design decision follows from that sentence.

## Hard rules

These are not preferences. Breaking one breaks the project's claim.

1. **The graph is never built by a language model.** Events arrive as
   structured records with explicit team, player, type, coordinates and
   outcome. There is nothing to extract. An extraction step could only
   introduce error into facts that arrived unambiguous.
2. **Significance is never decided by a language model.** It is a scored
   query whose weights can be read, argued with and tuned.
3. **Agents may only state what is in the evidence handed to them.** Every
   player name and number is checked against the supplied events before the
   sentence is shown. Unsupported output is rejected and the template is
   used instead.
4. **`ontology/match.yaml` describes structure only.** No conditions, no
   loops, no expressions. The moment it needs one, that logic belongs in
   `graph/loader.py`. A config language that grows a brain is a programming
   language nobody documented.
5. **Everything must work with `MODEL_ENABLED=false`.** Graceful degradation
   is explicitly scored by the chosen category, and it keeps the tests
   offline.

## Constraints worth knowing before you "simplify" something

- Inference is the **GitHub Copilot CLI**, a subprocess, not an HTTP API.
  GitHub Models was retired on 30 July 2026. Each call spawns a process and
  costs about a second. That is affordable only because the significance
  gate holds us to roughly 30 calls per match rather than one per event.
  Remove the gate and this design falls over.
- **Neo4j AuraDB Free** pauses after a few days idle and has node limits.
  Keep the graph to one match at a time.
- Tests must run **offline** — no Neo4j, no model. Use fakes.

## Layout

```
ontology/          the data model. Read match.yaml first.
src/chalkboard/
  generator/       DONE - synthetic match simulation. Do not modify.
  graph/           loader, derived edges, significance gate
  mcp/             read-only graph tools for the agents
  agents/          llm.py (Copilot CLI client), explainer, personaliser
  app/             FastAPI + the two-pane UI
tests/             must pass offline
```

Note `generator/models.py` is the **event schema**. `agents/llm.py` is the
**language model client**. Different things.

## Conventions

- Python 3.12, standard library first, few dependencies.
- Docstrings explain *why*, not *what*. The why is usually a constraint.
- Type hints on public functions.
- No secrets in code or tests. Config comes from the environment.
- British English in prose.

## Current state

| WP | Scope | State |
| --- | --- | --- |
| 1 | Synthetic event generator | Complete, calibrated, 10 tests |
| 2 | Loader, derived edges, significance gate | In progress |
| 3 | MCP server, Copilot CLI client, two agents | Not started |
| 4 | FastAPI and the two-pane UI | Not started |
| 5 | README, pitch, demo video | Not started |
