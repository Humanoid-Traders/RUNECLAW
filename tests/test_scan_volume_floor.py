"""
Scan-universe crypto volume floor (live-log diagnosis).

The crypto floor default was raised 50k -> 1.5M so thin-book meme coins are
dropped BEFORE analysis: on a small account they can't clear the execution
liquidity guard, so they only waste analysis cycles + scarce LLM quota. These
tests pin the filtering BEHAVIOUR at the shipped default — a mid-volume symbol
that used to pass is now filtered, while a deep-book major still passes, and
the floor stays operator-tunable via min_vol.
"""
from bot.config import CONFIG
from bot.core.market_scanner import MarketScanner


def _tick(volume):
    return {"last": 1.0, "percentage": 1.0, "quoteVolume": volume}


def test_default_floor_is_raised():
    """The shipped default keeps liquid names and drops the thin tail."""
    assert CONFIG.min_crypto_volume_usd >= 1_000_000


def test_thin_symbol_filtered_at_default():
    s = MarketScanner()
    # $500k 24h volume — above the OLD $50k floor, below the new one → dropped.
    assert s._process_ticker("MEME/USDT", _tick(500_000),
                             min_vol=CONFIG.min_crypto_volume_usd) is None


def test_deep_book_symbol_passes_at_default():
    s = MarketScanner()
    sig = s._process_ticker("BTC/USDT", _tick(2_000_000_000),
                            min_vol=CONFIG.min_crypto_volume_usd)
    assert sig is not None and sig.symbol == "BTC/USDT"


def test_floor_stays_tunable():
    """Lowering min_vol re-admits the thin symbol — the knob still governs."""
    s = MarketScanner()
    assert s._process_ticker("MEME/USDT", _tick(500_000), min_vol=50_000) is not None
