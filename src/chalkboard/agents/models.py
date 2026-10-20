"""Model client with three backends, selected by environment.

1. GitHub Models  - the real path, free, no card
2. Local stub     - deterministic templates, used by tests and when offline
3. Disabled       - the kill switch, for the failure-recovery demo

The stub is not a placeholder to delete later. It is the graceful degradation
the Best Multi-Agent category scores, and it keeps the test suite offline.

TODO(WP3): client + MODEL_ENABLED switch + template fallback.
"""
