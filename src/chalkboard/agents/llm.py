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
import tempfile
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
        args_template: str = "-p|{prompt}|--allow-all-tools",
        use_stdin: bool = False,
        isolated: bool = True,
    ) -> None:
        self.command = command
        self.timeout_s = timeout_s
        self.args_template = args_template
        #: Run from an empty directory rather than the project.
        #:
        #: The CLI is a coding agent: started inside a repository it reads
        #: files, inspects the project and plans before answering. For a
        #: one-sentence rewrite that work is pure latency. An empty working
        #: directory gives it nothing to explore.
        self.isolated = isolated
        self._scratch: str | None = None
        #: Pipe the prompt rather than passing it as an argument. On Windows
        #: the CLI is a .cmd shim, so the command line goes through cmd.exe,
        #: which mangles embedded newlines - the CLI then receives a
        #: truncated prompt and replies that it was given no details. Piping
        #: sidesteps the shell entirely.
        self.use_stdin = use_stdin

    def available(self) -> bool:
        return shutil.which(self.command) is not None

    def complete(self, prompt: str, system: str | None = None) -> str | None:
        if not self.available():
            log.warning("%s not found on PATH", self.command)
            return None

        full = f"{system}\n\n{prompt}" if system else prompt

        if self.use_stdin:
            # Drop the placeholder AND the flag that introduces it. Leaving
            # "-p" behind with nothing after it makes the CLI exit with
            # "a value is required for --prompt", which is a confusing way
            # to discover the prompt went down the pipe instead.
            parts = self.args_template.split("|")
            argv = [self.command]
            skip_next = False
            for index, part in enumerate(parts):
                if "{prompt}" in part:
                    continue
                nxt = parts[index + 1] if index + 1 < len(parts) else ""
                if part in ("-p", "--prompt", "-i", "--interactive") and "{prompt}" in nxt:
                    continue
                argv.append(part)
            stdin_text = full
        else:
            # Newlines in an argv element do not survive cmd.exe. Flatten to
            # a single line so the whole prompt arrives intact.
            flat = " ".join(line.strip() for line in full.splitlines() if line.strip())
            argv = [self.command] + [
                part.replace("{prompt}", flat)
                for part in self.args_template.split("|")
            ]
            stdin_text = None

        try:
            result = subprocess.run(
                argv,
                input=stdin_text,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
                encoding="utf-8",
                errors="replace",
                cwd=self._workdir(),
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


    def _workdir(self) -> str | None:
        if not self.isolated:
            return None
        if self._scratch is None:
            self._scratch = tempfile.mkdtemp(prefix="chalkboard-llm-")
        return self._scratch


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
        args_template=(os.environ.get("COPILOT_ARGS") or "-p|{prompt}|--allow-all-tools").strip(),
        use_stdin=(os.environ.get("COPILOT_STDIN") or "").strip().lower()
        in ("1", "true", "yes"),
        isolated=(os.environ.get("COPILOT_ISOLATED") or "true").strip().lower()
        not in ("0", "false", "no"),
    )
    if not client.available():
        log.warning("copilot CLI not on PATH - falling back to the stub")
        return StubClient(fallback)
    return client
