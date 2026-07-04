"""`--dataset dir1,dir2` combines multiple frozen snapshots into one universe —
needed to A/B a combined-universe hypothesis (e.g. "does a mixed majors+alts
portfolio underperform majors-only") without an ad-hoc script. A single dir with
no comma is just a one-element combination, so callers can use the multi-aware
functions unconditionally.
"""
import json

import pytest

from bot.backtest import snapshot as snap
from bot.backtest.data_loader import DataLoader


def _write(out, symbols: dict, limit=300):
    out.mkdir(parents=True, exist_ok=True)
    for sym, bars in symbols.items():
        for b in bars:
            b.symbol = sym
        DataLoader.save_csv(bars, str(out / f"{snap.safe_symbol(sym)}.csv.gz"))
    man = snap.build_manifest(out, "1h", limit, symbols)
    (out / snap.MANIFEST_NAME).write_text(json.dumps(man, indent=2) + "\n")
    return man


def _bars(seed, start):
    return DataLoader.generate_synthetic(bars=200, seed=seed, start_price=start)


def test_split_dataset_arg():
    assert snap.split_dataset_arg("a") == ["a"]
    assert snap.split_dataset_arg("a,b") == ["a", "b"]
    assert snap.split_dataset_arg(" a , b ") == ["a", "b"]
    assert snap.split_dataset_arg("a,,b") == ["a", "b"]


def test_single_dir_matches_plain_load_manifest(tmp_path):
    d = tmp_path / "one"
    _write(d, {"BTC/USDT:USDT": _bars(1, 100.0)})
    single = snap.load_manifest(d)
    multi = snap.load_manifest_multi(str(d))
    assert multi["dataset_hash"] == single["dataset_hash"]
    assert set(multi["symbols"]) == set(single["symbols"])


def test_combines_two_dirs(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a, {"BTC/USDT:USDT": _bars(1, 100.0), "ETH/USDT:USDT": _bars(2, 50.0)})
    _write(b, {"TAG/USDT:USDT": _bars(3, 0.001)})
    man = snap.load_manifest_multi(f"{a},{b}")
    assert set(man["symbols"]) == {"BTC/USDT:USDT", "ETH/USDT:USDT", "TAG/USDT:USDT"}
    assert man["symbols"]["TAG/USDT:USDT"]["_dir"] == str(b)


def test_load_symbol_multi_reads_from_the_right_dir(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    btc = _bars(1, 100.0)
    tag = _bars(3, 0.001)
    _write(a, {"BTC/USDT:USDT": btc})
    _write(b, {"TAG/USDT:USDT": tag})
    dataset = f"{a},{b}"
    loaded_btc = snap.load_symbol_multi(dataset, "BTC/USDT:USDT")
    loaded_tag = snap.load_symbol_multi(dataset, "TAG/USDT:USDT")
    assert DataLoader.content_hash(loaded_btc) == DataLoader.content_hash(btc)
    assert DataLoader.content_hash(loaded_tag) == DataLoader.content_hash(tag)


def test_load_dataset_multi_loads_every_symbol(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a, {"BTC/USDT:USDT": _bars(1, 100.0)})
    _write(b, {"TAG/USDT:USDT": _bars(3, 0.001), "BLESS/USDT:USDT": _bars(4, 0.002)})
    full = snap.load_dataset_multi(f"{a},{b}")
    assert set(full) == {"BTC/USDT:USDT", "TAG/USDT:USDT", "BLESS/USDT:USDT"}


def test_duplicate_symbol_across_dirs_is_ambiguous_and_raises(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a, {"BTC/USDT:USDT": _bars(1, 100.0)})
    _write(b, {"BTC/USDT:USDT": _bars(9, 200.0)})  # same symbol, different dir
    with pytest.raises(ValueError, match="ambiguous"):
        snap.load_manifest_multi(f"{a},{b}")


def test_missing_symbol_in_combined_dataset_raises(tmp_path):
    a = tmp_path / "a"
    _write(a, {"BTC/USDT:USDT": _bars(1, 100.0)})
    with pytest.raises(KeyError):
        snap.load_symbol_multi(str(a), "NOPE/USDT:USDT")


def test_dataset_hash_is_order_independent_of_dir_sequence(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a, {"BTC/USDT:USDT": _bars(1, 100.0)})
    _write(b, {"TAG/USDT:USDT": _bars(3, 0.001)})
    forward = snap.load_manifest_multi(f"{a},{b}")
    backward = snap.load_manifest_multi(f"{b},{a}")
    assert forward["dataset_hash"] == backward["dataset_hash"]
