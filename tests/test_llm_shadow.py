"""
LLM shadow A/B — the challenger model must be measurable but powerless.

Pins: disabled-by-default (no spawn, no file), the in-flight cap, record
shape on a successful shadow call, the trade-join scoring math (correct =
direction matched a realized winner or opposed a realized loser), and the
report rendering including the empty state.
"""

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace


from bot.compat import UTC
import bot.llm.shadow_eval as se
from bot.llm.shadow_eval import (
    ShadowEval,
    format_ab_html,
    score_against_trades,
)


def _spawn_probe(monkeypatch):
    """Patch ShadowEval._run to record invocations instead of calling out."""
    calls = []

    async def fake_run(self, *a, **k):
        calls.append(a)
    monkeypatch.setattr(ShadowEval, "_run", fake_run)
    return calls


def test_disabled_by_default_never_spawns(monkeypatch):
    monkeypatch.delenv("LLM_SHADOW_ENABLED", raising=False)
    monkeypatch.delenv("LLM_SHADOW_PROVIDER", raising=False)
    calls = _spawn_probe(monkeypatch)
    s = ShadowEval()

    async def main():
        s.maybe_spawn(SimpleNamespace(), "prompt", "hash", "BTC/USDT", {})
        await asyncio.sleep(0)   # let any scheduled task run
    asyncio.run(main())
    assert not calls, "shadow must be strictly opt-in"


def test_in_flight_cap_blocks_spawn(monkeypatch):
    monkeypatch.setenv("LLM_SHADOW_ENABLED", "true")
    monkeypatch.setenv("LLM_SHADOW_PROVIDER", "runeclaw")
    calls = _spawn_probe(monkeypatch)
    s = ShadowEval()
    s._in_flight = 99

    async def main():
        s.maybe_spawn(SimpleNamespace(), "p", "h", "BTC/USDT", {})
        await asyncio.sleep(0)
    asyncio.run(main())
    assert not calls

    # And the inverse: enabled + under the cap DOES spawn.
    s._in_flight = 0

    async def main2():
        s.maybe_spawn(SimpleNamespace(), "p", "h", "BTC/USDT", {})
        await asyncio.sleep(0)
    asyncio.run(main2())
    assert len(calls) == 1


def test_successful_shadow_call_writes_record(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_SHADOW_ENABLED", "true")
    monkeypatch.setenv("LLM_SHADOW_PROVIDER", "runeclaw")
    rec_file = tmp_path / "shadow.jsonl"
    monkeypatch.setattr(se, "_RECORD_FILE", rec_file)

    # OpenAI-compatible fake client + analyzer with a real-ish parser.
    async def create(**kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"direction": "SHORT", "confidence": 0.7, "reasoning": "x"}'))])
    client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create)))
    analyzer = SimpleNamespace(
        _build_client_for_config=lambda cfg: client,
        _parse_llm_response=lambda raw: {**json.loads(raw), "_parsed": True})

    s = ShadowEval()
    asyncio.run(s._run(analyzer, "the prompt", "abc123", "ETH/USDT",
                       {"model_used": "claude-sonnet-5",
                        "direction": "LONG", "confidence": 0.8}))
    lines = rec_file.read_text().splitlines()
    assert len(lines) == 1 and s.recorded == 1 and s.errors == 0
    r = json.loads(lines[0])
    assert r["primary_direction"] == "LONG" and r["shadow_direction"] == "SHORT"
    assert r["prompt_hash"] == "abc123" and r["shadow_latency_ms"] >= 0
    # The prompt text itself is NOT persisted — hashes only.
    assert "the prompt" not in lines[0]


def _rec(sym, ts, p_dir, s_dir):
    return {"ts": ts.isoformat(), "symbol": sym, "prompt_hash": "h",
            "primary_model": "claude", "primary_direction": p_dir,
            "primary_confidence": 0.8, "shadow_model": "runeclaw-v6",
            "shadow_direction": s_dir, "shadow_confidence": 0.7}


def _trade(sym, opened, direction, pnl):
    return {"symbol": sym, "opened_at": opened.isoformat(),
            "direction": direction, "pnl_usd": pnl}


def test_scoring_joins_trades_and_credits_correct_directions():
    t0 = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    records = [
        # Primary LONG (trade LONG won -> primary right); shadow SHORT wrong.
        _rec("BTC/USDT", t0, "LONG", "SHORT"),
        # Both SHORT; trade LONG lost -> opposing a loser = both right.
        _rec("ETH/USDT", t0, "SHORT", "SHORT"),
        # No trade within the window -> unmatched, unscored.
        _rec("SOL/USDT", t0, "LONG", "LONG"),
    ]
    trades = [
        _trade("BTC/USDT:USDT", t0 + timedelta(minutes=10), "LONG", 12.0),
        _trade("ETH/USDT:USDT", t0 + timedelta(minutes=5), "LONG", -8.0),
        _trade("SOL/USDT:USDT", t0 + timedelta(hours=6), "LONG", 5.0),
    ]
    s = score_against_trades(records, trades)
    assert s["records"] == 3 and s["matched"] == 2 and s["scored"] == 2
    assert s["primary_correct"] == 2      # right on BTC and ETH
    assert s["shadow_correct"] == 1       # wrong on BTC, right on ETH
    assert s["agreement"] == 2            # ETH + SOL agreed


def test_report_renders_and_handles_empty():
    empty = format_ab_html(score_against_trades([], []))
    assert "No shadow records" in empty and "LLM_SHADOW_ENABLED" in empty

    t0 = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    stats = score_against_trades(
        [_rec("BTC/USDT", t0, "LONG", "LONG")],
        [_trade("BTC/USDT:USDT", t0, "LONG", 10.0)])
    html = format_ab_html(stats)
    assert "shadow A/B" in html and "hit rate" in html
    assert "never influences trading" in html
