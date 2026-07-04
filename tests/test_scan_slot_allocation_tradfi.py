"""The all-markets slot allocator must never let the crypto priority list
starve the non-Crypto categories (metals, stocks, ETFs, commodities, pre-IPO).

Regression: `_allocate_slots` used to add every priority symbol first with no
cap, then append the per-category minimums, then `return result[:max_total]`.
Once the crypto PRIORITY_SYMBOLS list grew toward `top_movers_count` (80) the
final truncation cut exactly the category-minimum entries — so metals and stock
perps silently dropped to zero and never reached analysis. The minimums are now
reserved up front.
"""
from datetime import datetime, timezone

import bot.core.market_scanner as ms
from bot.config import CONFIG
from bot.core.market_scanner import MarketScanner
from bot.utils.models import MarketSignal


def _mk(symbol: str, category: str, momentum: float) -> MarketSignal:
    return MarketSignal(
        symbol=symbol,
        price=1.0,
        change_pct_24h=momentum * 10,
        volume_usd_24h=1_000_000.0,
        volume_spike=False,
        momentum_score=momentum,
        timestamp=datetime.now(timezone.utc),
        asset_category=category,
    )


def _by_cat(signals):
    out: dict[str, int] = {}
    for s in signals:
        out[s.asset_category] = out.get(s.asset_category, 0) + 1
    return out


def test_tradfi_minimums_survive_when_priority_list_exceeds_max_total(monkeypatch):
    """Even when the crypto priority list alone fills every slot, each present
    non-Crypto category still gets its minimum."""
    max_total = CONFIG.top_movers_count
    scanner = MarketScanner()

    # More priority-crypto symbols than there are total slots.
    pri_symbols = [f"PRI{i}/USDT" for i in range(max_total + 5)]
    monkeypatch.setattr(ms, "_PRIORITY_SET", set(pri_symbols))

    signals = [_mk(s, "Crypto", 0.9 - i * 0.001) for i, s in enumerate(pri_symbols)]
    signals += [_mk(s, "Metal", 0.99) for s in
                ("XAU/USDT:USDT", "XAG/USDT:USDT", "XPT/USDT:USDT")]
    signals += [_mk(s, "Stock", 0.99) for s in
                ("TSLA/USDT:USDT", "AAPL/USDT:USDT", "NVDA/USDT:USDT")]
    signals += [_mk("CL/USDT:USDT", "Commodity", 0.99)]
    signals.sort(key=lambda x: abs(x.momentum_score), reverse=True)

    top = scanner._allocate_slots(signals)
    cats = _by_cat(top)

    assert len(top) <= max_total
    # Every present non-Crypto category keeps at least min(2, available).
    assert cats.get("Metal", 0) >= 2, cats
    assert cats.get("Stock", 0) >= 2, cats
    assert cats.get("Commodity", 0) >= 1, cats  # only one supplied


def test_priority_symbols_still_dominate_when_slots_are_plentiful(monkeypatch):
    """The reservation only bites when slots are scarce — with headroom the
    priority symbols are all still included."""
    scanner = MarketScanner()
    pri_symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    monkeypatch.setattr(ms, "_PRIORITY_SET", set(pri_symbols))

    signals = [_mk(s, "Crypto", 0.9) for s in pri_symbols]
    signals += [_mk("XAU/USDT:USDT", "Metal", 0.5)]
    signals += [_mk("TSLA/USDT:USDT", "Stock", 0.5)]

    top = scanner._allocate_slots(signals)
    syms = {s.symbol for s in top}
    for p in pri_symbols:
        assert p in syms
    assert "XAU/USDT:USDT" in syms
    assert "TSLA/USDT:USDT" in syms


def test_all_crypto_universe_is_unaffected(monkeypatch):
    """No non-Crypto categories present → behaves exactly like a pure top-N."""
    scanner = MarketScanner()
    monkeypatch.setattr(ms, "_PRIORITY_SET", set())
    signals = [_mk(f"C{i}/USDT", "Crypto", 0.9 - i * 0.001) for i in range(200)]
    top = scanner._allocate_slots(signals)
    assert len(top) == CONFIG.top_movers_count
    assert all(s.asset_category == "Crypto" for s in top)


def _set_config(field: str, value):
    """CONFIG is a frozen dataclass; set/restore via object.__setattr__."""
    old = getattr(CONFIG, field)
    object.__setattr__(CONFIG, field, value)
    return field, old


def test_full_coverage_reserves_the_entire_tradfi_universe(monkeypatch):
    """With scan_tradfi_full_coverage ON (default), every present TradFi perp
    gets a slot even when the crypto priority list alone would fill the scan."""
    assert CONFIG.scan_tradfi_full_coverage is True  # ship default
    max_total = CONFIG.top_movers_count
    scanner = MarketScanner()

    pri = [f"PRI{i}/USDT" for i in range(max_total + 10)]
    monkeypatch.setattr(ms, "_PRIORITY_SET", set(pri))
    signals = [_mk(s, "Crypto", 0.9 - i * 0.001) for i, s in enumerate(pri)]
    tradfi = {"Metal": 6, "Stock": 15, "ETF": 6, "Commodity": 3, "Pre-IPO": 2}
    for cat, n in tradfi.items():
        signals += [_mk(f"{cat[:3].upper()}{i}/USDT:USDT", cat, 0.5) for i in range(n)]
    signals.sort(key=lambda x: abs(x.momentum_score), reverse=True)

    cats = _by_cat(scanner._allocate_slots(signals))
    for cat, n in tradfi.items():
        assert cats.get(cat, 0) == n, (cat, cats)
    # The rest is crypto — full TradFi universe (32) reserved, crypto gets the rest.
    assert cats["Crypto"] == max_total - sum(tradfi.values())


def test_coverage_knob_off_falls_back_to_per_category_minimum(monkeypatch):
    """Turning full coverage off reverts to scan_min_per_category per category."""
    max_total = CONFIG.top_movers_count
    f1, old1 = _set_config("scan_tradfi_full_coverage", False)
    f2, old2 = _set_config("scan_min_per_category", 2)
    try:
        scanner = MarketScanner()
        pri = [f"PRI{i}/USDT" for i in range(max_total + 10)]
        monkeypatch.setattr(ms, "_PRIORITY_SET", set(pri))
        signals = [_mk(s, "Crypto", 0.9 - i * 0.001) for i, s in enumerate(pri)]
        signals += [_mk(f"MET{i}/USDT:USDT", "Metal", 0.5) for i in range(6)]
        signals += [_mk(f"STK{i}/USDT:USDT", "Stock", 0.5) for i in range(15)]
        signals.sort(key=lambda x: abs(x.momentum_score), reverse=True)

        cats = _by_cat(scanner._allocate_slots(signals))
        assert cats.get("Metal", 0) == 2, cats
        assert cats.get("Stock", 0) == 2, cats
    finally:
        object.__setattr__(CONFIG, f1, old1)
        object.__setattr__(CONFIG, f2, old2)
