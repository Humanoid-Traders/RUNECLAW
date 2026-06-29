"""
Recorded-LLM replay for deterministic backtest parity (audit issue #4).

The backtest used to null the LLM (rule-only ≠ live) or hit the network
(non-reproducible). RecordedLLM replays the LLM theses logged in production so a
backtest exercises the SAME blended path live uses, deterministically. The
analyzer's _offline_thesis_fn hook short-circuits the network LLM.
"""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from bot.compat import UTC
from bot.backtest.recorded_llm import RecordedLLM
from bot.core.analyzer import Analyzer


def _entry(symbol, ts, direction="LONG", conf=0.7):
    return {"symbol": symbol, "ts": ts.isoformat(), "llm_direction": direction,
            "llm_confidence_raw": conf}


T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class TestRecordedLookup:
    def test_causal_returns_latest_prior(self):
        rec = RecordedLLM([
            _entry("BTC/USDT", T0, "LONG", 0.6),
            _entry("BTC/USDT", T0 + timedelta(hours=2), "SHORT", 0.8),
        ])
        sig = SimpleNamespace(symbol="BTC/USDT")
        # Between the two records → the first (causal).
        got = rec.thesis_at(sig, None, T0 + timedelta(hours=1))
        assert got["direction"] == "LONG" and got["confidence"] == 0.6
        # After both → the latest.
        got2 = rec.thesis_at(sig, None, T0 + timedelta(hours=3))
        assert got2["direction"] == "SHORT" and got2["confidence"] == 0.8

    def test_before_any_record_returns_none(self):
        rec = RecordedLLM([_entry("BTC/USDT", T0)])
        got = rec.thesis_at(SimpleNamespace(symbol="BTC/USDT"), None, T0 - timedelta(hours=1))
        assert got is None

    def test_unknown_symbol_returns_none(self):
        rec = RecordedLLM([_entry("BTC/USDT", T0)])
        assert rec.thesis_at(SimpleNamespace(symbol="ETH/USDT"), None, T0) is None

    def test_as_of_none_uses_latest(self):
        rec = RecordedLLM([
            _entry("BTC/USDT", T0, "LONG", 0.5),
            _entry("BTC/USDT", T0 + timedelta(hours=5), "SHORT", 0.9),
        ])
        got = rec.thesis_at(SimpleNamespace(symbol="BTC/USDT"), None, None)
        assert got["confidence"] == 0.9

    def test_source_tag_and_copy(self):
        rec = RecordedLLM([_entry("BTC/USDT", T0)])
        got = rec.thesis_at(SimpleNamespace(symbol="BTC/USDT"), None, T0)
        assert got["source"] == "RECORDED_LLM"
        got["confidence"] = 0.0  # mutate the copy
        again = rec.thesis_at(SimpleNamespace(symbol="BTC/USDT"), None, T0)
        assert again["confidence"] == 0.7  # store unchanged

    def test_malformed_entries_skipped(self):
        rec = RecordedLLM([
            {"symbol": "BTC/USDT"},                       # missing fields
            {"ts": "not-a-date", "symbol": "BTC/USDT", "llm_direction": "LONG", "llm_confidence_raw": 0.5},
            _entry("BTC/USDT", T0, "LONG", 0.7),          # the only valid one
        ])
        assert len(rec) == 1


class TestFromJsonl:
    def test_missing_file_is_empty(self, tmp_path):
        rec = RecordedLLM.from_jsonl(tmp_path / "nope.jsonl")
        assert len(rec) == 0
        assert rec.thesis_at(SimpleNamespace(symbol="BTC/USDT"), None, T0) is None

    def test_loads_and_skips_bad_lines(self, tmp_path):
        p = tmp_path / "cal.jsonl"
        import json
        p.write_text(
            json.dumps(_entry("BTC/USDT", T0)) + "\n"
            + "{bad json\n"
            + "\n"
            + json.dumps(_entry("ETH/USDT", T0)) + "\n"
        )
        rec = RecordedLLM.from_jsonl(p)
        assert len(rec) == 2


class TestAnalyzerOfflineHook:
    def test_offline_fn_short_circuits_to_record(self):
        a = Analyzer.__new__(Analyzer)
        a._offline_thesis_fn = lambda sig, ind, as_of: {
            "direction": "LONG", "confidence": 0.7, "source": "RECORDED_LLM"}
        sig = SimpleNamespace(symbol="BTC/USDT")
        result = asyncio.run(a._llm_thesis(sig, {}, as_of=T0))
        assert result["source"] == "RECORDED_LLM"
        assert result["confidence"] == 0.7
