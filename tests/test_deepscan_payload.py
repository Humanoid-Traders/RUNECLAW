"""Deep-scan pattern block in the website scan payload
(bot/skills/scan_skill.py::_deepscan_block + _build_scan_payload).

The regular scan payload flattens chart patterns to top-2 names on high-score
cards and drops candle patterns entirely. The /deepscan path passes its scored
hits so the payload carries a `deepscan` block with the FULL per-symbol
breakdown (name + bullish/bearish signal + confidence + candle chips), which is
what the website Deep Scan view renders. Absent for regular scans.
"""

from bot.skills.scan_skill import _build_scan_payload, _deepscan_block


def _hit(sym, **kw):
    base = {
        "symbol": sym, "price": 100.0, "chg": 1.2, "rsi": 28.0,
        "atr": 2.0, "vol_spike": True, "score": 9, "score_norm": 0.8, "tf": "4h",
        "chart_patterns": [
            {"name": "Double Top", "signal": "bearish", "confidence": 0.74,
             "description": "x", "key_levels": {}},
            {"name": "Wyckoff Distribution", "signal": "bearish", "confidence": 0.7},
        ],
        "candle_patterns": {"doji": "neutral", "hammer": "bullish"},
    }
    base.update(kw)
    return base


def test_deepscan_block_keeps_full_pattern_breakdown():
    block = _deepscan_block([_hit("BTC/USDT")])
    assert len(block) == 1
    row = block[0]
    assert row["symbol"] == "BTC/USDT"
    assert row["rsi"] == 28.0 and row["vol_spike"] is True
    assert row["score"] == 0.8 and row["tf"] == "4h"
    # Each chart pattern keeps name + signal + confidence (the % bar) —
    # the description/key_levels are dropped to bound the blob.
    cp = row["chart_patterns"][0]
    assert cp == {"name": "Double Top", "signal": "bearish", "confidence": 0.74}
    assert "description" not in cp and "key_levels" not in cp
    # Candle chips survive as name -> signal.
    assert row["candle_patterns"] == {"doji": "neutral", "hammer": "bullish"}


def test_deepscan_block_bounds_symbols_and_patterns():
    hits = [_hit(f"SYM{i}/USDT") for i in range(40)]
    # 5 chart patterns on the first hit — only the first 4 survive.
    hits[0]["chart_patterns"] = [
        {"name": f"P{i}", "signal": "bullish", "confidence": 0.5} for i in range(5)]
    block = _deepscan_block(hits, max_symbols=24, max_patterns=4)
    assert len(block) == 24
    assert len(block[0]["chart_patterns"]) == 4


def test_payload_gains_deepscan_only_when_hits_passed():
    # Regular scan — no deepscan key.
    assert "deepscan" not in _build_scan_payload([], engine=None)
    # Deep scan — the block rides along with a count + timeframe.
    payload = _build_scan_payload([], engine=None, deepscan_hits=[_hit("ETH/USDT")])
    ds = payload.get("deepscan")
    assert ds and ds["count"] == 1 and ds["tf"] == "4h"
    assert ds["hits"][0]["symbol"] == "ETH/USDT"
    assert "generated_at" in ds


def test_deepscan_block_is_json_safe_on_missing_fields():
    # A sparse hit (no patterns, missing rsi/chg) must not raise.
    block = _deepscan_block([{"symbol": "X/USDT", "price": 1.0}])
    assert block[0]["chart_patterns"] == [] and block[0]["candle_patterns"] == {}
    assert block[0]["rsi"] == 0.0 and block[0]["vol_spike"] is False
