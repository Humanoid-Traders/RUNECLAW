"""LLM A/B harness: metric parser + data-gated guard.

The A/B measures whether the 60% LLM confidence blend beats pure confluence
out-of-sample. It is data-gated on recorded live theses; until those accrue
it reports the finding that all backtests to date ran LLM-free while live
runs at LLM_BLEND_WEIGHT=0.6.
"""

import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "llm_ab", os.path.join(os.path.dirname(__file__), "..", "scripts", "llm_ab.py"))
ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab)


class TestParser:
    def test_parse_metrics(self):
        text = ("  Total Return:           +1.30%\n  Total Trades:     28\n"
                "  Profit Factor:    1.40\n  Sharpe Ratio:     0.90\n")
        p = ab._parse(text)
        assert p["ok"] and p["return_pct"] == 1.30 and p["profit_factor"] == 1.40
        assert p["sharpe"] == 0.90 and p["trades"] == 28

    def test_parse_failed_run(self):
        assert ab._parse("Traceback...\n")["ok"] is False


class TestDataGate:
    def test_recorded_count_zero_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ab, "LLM_LOG", tmp_path / "nope.jsonl")
        assert ab._recorded_count() == 0

    def test_recorded_count_counts_nonblank(self, tmp_path, monkeypatch):
        f = tmp_path / "cal.jsonl"
        f.write_text('{"a":1}\n\n{"b":2}\n')
        monkeypatch.setattr(ab, "LLM_LOG", f)
        assert ab._recorded_count() == 2
