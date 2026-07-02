"""Shared OHLCV candle hygiene helpers.

The repaint policy (DROP_UNCLOSED_CANDLE_ENABLED, default ON) must apply to
EVERY consumer of fetch_ohlcv — the engine's analysis path and the live
executor's limit-entry / trend checks alike — so the logic lives here rather
than as an engine method.
"""
from __future__ import annotations

import time


def timeframe_to_ms(timeframe: str) -> int:
    """Parse a ccxt timeframe ('5m','1h','4h','1d','1w') to milliseconds; 0 if
    unparseable."""
    try:
        unit = timeframe[-1].lower()
        n = int(timeframe[:-1])
        mult = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}.get(unit)
        return n * mult if mult else 0
    except Exception:
        return 0


def resample_ohlcv(candles, source_tf: str, target_tf: str):
    """Aggregate finer-timeframe OHLCV rows into CLOSED target-timeframe
    candles (ccxt row format [ts, o, h, l, c, v], ascending).

    Only complete target periods are returned — a trailing group whose period
    has not fully elapsed by the last source bar's close is dropped. This
    makes backtest replay see exactly the higher-TF history live would have
    had at that bar close: no lookahead into the unfinished 4h/1d candle.

    Returns [] when the timeframes are unparseable, equal, or the target is
    not an integer multiple of the source.
    """
    src_ms = timeframe_to_ms(source_tf)
    tgt_ms = timeframe_to_ms(target_tf)
    if src_ms <= 0 or tgt_ms <= src_ms or tgt_ms % src_ms != 0 or not candles:
        return []
    groups: dict[int, list] = {}
    for row in candles:
        key = int(row[0] // tgt_ms)
        g = groups.get(key)
        if g is None:
            groups[key] = [key * tgt_ms, row[1], row[2], row[3], row[4], row[5]]
        else:
            g[2] = max(g[2], row[2])
            g[3] = min(g[3], row[3])
            g[4] = row[4]
            g[5] += row[5]
    last_close_ms = candles[-1][0] + src_ms
    return [groups[k] for k in sorted(groups) if (k + 1) * tgt_ms <= last_close_ms]


def drop_forming_candle(ohlcv, timeframe: str):
    """Drop the in-progress (still-forming) last candle so indicators/patterns
    compute on CLOSED bars only — eliminating repaint. Gated by
    DROP_UNCLOSED_CANDLE_ENABLED (default ON; when disabled returns ohlcv
    unchanged and every closes[-1] consumer repaints intrabar). The last
    candle is dropped only when its period has not yet elapsed (its open time
    + timeframe is still in the future), so a feed that already excludes the
    forming bar is left intact. Fail-open: any error returns ohlcv as-is.
    """
    from bot.config import CONFIG
    if not getattr(CONFIG.analyzer, "drop_unclosed_candle_enabled", False):
        return ohlcv
    try:
        if not ohlcv or len(ohlcv) < 3:
            return ohlcv
        tf_ms = timeframe_to_ms(timeframe)
        if tf_ms <= 0:
            return ohlcv
        last_open = float(ohlcv[-1][0])
        now_ms = time.time() * 1000.0
        if now_ms < last_open + tf_ms:   # last candle's period not yet closed
            return ohlcv[:-1]
        return ohlcv
    except Exception:
        return ohlcv
