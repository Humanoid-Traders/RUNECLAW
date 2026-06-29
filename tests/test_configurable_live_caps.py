"""
Operator-configurable live position caps.

The live executor's hard caps ($100 margin/trade, $500 total, 5 positions) were
hardcoded — so every live trade was micro-sized regardless of account size.
They are now env-configurable via CONFIG.execution, defaulting to the same
micro-test values, so nothing changes until an operator raises them.
"""

import importlib

from bot.config import ExecutionConfig


class TestExecutionConfigDefaults:
    def test_defaults_match_micro_values(self):
        e = ExecutionConfig()
        assert e.max_live_position_usd == 100.0
        assert e.max_live_total_exposure_usd == 500.0
        assert e.max_live_open_positions == 5


class TestEnvOverride:
    def test_env_overrides_caps(self, monkeypatch):
        monkeypatch.setenv("MICRO_MAX_POSITION_USD", "250")
        monkeypatch.setenv("MICRO_MAX_TOTAL_EXPOSURE", "2000")
        monkeypatch.setenv("MICRO_MAX_OPEN_POSITIONS", "8")
        import bot.config as cfg
        importlib.reload(cfg)
        e = cfg.ExecutionConfig()
        assert e.max_live_position_usd == 250.0
        assert e.max_live_total_exposure_usd == 2000.0
        assert e.max_live_open_positions == 8
        # Restore the module so later tests see defaults.
        monkeypatch.undo()
        importlib.reload(cfg)

    def test_bounds_clamp_invalid(self, monkeypatch):
        # Below the lower bound (1) clamps up rather than disabling the cap.
        monkeypatch.setenv("MICRO_MAX_POSITION_USD", "0")
        import bot.config as cfg
        importlib.reload(cfg)
        assert cfg.ExecutionConfig().max_live_position_usd == 1.0
        monkeypatch.undo()
        importlib.reload(cfg)


class TestExecutorSourcesFromConfig:
    def test_executor_constants_track_config(self, monkeypatch):
        monkeypatch.setenv("MICRO_MAX_POSITION_USD", "333")
        import bot.config as cfg
        import bot.core.live_executor as le
        importlib.reload(cfg)
        importlib.reload(le)
        assert le.MICRO_MAX_POSITION_USD == 333.0
        # Restore.
        monkeypatch.undo()
        importlib.reload(cfg)
        importlib.reload(le)
