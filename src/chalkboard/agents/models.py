"""Model client with three backends, selected by environment.

1. GitHub Copilot CLI  - the real path. Free on an individual account, and a
                         Microsoft-owned service, which matters because the
                         hackathon scores use of its hero technologies.
2. Local stub          - deterministic templates. Used by the tests and when
                         offline.
3. Disabled            - MODEL_ENABLED=false, the kill switch demonstrated
                         on camera.

The stub is not a placeholder to delete later. It is the graceful degradation
the Best Multi-Agent category scores, and it keeps the test suite offline.

Why a subprocess and not an HTTP call
-------------------------------------
GitHub Models, the free OpenAI-compatible inference API, was retired on
30 July 2026. GitHub's replacement for free individual inference is the
Copilot CLI, which is a terminal program rather than an endpoint. So each
call spawns a process.

That costs perhaps a second per call, which is affordable only because the
significance gate keeps us to roughly 30 calls per match rather than one per
event. If the gate were ever removed, this design would fall over - worth
remembering before anyone "simplifies" it.

Consequences to design around:
  - no streaming; a call returns once, complete
  - stdout needs parsing, and may carry CLI chrome around the answer
  - a hung process must be killed, hence COPILOT_TIMEOUT_S
  - concurrency is bounded by process spawn cost, so calls are serialised

Setup (once, outside this code):
    npm install -g @github/copilot
    copilot          # sign in interactively

TODO(WP3): subprocess wrapper, timeout handling, stdout parsing,
           MODEL_ENABLED switch, template fallback.
"""
