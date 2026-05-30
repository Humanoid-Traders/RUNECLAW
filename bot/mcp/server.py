"""
RUNECLAW MCP Tool Server -- exposes the skill registry as MCP-callable tools
for the Bitget Agent Hub.

This module wraps each registered skill as a typed MCP tool with JSON Schema
input definitions, dispatches incoming tool calls to ``skill.execute()``, and
returns structured JSON responses.

Usage::

    from bot.mcp.server import RuneClawMCPServer

    server = RuneClawMCPServer()
    tools  = await server.list_tools()
    result = await server.call_tool("runeclaw_scan", {})
"""

from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import asdict, dataclass, field
from typing import Any

from bot.core.engine import RuneClawEngine
from bot.skills.skill_registry import SkillRegistry, build_default_registry
from bot.utils.logger import audit, system_log


# ---------------------------------------------------------------------------
# Tool definition helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MCPToolParam:
    """Single parameter in an MCP tool's input schema."""

    name: str
    type: str  # JSON Schema type (e.g. "string", "integer")
    description: str
    required: bool = True
    default: Any = None


@dataclass(frozen=True)
class MCPToolDef:
    """Declarative definition of one MCP tool."""

    mcp_name: str
    skill_name: str
    description: str
    params: tuple[MCPToolParam, ...] = ()


# ---------------------------------------------------------------------------
# Static tool catalogue -- maps MCP tool names to skill names + schemas
# ---------------------------------------------------------------------------

TOOL_CATALOGUE: tuple[MCPToolDef, ...] = (
    MCPToolDef(
        mcp_name="runeclaw_scan",
        skill_name="scan_market",
        description="Scan the exchange for top movers and volume anomalies.",
    ),
    MCPToolDef(
        mcp_name="runeclaw_analyze",
        skill_name="analyze_asset",
        description="Run AI analysis on a specific asset and generate a trade idea.",
        params=(
            MCPToolParam(
                name="symbol",
                type="string",
                description="Trading pair symbol, e.g. 'BTC/USDT'.",
                required=True,
            ),
        ),
    ),
    MCPToolDef(
        mcp_name="runeclaw_risk",
        skill_name="check_risk",
        description="Show current risk metrics, drawdown, and circuit-breaker status.",
    ),
    MCPToolDef(
        mcp_name="runeclaw_portfolio",
        skill_name="get_portfolio",
        description="Show paper-portfolio summary: balance, equity, win rate, PnL.",
    ),
    # SECURITY: runeclaw_execute intentionally excluded from MCP.
    # Exposing trade execution over MCP would bypass the human-confirmation
    # gate, violating the fail-closed design.  An agent on the Hub could
    # call runeclaw_analyze → runeclaw_execute fully autonomously.
    # Re-enable only behind MCP_ALLOW_EXECUTE=true AND with caller auth.
    # See: Audit finding A (MCP execute bypass).
    MCPToolDef(
        mcp_name="runeclaw_explain",
        skill_name="explain_trade",
        description="Explain a pending or historical trade idea.",
        params=(
            MCPToolParam(
                name="trade_id",
                type="string",
                description="The trade ID to explain. Omit to list pending ideas.",
                required=False,
                default="",
            ),
        ),
    ),
    MCPToolDef(
        mcp_name="runeclaw_macro",
        skill_name="macro_calendar",
        description="Show macro-event calendar: current risk state and upcoming events.",
    ),
    MCPToolDef(
        mcp_name="runeclaw_backtest",
        skill_name="run_backtest",
        description="Run a backtest with synthetic data.",
        params=(
            MCPToolParam(
                name="bars",
                type="integer",
                description="Number of OHLCV bars to generate (max 5000).",
                required=False,
                default=720,
            ),
            MCPToolParam(
                name="seed",
                type="integer",
                description="Random seed for reproducible synthetic data.",
                required=False,
                default=42,
            ),
        ),
    ),
)


# ---------------------------------------------------------------------------
# Response envelope
# ---------------------------------------------------------------------------

@dataclass
class MCPResponse:
    """Standardised response envelope returned by every tool call."""

    status: str  # "success" or "error"
    tool: str
    result: Any

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "tool": self.tool, "result": self.result}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

class RuneClawMCPServer:
    """
    Model Context Protocol server that wraps the RUNECLAW skill registry.

    The server is stateful: it owns a single ``RuneClawEngine`` instance and
    routes incoming ``call_tool`` requests to the matching skill via the
    ``SkillRegistry``.

    Lifecycle::

        server = RuneClawMCPServer()        # creates engine + registry
        tools  = await server.list_tools()   # JSON tool definitions
        resp   = await server.call_tool("runeclaw_scan", {})
        await server.shutdown()              # graceful cleanup
    """

    def __init__(
        self,
        engine: RuneClawEngine | None = None,
        registry: SkillRegistry | None = None,
    ) -> None:
        self._engine = engine or RuneClawEngine()
        self._registry = registry or build_default_registry()
        self._tool_index: dict[str, MCPToolDef] = {
            t.mcp_name: t for t in TOOL_CATALOGUE
        }

        audit(
            system_log,
            f"MCP server initialised with {len(self._tool_index)} tools",
            action="mcp_init",
        )

    # -- public API ---------------------------------------------------------

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return MCP tool definitions with JSON Schema ``inputSchema``."""
        tools: list[dict[str, Any]] = []
        for tdef in TOOL_CATALOGUE:
            tools.append(self._build_tool_schema(tdef))
        return tools

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Dispatch an MCP tool call to the corresponding skill.

        Parameters
        ----------
        name:
            The MCP tool name, e.g. ``"runeclaw_scan"``.
        arguments:
            Key-value arguments matching the tool's ``inputSchema``.

        Returns
        -------
        dict
            ``{"status": "success"|"error", "tool": "<name>", "result": "..."}``
        """
        arguments = arguments or {}

        # --- lookup --------------------------------------------------------
        tdef = self._tool_index.get(name)
        if tdef is None:
            return MCPResponse(
                status="error",
                tool=name,
                result=f"Unknown tool '{name}'. Available: {list(self._tool_index)}",
            ).to_dict()

        skill = self._registry.get(tdef.skill_name)
        if skill is None:
            return MCPResponse(
                status="error",
                tool=name,
                result=f"Skill '{tdef.skill_name}' is not registered.",
            ).to_dict()

        # --- validate required params -------------------------------------
        missing = [
            p.name
            for p in tdef.params
            if p.required and p.name not in arguments
        ]
        if missing:
            return MCPResponse(
                status="error",
                tool=name,
                result=f"Missing required parameters: {missing}",
            ).to_dict()

        # --- apply defaults for optional params ---------------------------
        kwargs: dict[str, Any] = {}
        for p in tdef.params:
            if p.name in arguments:
                kwargs[p.name] = self._coerce(arguments[p.name], p.type)
            elif p.default is not None:
                kwargs[p.name] = p.default

        # --- execute -------------------------------------------------------
        try:
            result_text = await skill.execute(self._engine, **kwargs)
            audit(
                system_log,
                f"MCP tool '{name}' executed successfully",
                action="mcp_call",
                result="ok",
                data={"tool": name, "kwargs": kwargs},
            )
            return MCPResponse(
                status="success", tool=name, result=result_text
            ).to_dict()

        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            audit(
                system_log,
                f"MCP tool '{name}' failed: {exc}",
                action="mcp_call",
                result="error",
                data={"tool": name, "traceback": tb},
            )
            return MCPResponse(
                status="error",
                tool=name,
                result=f"Skill execution failed: {exc}",
            ).to_dict()

    async def shutdown(self) -> None:
        """Graceful shutdown hook (close exchange connections, etc.)."""
        audit(system_log, "MCP server shutting down", action="mcp_shutdown")

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    def _build_tool_schema(tdef: MCPToolDef) -> dict[str, Any]:
        """Build a single MCP tool definition dict with ``inputSchema``."""
        properties: dict[str, Any] = {}
        required: list[str] = []

        for p in tdef.params:
            prop: dict[str, Any] = {
                "type": p.type,
                "description": p.description,
            }
            if p.default is not None:
                prop["default"] = p.default
            properties[p.name] = prop

            if p.required:
                required.append(p.name)

        input_schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            input_schema["required"] = required
        # Allow no additional properties for strict validation
        input_schema["additionalProperties"] = False

        return {
            "name": tdef.mcp_name,
            "description": tdef.description,
            "inputSchema": input_schema,
        }

    @staticmethod
    def _coerce(value: Any, json_type: str) -> Any:
        """Best-effort coercion of an argument to its declared JSON type."""
        if json_type == "integer":
            return int(value)
        if json_type == "number":
            return float(value)
        if json_type == "boolean":
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes")
            return bool(value)
        return value  # string or unknown -- pass through
