"""
Liquidity guard: top-of-book executable depth (deep-audit medium).

bid_depth_usd / ask_depth_usd SUM ~25 book levels, but the liquidity guard
treated that sum as immediately-fillable depth — a book deep when summed but
thin at the top would pass. The signal now also carries bid/ask_depth_top_usd
(top-N best levels), and when OF_GUARD_TOP_DEPTH_ENABLED is on the guard
additionally requires that executable depth to cover the position notional
(naturally position-scaled). Default OFF keeps the guard byte-identical.
"""

from bot.core.order_flow import OrderFlowAnalyzer, OrderFlowConfig, OrderFlowSignal


def _book():
    # Bids best-first: 100..91; asks best-first: 101..110; 1.0 base each.
    bids = [[float(p), 1.0] for p in range(100, 90, -1)]
    asks = [[float(p), 1.0] for p in range(101, 111)]
    return {"bids": bids, "asks": asks}


class TestFillBookMetrics:
    def test_top_n_vs_full_sum(self):
        sig = OrderFlowSignal(symbol="FOO/USDT")
        OrderFlowAnalyzer._fill_book_metrics(sig, _book(), top_levels=5)
        # Top 5 bids: 100+99+98+97+96 = 490; full 10: 100..91 = 955.
        assert sig.bid_depth_top_usd == 490.0
        assert sig.bid_depth_usd == 955.0
        # Top 5 asks: 101+102+103+104+105 = 515; full: 101..110 = 1055.
        assert sig.ask_depth_top_usd == 515.0
        assert sig.ask_depth_usd == 1055.0

    def test_top_levels_clamped_to_at_least_one(self):
        sig = OrderFlowSignal(symbol="FOO/USDT")
        OrderFlowAnalyzer._fill_book_metrics(sig, _book(), top_levels=0)
        assert sig.bid_depth_top_usd == 100.0  # just the best bid


def _sig(top_depth):
    sig = OrderFlowSignal(symbol="FOO/USDT")
    sig.components_ok = ["book"]
    sig.spread_bps = 1.0
    # Deep when summed over 25 levels → passes the legacy check easily.
    sig.bid_depth_usd = 100_000.0
    sig.ask_depth_usd = 100_000.0
    # ...but only `top_depth` is fillable near the top of book.
    sig.bid_depth_top_usd = top_depth
    sig.ask_depth_top_usd = top_depth
    return sig


class TestLiquidityGuardGated:
    def test_disabled_ignores_thin_top(self):
        an = OrderFlowAnalyzer(config=OrderFlowConfig(guard_top_depth_enabled=False))
        # Thin top ($500) but deep sum → legacy guard passes (byte-identical).
        assert an.liquidity_guard(_sig(500.0), position_size_usd=1_000.0, symbol="FOO/USDT") is None

    def test_enabled_rejects_thin_top(self):
        an = OrderFlowAnalyzer(config=OrderFlowConfig(guard_top_depth_enabled=True))
        reason = an.liquidity_guard(_sig(500.0), position_size_usd=1_000.0, symbol="FOO/USDT")
        assert reason is not None and "top-of-book" in reason

    def test_enabled_passes_when_top_covers_position(self):
        an = OrderFlowAnalyzer(config=OrderFlowConfig(guard_top_depth_enabled=True))
        # Executable top depth ($2,000) covers the $1,000 position → OK.
        assert an.liquidity_guard(_sig(2_000.0), position_size_usd=1_000.0, symbol="FOO/USDT") is None

    def test_enabled_no_position_size_skips_top_check(self):
        an = OrderFlowAnalyzer(config=OrderFlowConfig(guard_top_depth_enabled=True))
        # position_size_usd=0 → top check skipped (nothing to scale against).
        assert an.liquidity_guard(_sig(1.0), position_size_usd=0.0, symbol="FOO/USDT") is None


class TestDefaults:
    def test_flag_defaults_off(self):
        cfg = OrderFlowConfig()
        assert cfg.guard_top_depth_enabled is False
        assert cfg.book_top_levels == 5
