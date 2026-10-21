"""An MCP client wearing the same face as the direct graph tools.

Why this exists
---------------
The Explainer needs four narrow lookups against the graph. It could import
``GraphTools`` and call them as ordinary Python - and until now it did.

Routing them through MCP instead makes the boundary real rather than
documented. The agent's access to the graph becomes a declared, read-only
surface of four named tools, negotiated over a protocol, rather than an
object it happens to hold a reference to. Someone reading the repository can
see exactly what the agent may ask for, and a log shows exactly what it did
ask for.

That is the whole gain. The answers are identical. This is a boundary, not
an optimisation, and it is worth being honest about which.

Where MCP stops
---------------
Only the agent's lookups go through it. The loader, the derived-edge job,
the significance gate, the insight store and the API all speak to Neo4j
directly, because none of them is an agent. MCP is a protocol for giving a
model controlled access to tools; using it as a general database layer would
add latency to a batch loader and a hop to a query that must answer in
milliseconds, for no gain at all.

Async in a sync world
---------------------
The MCP SDK is asyncio. The pipeline is synchronous and must stay that way -
it is driven by a match clock and a generator. So this owns a single event
loop on a background thread, connects once, and exposes blocking methods.
One thread, one session, started lazily and closed with the process.

Never fatal
-----------
If the server cannot start, or a call fails, the caller falls back to the
direct tools. A protocol boundary that takes the demo down would be a poor
trade for architectural tidiness.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
from typing import Any

log = logging.getLogger(__name__)

#: Longer than any single lookup should need. A hung server must not block
#: the match clock.
CALL_TIMEOUT_S = 20.0
CONNECT_TIMEOUT_S = 30.0


class MCPGraphTools:
    """Calls the four graph tools over MCP, synchronously.

    Deliberately exposes the same four method names as
    :class:`chalkboard.mcp.tools.GraphTools`, so the Explainer neither knows
    nor cares which one it holds.
    """

    def __init__(
        self,
        command: list[str] | None = None,
        call_timeout_s: float = CALL_TIMEOUT_S,
    ) -> None:
        self.command = command or [sys.executable, "-m", "chalkboard.mcp.server"]
        self.call_timeout_s = call_timeout_s

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: Any = None
        self._stack: Any = None
        self.tool_names: list[str] = []

    # ------------------------------------------------------------ lifecycle

    def start(self) -> bool:
        """Launch the server and open a session. False if unavailable."""
        try:
            from mcp import ClientSession, StdioServerParameters  # noqa: F401
            from mcp.client.stdio import stdio_client  # noqa: F401
        except ImportError:
            log.info("the mcp package is not installed - using direct graph tools")
            return False

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="chalkboard-mcp", daemon=True
        )
        self._thread.start()

        try:
            self._submit(self._connect(), CONNECT_TIMEOUT_S)
        except Exception as error:  # noqa: BLE001
            log.warning("could not start the MCP server (%s) - using direct tools", error)
            self.close()
            return False

        log.info("MCP session open, tools: %s", ", ".join(self.tool_names))
        return True

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _connect(self) -> None:
        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._stack = AsyncExitStack()
        params = StdioServerParameters(command=self.command[0], args=self.command[1:])
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        listing = await self._session.list_tools()
        self.tool_names = [t.name for t in listing.tools]

    def close(self) -> None:
        if self._loop and self._loop.is_running():
            if self._stack is not None:
                try:
                    self._submit(self._stack.aclose(), 5.0)
                except Exception:  # noqa: BLE001 - shutdown is best effort
                    pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._session = self._stack = None

    # ----------------------------------------------------------------- call

    def _submit(self, coro, timeout_s: float):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout_s)

    def call(self, name: str, **arguments: Any) -> Any:
        """Invoke one tool. Returns ``None`` on any failure."""
        if self._session is None:
            return None
        try:
            result = self._submit(
                self._session.call_tool(name, arguments), self.call_timeout_s
            )
        except Exception as error:  # noqa: BLE001
            log.warning("MCP call %s failed: %s", name, error)
            return None
        return _unwrap(result)

    # ----------------------------------------------- the four graph tools

    def get_player(self, player_id: str, match_id: str) -> dict[str, Any]:
        return self.call("get_player", player_id=player_id, match_id=match_id) or {}

    def get_derived_edges(self, player_id: str, match_id: str) -> dict[str, Any]:
        return self.call(
            "get_derived_edges", player_id=player_id, match_id=match_id
        ) or {}

    def get_possession(self, possession_id: str) -> list[dict[str, Any]]:
        return self.call("get_possession", possession_id=possession_id) or []

    def get_zone_pressure(self, team_id: str, match_id: str) -> list[dict[str, Any]]:
        return self.call("get_zone_pressure", team_id=team_id, match_id=match_id) or []


def _unwrap(result: Any) -> Any:
    """Pull the payload out of an MCP tool result.

    The SDK returns structured content when the tool declared a return type
    and text blocks otherwise, and which you get has moved between versions.
    Handle both rather than pinning to one.
    """
    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured.get("result", structured)

    blocks = getattr(result, "content", None) or []
    for block in blocks:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            return text
    return None


def connect_tools(session: Any, prefer_mcp: bool = True) -> tuple[Any, str]:
    """Return the tools the Explainer should use, and how they were obtained.

    Tries MCP first so the agent's graph access goes over the declared
    protocol surface. Falls back to in-process tools if the server cannot
    start, because a boundary that breaks the demo is a poor trade.
    """
    from .tools import GraphTools

    if prefer_mcp:
        client = MCPGraphTools()
        if client.start():
            return client, "mcp"
    return GraphTools(session), "direct"
