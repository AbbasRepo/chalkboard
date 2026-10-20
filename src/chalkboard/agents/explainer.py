"""Explainer agent: why this moment mattered.

Input is a scored moment plus its evidence events. Output is one sentence,
plus the event ids it used. Every name and number in that sentence must appear
in the supplied evidence or the sentence is rejected and the template is used.

TODO(WP3): Agent Framework agent, MCP tool wiring, grounding check.
"""
