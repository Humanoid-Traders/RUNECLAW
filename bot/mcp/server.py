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
import os
import re
import traceback
from dataclasses import asdict, dataclass, field
from typing import Any

from bot.core.engine import RuneClawEngine
from bot.skills.skill_registry import SkillRegistry, build_default_registry
from bot.utils.logger import audit, system_log

# C5 FIX: bearer token authentication for MCP tool calls.
# Set MCP_AUTH_TOKEN in .env to require callers to authenticate.
_MCP_AUTH_TOKEN: str = os.environ.get("MCP_AUTH_TOKEN", "")

# SEC-H3 FIX: strict symbol format validator for MCP entry points.
_SYMBOL_RE = re.compile(r'^[A-Z0-9]{1,15}(/[A-Z0-9]{1,15})?$')


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
        mcp_name="runeclaw_shield",
        skill_name="_shield_evaluate",
        description=(
            "RUNECLAW Shield: 21 fail-closed risk checks on a trade proposal. "
            "Any external agent can call this to get an immutable safety decision. "
            "Returns approved/rejected with check details."
        ),
        params=(
            MCPToolParam(
                name="symbol", type="string",
                description="Trading pair, e.g. 'BTC/USDT'.",
            ),
            MCPToolParam(
                name="direction", type="string",
                description="Trade direction: 'long' or 'short'.",
            ),
            MCPToolParam(
                name="entry_price", type="number",
                description="Proposed entry price.",
            ),
            MCPToolParam(
                name="stop_loss", type="number",
                description="Stop loss price.",
            ),
            MCPToolParam(
                name="take_profit", type="number",
                description="Take profit price.",
            ),
            MCPToolParam(
                name="confidence", type="number",
                description="Signal confidence 0.0-1.0.",
                required=False,
                default=0.65,
            ),
        ),
    ),
    MCPToolDef(
        mcp_name="runeclaw_fullscan",
        skill_name="_fullscan",
        description=(
            "Run full 67-symbol market scan across the RUNECLAW universe. "
            "Returns ranked signals with RSI, volume, chart patterns, and scores."
        ),
        params=(
            MCPToolParam(
                name="mode", type="string",
                description="Scan mode: 'quick' (top 10), 'deep' (all 67), 'swing', 'scalp'.",
                required=False,
                default="quick",
            ),
        ),
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
        # C5 HARDENED: fail-closed -- refuse to start without auth token
        if not _MCP_AUTH_TOKEN:
            raise RuntimeError(
                "MCP_AUTH_TOKEN is not set. The MCP server refuses to start "
                "without authentication. Set MCP_AUTH_TOKEN in your .env file."
            )

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
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Dispatch an MCP tool call to the corresponding skill.

        Parameters
        ----------
        name:
            The MCP tool name, e.g. ``"runeclaw_scan"``.
        arguments:
            Key-value arguments matching the tool's ``inputSchema``.
        auth_token:
            Bearer token for authentication (required when MCP_AUTH_TOKEN is set).

        Returns
        -------
        dict
            ``{"status": "success"|"error", "tool": "<name>", "result": "..."}``
        """
        arguments = arguments or {}

        # --- C5 FIX: authenticate caller (always enforced) -----------------
        # Constructor already refuses to start without MCP_AUTH_TOKEN.
        # This check is unconditional — no fail-open path exists.
        import hmac
        if not auth_token or not hmac.compare_digest(auth_token, _MCP_AUTH_TOKEN):
            audit(
                system_log,
                f"MCP auth rejected for tool '{name}'",
                action="mcp_auth_fail",
            )
            return MCPResponse(
                status="error",
                tool=name,
                result="Authentication required. Provide a valid auth_token.",
            ).to_dict()

        # --- lookup --------------------------------------------------------
        tdef = self._tool_index.get(name)
        if tdef is None:
            return MCPResponse(
                status="error",
                tool=name,
                result=f"Unknown tool '{name}'. Available: {list(self._tool_index)}",
            ).to_dict()

        skill = self._registry.get(tdef.skill_name)
        if skill is None and not tdef.skill_name.startswith("_"):
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
        # Coercion runs before the execute try/except below; a bad numeric
        # argument (e.g. bars="abc") would otherwise raise an unhandled
        # ValueError that bypasses the structured error envelope + redaction.
        kwargs: dict[str, Any] = {}
        try:
            for p in tdef.params:
                if p.name in arguments:
                    kwargs[p.name] = self._coerce(arguments[p.name], p.type)
                elif p.default is not None:
                    kwargs[p.name] = p.default
        except (ValueError, TypeError) as exc:
            return MCPResponse(
                status="error",
                tool=name,
                result=f"Invalid argument type: {exc}",
            ).to_dict()

        # --- SEC-H3 FIX: validate symbol parameters ----------------------
        if "symbol" in kwargs and isinstance(kwargs["symbol"], str):
            if not _SYMBOL_RE.match(kwargs["symbol"]):
                return MCPResponse(
                    status="error",
                    tool=name,
                    result="Invalid symbol format. Expected e.g. 'BTC/USDT'.",
                ).to_dict()

        # --- execute -------------------------------------------------------
        try:
            # Special built-in tools that bypass the skill registry
            if tdef.skill_name == "_shield_evaluate":
                result_text = await self._shield_evaluate(**kwargs)
            elif tdef.skill_name == "_fullscan":
                result_text = await self._fullscan(**kwargs)
            else:
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
            # C3 FIX: redact secrets from traceback before logging
            from bot.utils.logger import _redact_string
            tb = _redact_string(tb)
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

    # -- RUNECLAW Shield: standalone risk evaluation -----------------------

    async def _shield_evaluate(
        self, symbol: str, direction: str, entry_price: float,
        stop_loss: float, take_profit: float, confidence: float = 0.65,
    ) -> str:
        """Run 21 fail-closed risk checks on a trade proposal."""
        from bot.utils.models import Direction, TradeIdea, RiskVerdict

        dir_enum = Direction.LONG if direction.lower() == "long" else Direction.SHORT
        idea = TradeIdea(
            asset=symbol, direction=dir_enum, entry_price=entry_price,
            stop_loss=stop_loss, take_profit=take_profit,
            confidence=confidence, reasoning="MCP Shield evaluation",
            source="mcp_shield",
        )
        # Compute ATR from stop distance as proxy
        atr = abs(entry_price - stop_loss) * 3 if entry_price > 0 else None
        result = self._engine.risk.evaluate(idea, atr=atr)
        approved = result.verdict == RiskVerdict.APPROVED
        return json.dumps({
            "approved": approved,
            "verdict": result.verdict.value,
            "confidence": round(confidence, 3),
            "risk_reward": round(idea.risk_reward_ratio, 2),
            "position_size_usd": result.position_size_usd,
            "checks_passed": len(result.checks_passed),
            "checks_failed": len(result.checks_failed),
            "failed_checks": result.checks_failed,
            "reason": result.reason,
        }, default=str)

    async def _fullscan(self, mode: str = "quick") -> str:
        """Run full 67-symbol scan and return structured results."""
        from bot.skills.scan_skill import UNIVERSE, _scan_symbol, _compute_rsi

        exchange = await self._engine.scanner._get_exchange()
        results = []
        symbols = UNIVERSE[:10] if mode == "quick" else UNIVERSE
        batch_size = 10
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            tasks = [_scan_symbol(exchange, sym) for sym in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in batch_results:
                if isinstance(r, dict) and r is not None:
                    # Serialize pattern dicts for JSON
                    if "patterns" in r:
                        r["patterns"] = [
                            {"name": p["name"], "signal": p["signal"],
                             "confidence": round(p.get("confidence", 0), 2)}
                            for p in r["patterns"][:3]
                        ]
                    results.append(r)
            if i + batch_size < len(symbols):
                await asyncio.sleep(1.0)

        results.sort(key=lambda r: r.get("score", 0), reverse=True)
        top = results[:20] if mode != "quick" else results[:10]
        return json.dumps({
            "mode": mode,
            "total_scanned": len(results),
            "signals": top,
        }, default=str)

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
