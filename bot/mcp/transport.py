"""
RUNECLAW MCP stdio transport adapter (analysis-only, OKX-AI).

Wraps the existing :class:`~bot.mcp.server.RuneClawMCPServer` (``bot/mcp/server.py``)
in the official ``mcp`` SDK so any MCP-compatible client — Claude Code, Codex,
OKX AI, Hermes, OpenClaw — can connect over **stdio** and call RUNECLAW as a
read-only signal / analysis / safety service.

Design constraints (mirrors ``docs/OKX_AI_MCP_INTEGRATION.md``):

* **Thin / additive.** No business logic lives here. ``list_tools`` / ``call_tool``
  delegate verbatim to the existing ``RuneClawMCPServer``; importing this module
  changes nothing about the in-process server.
* **Analysis-only, defence in depth.** The adapter serves *only* the read-only
  ``TOOL_CATALOGUE`` — there is no code path from MCP to trade execution. Four
  independent layers enforce this: (1) the catalogue allow-list omits
  ``runeclaw_execute``; (2) the invariant test in
  ``tests/test_mcp_transport.py`` asserts no catalogued tool/skill can reach the
  executor; (3) the engine runs in its read-only/paper posture; and (4)
  :func:`_assert_execute_disabled` refuses to serve when ``MCP_ALLOW_EXECUTE`` is
  set.
* **Fail-closed auth.** The SDK carries no per-call bearer token over stdio — the
  security boundary is the process, so :func:`_resolve_auth_token` reads
  ``MCP_AUTH_TOKEN`` from the environment and forwards it into the existing
  hmac-compared ``auth_token`` check. No token → refuse to start.
* **Lazy SDK import.** The official ``mcp`` SDK is imported only when the server is
  actually built, so this module (and the invariant test) import cleanly even
  when the SDK is not installed.

Run::

    MCP_AUTH_TOKEN=... python -m bot.mcp.transport
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from bot.mcp.server import RuneClawMCPServer
from bot.utils.logger import audit, system_log

SERVER_NAME = "runeclaw"

# Values that count as "execution enabled" for MCP_ALLOW_EXECUTE.
_TRUTHY = {"1", "true", "yes", "on"}


def _assert_execute_disabled() -> None:
    """Fail-closed guard: refuse to serve when ``MCP_ALLOW_EXECUTE`` is enabled.

    This analysis-only deployment must never expose trade execution over MCP. The
    tool catalogue already omits ``runeclaw_execute``; this is an *independent*
    fourth layer so a stray environment flag can't silently widen the surface.
    """
    flag = os.environ.get("MCP_ALLOW_EXECUTE", "").strip().lower()
    if flag in _TRUTHY:
        raise RuntimeError(
            "MCP_ALLOW_EXECUTE is set. The OKX-AI analysis-only MCP transport "
            "refuses to start with execution enabled. Unset MCP_ALLOW_EXECUTE."
        )


def _resolve_auth_token() -> str:
    """Read the bearer token the stdio transport forwards into ``call_tool``.

    For stdio there is no per-request auth header — whoever can launch the server
    already holds ``MCP_AUTH_TOKEN`` via the environment. We forward that token
    into the existing hmac-compared ``auth_token`` check so the in-process auth
    path stays identical to every other caller (no fail-open branch).

    Fail-closed: refuse to serve without a token.
    """
    token = os.environ.get("MCP_AUTH_TOKEN", "")
    if not token:
        raise RuntimeError(
            "MCP_AUTH_TOKEN is not set. The MCP transport refuses to start "
            "without authentication. Set MCP_AUTH_TOKEN in your environment."
        )
    return token


def build_server(rc_server: RuneClawMCPServer | None = None) -> tuple[Any, RuneClawMCPServer]:
    """Build the SDK ``Server`` wired to a :class:`RuneClawMCPServer`.

    Enforces the fail-closed guards first (execute-disabled, then auth token),
    then imports the ``mcp`` SDK lazily. Returns ``(sdk_server, rc_server)``.
    """
    # Security guards run BEFORE the SDK import so a misconfigured deployment
    # fails on the most security-critical condition first.
    _assert_execute_disabled()
    auth_token = _resolve_auth_token()

    try:
        from mcp.server import Server
        import mcp.types as types
    except ImportError as exc:  # pragma: no cover - exercised only without the SDK
        raise RuntimeError(
            "The official `mcp` SDK is not installed. Install it with "
            "`pip install mcp` (or `pip install -e .[mcp]`) to run the transport."
        ) from exc

    rc = rc_server or RuneClawMCPServer()
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[Any]:
        # Serve ONLY the read-only catalogue exposed by RuneClawMCPServer.
        return [
            types.Tool(
                name=d["name"],
                description=d["description"],
                inputSchema=d["inputSchema"],
            )
            for d in await rc.list_tools()
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[Any]:
        resp = await rc.call_tool(name, arguments or {}, auth_token=auth_token)
        text = json.dumps(resp, ensure_ascii=False, default=str)
        return [types.TextContent(type="text", text=text)]

    return server, rc


async def serve() -> None:
    """Run the analysis-only MCP server over stdio until the client disconnects."""
    from mcp.server.stdio import stdio_server

    server, rc = build_server()
    audit(
        system_log,
        f"MCP stdio transport starting as '{SERVER_NAME}'",
        action="mcp_transport_start",
    )
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        await rc.shutdown()
        audit(system_log, "MCP stdio transport stopped", action="mcp_transport_stop")


def main() -> None:
    """Console entrypoint: ``python -m bot.mcp.transport``."""
    asyncio.run(serve())


if __name__ == "__main__":
    main()
