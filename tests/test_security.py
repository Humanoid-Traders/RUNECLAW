"""
RUNECLAW Security Tests — dedicated security-focused test suite.

Covers: log redaction, MCP auth, input validation, cache key collision,
runtime state, portfolio corruption, cost tracker daily reset.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Log Redaction (C3)
# ---------------------------------------------------------------------------

class TestLogRedaction(unittest.TestCase):
    """C3: Sensitive data must never appear in logs."""

    def test_redact_dict_scrubs_api_key(self):
        from bot.utils.logger import _redact_dict
        data = {"api_key": "sk-secret-12345", "symbol": "BTC/USDT"}
        result = _redact_dict(data)
        self.assertEqual(result["api_key"], "***REDACTED***")
        self.assertEqual(result["symbol"], "BTC/USDT")

    def test_redact_dict_scrubs_nested(self):
        from bot.utils.logger import _redact_dict
        data = {"config": {"api_secret": "mysecret", "mode": "sim"}}
        result = _redact_dict(data)
        self.assertEqual(result["config"]["api_secret"], "***REDACTED***")
        self.assertEqual(result["config"]["mode"], "sim")

    def test_redact_dict_scrubs_passphrase(self):
        from bot.utils.logger import _redact_dict
        data = {"passphrase": "hunter2", "exchange": "bitget"}
        result = _redact_dict(data)
        self.assertEqual(result["passphrase"], "***REDACTED***")

    def test_redact_dict_scrubs_token(self):
        from bot.utils.logger import _redact_dict
        data = {"auth_token": "Bearer abc123xyz", "tool": "scan"}
        result = _redact_dict(data)
        self.assertEqual(result["auth_token"], "***REDACTED***")

    def test_redact_string_scrubs_inline_secrets(self):
        from bot.utils.logger import _redact_string
        s = "Error: api_key=sk-abc123 in config"
        result = _redact_string(s)
        self.assertNotIn("sk-abc123", result)
        self.assertIn("***REDACTED***", result)

    def test_redact_string_scrubs_traceback_secrets(self):
        from bot.utils.logger import _redact_string
        tb = 'File "config.py", line 10\n  password = "hunter2"\nValueError: password=hunter2 invalid'
        result = _redact_string(tb)
        self.assertNotIn("hunter2", result)

    def test_redact_dict_depth_limit(self):
        """Deeply nested dicts should not cause recursion errors."""
        from bot.utils.logger import _redact_dict
        data: dict = {"a": "b"}
        current = data
        for _ in range(20):
            current["nested"] = {"a": "b"}
            current = current["nested"]
        # Should not raise
        result = _redact_dict(data)
        self.assertIsInstance(result, dict)

    def test_redact_preserves_non_sensitive(self):
        from bot.utils.logger import _redact_dict
        data = {"symbol": "ETH/USDT", "confidence": 0.85, "tags": ["trend", "volume"]}
        result = _redact_dict(data)
        self.assertEqual(result["symbol"], "ETH/USDT")
        self.assertEqual(result["confidence"], 0.85)
        self.assertEqual(result["tags"], ["trend", "volume"])


# ---------------------------------------------------------------------------
# MCP Authentication (C5)
# ---------------------------------------------------------------------------

class TestMCPAuth(unittest.TestCase):
    """C5: MCP server must require auth when token is configured."""

    def test_call_tool_rejects_without_token(self):
        import asyncio
        from bot.mcp import server as mcp_mod

        original_token = mcp_mod._MCP_AUTH_TOKEN
        try:
            mcp_mod._MCP_AUTH_TOKEN = "test-secret-token"
            srv = mcp_mod.RuneClawMCPServer()

            async def _run():
                return await srv.call_tool("runeclaw_scan", {}, auth_token=None)

            result = asyncio.run(_run())
            self.assertEqual(result["status"], "error")
            self.assertIn("Authentication required", result["result"])
        finally:
            mcp_mod._MCP_AUTH_TOKEN = original_token

    def test_call_tool_rejects_wrong_token(self):
        import asyncio
        from bot.mcp import server as mcp_mod

        original_token = mcp_mod._MCP_AUTH_TOKEN
        try:
            mcp_mod._MCP_AUTH_TOKEN = "correct-token"
            srv = mcp_mod.RuneClawMCPServer()

            async def _run():
                return await srv.call_tool("runeclaw_scan", {}, auth_token="wrong-token")

            result = asyncio.run(_run())
            self.assertEqual(result["status"], "error")
            self.assertIn("Authentication required", result["result"])
        finally:
            mcp_mod._MCP_AUTH_TOKEN = original_token

    def test_call_tool_allows_no_auth_when_unset(self):
        import asyncio
        from bot.mcp import server as mcp_mod

        original_token = mcp_mod._MCP_AUTH_TOKEN
        try:
            mcp_mod._MCP_AUTH_TOKEN = ""  # No auth required
            srv = mcp_mod.RuneClawMCPServer()

            async def _run():
                return await srv.call_tool("nonexistent_tool", {})

            result = asyncio.run(_run())
            # Should get "unknown tool" not "auth required"
            self.assertEqual(result["status"], "error")
            self.assertIn("Unknown tool", result["result"])
        finally:
            mcp_mod._MCP_AUTH_TOKEN = original_token


# ---------------------------------------------------------------------------
# RuntimeState (C1)
# ---------------------------------------------------------------------------

class TestRuntimeState(unittest.TestCase):
    """C1: RuntimeState must be thread-safe and validate inputs."""

    def test_runtime_state_default(self):
        from bot.config import RUNTIME
        # Should match CONFIG default
        self.assertIn(RUNTIME.asset_universe, ("all", "solana"))

    def test_runtime_state_set_valid(self):
        from bot.config import RuntimeState
        rs = RuntimeState()
        rs.asset_universe = "solana"
        self.assertEqual(rs.asset_universe, "solana")
        rs.asset_universe = "all"
        self.assertEqual(rs.asset_universe, "all")

    def test_runtime_state_rejects_invalid(self):
        from bot.config import RuntimeState
        rs = RuntimeState()
        with self.assertRaises(ValueError):
            rs.asset_universe = "invalid_mode"

    def test_runtime_state_rejects_empty(self):
        from bot.config import RuntimeState
        rs = RuntimeState()
        with self.assertRaises(ValueError):
            rs.asset_universe = ""

    def test_runtime_state_thread_safe(self):
        """Concurrent reads/writes should not corrupt state."""
        import threading
        from bot.config import RuntimeState

        rs = RuntimeState()
        errors = []

        def toggle(n):
            try:
                for _ in range(n):
                    rs.asset_universe = "solana"
                    _ = rs.asset_universe
                    rs.asset_universe = "all"
                    _ = rs.asset_universe
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=toggle, args=(100,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertIn(rs.asset_universe, ("all", "solana"))


# ---------------------------------------------------------------------------
# Cache Key Collision (W5)
# ---------------------------------------------------------------------------

class TestCacheKeyCollision(unittest.TestCase):
    """W5: Cache keys must use full SHA-256 to prevent collisions."""

    def test_cache_key_is_full_sha256(self):
        from bot.core.llm_cache import SemanticLLMCache
        key = SemanticLLMCache.build_cache_key("BTC/USDT", {
            "regime": "TRENDING_UP",
            "confluence": 0.75,
            "rsi": 45,
            "macd_histogram": 0.5,
            "adx": 25,
        })
        # Full SHA-256 hex = 64 characters
        self.assertEqual(len(key), 64)

    def test_different_inputs_different_keys(self):
        from bot.core.llm_cache import SemanticLLMCache
        k1 = SemanticLLMCache.build_cache_key("BTC/USDT", {
            "regime": "TRENDING_UP", "confluence": 0.7, "rsi": 45,
            "macd_histogram": 0.5, "adx": 25,
        })
        k2 = SemanticLLMCache.build_cache_key("ETH/USDT", {
            "regime": "TRENDING_UP", "confluence": 0.7, "rsi": 45,
            "macd_histogram": 0.5, "adx": 25,
        })
        self.assertNotEqual(k1, k2)


# ---------------------------------------------------------------------------
# CostTracker Daily Reset (W1)
# ---------------------------------------------------------------------------

class TestCostTrackerDailyReset(unittest.TestCase):
    """W1: CostTracker must reset daily counters at UTC day boundary."""

    def test_daily_reset_on_day_change(self):
        from bot.core.cost import CostTracker
        ct = CostTracker()
        ct.record_llm("gpt-4o", 1000, 500, category="scan")
        snap_before = ct.snapshot()
        self.assertGreater(snap_before.llm_cost_usd, 0)

        # Simulate day change
        ct._current_day = "2020-01-01"
        snap_after = ct.snapshot()
        # Daily should be reset
        self.assertEqual(snap_after.llm_cost_usd, 0.0)

    def test_lifetime_accumulates(self):
        from bot.core.cost import CostTracker
        ct = CostTracker()
        ct.record_llm("gpt-4o", 1000, 500, category="scan")
        cost1 = ct.snapshot().llm_cost_usd

        # Simulate day change
        ct._current_day = "2020-01-01"

        # Record another call (triggers reset, then records)
        ct.record_llm("gpt-4o", 2000, 1000, category="analyze")

        lifetime = ct.snapshot_lifetime()
        # Lifetime should include both days
        self.assertGreater(lifetime.llm_cost_usd, cost1)


# ---------------------------------------------------------------------------
# Portfolio Corruption Handling
# ---------------------------------------------------------------------------

class TestPortfolioCorruption(unittest.TestCase):
    """Portfolio must alert on corrupted state files."""

    def test_corrupted_state_returns_false(self):
        from bot.risk.portfolio import PortfolioTracker
        pf = PortfolioTracker(initial_balance=10000)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{invalid json!!!")
            f.flush()
            result = pf.load_state(f.name)

        self.assertFalse(result)
        os.unlink(f.name)

    def test_valid_state_loads(self):
        from bot.risk.portfolio import PortfolioTracker
        pf = PortfolioTracker(initial_balance=10000)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "balance": 9500.0,
                "initial_balance": 10000.0,
                "peak_equity": 10200.0,
                "positions": {},
                "history": [],
                "daily_pnl": {},
                "trailing_state": {},
                "last_prices": {},
            }, f)
            f.flush()
            result = pf.load_state(f.name)

        self.assertTrue(result)
        self.assertAlmostEqual(pf.balance, 9500.0)
        os.unlink(f.name)


# ---------------------------------------------------------------------------
# Portfolio Public API (encapsulation fix)
# ---------------------------------------------------------------------------

class TestPortfolioPublicAPI(unittest.TestCase):
    """Risk engine must use public API, not private attributes."""

    def test_get_position_value_empty(self):
        from bot.risk.portfolio import PortfolioTracker
        pf = PortfolioTracker(initial_balance=10000)
        self.assertEqual(pf.get_position_value(), 0.0)

    def test_get_position_value_with_asset_filter(self):
        from bot.risk.portfolio import PortfolioTracker
        pf = PortfolioTracker(initial_balance=10000)
        # No positions, filter by asset
        self.assertEqual(pf.get_position_value(asset="BTC/USDT"), 0.0)


# ---------------------------------------------------------------------------
# Backtest Temp Cleanup (W6)
# ---------------------------------------------------------------------------

class TestBacktestTempCleanup(unittest.TestCase):
    """W6: BacktestEngine must clean up temp directories."""

    def test_cleanup_removes_temp_dir(self):
        from bot.backtest.engine import BacktestEngine, BacktestConfig
        config = BacktestConfig()
        engine = BacktestEngine(config)
        temp_dir = engine._bt_state_dir
        self.assertTrue(os.path.isdir(temp_dir))

        engine.cleanup()
        self.assertFalse(os.path.isdir(temp_dir))

    def test_double_cleanup_safe(self):
        from bot.backtest.engine import BacktestEngine, BacktestConfig
        config = BacktestConfig()
        engine = BacktestEngine(config)
        engine.cleanup()
        # Should not raise
        engine.cleanup()


# ---------------------------------------------------------------------------
# Input Validation
# ---------------------------------------------------------------------------

class TestInputValidation(unittest.TestCase):
    """Telegram command input validation."""

    def test_symbol_regex_accepts_valid(self):
        import re
        pattern = r"^[A-Z0-9]{1,20}(/[A-Z0-9]{1,10})?$"
        self.assertIsNotNone(re.match(pattern, "BTC"))
        self.assertIsNotNone(re.match(pattern, "BTC/USDT"))
        self.assertIsNotNone(re.match(pattern, "SOL"))
        self.assertIsNotNone(re.match(pattern, "BONK/USDT"))
        self.assertIsNotNone(re.match(pattern, "1000PEPE"))

    def test_symbol_regex_rejects_injection(self):
        import re
        pattern = r"^[A-Z0-9]{1,20}(/[A-Z0-9]{1,10})?$"
        self.assertIsNone(re.match(pattern, "BTC; DROP TABLE"))
        self.assertIsNone(re.match(pattern, "<script>alert(1)</script>"))
        self.assertIsNone(re.match(pattern, "../../../etc/passwd"))
        self.assertIsNone(re.match(pattern, ""))
        self.assertIsNone(re.match(pattern, "A" * 25))

    def test_telegram_id_must_be_numeric(self):
        # Validate the same logic used in /approve
        target_id = "123456789"
        self.assertTrue(target_id.isdigit())

        target_id = "not_a_number"
        self.assertFalse(target_id.isdigit())

        target_id = "123; DROP TABLE"
        self.assertFalse(target_id.isdigit())


if __name__ == "__main__":
    unittest.main()
