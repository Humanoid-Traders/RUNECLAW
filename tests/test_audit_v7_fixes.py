"""
Regression tests for the V7 audit remediation (docs/AUDIT_REPORT_V7.md).

Wave 1 — safe, fail-closed fixes:
  F-1  Blocked live trades must NOT be recorded as successful fills. The engine
       classifies execute()'s result via a single centralized classifier that
       lives next to the return strings, so the producer/consumer can't drift.
  F-5  adopt_exchange_limit_orders referenced CONFIG.exchange.leverage (which
       does not exist) — must use default_leverage.
  F-6  NaN/inf entry/SL/TP must be rejected at TradeIdea construction and by the
       risk engine's entry/SL checks.
  F-7  A future-dated idea timestamp must be rejected by the stale-data guard
       (negative age previously passed silently).
  F-14 The SIMULATION_MODE veto must run before any exchange-mutating side
       effect (pyramid SL->breakeven) and before the EXECUTING transition.
"""

import math
from datetime import datetime, timedelta

import pytest

from bot.compat import UTC
from bot.utils.models import TradeIdea, Direction


# ── F-1: execution result classification ─────────────────────────────

class TestExecutionFailureClassifier:
    def test_block_strings_classified_as_failure(self):
        from bot.core.live_executor import execution_indicates_failure as f
        # Every no-position outcome execute() can return — including the
        # emoji/HTML-prefixed ones that startswith() could never match.
        failures = [
            "REFUSED: position persistence is broken — cannot open new trades",
            "Live execution blocked: live mode was deactivated before order placement.",
            "EXECUTION BLOCKED: system is in degraded mode (paused) — WebSocket disconnected",
            "EXECUTION BLOCKED: system is in reduce-only mode — too many API errors",
            "EXECUTION FAILED: exchange returned no price for BTC/USDT",
            "INSUFFICIENT FUNDS: not enough margin",
            "INVALID ORDER: bad params",
            "BLOCKED: quantity too small after precision rounding for BTC/USDT",
            "PREFLIGHT FAILED: micro cap exceeded",
            "⚠️ <b>EXECUTION ABORTED — BTC/USDT</b>\nPosition opened but the stop-loss "
            "could not be placed, so it was CLOSED for safety.",
        ]
        for s in failures:
            assert f(s), f"should be classified as failure: {s!r}"

    def test_real_fills_classified_as_success(self):
        from bot.core.live_executor import execution_indicates_failure as f
        # A real live position resulted — must NOT be treated as a failure
        # (else the engine would retry and open a second position).
        successes = [
            "LIVE LONG BTC/USDT opened @ $65000 size $100",
            "🟢 <b>LIMIT ORDER BUY BTC/USDT</b> (LIVE 5x) [swing]",
            # Unprotected-but-live emergency case: the position EXISTS, so it must
            # be recorded/tracked, not retried.
            "🚨 <b>URGENT — BTC/USDT is LIVE with NO stop-loss</b>\n"
            "Automatic close also FAILED (timeout). Close this position MANUALLY.",
        ]
        for s in successes:
            assert not f(s), f"should be classified as success: {s!r}"

    def test_non_string_is_failure(self):
        from bot.core.live_executor import execution_indicates_failure as f
        assert f(None) is True


# ── F-5: leverage attribute exists ───────────────────────────────────

class TestLeverageAttribute:
    def test_exchange_config_uses_default_leverage(self):
        from bot.config import CONFIG
        # default_leverage is the real field; `leverage` does not exist.
        assert hasattr(CONFIG.exchange, "default_leverage")
        assert not hasattr(CONFIG.exchange, "leverage")

    def test_adopt_limit_orders_source_uses_default_leverage(self):
        # Guard against the exact regression: the adoption path must reference
        # default_leverage, never the non-existent `leverage` attribute.
        import inspect
        import bot.core.live_executor as le
        src = inspect.getsource(le.LiveExecutor.adopt_exchange_limit_orders)
        assert "CONFIG.exchange.default_leverage" in src
        assert "CONFIG.exchange.leverage" not in src


# ── F-6: non-finite price rejection ──────────────────────────────────

class TestNonFinitePrices:
    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_nan_inf_entry_rejected_at_construction(self, bad):
        with pytest.raises(Exception):
            TradeIdea(
                asset="BTC/USDT", direction=Direction.LONG,
                entry_price=bad, stop_loss=95.0, take_profit=115.0,
                confidence=0.8, reasoning="x", source="t",
            )

    @pytest.mark.parametrize("field", ["stop_loss", "take_profit"])
    def test_nan_sl_tp_rejected_at_construction(self, field):
        kwargs = dict(
            asset="BTC/USDT", direction=Direction.LONG,
            entry_price=100.0, stop_loss=95.0, take_profit=115.0,
            confidence=0.8, reasoning="x", source="t",
        )
        kwargs[field] = float("nan")
        with pytest.raises(Exception):
            TradeIdea(**kwargs)

    def test_risk_engine_rejects_nonfinite_entry_defensively(self):
        # Defense-in-depth: even an idea built via model_construct (bypassing the
        # validator) must be rejected by risk check #10.
        from bot.risk.risk_engine import RiskEngine
        from bot.risk.portfolio import PortfolioTracker
        import os, tempfile
        idea = TradeIdea.model_construct(
            id="TI-NAN", asset="BTC/USDT", direction=Direction.LONG,
            entry_price=float("nan"), stop_loss=float("nan"), take_profit=float("nan"),
            confidence=0.8, reasoning="x", source="t",
            timestamp=datetime.now(UTC), order_type="market",
            strategy_type="swing", signal_type="momentum_confluence",
            signals_used=[],
        )
        eng = RiskEngine(PortfolioTracker(initial_balance=10_000.0),
                         state_file=os.path.join(tempfile.mkdtemp(), "s.json"))
        chk = eng.evaluate(idea, atr=1.0)
        assert chk.verdict.value == "REJECTED"
        assert any("ENTRY_PRICE: invalid" in c for c in chk.checks_failed)


# ── F-7: future-dated timestamp rejection ────────────────────────────

class TestFutureTimestampGuard:
    def _engine(self):
        from bot.risk.risk_engine import RiskEngine
        from bot.risk.portfolio import PortfolioTracker
        import os, tempfile
        return RiskEngine(PortfolioTracker(initial_balance=10_000.0),
                          state_file=os.path.join(tempfile.mkdtemp(), "s.json"))

    def _idea(self, ts):
        return TradeIdea(
            asset="BTC/USDT", direction=Direction.LONG,
            entry_price=100.0, stop_loss=95.0, take_profit=115.0,
            confidence=0.8, reasoning="x", source="t", timestamp=ts,
        )

    def test_far_future_timestamp_rejected(self):
        eng = self._engine()
        chk = eng.evaluate(self._idea(datetime.now(UTC) + timedelta(hours=5)), atr=1.0)
        assert any("FUTURE" in c for c in chk.checks_failed)
        assert chk.verdict.value == "REJECTED"

    def test_small_skew_still_passes_staleness(self):
        # A few seconds of forward skew is tolerated (not flagged future/stale).
        eng = self._engine()
        chk = eng.evaluate(self._idea(datetime.now(UTC) + timedelta(seconds=5)), atr=1.0)
        assert not any("FUTURE" in c for c in chk.checks_failed)
        assert any("STALE_DATA" in c and "OK" in c for c in chk.checks_passed)


# ── F-9 / F-10: executor robustness (source guards) ──────────────────

class TestExecutorRobustness:
    def test_v3_sltp_no_longer_uses_placeholder_order_id(self):
        # F-9: a success code with no order id must not be stored as a placed
        # stop; the "v3-strategy" sentinel must be gone.
        import inspect
        import bot.core.live_executor as le
        src = inspect.getsource(le)
        # The sentinel must not be used as a fallback order id (it may still be
        # mentioned in an explanatory comment, so match the assignment form).
        assert 'or "v3-strategy"' not in src
        assert 'order_id = "v3-strategy"' not in src
        # The no-order-id failure branch must exist.
        assert "NO_ORDER_ID" in src

    def test_post_only_retry_reverifies_before_resubmit(self):
        # F-10: before resubmitting with a fresh clientOid, the code must
        # re-verify the original isn't resting (index-lag double-fill guard).
        import inspect
        import bot.core.live_executor as le
        src = inspect.getsource(le.LiveExecutor.execute)
        assert "ABORT_UNVERIFIED_RECHECK" in src


# ── F-14: sim-veto ordering ──────────────────────────────────────────

class TestSimVetoOrdering:
    def test_sim_veto_precedes_exchange_mutation_in_source(self):
        # The SIMULATION_MODE veto must appear BEFORE the EXECUTING transition
        # and the pyramid SL->breakeven _update_exchange_sl side effect.
        import inspect
        from bot.core.engine import RuneClawEngine
        src = inspect.getsource(RuneClawEngine.confirm_trade)
        veto_idx = src.index("_live_execution_vetoed_by_simulation")
        exec_idx = src.index('AgentState.EXECUTING, f"executing LIVE trade')
        # Match the actual call (not the explanatory comment that mentions it).
        mutate_idx = src.index("live_executor._update_exchange_sl(")
        assert veto_idx < exec_idx, "sim veto must run before EXECUTING transition"
        assert veto_idx < mutate_idx, "sim veto must run before exchange SL mutation"
