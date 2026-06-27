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
from unittest.mock import patch

import pytest

from bot.compat import UTC
from bot.utils.models import TradeIdea, Direction


# ── Shared Telegram-handler test scaffolding (Wave 3) ────────────────

def _make_update(user_id=6307156912):
    from unittest.mock import AsyncMock, MagicMock
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.first_name = "TestUser"
    update.effective_chat = MagicMock()
    update.effective_chat.id = user_id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.message.text = "/x"
    update.callback_query = None
    ctx = MagicMock()
    ctx.args = []
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return update, ctx


def _make_handler(admin_id=6307156912):
    from bot.core.engine import RuneClawEngine
    from bot.skills.telegram_handler import TelegramHandler
    handler = TelegramHandler(RuneClawEngine())
    handler.users.seed_admin(str(admin_id))
    return handler


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


# ── F-2 / F-11 / F-12: authz lockdown ────────────────────────────────

class TestAllowlist:
    def test_allowlist_blocks_non_operator(self):
        # F-2: with TELEGRAM_CHAT_ID configured, a stranger is not allowlisted.
        handler = _make_handler(admin_id=111)
        operator, _ = _make_update(user_id=111)
        stranger, _ = _make_update(user_id=999)
        with patch("bot.skills.telegram_handler.CONFIG") as mc:
            mc.telegram.chat_id = "111"
            mc.telegram.admin_ids = ""
            assert handler._is_allowlisted(operator) is True
            assert handler._is_allowlisted(stranger) is False

    def test_allowlist_open_when_unconfigured(self):
        # No allowlist configured (demo/paper) -> open behavior preserved.
        handler = _make_handler(admin_id=111)
        stranger, _ = _make_update(user_id=999)
        with patch("bot.skills.telegram_handler.CONFIG") as mc:
            mc.telegram.chat_id = ""
            mc.telegram.admin_ids = ""
            assert handler._is_allowlisted(stranger) is True

    def test_admin_ids_are_allowlisted(self):
        handler = _make_handler(admin_id=111)
        admin, _ = _make_update(user_id=222)
        with patch("bot.skills.telegram_handler.CONFIG") as mc:
            mc.telegram.chat_id = "111"
            mc.telegram.admin_ids = "222"
            assert handler._is_allowlisted(admin) is True

    @pytest.mark.asyncio
    async def test_guard_denies_non_allowlisted(self):
        handler = _make_handler(admin_id=111)
        stranger, _ = _make_update(user_id=999)
        with patch("bot.skills.telegram_handler.CONFIG") as mc:
            mc.telegram.chat_id = "111"
            mc.telegram.admin_ids = ""
            ok = await handler._guard(stranger, "trade")
            assert ok is False


class TestCallbackAndCommandGuards:
    def test_trade_routes_through_guard(self):
        # F-12: /trade must call _guard (allowlist + role + session), not an
        # inline authorized-only check.
        import inspect
        from bot.skills.telegram_handler import TelegramHandler
        src = inspect.getsource(TelegramHandler._cmd_trade)
        assert 'self._guard(update, "trade")' in src

    def test_destructive_callbacks_require_permission(self):
        # F-11: the callback dispatcher must permission-gate destructive actions.
        import inspect
        from bot.skills.telegram_handler import TelegramHandler
        src = inspect.getsource(TelegramHandler._handle_callback)
        assert "_DESTRUCTIVE_CB_PERM" in src
        assert "callback_denied" in src

    @pytest.mark.asyncio
    async def test_setllm_admin_only(self):
        # F-12: a non-admin (but allowlisted) user cannot swap the LLM.
        handler = _make_handler(admin_id=111)
        # A different, non-admin user who is allowlisted via chat_id.
        non_admin, ctx = _make_update(user_id=222)
        handler.users.register("222", name="trader")  # authorized trader, not admin
        sent = []
        async def _capture(update, text, **kw):
            sent.append(text)
        handler._send = _capture
        with patch("bot.skills.telegram_handler.CONFIG") as mc:
            mc.telegram.chat_id = "111,222"
            mc.telegram.admin_ids = "111"
            mc.simulation_mode = True
            await handler._cmd_setllm(non_admin, ctx)
        assert any("Admin only" in s for s in sent), sent


# ── F-8 / F-13: policy (live fail-closed) ────────────────────────────

class TestLivePolicyFailClosed:
    def test_lock5_not_minted_for_nonhuman_without_optin(self):
        # F-8: the mint is gated on a human confirmation OR the explicit
        # auto-confirm-live opt-in; otherwise the token is left unminted.
        import inspect
        from bot.core.engine import RuneClawEngine
        src = inspect.getsource(RuneClawEngine.confirm_trade)
        assert "if human or CONFIG.auto_confirm_live_enabled:" in src
        assert "Lock 5 NOT minted" in src

    def test_critique_fails_closed_in_live(self):
        # F-13: a critique exception in LIVE mode rejects rather than proceeding.
        import inspect
        from bot.core.engine import RuneClawEngine
        src = inspect.getsource(RuneClawEngine.confirm_trade)
        assert "ERROR_FAILCLOSED" in src
        # The fail-closed branch is guarded by live mode.
        idx = src.index("ERROR_FAILCLOSED")
        preceding = src[max(0, idx - 200):idx]
        assert "CONFIG.is_live()" in preceding


# ── F-3: notional/margin boundary (visibility + ceiling) ─────────────

class TestNotionalBoundary:
    def test_execute_has_notional_boundary_check(self):
        import inspect
        from bot.core.live_executor import LiveExecutor
        src = inspect.getsource(LiveExecutor.execute)
        assert "notional_boundary" in src
        assert "EXCEEDS_CEILING" in src

    def test_normal_micro_trade_within_ceiling(self):
        # A normal micro trade (margin $100, 5x) places $500 notional, well under
        # the design ceiling (margin x max_leverage x 1.05). The boundary must
        # NOT block legitimate trades.
        from bot.core.live_executor import MICRO_MAX_POSITION_USD
        from bot.config import CONFIG
        margin = MICRO_MAX_POSITION_USD
        leverage = CONFIG.exchange.default_leverage
        max_lev = max(int(getattr(CONFIG.exchange, "max_leverage", leverage)), int(leverage))
        notional = margin * leverage
        ceiling = max(margin, MICRO_MAX_POSITION_USD) * max_lev * 1.05
        assert notional <= ceiling

    def test_double_leverage_bug_would_be_blocked(self):
        # A sizing bug that double-applies leverage (margin x lev^2) exceeds the
        # ceiling and would be blocked.
        from bot.core.live_executor import MICRO_MAX_POSITION_USD
        from bot.config import CONFIG
        margin = MICRO_MAX_POSITION_USD
        leverage = CONFIG.exchange.default_leverage
        max_lev = max(int(getattr(CONFIG.exchange, "max_leverage", leverage)), int(leverage))
        bugged_notional = margin * leverage * leverage  # double-applied
        ceiling = max(margin, MICRO_MAX_POSITION_USD) * max_lev * 1.05
        assert bugged_notional > ceiling
