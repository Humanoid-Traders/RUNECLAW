"""
Recorded-LLM replay for deterministic backtest parity.

The backtest used to either NULL the LLM (rule-only — not the path live runs) or
hit the real network (non-reproducible). Neither validates the live blended
path (blended = llm*w + confluence*(1-w)). This module replays the LLM theses
that were actually logged in production (data/learning/llm_calibration.jsonl) so
a backtest can exercise the SAME blended/calibration/gating path live uses —
deterministically, with no network call.

Usage (wired by BacktestEngine when config.use_recorded_llm is set):

    rec = RecordedLLM.from_jsonl("data/learning/llm_calibration.jsonl")
    analyzer._offline_thesis_fn = rec.thesis_at   # (signal, indicators, as_of)

`thesis_at` returns the most recent recorded thesis for the symbol at or before
the simulated bar time (causal), or None — in which case the analyzer falls back
to its rule-based thesis. Record live first (shadow), then replay in backtest.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from datetime import datetime
from pathlib import Path
from typing import Optional

from bot.compat import UTC


def _parse_ts(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


class RecordedLLM:
    """Causal replay of recorded LLM theses keyed by symbol."""

    def __init__(self, entries: Optional[list[dict]] = None) -> None:
        # symbol -> (sorted list of epoch-seconds, parallel list of thesis dicts)
        self._by_symbol: dict[str, tuple[list[float], list[dict]]] = {}
        if entries:
            self._index(entries)

    def _index(self, entries: list[dict]) -> None:
        staging: dict[str, list[tuple[float, dict]]] = {}
        for e in entries:
            sym = e.get("symbol")
            direction = e.get("llm_direction")
            conf = e.get("llm_confidence_raw")
            ts = _parse_ts(e.get("ts", ""))
            if not sym or direction is None or conf is None or ts is None:
                continue
            thesis = {
                "direction": str(direction),
                "confidence": max(0.0, min(1.0, float(conf))),
                "source": "RECORDED_LLM",
            }
            staging.setdefault(sym, []).append((ts.timestamp(), thesis))
        for sym, pairs in staging.items():
            pairs.sort(key=lambda p: p[0])
            self._by_symbol[sym] = ([p[0] for p in pairs], [p[1] for p in pairs])

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "RecordedLLM":
        """Load recorded theses from a calibration JSONL file. Missing/empty file
        → an empty replay (every lookup returns None → rule fallback)."""
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

    def thesis_at(self, signal, indicators=None, as_of: Optional[datetime] = None) -> Optional[dict]:
        """Most recent recorded thesis for the signal's symbol at/before as_of
        (causal). Returns None when there is no usable record (→ rule fallback).
        as_of=None uses the latest available record for the symbol."""
        try:
            symbol = getattr(signal, "symbol", None) or (signal.get("symbol") if isinstance(signal, dict) else None)
            bucket = self._by_symbol.get(symbol)
            if not bucket:
                return None
            times, theses = bucket
            if as_of is None:
                idx = len(times) - 1
            else:
                cutoff = as_of.timestamp() if as_of.tzinfo else as_of.replace(tzinfo=UTC).timestamp()
                idx = bisect_right(times, cutoff) - 1
            if idx < 0:
                return None
            return dict(theses[idx])  # copy so callers can't mutate the store
        except Exception:
            return None

    def __len__(self) -> int:
        return sum(len(t) for t, _ in self._by_symbol.values())
