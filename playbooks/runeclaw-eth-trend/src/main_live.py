"""Live signal-only read for the RUNECLAW ETH pullback package.

Recomputes the same deterministic decision used in the backtest on the most
recent bars and emits one signal. This package is signal_only and never calls
the trade SDK.
"""
import math
from typing import Any

from getagent import data, runtime

from . import indicators


def _f(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _prepare(raw: Any) -> Any:
    try:
        from getagent import backtest

        return backtest.prepare_frame(raw, datetime_index="date")
    except Exception:
        return None


def run() -> None:
    cfg = runtime.manifest.get("strategy_config", {}) or {}
    exchange = str(cfg.get("data_exchange", "bitget"))
    interval = str(cfg.get("interval", "1h"))
    limit = int(cfg.get("kline_limit", 200))

    eth_raw = data.crypto.futures.kline(symbol="ETHUSDT", interval=interval, exchange=exchange, limit=limit)
    btc_raw = data.crypto.futures.kline(symbol="BTCUSDT", interval=interval, exchange=exchange, limit=limit)
    eth = _prepare(eth_raw)
    btc = _prepare(btc_raw)
    if eth is None or eth.empty or btc is None or btc.empty:
        runtime.emit_signal(action="watch", symbol="ETHUSDT", confidence=0.0,
                            metrics={}, meta={"reason": "no_live_bars", "run_id": runtime.run_id})
        return

    frame = indicators.compute_decision_frame(eth, btc, cfg)
    row = frame.iloc[-1]
    entry = int(row.get("rc_entry", 0) or 0) == 1
    score = _f(row.get("rc_score"))
    metrics = {
        "score": round(score, 1),
        "gate_score": _f(row.get("rc_gate_score")),
        "limit_price": _f(row.get("rc_limit")),
        "sl_price": _f(row.get("rc_sl")),
        "tp1_price": _f(row.get("rc_tp1")),
        "range_pos": round(_f(row.get("rc_range_pos")), 3),
        "size_factor": _f(row.get("rc_size_factor")),
    }
    runtime.emit_signal(
        action="long" if entry else "watch",
        symbol="ETHUSDT",
        confidence=max(0.0, min(1.0, score / 100.0)),
        metrics=metrics,
        meta={"interval": interval, "exchange": exchange, "min_score": cfg.get("min_score", 70),
              "run_id": runtime.run_id},
    )


if __name__ == "__main__":
    run()
