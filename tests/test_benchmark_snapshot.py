"""The frozen benchmark dataset is the keystone that makes A/B testing honest.

The live benchmark fetches ~6000 FRESH bars per symbol every run (anchored to the
exchange clock), so two runs minutes apart measure DIFFERENT data and the ~0.5pp
run-to-run variance swamps the small effects a signal A/B is trying to detect.
``bot.backtest.snapshot`` freezes the universe once; this suite locks in the
guarantees that make it trustworthy:

  * gzip CSV round-trips preserve the exact candles (content hash stable),
  * the same candles re-snapshot to byte-identical files (clean git diffs),
  * the manifest hash is deterministic and drift-sensitive (integrity anchor),
  * ``verify_dataset`` catches any tampering,
  * a missing symbol is a hard error, never a silent live fallback,
  * and — the self-validating proof — two portfolio backtests on the SAME frozen
    dataset produce byte-identical P&L, so any A/B delta is attributable to the
    code change, not to data drift.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import pytest

from bot.backtest import snapshot as snap
from bot.backtest.data_loader import DataLoader


# ── fixtures ────────────────────────────────────────────────────────────

def _synth(seed: int, start: float, n: int = 300):
    bars = DataLoader.generate_synthetic(bars=n, seed=seed, start_price=start)
    return bars


def _write_snapshot(out: Path, symbols: dict[str, list], timeframe="1h", limit=300) -> dict:
    """Freeze a set of {symbol: bars} to disk the way snapshot_dataset would,
    without touching the network."""
    out.mkdir(parents=True, exist_ok=True)
    for sym, bars in symbols.items():
        for b in bars:
            b.symbol = sym
        DataLoader.save_csv(bars, str(out / f"{snap.safe_symbol(sym)}.csv.gz"))
    man = snap.build_manifest(out, timeframe, limit, symbols)
    (out / snap.MANIFEST_NAME).write_text(json.dumps(man, indent=2) + "\n")
    return man


# ── content hash + gzip round-trip ──────────────────────────────────────

def test_gzip_round_trip_preserves_content_hash(tmp_path):
    bars = _synth(1, 100.0)
    h0 = DataLoader.content_hash(bars)
    for suffix in (".csv", ".csv.gz"):
        p = tmp_path / f"d{suffix}"
        DataLoader.save_csv(bars, str(p))
        loaded = DataLoader.from_csv(str(p))
        assert DataLoader.content_hash(loaded) == h0


def test_content_hash_is_sensitive_to_any_change(tmp_path):
    a = _synth(1, 100.0)
    b = _synth(2, 100.0)
    assert DataLoader.content_hash(a) != DataLoader.content_hash(b)
    # A single perturbed close must move the hash.
    c = _synth(1, 100.0)
    c[5].close += 0.01
    assert DataLoader.content_hash(c) != DataLoader.content_hash(a)


def test_gzip_same_path_is_byte_reproducible(tmp_path):
    bars = _synth(3, 250.0)
    p = tmp_path / "d.csv.gz"
    DataLoader.save_csv(bars, str(p))
    first = p.read_bytes()
    DataLoader.save_csv(bars, str(p))
    assert p.read_bytes() == first, "re-snapshot of identical candles must not churn bytes"


# ── manifest + load + verify ────────────────────────────────────────────

def test_manifest_records_symbols_and_deterministic_dataset_hash(tmp_path):
    syms = {"BTC/USDT:USDT": _synth(1, 100.0), "ETH/USDT:USDT": _synth(2, 50.0)}
    man = _write_snapshot(tmp_path, syms)
    assert set(man["symbols"]) == set(syms)
    assert man["symbols"]["BTC/USDT:USDT"]["bars"] == 300
    assert man["symbols"]["BTC/USDT:USDT"]["file"] == "BTC_USDT_USDT.csv.gz"
    # dataset_hash is a pure function of the per-symbol content hashes.
    hashes = {s: man["symbols"][s]["sha256"] for s in syms}
    assert man["dataset_hash"] == snap.dataset_hash(hashes)
    # Independent of insertion order.
    assert snap.dataset_hash(dict(reversed(list(hashes.items())))) == man["dataset_hash"]


def test_load_symbol_and_dataset_round_trip(tmp_path):
    syms = {"BTC/USDT:USDT": _synth(1, 100.0), "SOL/USDT:USDT": _synth(4, 30.0)}
    _write_snapshot(tmp_path, syms)
    one = snap.load_symbol(tmp_path, "SOL/USDT:USDT")
    assert DataLoader.content_hash(one) == DataLoader.content_hash(syms["SOL/USDT:USDT"])
    assert all(b.symbol == "SOL/USDT:USDT" for b in one)
    full = snap.load_dataset(tmp_path)
    assert set(full) == set(syms)


def test_load_symbol_missing_is_hard_error(tmp_path):
    _write_snapshot(tmp_path, {"BTC/USDT:USDT": _synth(1, 100.0)})
    with pytest.raises(KeyError):
        snap.load_symbol(tmp_path, "DOGE/USDT:USDT")


def test_load_manifest_missing_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        snap.load_manifest(tmp_path / "nope")


def test_verify_dataset_ok_then_detects_drift(tmp_path):
    syms = {"BTC/USDT:USDT": _synth(1, 100.0), "ETH/USDT:USDT": _synth(2, 50.0)}
    _write_snapshot(tmp_path, syms)
    ok, problems = snap.verify_dataset(tmp_path)
    assert ok and not problems

    # Tamper one candle file: verify must flag a content-hash drift.
    tampered = _synth(9, 100.0)
    DataLoader.save_csv(tampered, str(tmp_path / "BTC_USDT_USDT.csv.gz"))
    ok2, problems2 = snap.verify_dataset(tmp_path)
    assert not ok2
    assert any("BTC/USDT:USDT" in p for p in problems2)


def test_verify_flags_missing_file(tmp_path):
    _write_snapshot(tmp_path, {"BTC/USDT:USDT": _synth(1, 100.0)})
    (tmp_path / "BTC_USDT_USDT.csv.gz").unlink()
    ok, problems = snap.verify_dataset(tmp_path)
    assert not ok and any("missing" in p for p in problems)


# ── snapshot_dataset end-to-end (network stubbed) ───────────────────────

def test_snapshot_dataset_freezes_and_verifies(tmp_path, monkeypatch):
    canned = {"BTC/USDT:USDT": _synth(1, 100.0), "ETH/USDT:USDT": _synth(2, 50.0)}

    async def fake_from_bitget(symbol="BTC/USDT", timeframe="1h", limit=500):
        return list(canned[symbol])

    monkeypatch.setattr(DataLoader, "from_bitget", staticmethod(fake_from_bitget))
    man = asyncio.run(snap.snapshot_dataset(
        list(canned), "1h", 300, tmp_path, min_bars=100))
    assert set(man["symbols"]) == set(canned)
    ok, problems = snap.verify_dataset(tmp_path)
    assert ok and not problems


def test_snapshot_skips_thin_symbol(tmp_path, monkeypatch):
    canned = {"BTC/USDT:USDT": _synth(1, 100.0), "THIN/USDT:USDT": _synth(2, 50.0, n=10)}

    async def fake_from_bitget(symbol="BTC/USDT", timeframe="1h", limit=500):
        return list(canned[symbol])

    monkeypatch.setattr(DataLoader, "from_bitget", staticmethod(fake_from_bitget))
    man = asyncio.run(snap.snapshot_dataset(
        list(canned), "1h", 300, tmp_path, min_bars=100))
    # The 10-bar symbol is skipped, not written into the dataset.
    assert "THIN/USDT:USDT" not in man["symbols"]
    assert "BTC/USDT:USDT" in man["symbols"]


def test_snapshot_refuses_empty_dataset(tmp_path, monkeypatch):
    async def fake_from_bitget(symbol="BTC/USDT", timeframe="1h", limit=500):
        raise RuntimeError("offline")

    monkeypatch.setattr(DataLoader, "from_bitget", staticmethod(fake_from_bitget))
    with pytest.raises(RuntimeError):
        asyncio.run(snap.snapshot_dataset(["BTC/USDT:USDT"], "1h", 300, tmp_path))


# ── runner wiring ───────────────────────────────────────────────────────

def test_runner_exposes_dataset_flag_and_routes_through_snapshot():
    from bot.backtest import runner
    ns = runner.build_parser().parse_args(["--dataset", "some/dir"])
    assert ns.dataset == "some/dir"
    # Both the single/walk-forward loader and the portfolio loader must consult
    # the snapshot module when --dataset is set (not the live exchange).
    assert "snapshot" in inspect.getsource(runner._load_bars)
    assert "frozen_snapshot:" in inspect.getsource(runner._load_bars)
    assert "load_symbol" in inspect.getsource(runner._run_portfolio)


# ── the keystone: determinism of the frozen A/B ─────────────────────────

_DROP = {"duration_seconds", "timestamp"}  # wall-clock runtime metadata only


def _pnl_fingerprint(result) -> str:
    """A stable digest of everything that matters for an A/B: the scalar P&L
    metrics and every trade EXCEPT its random id / wall-clock idea timestamp."""
    d = result.model_dump(mode="json", exclude={"equity_curve"})
    scalars = {k: v for k, v in d.items() if k not in _DROP and k != "trades"}
    trades = [{k: v for k, v in t.items() if k != "trade_id"} for t in d.get("trades", [])]
    return json.dumps({"scalars": scalars, "trades": trades}, sort_keys=True, default=str)


def test_frozen_dataset_backtest_is_deterministic(tmp_path):
    """Two portfolio backtests over the SAME frozen snapshot yield identical
    P&L. This is what makes the benchmark trustworthy for small A/Bs: with the
    data pinned, the only thing that can move the result is the code."""
    from bot.backtest.models import BacktestConfig
    from bot.backtest.portfolio_engine import PortfolioBacktester

    syms = {
        "BTC/USDT:USDT": _synth(100, 100.0),
        "ETH/USDT:USDT": _synth(101, 50.0),
        "SOL/USDT:USDT": _synth(102, 30.0),
    }
    _write_snapshot(tmp_path, syms)

    async def run_once():
        data = snap.load_dataset(tmp_path)
        cfg = BacktestConfig(symbol=list(data)[0], timeframe="1h", fill_mode="next_open")
        pb = PortfolioBacktester(cfg, symbols=list(data))
        res = await pb.run(data)
        pb.cleanup()
        return _pnl_fingerprint(res)

    a = asyncio.run(run_once())
    b = asyncio.run(run_once())
    assert a == b, "frozen-dataset backtest must be deterministic across runs"
