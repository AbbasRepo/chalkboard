"""Model client. Three backends, one interface.

Where this sits
---------------
Everything upstream - loader, derived edges, significance gate - is
deterministic graph work. This module is the *first* point in the pipeline
where a language model is involved, and it is involved for one narrow job:
turning facts the graph has already established into a sentence.

It never decides what to talk about. It never reads the graph. It is handed
a prompt built from evidence and returns prose.

The three backends
------------------
``CopilotCLIClient``
    The real path. GitHub Copilot CLI, free on an individual account, and a
    Microsoft service - which matters because the hackathon scores use of
    its hero technologies.

``StubClient``
    Deterministic templates. Used by every test, and by the running system
    whenever inference is unavailable. This is not a placeholder to delete
    later: it is the graceful degradation the Best Multi-Agent category
    explicitly scores, and it is what keeps the suite offline.

``None`` (disabled)
    ``MODEL_ENABLED=false``. The kill switch demonstrated on camera.

Why a subprocess and not an HTTP call
-------------------------------------
GitHub Models, the free OpenAI-compatible inference API, was retired on
30 July 2026. GitHub's replacement for free individual inference is the
Copilot CLI, a terminal program rather than an endpoint. So each call spawns
a process and costs roughly a second.

That is affordable only because the significance gate holds the system to
about thirty calls a match rather than one per action. Remove the gate and
this design falls over.

Failure is never raised
-----------------------
``complete`` returns ``None`` rather than throwing. A timeout, a missing
binary, a rate limit and a disabled switch all look the same to the caller,
which falls back to graph facts. An exception here would take the demo down
at the worst moment; a plainer sentence will not.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Protocol

log = logging.getLogger(__name__)

#: A hung CLI must be killed rather than blocking the pipeline.
DEFAULT_TIMEOUT_S = 45.0

#: Lines the CLI prints around its answer. Stripped so the caller gets prose
#: rather than terminal furniture.
_CHROME = (
    "Welcome to", "GitHub Copilot", "Type /help", "Thinking",
    "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏",
)


class ModelClient(Protocol):
    def complete(self, prompt: str, system: str | None = None) -> str | None: ...

    @property
    def name(self) -> str: ...


class StubClient:
    """Deterministic. No network, no subprocess, no model.

    Returns the caller's own fallback text when one is supplied, so the
    system degrades to graph facts rather than to silence.
    """

    name = "stub"

    def __init__(self, fallback: str | None = None) -> None:
        self._fallback = fallback

    def complete(self, prompt: str, system: str | None = None) -> str | None:
        for line in prompt.splitlines():
            if line.startswith("FALLBACK:"):
                return line.removeprefix("FALLBACK:").strip()
        return self._fallback


class CopilotCLIClient:
    """Shells out to the GitHub Copilot CLI.

    The invocation is configurable because the CLI's flags have moved more
    than once. Verify with ``copilot --help`` and override ``COPILOT_ARGS``
    if the default stops working - a template where ``{prompt}`` is
    substituted, arguments separated by ``|``.
    """

    name = "copilot-cli"

    def __init__(
        self,
        command: str = "copilot",
        timeout_s: float = DEFAULT_TIMEOUT_S,
        args_template: str = "-p|{prompt}",
    ) -> None:
        self.command = command
        self.timeout_s = timeout_s
        self.args_template = args_template

    def available(self) -> bool:
        return shutil.which(self.command) is not None

    def complete(self, prompt: str, system: str | None = None) -> str | None:
        if not self.available():
            log.warning("%s not found on PATH", self.command)
            return None

        full = f"{system}\n\n{prompt}" if system else prompt
        argv = [self.command] + [
            part.replace("{prompt}", full)
            for part in self.args_template.split("|")
        ]

        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            log.warning("copilot timed out after %.0fs", self.timeout_s)
            return None
        except OSError as error:
            log.warning("copilot failed to start: %s", error)
            return None

        if result.returncode != 0:
            log.warning("copilot exited %s: %s", result.returncode, result.stderr[:200])
            return None

        return _clean(result.stdout)


def _clean(raw: str) -> str | None:
    """Strip terminal furniture and return the answer, or nothing."""
    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or any(stripped.startswith(c) for c in _CHROME):
            continue
        lines.append(stripped)
    text = " ".join(lines).strip()
    return text or None


def get_client(fallback: str | None = None) -> ModelClient:
    """Build the client the environment asks for.

    ``MODEL_ENABLED=false`` is the kill switch: it returns the stub, so the
    system keeps producing correct output with no model in the path. That is
    the behaviour demonstrated on camera, not an error state.
    """
    enabled = (os.environ.get("MODEL_ENABLED") or "true").strip().lower()
    if enabled in ("false", "0", "no", "off"):
        log.info("MODEL_ENABLED is off - using the stub")
        return StubClient(fallback)

    client = CopilotCLIClient(
        command=(os.environ.get("COPILOT_CMD") or "copilot").strip(),
        timeout_s=float(os.environ.get("COPILOT_TIMEOUT_S") or DEFAULT_TIMEOUT_S),
        args_template=(os.environ.get("COPILOT_ARGS") or "-p|{prompt}").strip(),
    )
    if not client.available():
        log.warning("copilot CLI not on PATH - falling back to the stub")
        return StubClient(fallback)
    return client
