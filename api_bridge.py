"""
RUNECLAW API Bridge -- FastAPI server exposing trading engine endpoints.

Endpoints:
  GET  /health              - Engine health + uptime
  POST /scan                - Batch scan universe for signals + indicators
  POST /analyze             - Analyze a specific signal via AI analyzer
  GET  /portfolio           - Portfolio snapshot + open positions
  POST /confirm             - Confirm a pending trade idea
  POST /portfolio/close/{symbol} - Close an open position
  GET  /risk/status         - Risk engine state + circuit breaker
  GET  /blackswan           - Black swan detector (stub)
  GET  /patterns/{symbol}   - Chart + candlestick pattern detection
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.api.auth_routes import auth_router
from bot.core.chart_patterns import scan_all_chart_patterns
from bot.core.analyzer import _detect_candlestick_patterns
from bot.utils.models import (
    Direction,
    MarketSignal,
    RiskVerdict,
    TradeIdea,
)

# ── Universe ─────────────────────────────────────────────────────
# 67 symbols covering majors, alt L1s, DeFi, memes, infra, AI
UNIVERSE: list[str] = [
    # Majors
    "BTC/USDT", "ETH/USDT",
    # Alt L1s
    "SOL/USDT", "AVAX/USDT", "ADA/USDT", "DOT/USDT", "NEAR/USDT",
    "SUI/USDT", "APT/USDT", "ATOM/USDT", "TON/USDT", "HBAR/USDT",
    "TRX/USDT", "FTM/USDT", "SEI/USDT", "INJ/USDT",
    # L2 / Scaling
    "MATIC/USDT", "ARB/USDT", "OP/USDT", "STRK/USDT", "IMX/USDT",
    "MANTA/USDT",
    # DeFi
    "LINK/USDT", "UNI/USDT", "AAVE/USDT", "MKR/USDT", "SNX/USDT",
    "CRV/USDT", "DYDX/USDT", "LDO/USDT", "PENDLE/USDT", "JUP/USDT",
    "JTO/USDT",
    # Meme
    "DOGE/USDT", "SHIB/USDT", "PEPE/USDT", "FLOKI/USDT", "WIF/USDT",
    "BONK/USDT", "BRETT/USDT", "MEME/USDT",
    # AI / Compute
    "RENDER/USDT", "FET/USDT", "RNDR/USDT", "TAO/USDT", "ARKM/USDT",
    # Infra / Oracle
    "PYTH/USDT", "TIA/USDT", "DYM/USDT", "ALT/USDT",
    # Gaming / Metaverse
    "GALA/USDT", "AXS/USDT", "SAND/USDT", "MANA/USDT", "PIXEL/USDT",
    # Exchange tokens
    "BNB/USDT", "OKB/USDT", "CRO/USDT",
    # Storage / Data
    "FIL/USDT", "AR/USDT",
    # Solana ecosystem extras
    "RAY/USDT", "ORCA/USDT", "HNT/USDT", "W/USDT", "DRIFT/USDT",
    # Cross-chain
    "RUNE/USDT", "STX/USDT",
    # Privacy
    "XMR/USDT",
    # Misc
    "XRP/USDT", "LTC/USDT", "ETC/USDT",
]

# ── Globals ──────────────────────────────────────────────────────
engine: RuneClawEngine | None = None
_start_time: float = 0.0

# Rate-limit: max concurrent exchange requests
_EXCHANGE_SEMAPHORE = asyncio.Semaphore(5)
_SCAN_BATCH_SIZE = 10


# ── Helpers ──────────────────────────────────────────────────────

def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average."""
    out = np.full_like(arr, np.nan)
    if len(arr) < period:
        return out
    out[period - 1] = np.mean(arr[:period])
    k = 2.0 / (period + 1)
    for i in range(period, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    """Compute latest RSI value."""
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1.0 + rs), 2)


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    """Average True Range (latest value)."""
    if len(closes) < period + 1:
        return 0.0
    tr = np.maximum(highs[1:] - lows[1:],
                    np.maximum(np.abs(highs[1:] - closes[:-1]),
                               np.abs(lows[1:] - closes[:-1])))
    return float(np.mean(tr[-period:]))


def _confluence_score(rsi: float, ema_short: float, ema_long: float,
                      price: float, vol_ratio: float) -> dict:
    """Simple confluence scoring from basic indicators."""
    votes = 0
    total = 5
    signals = []

    # RSI
    if rsi < 30:
        votes += 1
        signals.append("RSI oversold (bullish)")
    elif rsi > 70:
        votes += 1
        signals.append("RSI overbought (bearish)")

    # EMA crossover
    if not np.isnan(ema_short) and not np.isnan(ema_long):
        if ema_short > ema_long:
            votes += 1
            signals.append("EMA9 > EMA21 (bullish)")
        else:
            votes += 1
            signals.append("EMA9 < EMA21 (bearish)")

    # Price vs EMA
    if not np.isnan(ema_long):
        if price > ema_long:
            votes += 1
            signals.append("Price > EMA21 (bullish)")
        else:
            votes += 1
            signals.append("Price < EMA21 (bearish)")

    # Volume spike
    if vol_ratio > 1.5:
        votes += 1
        signals.append(f"Volume spike {vol_ratio:.1f}x")

    # Momentum (RSI slope proxy)
    if 40 < rsi < 60:
        signals.append("RSI neutral — no momentum edge")
    else:
        votes += 1
        signals.append("RSI momentum confirmation")

    return {
        "score": round(votes / max(total, 1), 2),
        "votes": votes,
        "total": total,
        "signals": signals,
    }


async def _fetch_ohlcv(symbol: str, timeframe: str = "1h", limit: int = 100) -> list | None:
    """Fetch OHLCV with rate limiting. Returns None on failure."""
    assert engine is not None
    async with _EXCHANGE_SEMAPHORE:
        try:
            exchange = await engine.scanner._get_exchange()
            return await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        except Exception:
            return None


# ── Request / Response models ────────────────────────────────────

class ScanRequest(BaseModel):
    timeframe: str = "1h"
    limit: int = 100
    symbols: list[str] | None = None  # None = full universe

class AnalyzeRequest(BaseModel):
    symbol: str
    timeframe: str = "1h"
    limit: int = 100

class ConfirmRequest(BaseModel):
    trade_id: str
    asset: str
    direction: str  # "LONG" or "SHORT"
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: float = 0.7
    reasoning: str = "Manual confirmation via API"


# ── Lifespan ─────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine, _start_time
    engine = RuneClawEngine()
    _start_time = time.time()
    yield
    # Cleanup: close exchange connection
    try:
        ex = engine.scanner._exchange
        if ex is not None:
            await ex.close()
    except Exception:
        pass


# ── App ──────────────────────────────────────────────────────────

app = FastAPI(
    title="RUNECLAW API Bridge",
    version="1.0.0",
    lifespan=lifespan,
)

_allowed_origins = os.getenv("CORS_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=len(_allowed_origins) == 1 and _allowed_origins[0] != "*",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Mount auth routes for multi-user registration / login / link flow
app.include_router(auth_router, prefix="/auth", tags=["auth"])


# ── Endpoints ────────────────────────────────────────────────────

@app.get("/health")
async def health():
    uptime = round(time.time() - _start_time, 1) if _start_time else 0
    return {
        "status": "ok",
        "uptime_seconds": uptime,
        "simulation_mode": CONFIG.simulation_mode,
        "circuit_breaker_active": engine.risk.circuit_breaker_active if engine else False,
        "open_positions": len(engine.portfolio.open_positions) if engine else 0,
        "universe_size": len(UNIVERSE),
    }


@app.post("/scan")
async def scan(req: ScanRequest):
    """Batch-scan universe symbols for signals and indicators."""
    assert engine is not None
    symbols = req.symbols or UNIVERSE
    results: list[dict] = []
    errors: list[str] = []

    # Process in batches
    for batch_start in range(0, len(symbols), _SCAN_BATCH_SIZE):
        batch = symbols[batch_start : batch_start + _SCAN_BATCH_SIZE]
        tasks = [_scan_single(sym, req.timeframe, req.limit) for sym in batch]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        for sym, res in zip(batch, batch_results):
            if isinstance(res, Exception):
                errors.append(f"{sym}: {res}")
            elif res is not None:
                results.append(res)

    # Sort by confluence score descending
    results.sort(key=lambda r: r.get("confluence", {}).get("score", 0), reverse=True)
    return {
        "count": len(results),
        "errors": len(errors),
        "symbols_scanned": len(symbols),
        "results": results,
    }


async def _scan_single(symbol: str, timeframe: str, limit: int) -> dict | None:
    """Scan a single symbol: fetch OHLCV, compute indicators, detect patterns."""
    ohlcv = await _fetch_ohlcv(symbol, timeframe, limit)
    if not ohlcv or len(ohlcv) < 20:
        return None

    candles = np.array(ohlcv)
    opens = candles[:, 1].astype(float)
    highs = candles[:, 2].astype(float)
    lows = candles[:, 3].astype(float)
    closes = candles[:, 4].astype(float)
    volumes = candles[:, 5].astype(float)

    price = float(closes[-1])
    rsi_val = _rsi(closes)
    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    atr_val = _atr(highs, lows, closes)

    # Volume ratio: current vs 20-bar average
    vol_avg = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else float(np.mean(volumes))
    vol_ratio = float(volumes[-1] / vol_avg) if vol_avg > 0 else 1.0

    confluence = _confluence_score(rsi_val, float(ema9[-1]), float(ema21[-1]), price, vol_ratio)

    # Chart patterns
    try:
        chart_pats = scan_all_chart_patterns(opens, highs, lows, closes, lookback=5)
    except Exception:
        chart_pats = []

    return {
        "symbol": symbol,
        "price": round(price, 6),
        "indicators": {
            "rsi_14": rsi_val,
            "ema_9": round(float(ema9[-1]), 6) if not np.isnan(ema9[-1]) else None,
            "ema_21": round(float(ema21[-1]), 6) if not np.isnan(ema21[-1]) else None,
            "atr_14": round(atr_val, 6),
            "volume_ratio": round(vol_ratio, 2),
        },
        "confluence": confluence,
        "chart_patterns": chart_pats[:5],  # top 5 by confidence
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    """Run the full AI analyzer pipeline on a symbol."""
    assert engine is not None

    # 1. Fetch OHLCV
    ohlcv = await _fetch_ohlcv(req.symbol, req.timeframe, req.limit)
    if not ohlcv or len(ohlcv) < 20:
        raise HTTPException(status_code=400, detail=f"Insufficient OHLCV data for {req.symbol}")

    candles = np.array(ohlcv)
    closes = candles[:, 4].astype(float)
    price = float(closes[-1])

    # 2. Build a MarketSignal for the analyzer
    signal = MarketSignal(
        symbol=req.symbol,
        price=price,
        change_pct_24h=0.0,
        volume_usd_24h=float(candles[-1, 5]),
        volume_spike=False,
        momentum_score=0.0,
    )

    # 3. Get order flow (optional)
    of_signal = None
    try:
        exchange = await engine.scanner._get_exchange()
        of_signal = await engine.order_flow.analyze(exchange, req.symbol)
    except Exception:
        pass

    # 4. Run analyzer
    try:
        idea: Optional[TradeIdea] = await engine.analyzer.analyze(
            signal, ohlcv, order_flow=of_signal
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Analyzer error: {exc}")

    if idea is None:
        return {"symbol": req.symbol, "idea": None, "reason": "Analyzer returned no trade idea"}

    # 5. Risk check
    highs = candles[:, 2].astype(float)
    lows = candles[:, 3].astype(float)
    atr_val = _atr(highs, lows, closes)
    risk_result = engine.risk.evaluate(idea, atr=atr_val)

    return {
        "symbol": req.symbol,
        "idea": idea.model_dump(mode="json"),
        "risk": {
            "verdict": risk_result.verdict.value,
            "reason": risk_result.reason,
            "position_size_usd": risk_result.position_size_usd,
            "checks_passed": risk_result.checks_passed,
            "checks_failed": risk_result.checks_failed,
        },
    }


@app.get("/portfolio")
async def portfolio():
    """Return portfolio snapshot and open positions."""
    assert engine is not None
    snap = engine.portfolio.snapshot()
    positions = [p.model_dump(mode="json") for p in engine.portfolio.open_positions]
    return {
        "snapshot": snap.model_dump(mode="json"),
        "positions": positions,
    }


@app.post("/confirm")
async def confirm_trade(req: ConfirmRequest):
    """Confirm a trade idea and open a position."""
    assert engine is not None

    direction = Direction.LONG if req.direction.upper() == "LONG" else Direction.SHORT
    idea = TradeIdea(
        id=req.trade_id,
        asset=req.asset,
        direction=direction,
        entry_price=req.entry_price,
        stop_loss=req.stop_loss,
        take_profit=req.take_profit,
        confidence=req.confidence,
        reasoning=req.reasoning,
    )

    # Risk gate
    risk_result = engine.risk.evaluate(idea)
    if risk_result.verdict == RiskVerdict.REJECTED:
        return {
            "status": "rejected",
            "reason": risk_result.reason,
            "checks_failed": risk_result.checks_failed,
        }

    # Open position
    try:
        execution = engine.portfolio.open_position(idea, risk_result.position_size_usd)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Position open failed: {exc}")

    return {
        "status": "confirmed",
        "execution": execution.model_dump(mode="json"),
        "position_size_usd": risk_result.position_size_usd,
    }


@app.post("/portfolio/close/{symbol}")
async def close_position(symbol: str):
    """Close an open position by symbol (paper trading)."""
    assert engine is not None

    # Find position
    positions = engine.portfolio.open_positions
    target = None
    for pos in positions:
        if pos.asset == symbol:
            target = pos
            break

    if target is None:
        raise HTTPException(status_code=404, detail=f"No open position for {symbol}")

    # Fetch current price for mark-to-market
    ohlcv = await _fetch_ohlcv(symbol, "1m", limit=1)
    if ohlcv:
        current_price = float(ohlcv[-1][4])
    else:
        current_price = target.entry_price  # fallback

    # Use check_stops with a price that triggers the close
    closed = engine.portfolio.check_stops({symbol: current_price})
    if not closed:
        # Force mark-to-market and close via stop price
        # Set stop to current price to force close
        engine.portfolio.mark_to_market({symbol: current_price})
        return {
            "status": "marked_to_market",
            "symbol": symbol,
            "current_price": current_price,
            "note": "Position marked but not closed — adjust stop to force exit",
        }

    return {
        "status": "closed",
        "executions": [e.model_dump(mode="json") for e in closed],
    }


@app.get("/risk/status")
async def risk_status():
    """Return risk engine state."""
    assert engine is not None
    return {
        "circuit_breaker_active": engine.risk.circuit_breaker_active,
        "consecutive_losses": engine.risk.consecutive_losses,
        "stats": engine.risk.stats,
        "rejection_history": engine.risk.rejection_history[-10:],
        "config": {
            "min_confidence": CONFIG.risk.min_confidence,
            "max_open_positions": CONFIG.risk.max_open_positions,
            "max_drawdown_pct": CONFIG.risk.max_drawdown_pct,
            "cooldown_after_loss_seconds": CONFIG.risk.cooldown_after_loss_seconds,
            "max_daily_loss_pct": CONFIG.risk.max_daily_loss_pct,
        },
    }


@app.get("/blackswan")
async def blackswan_status():
    """Black swan detector — stub endpoint (module not available)."""
    return {
        "status": "inactive",
        "alerts": [],
        "note": "Black swan detection is not yet integrated. This is a stub endpoint.",
    }


@app.get("/patterns/{symbol}")
async def patterns(symbol: str, timeframe: str = "1h", limit: int = 100):
    """Detect chart patterns and candlestick patterns for a symbol."""
    assert engine is not None

    ohlcv = await _fetch_ohlcv(symbol, timeframe, limit)
    if not ohlcv or len(ohlcv) < 20:
        raise HTTPException(status_code=400, detail=f"Insufficient data for {symbol}")

    candles = np.array(ohlcv)
    opens = candles[:, 1].astype(float)
    highs = candles[:, 2].astype(float)
    lows = candles[:, 3].astype(float)
    closes = candles[:, 4].astype(float)

    # Chart patterns (geometric)
    try:
        chart_pats = scan_all_chart_patterns(opens, highs, lows, closes, lookback=5)
    except Exception:
        chart_pats = []

    # Candlestick patterns
    try:
        candle_pats = _detect_candlestick_patterns(opens, highs, lows, closes)
    except Exception:
        candle_pats = {}

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candle_count": len(ohlcv),
        "chart_patterns": chart_pats,
        "candlestick_patterns": candle_pats,
        "price": round(float(closes[-1]), 6),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Emergency halt ──────────────────────────────────────────────

@app.post("/risk/halt")
async def risk_halt():
    """Emergency stop — activate circuit breaker, close all positions."""
    if engine is None:
        raise HTTPException(503, "Engine not initialized")
    try:
        engine.risk._trip_circuit_breaker("Emergency halt from dashboard")
    except Exception:
        pass
    return {"ok": True, "circuit_breaker_active": True, "message": "Emergency halt activated"}


# ── Static website serving ──────────────────────────────────────
# Serve website/ directory for dashboard, warroom, live-signals, register
# This must be LAST so API routes take priority over static file catchall

_WEBSITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "website")
if os.path.isdir(_WEBSITE_DIR):
    @app.get("/warroom")
    @app.get("/warroom.html")
    async def serve_warroom():
        return FileResponse(os.path.join(_WEBSITE_DIR, "warroom.html"))

    @app.get("/live-signals")
    @app.get("/live-signals.html")
    async def serve_live_signals():
        return FileResponse(os.path.join(_WEBSITE_DIR, "live-signals.html"))

    @app.get("/register")
    @app.get("/register.html")
    async def serve_register():
        return FileResponse(os.path.join(_WEBSITE_DIR, "register.html"))

    @app.get("/dashboard")
    async def serve_dashboard():
        _dash = os.path.join(_WEBSITE_DIR, "dashboard-pro.html")
        if os.path.exists(_dash):
            return FileResponse(_dash)
        return FileResponse(os.path.join(_WEBSITE_DIR, "index.html"))

    # Mount static assets (JS, CSS, images) — must be after named routes
    app.mount("/", StaticFiles(directory=_WEBSITE_DIR, html=True), name="website")


# ── Run ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_bridge:app", host="0.0.0.0", port=8000, reload=False)
