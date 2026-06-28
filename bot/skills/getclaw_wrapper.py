"""
GetClaw SDK Adapter for RUNECLAW — Bitget GetClaw Hackathon Integration.

Bridges RUNECLAW's risk engine and learning layer into the official
Bitget GetClaw SDK so external consumers can call evaluate_signal()
and receive a standardised risk verdict without touching internals.
"""

from __future__ import annotations

from bot.core.engine import RuneClawEngine
from bot.utils.models import Direction, RiskVerdict, TradeIdea


class GetClawAdapter:
    """Thin adapter exposing RUNECLAW risk + learning to GetClaw SDK."""

    def __init__(self, engine: RuneClawEngine) -> None:
        self._engine = engine

    async def evaluate_signal(
        self,
        symbol: str,
        direction: str,
        entry: float,
        stop: float,
        take_profit: float,
    ) -> dict:
        """Run all 18 risk-engine checks against a proposed signal.

        Returns a dict ready for downstream consumption with verdict,
        sizing, risk-reward, and an optional quant score.
        """
        idea = TradeIdea(
            asset=symbol,
            direction=Direction(direction.upper()),
            entry_price=entry,
            stop_loss=stop,
            take_profit=take_profit,
            confidence=0.7,
            reasoning="GetClaw SDK signal",
            source="getclaw",
        )
        rc = self._engine.risk.evaluate(idea)
        quant = None
        if hasattr(self._engine, "quant") and self._engine.quant is not None:
            try:
                quant = await self._engine.quant.score(symbol)
            except Exception:
                pass
        return {
            "approved": rc.verdict == RiskVerdict.APPROVED,
            "position_size_usd": rc.position_size_usd,
            "risk_reward": idea.risk_reward_ratio,
            "checks_passed": rc.checks_passed,
            "checks_failed": rc.checks_failed,
            "quant_score": quant,
        }

    async def get_learning_status(self) -> dict:
        """Return learning module health snapshot, if available."""
        if not hasattr(self._engine, "learning"):
            return {"available": False}
        try:
            dash = self._engine.learning.dashboard()
            return {"available": True, **dash}
        except Exception as exc:
            return {"available": False, "error": str(exc)}

    def to_getclaw_format(self, result: dict) -> dict:
        """Re-shape an evaluate_signal result for GetClaw SDK consumption."""
        return {
            "action": "ENTER" if result["approved"] else "SKIP",
            "size_usd": result["position_size_usd"],
            "rr_ratio": result["risk_reward"],
            "quant_score": result["quant_score"],
            "risk_flags": result["checks_failed"],
        }


def register_getclaw_wrapper(registry) -> None:
    """Register the adapter as a discoverable skill in RUNECLAW."""
    from bot.skills.skill_registry import BaseSkill

    class _GetClawSkill(BaseSkill):
        name = "getclaw"
        description = "GetClaw SDK integration bridge"

        async def execute(self, engine, **kw):
            adapter = GetClawAdapter(engine)
            status = await adapter.get_learning_status()
            return f"GetClaw adapter ready — learning: {status.get('available')}"

    registry.register(_GetClawSkill())
