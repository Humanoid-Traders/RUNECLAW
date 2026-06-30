"""
Recorded order-flow replay for backtest ↔ live parity (deep-audit medium #17).

The backtest calls ``analyzer.analyze(signal, candles)`` with ``order_flow=None``,
so the entire microstructure path live runs — the smart-money voter, the
order-flow confluence votes, the order-flow opposition veto, the funding-cost
haircut — is ABSENT in the backtest. Backtest signals therefore diverge
systematically from live signals on exactly the inputs order flow drives.

Live order flow is computed from the live book + trades + funding and cannot be
reconstructed from OHLCV after the fact, so — like the recorded-LLM path — it
must be SHADOW-RECORDED in production and then replayed deterministically:

    # live (gated by OF_RECORD_SNAPSHOTS): after computing of_signal
    record_snapshot("data/learning/order_flow_snapshots.jsonl", of_signal)

    # backtest (wired by BacktestEngine when config.use_recorded_order_flow):
    rec = RecordedOrderFlow.from_jsonl("data/learning/order_flow_snapshots.jsonl")
    of = rec.signal_at("BTC/USDT", as_of=bar.timestamp)   # causal, or None

``signal_at`` returns the most recent recorded ``OrderFlowSignal`` for the symbol
at or before the simulated bar time (causal), or None — in which case the
analyzer simply runs without order flow, exactly as it does today.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from datetime import datetime
from pathlib import Path
from typing import Optional

from bot.compat import UTC
from bot.core.order_flow import OrderFlowSignal


def _parse_ts(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def record_snapshot(path: str | Path, signal: OrderFlowSignal) -> bool:
    """Append one order-flow snapshot to a JSONL file (best-effort, fail-open).

    The line is ``{"symbol", "ts", "signal": <model_dump>}`` where ``ts`` is the
    signal's own timestamp (ISO-8601). Returns True on success, False on any
    error — recording must never break the live hot path. Used on the live side,
    gated by OF_RECORD_SNAPSHOTS, to build the dataset the backtest replays.
    """
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        ts = getattr(signal, "timestamp", None)
        ts_iso = ts.isoformat() if isinstance(ts, datetime) else ""
        record = {
            "symbol": signal.symbol,
            "ts": ts_iso,
            "signal": signal.model_dump(mode="json"),
        }
        with open(p, "a") as f:
            f.write(json.dumps(record) + "\n")
        return True
    except Exception:
        return False


class RecordedOrderFlow:
    """Causal replay of recorded ``OrderFlowSignal`` snapshots keyed by symbol."""

    def __init__(self, entries: Optional[list[dict]] = None) -> None:
        # symbol -> (sorted epoch-seconds list, parallel OrderFlowSignal list)
        self._by_symbol: dict[str, tuple[list[float], list[OrderFlowSignal]]] = {}
        if entries:
            self._index(entries)

    def _index(self, entries: list[dict]) -> None:
        staging: dict[str, list[tuple[float, OrderFlowSignal]]] = {}
        for e in entries:
            sym = e.get("symbol")
            raw_sig = e.get("signal")
            ts = _parse_ts(e.get("ts", ""))
            if not sym or not isinstance(raw_sig, dict) or ts is None:
                continue
            try:
                sig = OrderFlowSignal.model_validate(raw_sig)
            except Exception:
                continue  # skip malformed/schema-drifted rows, keep the rest
            staging.setdefault(sym, []).append((ts.timestamp(), sig))
        for sym, pairs in staging.items():
            pairs.sort(key=lambda p: p[0])
            self._by_symbol[sym] = ([p[0] for p in pairs], [p[1] for p in pairs])

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "RecordedOrderFlow":
        """Load recorded snapshots from a JSONL file. Missing/empty file → an
        empty replay (every lookup returns None → analyzer runs without order
        flow, identical to today's backtest)."""
        p = Path(path)
        entries: list[dict] = []
        if p.exists():
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return cls(entries)

    def signal_at(self, symbol: str, as_of: Optional[datetime] = None) -> Optional[OrderFlowSignal]:
        """Most recent recorded snapshot for ``symbol`` at/before ``as_of``
        (causal). Returns None when there is no usable record. ``as_of=None``
        uses the latest available snapshot for the symbol."""
        try:
            bucket = self._by_symbol.get(symbol)
            if not bucket:
                return None
            times, sigs = bucket
            if as_of is None:
                idx = len(times) - 1
            else:
                cutoff = as_of.timestamp() if as_of.tzinfo else as_of.replace(tzinfo=UTC).timestamp()
                idx = bisect_right(times, cutoff) - 1
            if idx < 0:
                return None
            return sigs[idx]
        except Exception:
            return None

    def __len__(self) -> int:
        return sum(len(t) for t, _ in self._by_symbol.values())
