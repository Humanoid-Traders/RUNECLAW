"""Historical replay entry for the RUNECLAW ETH pullback backtest.

Fetches ETH (traded) + BTC (gate context) klines, runs the self-contained
Nautilus replay through ``backtest.run`` with both instruments, writes the
output files, and emits a summary signal. The strategy computes every decision
input internally, so the managed backtest can reconstruct the same run from the
spec alone.
"""
import json
import math
from pathlib import Path
from typing import Any

from getagent import backtest, data, runtime

_ETH_KEY = "ETHUSDT.BITGET"
_BTC_KEY = "BTCUSDT.BITGET"
_OUT = Path("/workspace/output")


def _f(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _emit_watch(reason: str) -> None:
    runtime.emit_signal(
        action="watch",
        symbol="ETHUSDT",
        confidence=0.0,
        metrics={"total_trades": 0},
        meta={"reason": reason, "run_id": runtime.run_id},
    )


def run() -> None:
    cfg = runtime.manifest.get("strategy_config", {}) or {}
    exchange = str(cfg.get("data_exchange", "bitget"))
    interval = str(cfg.get("interval", "1h"))
    limit = int(cfg.get("kline_limit", 1000))

    eth_raw = data.crypto.futures.kline(symbol="ETHUSDT", interval=interval, exchange=exchange, limit=limit)
    btc_raw = data.crypto.futures.kline(symbol="BTCUSDT", interval=interval, exchange=exchange, limit=limit)
    eth = backtest.prepare_frame(eth_raw, datetime_index="date")
    btc = backtest.prepare_frame(btc_raw, datetime_index="date")
    if eth is None or eth.empty or btc is None or btc.empty:
        _emit_watch("no_historical_bars")
        return

    print(f"[runeclaw-eth] eth_rows={len(eth)} btc_rows={len(btc)} "
          f"first={eth.index[0]} last={eth.index[-1]}")

    result = backtest.run(ohlcv_data={_ETH_KEY: eth, _BTC_KEY: btc}, spec=runtime.backtest_spec)
    summary = result.summary or {}
    raw = result.raw if isinstance(result.raw, dict) else {}
    total_trades = int(getattr(result, "total_trades", 0) or 0)
    print(f"[runeclaw-eth] raw_keys={sorted(raw.keys())} trades={total_trades} "
          f"return_pct={getattr(result, 'total_return_pct', None)} "
          f"win_rate={getattr(result, 'win_rate', None)}")

    starting_balance = _f(summary.get("starting_balance"), 100000.0)
    net_pnl = _f(summary.get("net_pnl"), 0.0)
    account_return_pct = (net_pnl / starting_balance * 100.0) if starting_balance else 0.0

    raw["net_pnl"] = round(net_pnl, 6)
    raw["total_return_pct"] = round(account_return_pct, 6)
    raw["starting_balance"] = starting_balance

    _OUT.mkdir(parents=True, exist_ok=True)
    report = {k: v for k, v in raw.items() if k != "reports"}
    report["reports"] = {k: v for k, v in (raw.get("reports") or {}).items() if k != "equity_curve"}
    try:
        (_OUT / "backtest_report.json").write_text(json.dumps(report, default=str), encoding="utf-8")
    except Exception as exc:
        print(f"[runeclaw-eth] report write failed: {type(exc).__name__}: {exc}")

    _write_equity_curve(eth, starting_balance, net_pnl, raw)

    chart_path = ""
    try:
        chart_path = backtest.generate_chart(result)
    except Exception as exc:
        print(f"[runeclaw-eth] chart failed: {type(exc).__name__}: {exc}")

    metrics = {
        "total_return_pct": _f(getattr(result, "total_return_pct", 0.0)),
        "account_return_pct": round(account_return_pct, 4),
        "net_pnl": round(net_pnl, 4),
        "starting_balance": starting_balance,
        "sharpe_ratio": _f(getattr(result, "sharpe_ratio", 0.0)),
        "max_drawdown_pct": _f(getattr(result, "max_drawdown_pct", 0.0)),
        "win_rate": _f(getattr(result, "win_rate", 0.0)),
        "total_trades": total_trades,
        "profit_factor": _f(getattr(result, "profit_factor", 0.0)),
        "rows": len(eth),
    }
    action = "long" if net_pnl > 0 and total_trades > 0 else "watch"
    runtime.emit_signal(
        action=action,
        symbol="ETHUSDT",
        confidence=_f(getattr(result, "win_rate", 0.0)),
        metrics=metrics,
        meta={"chart_path": chart_path, "interval": interval, "exchange": exchange,
              "gate_symbol": "BTCUSDT", "run_id": runtime.run_id},
    )


def _write_equity_curve(frame, starting_balance: float, net_pnl: float, raw: dict) -> None:
    lines = ["timestamp,value,nav"]
    points: list = []
    try:
        positions = (raw.get("reports") or {}).get("positions") or []
        running = starting_balance
        for pos in positions:
            ts = pos.get("ts_closed") or pos.get("closing_time") or pos.get("ts_last")
            pnl = _f(pos.get("realized_pnl") or pos.get("realized_return") or 0.0)
            running += pnl
            if ts is not None:
                points.append((str(ts), running))
    except Exception:
        points = []

    if not points:
        points = [(str(frame.index[0]), starting_balance),
                  (str(frame.index[-1]), starting_balance + net_pnl)]

    for ts, value in points:
        nav = value / starting_balance if starting_balance else 1.0
        lines.append(f"{ts},{round(value, 6)},{round(nav, 8)}")
    try:
        (_OUT / "equity_curve.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"[runeclaw-eth] equity curve write failed: {type(exc).__name__}: {exc}")
