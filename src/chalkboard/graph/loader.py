"""Reads ontology/match.yaml and writes each event into the graph.

Generic by design: the mapping file decides which nodes and edges an event
produces, and this module only executes that mapping. Adding an event type
should mean editing YAML, not editing Python.

Reads JSON Lines on stdin so it can be piped straight from the generator:

    python -m chalkboard.generator.cli stream | python -m chalkboard.graph.loader

TODO(WP2): MERGE nodes idempotently; batch writes; handle MATCH_META.
"""
