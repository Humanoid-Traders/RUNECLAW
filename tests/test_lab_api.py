"""
Strategy Lab router — bounded web-triggered backtests on frozen snapshots.

Pins the safety envelope: dataset whitelisting (no path input reaches the
filesystem), symbol validation against the snapshot manifest, parameter
clamping, the single-job gate, and a real end-to-end run against the
committed majors benchmark (subprocess, --strict-data — deterministic, no
network).
"""

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bot.api import lab as lab_mod
from bot.api.lab import lab_router


@pytest.fixture()
def client(monkeypatch):
    # Fresh job registry + no submit-gap interference between tests.
    monkeypatch.setattr(lab_mod, "_jobs", {})
    monkeypatch.setattr(lab_mod, "_running_id", None)
    monkeypatch.setattr(lab_mod, "_last_submit", 0.0)
    app = FastAPI()
    app.include_router(lab_router)
    # Context manager keeps ONE event loop alive across requests so the
    # background job task actually runs concurrently (as under uvicorn).
    with TestClient(app) as c:
        yield c


def test_meta_lists_committed_benchmarks(client):
    r = client.get("/lab/meta")
    assert r.status_code == 200
    ds = r.json()["datasets"]
    assert "majors_1h_v2" in ds
    assert "BTC/USDT:USDT" in ds["majors_1h_v2"]["symbols"]
    assert ds["majors_1h_v2"]["bars"] > 1000


def test_run_rejects_unknown_dataset_and_symbols(client):
    r = client.post("/lab/run", json={"dataset": "../../etc"})
    assert r.status_code == 400
    r = client.post("/lab/run", json={"dataset": "majors_1h_v2",
                                      "symbols": ["EVIL/USDT:USDT"]})
    assert r.status_code == 400
    assert "Not in this snapshot" in r.json()["detail"]


def test_full_run_returns_metrics_and_curve(client, monkeypatch):
    r = client.post("/lab/run", json={
        "dataset": "majors_1h_v2", "symbols": ["BTC/USDT:USDT"],
        "last_bars": 200, "balance": 5000,
    })
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    # Params were clamped/echoed back.
    assert r.json()["params"]["last_bars"] == 200

    # Second submission while running must be refused (single-job gate).
    monkeypatch.setattr(lab_mod, "_last_submit", 0.0)  # bypass the pace gate
    r2 = client.post("/lab/run", json={"dataset": "majors_1h_v2",
                                       "symbols": ["BTC/USDT:USDT"]})
    assert r2.status_code == 409

    deadline = time.time() + 180
    while time.time() < deadline:
        st = client.get(f"/lab/status/{job_id}").json()
        if st["status"] != "running":
            break
        time.sleep(1)
    assert st["status"] == "done", st.get("error", st)
    res = st["result"]
    assert res["initial_balance"] == 5000
    assert "profit_factor" in res and "max_drawdown_pct" in res
    assert isinstance(res["equity_curve_points"], list)
    assert res["data_source"].startswith("frozen_snapshot") or res["bars_processed"] > 0


def test_status_unknown_job_404(client):
    assert client.get("/lab/status/nope").status_code == 404
