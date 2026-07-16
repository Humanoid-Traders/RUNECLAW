"""
Strategy Lab — run the frozen-snapshot backtester from the web dashboard.

The bot already has a serious backtest engine (honest fee/fill fidelity,
frozen benchmark snapshots with content hashes) that was only reachable from
a shell. This router exposes it to the web app in a tightly-bounded way:

    GET  /lab/meta        what can be run (datasets, symbols, bar ranges)
    POST /lab/run         start ONE backtest job  -> {job_id}
    GET  /lab/status/{id} poll                    -> running | done | error

Bounded on purpose:
  - one job at a time, process-wide (a backtest saturates a core; a queue of
    them would starve the live engine sharing the host);
  - dataset names are whitelisted against data/benchmark/ (no path input);
  - symbols must exist in the chosen snapshot's manifest, max 4 per run;
  - last_bars/balance/confidence are clamped to sane ranges;
  - the run happens in a SUBPROCESS (python -m bot.backtest.runner) with a
    hard timeout, so a hung run can't wedge the bridge event loop, and the
    runner's own --strict-data guarantee holds (frozen bars only, no network).

Read-only with respect to the live account: the runner never touches the
exchange in --dataset mode.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

lab_router = APIRouter()

_BENCH_DIR = Path("data/benchmark")
_OUT_DIR = Path(os.environ.get("RUNECLAW_STATE_DIR", "data")) / "lab"
_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_TIMEOUT_SEC = float(os.environ.get("LAB_TIMEOUT_SEC", "600"))
_MIN_SUBMIT_GAP_SEC = 5.0

# Single-slot job registry. Results are kept for the session (small dicts).
_jobs: dict[str, dict] = {}
_running_id: Optional[str] = None
_last_submit: float = 0.0


class LabRunRequest(BaseModel):
    dataset: str
    symbols: list[str] = Field(default_factory=list, max_length=4)
    last_bars: int = 1500
    confidence_threshold: float = 0.0
    balance: float = 10_000.0


def _datasets() -> dict[str, dict]:
    """{name: manifest-summary} for every valid snapshot under data/benchmark."""
    out: dict[str, dict] = {}
    if not _BENCH_DIR.is_dir():
        return out
    for d in sorted(_BENCH_DIR.iterdir()):
        man_path = d / "manifest.json"
        if not d.is_dir() or not _NAME_RE.match(d.name) or not man_path.exists():
            continue
        try:
            man = json.loads(man_path.read_text())
            syms = man.get("symbols", {})
            first = min((v.get("first", "") for v in syms.values()), default="")
            last = max((v.get("last", "") for v in syms.values()), default="")
            out[d.name] = {
                "timeframe": man.get("timeframe", "1h"),
                "symbols": sorted(syms.keys()),
                "bars": max((int(v.get("bars", 0)) for v in syms.values()), default=0),
                "first": first, "last": last,
                "dataset_hash": str(man.get("dataset_hash", ""))[:12],
            }
        except Exception:
            continue
    return out


@lab_router.get("/lab/meta")
async def lab_meta():
    return {"datasets": _datasets(),
            "limits": {"max_symbols": 4, "last_bars": [200, 6000],
                       "timeout_sec": _TIMEOUT_SEC}}


@lab_router.post("/lab/run")
async def lab_run(req: LabRunRequest):
    global _running_id, _last_submit
    now = time.monotonic()
    if now - _last_submit < _MIN_SUBMIT_GAP_SEC:
        raise HTTPException(status_code=429, detail="Slow down — one submission "
                            "every few seconds.")
    if _running_id and _jobs.get(_running_id, {}).get("status") == "running":
        raise HTTPException(status_code=409, detail="A backtest is already "
                            "running — poll it or wait for it to finish.")

    cats = _datasets()
    if req.dataset not in cats:
        raise HTTPException(status_code=400,
                            detail=f"Unknown dataset. Available: {sorted(cats)}")
    available = set(cats[req.dataset]["symbols"])
    symbols = [s.strip() for s in req.symbols if s.strip()][:4]
    if not symbols:
        symbols = sorted(available)[:3]
    bad = [s for s in symbols if s not in available]
    if bad:
        raise HTTPException(status_code=400,
                            detail=f"Not in this snapshot: {bad}")
    last_bars = max(200, min(6000, int(req.last_bars)))
    confidence = max(0.0, min(1.0, float(req.confidence_threshold)))
    balance = max(100.0, min(1_000_000.0, float(req.balance)))

    job_id = uuid.uuid4().hex[:12]
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = _OUT_DIR / f"{job_id}.json"
    cmd = [
        sys.executable, "-m", "bot.backtest.runner",
        "--dataset", str(_BENCH_DIR / req.dataset),
        "--symbols", ",".join(symbols),
        "--last-bars", str(last_bars),
        "--confidence-threshold", str(confidence),
        "--balance", str(balance),
        "--honest", "--strict-data",
        "--output", str(out_file),
    ]
    _jobs[job_id] = {
        "status": "running", "started_at": time.time(),
        "params": {"dataset": req.dataset, "symbols": symbols,
                   "last_bars": last_bars, "confidence_threshold": confidence,
                   "balance": balance},
    }
    _running_id = job_id
    _last_submit = now
    asyncio.get_running_loop().create_task(_run_job(job_id, cmd, out_file))
    return {"job_id": job_id, "status": "running",
            "params": _jobs[job_id]["params"]}


async def _run_job(job_id: str, cmd: list[str], out_file: Path) -> None:
    global _running_id
    job = _jobs[job_id]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(),
                                               timeout=_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            proc.kill()
            job.update(status="error",
                       error=f"Backtest exceeded {_TIMEOUT_SEC:.0f}s — try "
                             "fewer symbols or a smaller last_bars.")
            return
        tail = (stdout or b"").decode(errors="replace")[-2000:]
        if proc.returncode != 0 or not out_file.exists():
            job.update(status="error",
                       error="Backtest run failed.", log_tail=tail)
            return
        result = json.loads(out_file.read_text())
        job.update(status="done", finished_at=time.time(), result=result)
    except Exception as exc:
        job.update(status="error", error=f"Lab job crashed: {exc}")
    finally:
        try:
            out_file.unlink(missing_ok=True)
        except OSError:
            pass
        if _running_id == job_id:
            _running_id = None


@lab_router.get("/lab/status/{job_id}")
async def lab_status(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job.")
    out = {"job_id": job_id, "status": job["status"],
           "params": job.get("params"),
           "elapsed_sec": round(time.time() - job["started_at"], 1)}
    if job["status"] == "done":
        out["result"] = job.get("result")
    elif job["status"] == "error":
        out["error"] = job.get("error")
        if job.get("log_tail"):
            out["log_tail"] = job["log_tail"]
    return out
