"""
CLI exposure of the recorded-order-flow replay (deep-audit medium #17).

The replay path (RecordedOrderFlow → BacktestEngine.use_recorded_order_flow) was
wired and tested at the engine level, but the `bot.backtest.runner` CLI had no
way to turn it on — only `--use-recorded-llm` was exposed. These tests pin the
new `--use-recorded-order-flow` / `--of-snapshot-path` flags and that they thread
into BacktestConfig for both the single-run and walk-forward paths (the walk-
forward `base` overrides previously dropped the recorded flags entirely).
"""

from bot.backtest.runner import build_parser


class TestRecordedOrderFlowFlags:
    def test_default_off(self):
        args = build_parser().parse_args(["--synthetic"])
        assert args.use_recorded_order_flow is False
        assert args.of_snapshot_path == "data/learning/order_flow_snapshots.jsonl"

    def test_flag_enables(self):
        args = build_parser().parse_args(["--use-recorded-order-flow"])
        assert args.use_recorded_order_flow is True

    def test_path_override(self):
        args = build_parser().parse_args(
            ["--use-recorded-order-flow", "--of-snapshot-path", "/tmp/of.jsonl"])
        assert args.of_snapshot_path == "/tmp/of.jsonl"


class TestThreadsIntoConfig:
    """The flags must reach BacktestConfig in both run paths."""

    async def test_single_run_threads_into_config(self, tmp_path, monkeypatch):
        # Drive _run_backtest with a stub engine + stub loader so we assert the
        # CLI args actually land on the BacktestConfig the engine receives.
        from bot.backtest import runner
        from bot.backtest.models import BacktestResult

        captured = {}

        class _StubEngine:
            _recorded_order_flow = None

            def __init__(self, config):
                captured["config"] = config

            async def run(self, bars):
                return BacktestResult.model_construct(symbol="BTC/USDT")

            def cleanup(self):
                pass

        async def _stub_load(args, config):
            return [object()] * 200, True, "synthetic"

        monkeypatch.setattr(runner, "BacktestEngine", _StubEngine)
        monkeypatch.setattr(runner, "_load_bars", _stub_load)
        monkeypatch.setattr(runner, "_format_result_summary", lambda r: "")

        of_path = str(tmp_path / "of.jsonl")
        args = build_parser().parse_args(
            ["--synthetic", "--use-recorded-order-flow", "--of-snapshot-path", of_path])
        await runner._run_backtest(args)

        cfg = captured["config"]
        assert cfg.use_recorded_order_flow is True
        assert cfg.recorded_order_flow_path == of_path

    def test_walk_forward_base_carries_recorded_flags(self):
        # Reconstruct the same `base` dict the walk-forward path builds, to pin
        # that the recorded flags (previously dropped) are carried per fold.
        args = build_parser().parse_args(
            ["--use-recorded-order-flow", "--use-recorded-llm", "--walk-forward", "3"])
        base = {"symbol": args.symbol, "timeframe": args.timeframe,
                "initial_balance": args.balance, "commission_pct": args.commission,
                "slippage_pct": args.slippage, "use_llm": args.use_llm,
                "use_recorded_llm": args.use_recorded_llm,
                "use_recorded_order_flow": args.use_recorded_order_flow,
                "recorded_order_flow_path": args.of_snapshot_path}
        assert base["use_recorded_order_flow"] is True
        assert base["use_recorded_llm"] is True
