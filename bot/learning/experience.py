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
        blended_confidence_raw: float = 0.0,
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
        confluence_votes: Optional[list] = None,
    ) -> DecisionMemory:
        """Record a complete trading decision."""
        record = DecisionMemory(
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            blended_confidence_raw=blended_confidence_raw,
            confluence_score=confluence_score,
            confluence_votes=confluence_votes or [],
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

    def record_closed_outcome(
        self,
        *,
        symbol: str,
        direction: str,
        pnl_result: float,
        market_regime: str = "",
        trade_id: str = "",
        source: str = "live_outcome",
    ) -> DecisionMemory:
        """Record a CLOSED-trade outcome as a COMPLETE, queryable record.

        The legacy ``record_trade_result`` appended a result-only record with no
        symbol/direction, which ``get_similar_setups`` (which filters by
        symbol + direction + non-null pnl) could never match — leaving the
        learning loop open. This writes the symbol, direction, regime AND the
        realized pnl together so similar-setup lookups actually find it.

        ``source`` tags the record as ``"live_outcome"`` (default) or
        ``"paper_outcome"`` so live and paper evidence can be distinguished (and
        weighted) by consumers; the default keeps the live caller byte-identical.
        """
        record = DecisionMemory(
            source=source or "live_outcome",
            symbol=symbol,
            direction=direction,
            market_regime=market_regime or "",
            pnl_result=pnl_result,
            decision=f"OUTCOME:{trade_id}" if trade_id else "OUTCOME",
            paper_trade_id=trade_id,
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
            # Regime/direction are optional filters (empty = match any). With the
            # sparse data a live bot accumulates, scoping by symbol+direction
            # already isolates the dominant signal (e.g. longs-on-X losing);
            # requiring an exact regime match would starve the sample.
            if (not market_regime or d.market_regime == market_regime)
            and (not direction or d.direction == direction)
            and d.pnl_result is not None  # only completed trades
        ]
        return similar[-limit:]

    def get_rejection_patterns(self, symbol: Optional[str] = None, limit: int = 50) -> list[DecisionMemory]:
        """Get rejected trades to learn from."""
        decisions = self._store.get_decisions(symbol=symbol, limit=500)
        return [d for d in decisions if d.risk_engine_result == "REJECTED"][-limit:]
