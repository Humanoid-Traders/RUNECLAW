"""
Tests for Move 1 (NL Intent Router) and Move 2 (Proactive Alert Monitor).
"""

from __future__ import annotations

import asyncio
import time
import pytest

from bot.nlp.intent_router import IntentRouter, IntentResult, _extract_symbol
from bot.core.proactive_monitor import ProactiveMonitor, Alert


# ── Intent Router Tests ──────────────────────────────────────────────


class TestSymbolExtraction:
    """Test _extract_symbol helper."""

    def test_extract_btc(self):
        assert _extract_symbol("how's BTC doing?") == "BTC/USDT"

    def test_extract_bitcoin_name(self):
        assert _extract_symbol("analyze bitcoin") == "BTC/USDT"

    def test_extract_ethereum_name(self):
        assert _extract_symbol("what about ethereum?") == "ETH/USDT"

    def test_extract_solana(self):
        assert _extract_symbol("check SOL") == "SOL/USDT"

    def test_extract_dollar_sign(self):
        assert _extract_symbol("thoughts on $ETH") == "ETH/USDT"

    def test_extract_explicit_pair(self):
        assert _extract_symbol("analyze BTC/USDT") == "BTC/USDT"

    def test_no_symbol(self):
        assert _extract_symbol("hello world") is None

    def test_case_insensitive(self):
        assert _extract_symbol("how's btc?") == "BTC/USDT"

    def test_doge(self):
        assert _extract_symbol("buy doge") == "DOGE/USDT"

    def test_dogecoin_name(self):
        assert _extract_symbol("what about dogecoin") == "DOGE/USDT"


class TestIntentRouterRules:
    """Test rule-based intent classification."""

    def setup_method(self):
        self.router = IntentRouter()

    def test_scan_market(self):
        result = self.router.classify_rules("what's moving in the market?")
        assert result.matched
        assert result.skill == "scan_market"
        assert result.confidence == 1.0

    def test_scan_top_movers(self):
        result = self.router.classify_rules("show me top movers")
        assert result.skill == "scan_market"

    def test_analyze_btc(self):
        result = self.router.classify_rules("analyze BTC")
        assert result.skill == "analyze_asset"
        assert result.kwargs.get("symbol") == "BTC/USDT"
        assert result.confidence == 1.0

    def test_analyze_no_symbol(self):
        result = self.router.classify_rules("analyze the charts")
        assert result.skill == "analyze_asset"
        assert result.confidence == 0.5  # partial match
        assert "symbol" not in result.kwargs

    def test_portfolio(self):
        result = self.router.classify_rules("show my portfolio")
        assert result.skill == "get_portfolio"

    def test_pnl(self):
        result = self.router.classify_rules("what's my pnl?")
        assert result.skill == "get_portfolio"

    def test_risk_check(self):
        result = self.router.classify_rules("check risk status")
        assert result.skill == "check_risk"

    def test_status(self):
        result = self.router.classify_rules("what is the bot status?")
        assert result.skill == "status"

    def test_journal(self):
        result = self.router.classify_rules("show trade history")
        assert result.skill == "trade_journal"

    def test_macro(self):
        result = self.router.classify_rules("when is the next FOMC?")
        assert result.skill == "macro_calendar"

    def test_backtest(self):
        result = self.router.classify_rules("run a backtest")
        assert result.skill == "run_backtest"

    def test_costs(self):
        result = self.router.classify_rules("how much am I spending?")
        assert result.skill == "costs"

    def test_help(self):
        result = self.router.classify_rules("help me")
        assert result.skill == "help"

    def test_halt(self):
        result = self.router.classify_rules("stop everything")
        assert result.skill == "halt"

    def test_no_match(self):
        result = self.router.classify_rules("tell me a joke please")
        assert not result.matched

    def test_should_i_buy(self):
        result = self.router.classify_rules("should I buy SOL?")
        assert result.skill == "analyze_asset"
        assert result.kwargs.get("symbol") == "SOL/USDT"

    def test_how_is_ethereum(self):
        result = self.router.classify_rules("how's ethereum doing")
        assert result.skill == "analyze_asset"
        assert result.kwargs.get("symbol") == "ETH/USDT"


class TestIntentRouterAsync:
    """Test async classify with LLM fallback."""

    def setup_method(self):
        self.router = IntentRouter()

    @pytest.mark.asyncio
    async def test_classify_rules_hit(self):
        result = await self.router.classify("scan the market")
        assert result.skill == "scan_market"
        assert result.source == "rules"

    @pytest.mark.asyncio
    async def test_classify_no_match_no_llm(self):
        result = await self.router.classify("tell me a joke")
        assert not result.matched

    @pytest.mark.asyncio
    async def test_classify_llm_fallback(self):
        async def mock_llm(prompt):
            return "scan_market"

        result = await self.router.classify("yo what coins are popping", llm_fn=mock_llm)
        assert result.skill == "scan_market"
        assert result.source == "llm"
        assert result.confidence == 0.7

    @pytest.mark.asyncio
    async def test_classify_llm_returns_none(self):
        async def mock_llm(prompt):
            return "NONE"

        result = await self.router.classify("tell me a joke", llm_fn=mock_llm)
        assert not result.matched

    @pytest.mark.asyncio
    async def test_classify_llm_error_handled(self):
        async def mock_llm(prompt):
            raise RuntimeError("API down")

        result = await self.router.classify("anything interesting", llm_fn=mock_llm)
        assert not result.matched  # graceful fallback


class TestIntentResult:
    def test_matched_true(self):
        r = IntentResult(skill="scan_market")
        assert r.matched

    def test_matched_false(self):
        r = IntentResult(skill="")
        assert not r.matched

    def test_defaults(self):
        r = IntentResult(skill="test")
        assert r.confidence == 0.0
        assert r.source == "rules"
        assert r.kwargs == {}


# ── Proactive Monitor Tests ─────────────────────────────────────────


class FakeRisk:
    circuit_breaker_active = False


class FakeEngine:
    def __init__(self):
        self.risk = FakeRisk()
        self.state = "IDLE"
        self._pending_ideas = {}
        self._last_scan_signals = []


class TestProactiveMonitor:
    def setup_method(self):
        self.engine = FakeEngine()
        self.monitor = ProactiveMonitor(self.engine)

    def test_enable_disable_chat(self):
        self.monitor.enable_chat("123")
        assert self.monitor.is_enabled("123")
        assert self.monitor.enabled_chat_count == 1

        self.monitor.disable_chat("123")
        assert not self.monitor.is_enabled("123")
        assert self.monitor.enabled_chat_count == 0

    def test_disable_nonexistent_chat(self):
        self.monitor.disable_chat("999")  # should not raise

    def test_circuit_breaker_trip_alert(self):
        self.engine.risk.circuit_breaker_active = True
        alerts = self.monitor._check_circuit_breaker()
        assert len(alerts) == 1
        assert alerts[0].alert_type == "CIRCUIT_BREAKER"
        assert alerts[0].severity == "CRITICAL"

    def test_circuit_breaker_clear_alert(self):
        # First trip it
        self.engine.risk.circuit_breaker_active = True
        self.monitor._check_circuit_breaker()
        # Then clear it
        self.engine.risk.circuit_breaker_active = False
        alerts = self.monitor._check_circuit_breaker()
        assert len(alerts) == 1
        assert alerts[0].severity == "INFO"
        assert "Cleared" in alerts[0].title

    def test_no_alert_when_unchanged(self):
        alerts = self.monitor._check_circuit_breaker()
        assert len(alerts) == 0

    def test_state_change_halted(self):
        self.monitor._last_state = "IDLE"
        self.engine.state = "HALTED"
        alerts = self.monitor._check_state_changes()
        assert len(alerts) == 1
        assert alerts[0].alert_type == "STATE_CHANGE"
        assert alerts[0].severity == "CRITICAL"

    def test_state_change_cooldown(self):
        self.monitor._last_state = "IDLE"
        self.engine.state = "COOLING_DOWN"
        alerts = self.monitor._check_state_changes()
        assert len(alerts) == 1
        assert alerts[0].severity == "WARNING"

    def test_state_change_ignored_normal(self):
        self.monitor._last_state = "IDLE"
        self.engine.state = "SCANNING"
        alerts = self.monitor._check_state_changes()
        assert len(alerts) == 0

    def test_dedup_prevents_repeat(self):
        self.monitor.enable_chat("123")
        alert = Alert(
            alert_type="TEST", severity="INFO",
            title="Test", body="test", dedup_key="test_key")
        assert self.monitor._should_send(alert)
        self.monitor._mark_sent(alert)
        assert not self.monitor._should_send(alert)  # dedup blocks

    def test_dedup_no_key_always_sends(self):
        self.monitor.enable_chat("123")
        alert = Alert(
            alert_type="TEST", severity="INFO",
            title="Test", body="test", dedup_key="")
        assert self.monitor._should_send(alert)
        self.monitor._mark_sent(alert)
        assert self.monitor._should_send(alert)  # no dedup key

    def test_no_send_without_enabled_chats(self):
        alert = Alert(
            alert_type="TEST", severity="INFO",
            title="Test", body="test")
        assert not self.monitor._should_send(alert)

    def test_dedup_cache_pruning(self):
        self.monitor.enable_chat("123")
        # Fill dedup cache beyond 200
        for i in range(210):
            self.monitor._dedup_cache[f"key_{i}"] = time.monotonic()
        alert = Alert(
            alert_type="TEST", severity="INFO",
            title="Test", body="test", dedup_key="prune_trigger")
        self.monitor._mark_sent(alert)
        assert len(self.monitor._dedup_cache) <= 201  # pruned to ~101

    def test_alerted_signals_pruning(self):
        for i in range(510):
            self.monitor._alerted_signals.add(f"sig_{i}")
        alert = Alert(
            alert_type="TEST", severity="INFO",
            title="Test", body="test")
        self.monitor._mark_sent(alert)
        assert len(self.monitor._alerted_signals) == 0  # cleared at 500+

    def test_stop(self):
        self.monitor._running = True
        self.monitor.stop()
        assert not self.monitor._running

    @pytest.mark.asyncio
    async def test_dispatch_sends_to_enabled_chats(self):
        sent = []

        async def mock_send(chat_id, text):
            sent.append((chat_id, text))

        self.monitor.enable_chat("111")
        self.monitor.enable_chat("222")
        alert = Alert(
            alert_type="TEST", severity="WARNING",
            title="Test", body="Test body")
        await self.monitor._dispatch(alert, mock_send)
        assert len(sent) == 2
        assert sent[0][0] in ("111", "222")

    def test_check_all_returns_list(self):
        alerts = self.monitor._check_all()
        assert isinstance(alerts, list)


class TestAlert:
    def test_alert_creation(self):
        a = Alert(
            alert_type="TEST", severity="INFO",
            title="Title", body="Body")
        assert a.alert_type == "TEST"
        assert a.dedup_key == ""

    def test_alert_with_dedup_key(self):
        a = Alert(
            alert_type="TEST", severity="CRITICAL",
            title="Title", body="Body", dedup_key="my_key")
        assert a.dedup_key == "my_key"
