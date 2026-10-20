# Chalkboard - instructions for GitHub Copilot

**The graph reasons. The model narrates.** Hold that line in every suggestion.

## Rules

- Never build or modify the graph with a language model. Events are already
  structured; the mapping in `ontology/match.yaml` is the only source of truth
  for how they become nodes and edges.
- Never let an agent decide what is significant. That is a Cypher query.
- Agents may only state what is in the evidence they were handed.
- `ontology/match.yaml` describes structure only - no conditions, no loops.
- Every model call goes through `agents/models.py`, which must keep working
  with `MODEL_ENABLED=false`.

## Conventions

- Python 3.12, standard library first, minimal dependencies.
- Docstrings say *why*, not *what*.
- Tests must run offline, with no Neo4j and no model access.
