"""RUNECLAW AI Learning — Experience Memory Module.

Records every observation, prediction, rejection, simulation, and result.
This is the raw data layer — no interpretation, just facts.
"""

from __future__ import annotations

import logging
from typing import Optional

from .models import DecisionMemory
from .store import LearningStore

logger = logging.getLogger("runeclaw.learning.experience")


class ExperienceMemory:
    """Append-only experience recorder.

    Every market observation and trading decision flows through here.
    No secrets. No PII. Every record has audit_id + timestamp + source.
    """

    def __init__(self, store: LearningStore):
        self._store = store

    def record_trade_decision(
        self,
        *,
        symbol: str,
        direction: str,
        confidence: float,
        confluence_score: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        risk_reward: float,
        position_size_usd: float,
        market_regime: str = "",
        macro_state: str = "",
        volatility_state: str = "",
        funding_state: str = "",
        oi_state: str = "",
        liquidity_state: str = "",
        strategy_signal: str = "",
        risk_engine_result: str = "",
        checks_passed: Optional[list[str]] = None,
        checks_failed: Optional[list[str]] = None,
        rejected_reason: str = "",
        decision: str = "",
        paper_trade_id: str = "",
        prompt_version: str = "v1",
        strategy_version: str = "v1",
        risk_engine_version: str = "v1",
        mode: str = "paper",
    ) -> DecisionMemory:
        """Record a complete trading decision."""
        record = DecisionMemory(
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            confluence_score=confluence_score,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward=risk_reward,
            position_size_usd=position_size_usd,
            market_regime=market_regime,
            macro_state=macro_state,
            volatility_state=volatility_state,
            funding_state=funding_state,
            oi_state=oi_state,
            liquidity_state=liquidity_state,
            strategy_signal=strategy_signal,
            risk_engine_result=risk_engine_result,
            checks_passed=checks_passed or [],
            checks_failed=checks_failed or [],
            rejected_reason=rejected_reason,
            decision=decision,
            paper_trade_id=paper_trade_id,
            prompt_version=prompt_version,
            strategy_version=strategy_version,
            risk_engine_version=risk_engine_version,
            mode=mode,
        )
        self._store.record_decision(record)
        return record

    def record_trade_result(
        self,
        decision_audit_id: str,
        *,
        pnl_result: float,
        gross_pnl: float,
        commission: float,
        max_drawdown: float = 0.0,
        slippage: float = 0.0,
        post_trade_review: str = "",
    ) -> None:
        """Update a decision record with trade result.

        Since JSONL is append-only, we append a result record
        that references the original decision audit_id.
        """
        result_record = DecisionMemory(
            source="trade_result",
            decision=f"RESULT_FOR:{decision_audit_id}",
            pnl_result=pnl_result,
            gross_pnl=gross_pnl,
            commission=commission,
            max_drawdown=max_drawdown,
            slippage=slippage,
            post_trade_review=post_trade_review,
        )
        self._store.record_decision(result_record)

    def get_similar_setups(
        self,
        symbol: str,
        market_regime: str,
        direction: str,
        limit: int = 10,
    ) -> list[DecisionMemory]:
        """Find similar past setups for learning context."""
        decisions = self._store.get_decisions(symbol=symbol, limit=500)
        similar = [
            d for d in decisions
            if d.market_regime == market_regime
            and (not direction or d.direction == direction)
            and d.pnl_result is not None  # only completed trades
        ]
        return similar[-limit:]

    def get_rejection_patterns(self, symbol: Optional[str] = None, limit: int = 50) -> list[DecisionMemory]:
        """Get rejected trades to learn from."""
        decisions = self._store.get_decisions(symbol=symbol, limit=500)
        return [d for d in decisions if d.risk_engine_result == "REJECTED"][-limit:]
