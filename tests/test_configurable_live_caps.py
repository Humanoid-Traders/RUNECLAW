"""
Operator-configurable live position caps.

The live executor's hard caps ($100 margin/trade, $500 total, 5 positions) were
hardcoded — so every live trade was micro-sized regardless of account size.
They are now env-configurable via CONFIG.execution, defaulting to the same
micro-test values, so nothing changes until an operator raises them.

The env-override tests run in a SUBPROCESS because the caps are read at
import/class-definition time. This file previously importlib.reload()ed
bot.config and bot.core.live_executor in-process to test the same thing —
which replaced the LiveExecutor class object mid-suite and silently broke
every later test that monkeypatched the collection-time class (30 failures
across the full run). Never reload shared modules in tests.
"""

from bot.config import ExecutionConfig

from tests._env_subprocess import run_py


class TestExecutionConfigDefaults:
    def test_defaults_match_micro_values(self):
        e = ExecutionConfig()
        assert e.max_live_position_usd == 100.0
        assert e.max_live_total_exposure_usd == 500.0
        assert e.max_live_open_positions == 5


class TestEnvOverride:
    def test_env_overrides_caps(self):
        out = run_py(
            "from bot.config import ExecutionConfig\n"
            "e = ExecutionConfig()\n"
            "print(e.max_live_position_usd, e.max_live_total_exposure_usd,"
            " e.max_live_open_positions)",
            env_overrides={"MICRO_MAX_POSITION_USD": "250",
                           "MICRO_MAX_TOTAL_EXPOSURE": "2000",
                           "MICRO_MAX_OPEN_POSITIONS": "8"})
        assert out == "250.0 2000.0 8"

    def test_bounds_clamp_invalid(self):
        # Below the lower bound (1) clamps up rather than disabling the cap.
        out = run_py(
            "from bot.config import ExecutionConfig\n"
            "print(ExecutionConfig().max_live_position_usd)",
            env_overrides={"MICRO_MAX_POSITION_USD": "0"})
        assert out == "1.0"


class TestExecutorSourcesFromConfig:
    def test_executor_constants_track_config(self):
        out = run_py(
            "import bot.core.live_executor as le\n"
            "print(le.MICRO_MAX_POSITION_USD)",
            env_overrides={"MICRO_MAX_POSITION_USD": "333"})
        assert out == "333.0"
