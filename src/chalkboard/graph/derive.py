"""Recomputes the derived edges on a timer.

PASSES_TO, PRESSED_INTO, NEMESIS_OF and LINKS_WITH are materialised rather
than traversed at read time, so the significance gate stays fast enough to
run every few seconds.

TODO(WP2): run ontology/derived.cypher over a rolling window.
"""
