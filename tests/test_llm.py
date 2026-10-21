"""Tests for the model client.

The important property is that failure never propagates. A timeout, a
missing binary, a non-zero exit and a disabled switch must all look the same
to the caller - no sentence - so the system falls back to graph facts rather
than taking the demo down.
"""

from __future__ import annotations

import subprocess

import pytest

from chalkboard.agents.llm import CopilotCLIClient, StubClient, _clean, get_client


# ------------------------------------------------------------------- stub

def test_stub_returns_the_prompt_fallback():
    assert StubClient().complete("noise\nFALLBACK: three turnovers\nmore") == "three turnovers"


def test_stub_returns_its_own_fallback_when_the_prompt_has_none():
    assert StubClient("graph line").complete("nothing here") == "graph line"


def test_kill_switch_selects_the_stub(monkeypatch):
    """MODEL_ENABLED=false is a demonstrated behaviour, not an error state."""
    monkeypatch.setenv("MODEL_ENABLED", "false")
    client = get_client("graph line")
    assert client.name == "stub"
    assert client.complete("anything") == "graph line"


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off"])
def test_kill_switch_accepts_the_obvious_spellings(monkeypatch, value):
    monkeypatch.setenv("MODEL_ENABLED", value)
    assert get_client().name == "stub"


def test_missing_binary_falls_back_to_the_stub(monkeypatch):
    monkeypatch.setenv("MODEL_ENABLED", "true")
    monkeypatch.setenv("COPILOT_CMD", "definitely-not-a-real-binary-xyz")
    assert get_client("graph line").name == "stub"


def test_empty_env_values_do_not_crash(monkeypatch):
    """A key with nothing after the equals sign is common in a .env."""
    monkeypatch.setenv("MODEL_ENABLED", "")
    monkeypatch.setenv("COPILOT_TIMEOUT_S", "")
    monkeypatch.setenv("COPILOT_CMD", "")
    get_client()  # must not raise


# ------------------------------------------------------------ the CLI path

def test_cli_strips_terminal_furniture():
    raw = "Welcome to GitHub Copilot\n⠋ Thinking\n\nThe press found the seam.\n"
    assert _clean(raw) == "The press found the seam."


def test_cli_returns_nothing_for_an_empty_answer():
    assert _clean("Welcome to GitHub Copilot\n\n") is None


def test_timeout_returns_nothing_rather_than_raising(monkeypatch):
    """A hung CLI must not take the pipeline with it."""
    client = CopilotCLIClient(timeout_s=0.01)
    monkeypatch.setattr(client, "available", lambda: True)

    def explode(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="copilot", timeout=0.01)

    monkeypatch.setattr(subprocess, "run", explode)
    assert client.complete("prompt") is None


def test_non_zero_exit_returns_nothing(monkeypatch):
    client = CopilotCLIClient()
    monkeypatch.setattr(client, "available", lambda: True)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "rate limited"),
    )
    assert client.complete("prompt") is None


def test_invocation_is_configurable(monkeypatch):
    """The CLI's flags have moved more than once, so the call is a template."""
    seen = {}

    def capture(argv, **kwargs):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, "answer", "")

    client = CopilotCLIClient(command="copilot", args_template="--ask|{prompt}")
    monkeypatch.setattr(client, "available", lambda: True)
    monkeypatch.setattr(subprocess, "run", capture)

    assert client.complete("why did that matter?") == "answer"
    assert seen["argv"] == ["copilot", "--ask", "why did that matter?"]


def test_stdin_mode_drops_the_prompt_flag_as_well(monkeypatch):
    """Leaving "-p" behind with nothing after it makes the CLI exit with
    "a value is required for --prompt" - a confusing way to discover the
    prompt went down the pipe instead."""
    seen = {}

    def capture(argv, **kwargs):
        seen["argv"] = argv
        seen["stdin"] = kwargs.get("input")
        return subprocess.CompletedProcess(argv, 0, "answer", "")

    client = CopilotCLIClient(
        args_template="-p|{prompt}|--allow-all-tools", use_stdin=True
    )
    monkeypatch.setattr(client, "available", lambda: True)
    monkeypatch.setattr(subprocess, "run", capture)

    assert client.complete("why did that matter?") == "answer"
    assert seen["argv"] == ["copilot", "--allow-all-tools"]
    assert "why did that matter?" in seen["stdin"]


def test_argument_mode_flattens_newlines(monkeypatch):
    """Newlines in an argv element do not survive cmd.exe on Windows, and
    the CLI then receives a truncated prompt."""
    seen = {}

    def capture(argv, **kwargs):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, "answer", "")

    client = CopilotCLIClient(args_template="-p|{prompt}")
    monkeypatch.setattr(client, "available", lambda: True)
    monkeypatch.setattr(subprocess, "run", capture)

    client.complete("line one\nline two\n\nline three")
    assert "\n" not in seen["argv"][2]
    assert "line one line two line three" in seen["argv"][2]
