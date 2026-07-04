"""`--confidence-threshold` CLI exposure for the portfolio backtest path, and
the live `MIN_CONFIDENCE` default it justified raising.

`BacktestConfig.confidence_threshold` was already honored by `BacktestEngine`
(bot/backtest/engine.py) and already swept by `--wf-optimize` for the
single-symbol grid, but `_run_portfolio` never read it from argv — the
`--symbols`/`--dataset` portfolio path had no way to raise the entry bar to
cut commission churn. The CLI default stays 0.0 (no extra gate), so existing
invocations are unaffected.

Sweeping 0.0/0.5/0.55/0.6/0.65 on the frozen honest-fidelity benchmark
(docs/FROZEN_BENCHMARK.md) showed 0.55 is a no-op — `CONFIG.risk.min_confidence`
already enforces that floor live (and backtest reuses the same risk-engine
check), so 0.0/0.55 extra gate both reproduce the plain baseline byte-for-byte.
0.60 is a real, robust improvement on both universes; 0.65 beat baseline but
underperformed 0.60. `CONFIG.risk.min_confidence`'s default was raised to
0.60 to actually enable the finding live (see bot/config.py).
"""
from bot.backtest.runner import build_parser
from bot.config import RiskLimits


class TestConfidenceThresholdFlag:
    def test_default_is_zero(self):
        args = build_parser().parse_args(["--synthetic"])
        assert args.confidence_threshold == 0.0

    def test_flag_sets_value(self):
        args = build_parser().parse_args(["--confidence-threshold", "0.6"])
        assert args.confidence_threshold == 0.6


class TestThreadsIntoPortfolioConfig:
    async def test_run_portfolio_threads_into_config(self, monkeypatch):
        from bot.backtest import runner
        from bot.backtest.data_loader import DataLoader
        from bot.backtest.models import BacktestResult

        captured = {}

        class _StubPortfolio:
            per_symbol = {}

            def __init__(self, config, symbols, **kwargs):
                captured["config"] = config

            async def run(self, data):
                return BacktestResult.model_construct(
                    symbol="PORTFOLIO", trades=[], equity_curve=[])

            def cleanup(self):
                pass

        async def _fake_fetch(symbol, timeframe, limit):
            return DataLoader.generate_synthetic(bars=200, seed=1, start_price=100.0)

        import bot.backtest.portfolio_engine as pe
        monkeypatch.setattr(DataLoader, "from_bitget", staticmethod(_fake_fetch))
        monkeypatch.setattr(pe, "PortfolioBacktester", _StubPortfolio)
        monkeypatch.setattr(runner, "_format_result_summary", lambda r: "")
        monkeypatch.setattr(runner, "_attribution_report", lambda r: "")
        monkeypatch.setattr(runner, "_narrative", lambda r, per_symbol: "")

        args = build_parser().parse_args(
            ["--symbols", "BTC/USDT:USDT,ETH/USDT:USDT",
             "--confidence-threshold", "0.55"])
        await runner._run_portfolio(args)

        assert captured["config"].confidence_threshold == 0.55


class TestLiveMinConfidenceRaisedToProvenValue:
    def test_default_is_the_frozen_benchmark_winner(self):
        assert RiskLimits().min_confidence == 0.60
