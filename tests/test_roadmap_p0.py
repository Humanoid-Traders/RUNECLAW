"""
Regression tests for the P0 safety sweep (docs/IMPROVEMENT_ROADMAP.md).

P0-1  Alert dedup must SEND the first alert for a key (the sentinel-0 vs
      time.monotonic() bug suppressed it for the first ~5 min of uptime).
P0-2  The slippage guard config must actually be enforced in execution.
P0-3  Oversized orders must be BLOCKED while tranching is unimplemented (not
      silently single-filled while logging "SPLITTING").
P0-4  .env.example risk defaults must match the in-code RiskLimits defaults.
P0-6  The engine main loop must back off on repeated tick failures.
"""

import re
import inspect

import pytest

from bot.config import CONFIG


# ── P0-1: dedup sends the first alert ────────────────────────────────

class TestDedupFirstAlert:
    def _monitor(self):
        from bot.core.proactive_monitor import ProactiveMonitor
        from unittest.mock import MagicMock
        m = ProactiveMonitor(engine=MagicMock())
        m.enable_chat("123")
        return m

    def test_first_alert_for_key_is_sent(self):
        from bot.core.proactive_monitor import Alert
        m = self._monitor()
        alert = Alert(alert_type="CB", severity="CRITICAL",
                      title="t", body="b", dedup_key="cb_tripped")
        # Fresh monitor, key never seen -> must send (was suppressed by the
        # sentinel-0 bug for the first ~5 min of process uptime).
        assert m._should_send(alert) is True

    def test_repeat_within_cooldown_suppressed(self):
        from bot.core.proactive_monitor import Alert
        m = self._monitor()
        alert = Alert(alert_type="CB", severity="CRITICAL",
                      title="t", body="b", dedup_key="cb_tripped")
        assert m._should_send(alert) is True
        m._mark_sent(alert)
        assert m._should_send(alert) is False  # within cooldown


# ── P0-2: slippage guard is wired ────────────────────────────────────

class TestSlippageGuardWired:
    def test_config_is_consumed_not_just_defined(self):
        import bot.core.live_executor as le
        src = inspect.getsource(le.LiveExecutor.execute)
        assert "slippage_guard_enabled" in src
        assert "max_slippage_edge_ratio" in src
        assert "slippage_guard" in src  # the audit action

    def test_guard_math_trips_on_excessive_adverse_slippage(self):
        # Mirror the guard's decision rule to pin the intended threshold.
        entry, sl = 100.0, 98.0           # 2% stop distance
        ratio = CONFIG.execution.max_slippage_edge_ratio
        stop_dist = abs(entry - sl) / entry
        limit = ratio * stop_dist
        # A LONG fill 1% above entry consumes 50% of a 2% stop -> trips at 0.30.
        adverse_fill = 101.0
        slip = abs(adverse_fill - entry) / entry
        assert slip > limit
        # A small 0.1% adverse fill does not trip.
        small_fill = 100.1
        assert abs(small_fill - entry) / entry <= limit


# ── P0-3: oversized order blocked ────────────────────────────────────

class TestOrderSplitHardBlock:
    def test_split_path_blocks_not_fakes(self):
        import bot.core.live_executor as le
        src = inspect.getsource(le.LiveExecutor.execute)
        assert "BLOCKED_NOT_IMPLEMENTED" in src
        # The misleading "SPLITTING" success result must be gone.
        assert 'result="SPLITTING"' not in src


# ── P0-4: .env.example matches code defaults ─────────────────────────

class TestEnvExampleMatchesCode:
    # (env var name -> RiskLimits attribute) for the risk block.
    RISK_VARS = {
        "MAX_POSITION_PCT": "max_position_pct",
        "MAX_DAILY_LOSS_PCT": "max_daily_loss_pct",
        "MAX_DRAWDOWN_PCT": "max_drawdown_pct",
        "MAX_OPEN_POSITIONS": "max_open_positions",
        "MIN_RISK_REWARD": "min_risk_reward",
        "MIN_CONFIDENCE": "min_confidence",
        "MAX_CONSECUTIVE_LOSSES": "max_consecutive_losses",
        "COOLDOWN_AFTER_LOSS_SEC": "cooldown_after_loss_seconds",
        "MAX_PORTFOLIO_EXPOSURE_PCT": "max_portfolio_exposure_pct",
        "MAX_SYMBOL_EXPOSURE_PCT": "max_symbol_exposure_pct",
        "MAX_CORRELATION_PER_GROUP": "max_correlation_per_group",
        "VOLATILITY_GUARD_ATR_PCT": "volatility_guard_atr_pct",
        "STALE_DATA_MAX_AGE_SEC": "stale_data_max_age_seconds",
    }

    def _env_values(self):
        import pathlib
        text = pathlib.Path(__file__).resolve().parent.parent.joinpath(".env.example").read_text()
        out = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
        return out

    def test_risk_defaults_match(self):
        env = self._env_values()
        for var, attr in self.RISK_VARS.items():
            assert var in env, f"{var} missing from .env.example"
            code_val = float(getattr(CONFIG.risk, attr))
            assert float(env[var]) == pytest.approx(code_val), (
                f"{var}: .env.example={env[var]} but code default={code_val}")


# ── P0-6: main-loop backoff ──────────────────────────────────────────

class TestMainLoopBackoff:
    def test_engine_loop_has_backoff(self):
        from bot.core.engine import RuneClawEngine
        # The run loop lives in the start/coroutine; check the source of the
        # method that contains the while-loop.
        src = inspect.getsource(RuneClawEngine)
        assert "consecutive" in src.lower()
        assert "CRITICAL_CONSECUTIVE_FAILURES" in src
