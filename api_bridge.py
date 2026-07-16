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
  GET  /blackswan           - Black swan detector status
  GET  /patterns/{symbol}   - Chart + candlestick pattern detection
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Depends, Security, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse  # noqa: F401 (FileResponse kept for future static routes)
from pydantic import BaseModel

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.api.auth_routes import auth_router
from bot.api.lab import lab_router
from bot.core.chart_patterns import scan_all_chart_patterns
from bot.core.analyzer import _detect_candlestick_patterns
from bot.utils.models import (
    Direction,
    MarketSignal,
    RiskVerdict,
    TradeIdea,
)

# ── Universe ─────────────────────────────────────────────────────
# 66 symbols covering majors, alt L1s, DeFi, memes, infra, AI
UNIVERSE: list[str] = [
    # Majors
    "BTC/USDT", "ETH/USDT",
    # Alt L1s
    "SOL/USDT", "AVAX/USDT", "ADA/USDT", "DOT/USDT", "NEAR/USDT",
    "SUI/USDT", "APT/USDT", "ATOM/USDT", "TON/USDT", "HBAR/USDT",
    "TRX/USDT", "FTM/USDT", "SEI/USDT", "INJ/USDT",
    # L2 / Scaling
    "POL/USDT", "ARB/USDT", "OP/USDT", "STRK/USDT", "IMX/USDT",
    "MANTA/USDT",
    # DeFi
    "LINK/USDT", "UNI/USDT", "AAVE/USDT", "MKR/USDT", "SNX/USDT",
    "CRV/USDT", "DYDX/USDT", "LDO/USDT", "PENDLE/USDT", "JUP/USDT",
    "JTO/USDT",
    # Meme
    "DOGE/USDT", "SHIB/USDT", "PEPE/USDT", "FLOKI/USDT", "WIF/USDT",
    "BONK/USDT", "BRETT/USDT", "MEME/USDT",
    # AI / Compute
    "RENDER/USDT", "FET/USDT", "TAO/USDT", "ARKM/USDT",
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

# SEC-H4: In-memory rate limiter for API endpoints
#
# NOTE: this limiter is in-process and per-worker — it is best-effort only. For
# multi-worker / multi-replica deployments, rate limiting should move to the
# reverse proxy or a shared store (Redis is already provisioned in
# docker-compose.yml) so a single budget is enforced across all workers.
_rate_limits: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT = 30  # requests per minute per client IP

# RC-AUD-012: trusted-proxy handling for client-IP derivation.
# By default we key the limiter on request.client.host. X-Forwarded-For is
# client-SPOOFABLE, so we only consult it when TRUSTED_PROXY is configured
# (comma-separated list of the proxy IPs that sit in front of us). When set, we
# take the right-most XFF entry that is NOT itself a trusted proxy — i.e. the
# closest hop our trusted edge actually observed. Attacker-prepended entries are
# to the LEFT and are skipped, so the key cannot be spoofed past a real proxy.
_TRUSTED_PROXY_RAW = os.getenv("TRUSTED_PROXY", "").strip()
_TRUSTED_PROXIES: set[str] = {
    p.strip() for p in _TRUSTED_PROXY_RAW.split(",") if p.strip()
}


def _client_ip(request: Request) -> str:
    """Derive the client IP for rate limiting.

    Default (TRUSTED_PROXY unset): return request.client.host — unchanged
    behavior. When TRUSTED_PROXY is set, derive the IP from the right-most
    untrusted X-Forwarded-For entry, falling back to request.client.host when
    the header is missing/empty or every entry is a trusted proxy.
    """
    direct = request.client.host if request.client else "unknown"
    if not _TRUSTED_PROXIES:
        return direct
    xff = request.headers.get("x-forwarded-for", "")
    if not xff:
        return direct
    # Right-to-left: first entry that is not a known trusted proxy is the client.
    for hop in reversed([h.strip() for h in xff.split(",") if h.strip()]):
        if hop not in _TRUSTED_PROXIES:
            return hop
    return direct


def _check_rate_limit(client_ip: str) -> bool:
    """Return True if the request is allowed, False if rate-limited."""
    now = time.time()
    _rate_limits[client_ip] = [t for t in _rate_limits[client_ip] if now - t < 60]
    if len(_rate_limits[client_ip]) >= _RATE_LIMIT:
        return False
    _rate_limits[client_ip].append(now)
    # Prune stale entries to prevent unbounded memory growth
    if len(_rate_limits) > 1000:
        stale = [ip for ip, ts in _rate_limits.items()
                 if not ts or now - ts[-1] > 120]
        for ip in stale:
            del _rate_limits[ip]
    return True


async def _require_rate_limit(request: Request) -> None:
    """FastAPI dependency that enforces per-IP rate limiting."""
    client_ip = _client_ip(request)  # RC-AUD-012: trusted-proxy-aware
    if not _check_rate_limit(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Max 30 requests per minute.",
        )

# SEC-H3 FIX: strict symbol format validator for API entry points.
_SYMBOL_RE = re.compile(r'^[A-Z0-9]{1,15}(/[A-Z0-9]{1,15})?$')


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
    """Directional confluence scoring from basic indicators.

    Note: This is a simplified 5-indicator model used by the /scan API endpoint.
    The full 10/11-voter confluence model runs inside the AI analyzer (bot/core/analyzer.py).
    """
    bullish = 0
    bearish = 0
    total = 5
    signals = []

    # RSI
    if rsi < 30:
        bullish += 1
        signals.append("RSI oversold (bullish)")
    elif rsi > 70:
        bearish += 1
        signals.append("RSI overbought (bearish)")

    # EMA crossover
    if not np.isnan(ema_short) and not np.isnan(ema_long):
        if ema_short > ema_long:
            bullish += 1
            signals.append("EMA9 > EMA21 (bullish)")
        else:
            bearish += 1
            signals.append("EMA9 < EMA21 (bearish)")

    # Price vs EMA
    if not np.isnan(ema_long):
        if price > ema_long:
            bullish += 1
            signals.append("Price > EMA21 (bullish)")
        else:
            bearish += 1
            signals.append("Price < EMA21 (bearish)")

    # Volume spike
    if vol_ratio > 1.5:
        bullish += 1  # volume confirms momentum direction
        signals.append(f"Volume spike {vol_ratio:.1f}x")

    # Momentum (RSI outside neutral zone)
    if rsi < 40:
        bullish += 1
        signals.append("RSI bearish-to-neutral momentum")
    elif rsi > 60:
        bearish += 1
        signals.append("RSI bullish-to-neutral momentum")
    else:
        signals.append("RSI neutral — no momentum edge")

    # Directional score: positive = bullish, negative = bearish
    direction = "BULLISH" if bullish > bearish else "BEARISH" if bearish > bullish else "NEUTRAL"
    conviction = max(bullish, bearish)

    return {
        "score": round(conviction / max(total, 1), 2),
        "bullish": bullish,
        "bearish": bearish,
        "direction": direction,
        "total": total,
        "signals": signals,
    }


async def _fetch_ohlcv(symbol: str, timeframe: str = "1h", limit: int = 100) -> list | None:
    """Fetch OHLCV with rate limiting. Returns None on failure."""
    if engine is None: raise HTTPException(status_code=503, detail="Engine not initialized")
    async with _EXCHANGE_SEMAPHORE:
        try:
            exchange = await engine.get_exchange()
            ohlcv = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            # Closed bars only (audit): /scan and /analyze read closes[-1],
            # and an in-progress bar made the dashboard repaint intrabar and
            # disagree with the engine/executor, which analyze closed bars.
            if ohlcv:
                from bot.utils.candles import drop_forming_candle
                ohlcv = drop_forming_candle(ohlcv, timeframe)
            return ohlcv
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
        ex = await engine.get_exchange()
        if ex is not None:
            await ex.close()
    except Exception:
        pass


# ── App ──────────────────────────────────────────────────────────

app = FastAPI(
    title="RUNECLAW API Bridge",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,       # Disable Swagger UI (F-08)
    redoc_url=None,       # Disable ReDoc (F-08)
    openapi_url=None,     # Disable OpenAPI schema (F-09)
)

# RC-AUD-003: default to same-origin (no cross-origin) instead of "*".
# Operators who need cross-origin access set DASHBOARD_CORS_ORIGIN explicitly.
_cors_env = os.getenv("DASHBOARD_CORS_ORIGIN", os.getenv("CORS_ORIGINS", "")).strip()
_allowed_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=len(_allowed_origins) == 1 and _allowed_origins[0] != "*",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Mount auth routes for multi-user registration / login / link flow
app.include_router(auth_router, prefix="/auth", tags=["auth"])
# Strategy Lab: frozen-snapshot backtests for the web dashboard (bounded,
# single-job, subprocess-isolated — see bot/api/lab.py).
app.include_router(lab_router, tags=["lab"])

# ── Auth dependency for state-changing endpoints ────────────────
_DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")
_security = HTTPBearer(auto_error=False)

async def require_dashboard_token(
    credentials: HTTPAuthorizationCredentials = Security(_security),
) -> str:
    """Validate bearer token on state-changing endpoints."""
    if not _DASHBOARD_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="DASHBOARD_TOKEN not configured — state-changing endpoints disabled",
        )
    # AUDIT FIX: constant-time comparison to prevent timing attacks
    import hmac as _hmac
    if credentials is None or not _hmac.compare_digest(credentials.credentials, _DASHBOARD_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")
    return credentials.credentials


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
async def scan(req: ScanRequest, _rl: None = Depends(_require_rate_limit)):
    """Batch-scan universe symbols for signals and indicators."""
    if engine is None: raise HTTPException(status_code=503, detail="Engine not initialized")
    symbols = req.symbols or UNIVERSE

    # SEC-H3 FIX: validate any user-supplied symbols
    if req.symbols:
        for sym in req.symbols:
            if not _SYMBOL_RE.match(sym):
                raise HTTPException(status_code=400, detail=f"Invalid symbol format: '{sym}'")

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
async def analyze(req: AnalyzeRequest, _token: str = Depends(require_dashboard_token), _rl: None = Depends(_require_rate_limit)):
    """Run the full AI analyzer pipeline on a symbol."""
    if engine is None: raise HTTPException(status_code=503, detail="Engine not initialized")

    # SEC-H3 FIX: validate symbol before it reaches CCXT/LLM
    if not _SYMBOL_RE.match(req.symbol):
        raise HTTPException(status_code=400, detail="Invalid symbol format. Expected e.g. 'BTC/USDT'.")

    # 1. Fetch OHLCV
    ohlcv = await _fetch_ohlcv(req.symbol, req.timeframe, req.limit)
    if not ohlcv or len(ohlcv) < 20:
        raise HTTPException(status_code=400, detail=f"Insufficient OHLCV data for {req.symbol}")

    candles = np.array(ohlcv)
    closes = candles[:, 4].astype(float)
    price = float(closes[-1])

    # 2. Compute real values from OHLCV (not placeholders)
    change_pct_24h = ((price - float(closes[0])) / float(closes[0]) * 100) if float(closes[0]) > 0 else 0.0
    vol_avg = float(np.mean(candles[-20:, 5])) if len(candles) >= 20 else float(np.mean(candles[:, 5]))
    vol_current = float(candles[-1, 5])
    vol_spike = vol_current > vol_avg * 1.5

    signal = MarketSignal(
        symbol=req.symbol,
        price=price,
        change_pct_24h=round(change_pct_24h, 2),
        volume_usd_24h=vol_current,
        volume_spike=vol_spike,
        momentum_score=round((_rsi(closes) - 50) / 50, 2),  # normalize RSI to [-1, 1]
    )

    # 3. Get order flow (optional)
    of_signal = None
    try:
        exchange = await engine.get_exchange()
        of_signal = await engine.order_flow.analyze(exchange, req.symbol)
    except Exception:
        pass

    # 4. Run analyzer
    try:
        idea: Optional[TradeIdea] = await engine.analyzer.analyze(
            signal, ohlcv, order_flow=of_signal
        )
    except Exception as exc:
        # RC-AUD-013: log full error server-side; return a generic message so
        # exchange/library internals are not echoed to the client.
        import logging
        logging.getLogger("api_bridge").error("Analyzer error for %s: %s", req.symbol, exc)
        raise HTTPException(status_code=500, detail="Analyzer error (logged server-side)")

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
async def portfolio(_token: str = Depends(require_dashboard_token)):
    """Return portfolio snapshot and open positions."""
    if engine is None: raise HTTPException(status_code=503, detail="Engine not initialized")
    snap = engine.portfolio.snapshot()
    positions = [p.model_dump(mode="json") for p in engine.portfolio.open_positions]
    return {
        "snapshot": snap.model_dump(mode="json"),
        "positions": positions,
    }


@app.post("/confirm")
async def confirm_trade(req: ConfirmRequest, _token: str = Depends(require_dashboard_token), _rl: None = Depends(_require_rate_limit)):
    """Confirm a trade idea and open a position."""
    if engine is None: raise HTTPException(status_code=503, detail="Engine not initialized")

    # SEC-H3 FIX: validate asset symbol before it reaches CCXT
    if not _SYMBOL_RE.match(req.asset):
        raise HTTPException(status_code=400, detail="Invalid asset format. Expected e.g. 'BTC/USDT'.")

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

    # Fetch ATR for volatility guard (audit fix: /confirm must pass ATR)
    atr_val = None
    ohlcv = await _fetch_ohlcv(req.asset, "1h", limit=100)
    if ohlcv and len(ohlcv) >= 15:
        candles = np.array(ohlcv)
        highs = candles[:, 2].astype(float)
        lows = candles[:, 3].astype(float)
        closes = candles[:, 4].astype(float)
        atr_val = _atr(highs, lows, closes)

    # Risk gate with ATR
    risk_result = engine.risk.evaluate(idea, atr=atr_val)
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
        # RC-AUD-013: do not echo raw exception text to the client.
        import logging
        logging.getLogger("api_bridge").error("Position open failed: %s", exc)
        raise HTTPException(status_code=400, detail="Position open failed (logged server-side)")

    return {
        "status": "confirmed",
        "execution": execution.model_dump(mode="json"),
        "position_size_usd": risk_result.position_size_usd,
    }


@app.post("/portfolio/close/{symbol}")
async def close_position(symbol: str, _token: str = Depends(require_dashboard_token), _rl: None = Depends(_require_rate_limit)):
    """Force-close an open position by symbol (paper trading)."""
    if engine is None: raise HTTPException(status_code=503, detail="Engine not initialized")

    # SEC-H3 FIX: validate symbol before it reaches CCXT
    if not _SYMBOL_RE.match(symbol):
        raise HTTPException(status_code=400, detail="Invalid symbol format.")

    positions = engine.portfolio.open_positions
    target = None
    for pos in positions:
        if pos.asset == symbol:
            target = pos
            break

    if target is None:
        raise HTTPException(status_code=404, detail=f"No open position for {symbol}")

    # Fetch current price
    ohlcv = await _fetch_ohlcv(symbol, "1m", limit=1)
    current_price = float(ohlcv[-1][4]) if ohlcv else target.entry_price

    # Force close using public API
    execution = engine.portfolio.close_position(target.id, current_price)
    if execution is None:
        raise HTTPException(status_code=500, detail="Failed to close position")

    return {
        "status": "closed",
        "execution": execution.model_dump(mode="json"),
    }


@app.get("/risk/status")
async def risk_status(_token: str = Depends(require_dashboard_token)):
    """Return risk engine state."""
    if engine is None: raise HTTPException(status_code=503, detail="Engine not initialized")
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
    """Black swan detector status — returns latest anomaly alerts if available."""
    if engine is None: raise HTTPException(status_code=503, detail="Engine not initialized")
    try:
        detector = getattr(engine, 'black_swan', None)
        if detector is None:
            return {
                "status": "not_initialized",
                "alerts": [],
                "note": "Black swan detector not initialized in current engine configuration.",
            }
        alerts = getattr(detector, 'recent_alerts', [])
        return {
            "status": "active" if alerts else "monitoring",
            "alerts": [a.model_dump(mode="json") if hasattr(a, 'model_dump') else a for a in alerts[-10:]],
            "anomaly_types": ["CORRELATION_BREAKDOWN", "VOLUME_COLLAPSE", "PRICE_ACCELERATION", "VOLATILITY_EXPLOSION", "SPREAD_WIDENING"],
        }
    except Exception:
        return {
            "status": "error",
            "alerts": [],
            "note": "Black swan detector encountered an error.",
        }


@app.get("/patterns/{symbol}")
async def patterns(symbol: str, timeframe: str = "1h", limit: int = 100, _rl: None = Depends(_require_rate_limit)):
    """Detect chart patterns and candlestick patterns for a symbol."""
    if engine is None: raise HTTPException(status_code=503, detail="Engine not initialized")

    # SEC-H3 FIX: validate symbol before it reaches CCXT
    if not _SYMBOL_RE.match(symbol):
        raise HTTPException(status_code=400, detail="Invalid symbol format.")

    ohlcv = await _fetch_ohlcv(symbol, timeframe, limit)
    # Repaint guard (audit fix): drop the in-progress candle so a mid-bar
    # "doji"/"hammer" shown on the dashboard can't vanish at bar close.
    if ohlcv:
        from bot.utils.candles import drop_forming_candle
        ohlcv = drop_forming_candle(ohlcv, timeframe)
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

@app.get("/insight/{symbol}")
async def insight(symbol: str, timeframe: str = "1h", limit: int = 200,
                  _rl: None = Depends(_require_rate_limit)):
    """Consolidated decision picture for the dashboard: scored S/R levels,
    FVGs, liquidity pools, premium/discount, regime, the signed per-voter
    confluence breakdown, tape CVD and gate telemetry — the same inputs the
    bot trades off, not a parallel implementation."""
    if engine is None: raise HTTPException(status_code=503, detail="Engine not initialized")
    if not _SYMBOL_RE.match(symbol):
        raise HTTPException(status_code=400, detail="Invalid symbol format.")

    ohlcv = await _fetch_ohlcv(symbol, timeframe, limit)
    if ohlcv:
        from bot.utils.candles import drop_forming_candle
        ohlcv = drop_forming_candle(ohlcv, timeframe)
    if not ohlcv or len(ohlcv) < 40:
        raise HTTPException(status_code=400, detail=f"Insufficient data for {symbol}")

    candles = np.array(ohlcv, dtype=float)
    times, opens = candles[:, 0], candles[:, 1]
    highs, lows = candles[:, 2], candles[:, 3]
    closes, volumes = candles[:, 4], candles[:, 5]

    from bot.core.analyzer import Analyzer
    from bot.core.levels import gather_levels
    from bot.core.smc import equal_level_pools, find_fvgs, premium_discount
    from bot.core.volume_profile import compute_volume_profile
    from bot.utils.models import MarketSignal

    ind = Analyzer._compute_indicators(highs, lows, closes, volumes,
                                       opens=opens, times=times)
    atr = float(ind.get("atr") or 0.0)
    vp = None
    try:
        vpr = compute_volume_profile(highs, lows, closes, volumes,
                                     current_price=float(closes[-1]))
        if vpr is not None:
            vp = {"poc": vpr.poc, "vah": vpr.vah, "val": vpr.val}
            ind["volume_profile"] = {**vp, "price_vs_poc": vpr.price_vs_poc,
                                     "in_value_area": vpr.price_in_value_area,
                                     "skew": vpr.profile_skew}
    except Exception:
        vp = None

    levels = gather_levels(highs, lows, closes, atr, times=times, vp=vp) if atr > 0 else []
    fvgs = find_fvgs(highs, lows, closes) if atr > 0 else []
    pools = equal_level_pools(highs, lows, atr) if atr > 0 else {"eqh": [], "eql": []}
    pd_pos = premium_discount(highs, lows, closes, window=min(100, len(closes)))

    sig = MarketSignal(symbol=symbol, price=float(closes[-1]),
                       change_pct_24h=float((closes[-1] - closes[-25]) / closes[-25] * 100)
                       if len(closes) > 25 and closes[-25] > 0 else 0.0,
                       volume_usd_24h=float(np.sum(volumes[-24:] * closes[-24:])))
    regime = engine.analyzer._detect_regime(ind, symbol)
    breakdown: list = []
    try:
        confluence = Analyzer._score_confluence(ind, regime, sig, breakdown=breakdown)
    except Exception:
        confluence = 0.5

    # Regime ribbon: the SAME detector the bot trades with, replayed at a
    # coarse stride over the window (each sample sees only bars up to its
    # own close — no lookahead). ~40 samples bounds the cost; the client
    # paints forward from each sample to the next.
    regime_series: list = []
    try:
        n_bars = len(closes)
        stride = max(5, n_bars // 40)
        for i in range(60, n_bars + 1, stride):
            ind_i = Analyzer._compute_indicators(
                highs[:i], lows[:i], closes[:i], volumes[:i],
                opens=opens[:i], times=times[:i])
            r_i = engine.analyzer._detect_regime(ind_i, symbol)
            regime_series.append({"t": int(times[i - 1]), "regime": r_i.value})
    except Exception:
        regime_series = []

    cvd = None
    try:
        tape = engine.ws_feed.get_cvd(symbol) if engine.ws_feed else None
        if tape:
            cvd = {"cum_delta_usd": tape["cum_delta_usd"],
                   "series": tape["series"][-120:],
                   "prices": tape["prices"][-120:],
                   "trades": tape["trades"], "age_sec": tape["age_sec"]}
    except Exception:
        cvd = None

    return {
        "symbol": symbol, "timeframe": timeframe,
        "price": float(closes[-1]), "atr": atr,
        "regime": regime.value, "regime_series": regime_series,
        "confluence": round(float(confluence), 4),
        "levels": [{"price": lv.price, "kind": lv.kind, "touches": lv.touches,
                    "score": round(lv.score, 2)} for lv in levels],
        "fvgs": [{"kind": g.kind, "top": g.top, "bottom": g.bottom,
                  "filled": g.filled} for g in fvgs],
        "pools": pools,
        "premium_discount": round(pd_pos, 4) if pd_pos is not None else None,
        "cvd": cvd,
        "votes": [{"name": n, "vote": round(float(v), 4),
                   "weight": round(float(w), 4)}
                  for n, v, w in breakdown if abs(w) > 1e-9],
        "gates": engine.risk.gate_stats() if hasattr(engine.risk, "gate_stats") else {},
        "risk_state": (engine.risk.streak_state()
                       if hasattr(engine.risk, "streak_state") else {}),
        "flow": (engine.risk.eval_stats()
                 if hasattr(engine.risk, "eval_stats") else {}),
    }


@app.post("/risk/halt")
async def risk_halt(_token: str = Depends(require_dashboard_token), _rl: None = Depends(_require_rate_limit)):
    """Emergency stop — activate circuit breaker, close all positions."""
    if engine is None:
        raise HTTPException(503, "Engine not initialized")
    try:
        engine.risk.emergency_halt("Emergency halt from dashboard")
    except Exception:
        pass
    return {"ok": True, "circuit_breaker_active": True, "message": "Emergency halt activated"}


# ── Static website serving ──────────────────────────────────────
# The legacy hackathon dashboards (warroom, dashboard-pro, live-signals, …)
# were retired: the product's one face is the app/ web platform. website/
# keeps only the gateway landing page, privacy, the archived hackathon
# submission, and media assets. Old page URLs redirect so bookmarks land on
# the gateway page instead of a 404.
# This must be LAST so API routes take priority over the static catchall.

_WEBSITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "website")


@app.get("/platform-url")
async def platform_url():
    """The app platform's base URL (operator-config WEBSITE_URL, empty if
    unset). The gateway landing page uses this to render its CTA."""
    return {"url": os.environ.get("WEBSITE_URL", "").rstrip("/")}


if os.path.isdir(_WEBSITE_DIR):
    @app.get("/warroom")
    @app.get("/warroom.html")
    @app.get("/live-signals")
    @app.get("/live-signals.html")
    @app.get("/register")
    @app.get("/register.html")
    @app.get("/dashboard")
    async def legacy_page_redirect():
        # Bookmarked legacy dashboards -> the platform when configured,
        # else the gateway landing page.
        target = os.environ.get("WEBSITE_URL", "").rstrip("/") or "/"
        return RedirectResponse(target, status_code=302)

    # Mount static assets (landing page, privacy, submission archive, media)
    app.mount("/", StaticFiles(directory=_WEBSITE_DIR, html=True), name="website")


# ── Run ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_bridge:app", host="0.0.0.0", port=8000, reload=False)
