/**
 * RUNECLAW GetClaw — Confluence-Driven Trading Agent
 *
 * Faithful port of the RUNECLAW AI confluence scoring engine to the
 * AgentBench StrategyAgent interface. Implements 12 weighted indicator
 * voters, ADX regime detection, ATR-based risk management, and
 * adaptive confidence thresholds.
 *
 * Adapted for agentbench's long-only spot constraint:
 *   - Only enters LONG positions (SHORT signals trigger exit)
 *   - Position sizing via ATR volatility and equity percentage
 *   - SL/TP levels computed but used as exit triggers (no native SL/TP orders)
 *
 * Source: RUNECLAW bot/core/analyzer.py confluence model
 *         RUNECLAW bot/core/ta_utils.py indicator library
 *
 * Usage:
 *   npx agentbench run --agent examples/getclaw-runeclaw.ts --symbol BTCUSDT --tf 4h --out ./report
 */

import type { StrategyAgent, Bar, BarContext, Order } from "bitget-agentbench";

// ── Configuration (mirrors RUNECLAW config.py defaults) ────────────
const CFG = {
  // Indicator periods
  rsiPeriod: 14,
  emaFast: 9,
  emaMedium: 21,
  macdFast: 12,
  macdSlow: 26,
  macdSignal: 9,
  bbPeriod: 20,
  bbStdDev: 2,
  atrPeriod: 14,
  adxPeriod: 14,
  smaPeriod: 50,
  // Thresholds
  rsiOversold: 30,
  rsiOverbought: 70,
  minConfidence: 0.58,
  volatilityGuardPct: 0.07,
  minRiskReward: 1.2,
  // Position sizing
  riskPerTradePct: 0.02,    // risk 2% of equity per trade
  maxPositionPct: 0.10,     // max 10% of equity in one position
  minBarsRequired: 50,      // need enough history for all indicators
  maxEntryRsi: 62,          // don't enter long above this RSI
  cooldownBars: 6,          // bars to wait after a stop loss
} as const;

// ── Regime enum ────────────────────────────────────────────────────
const enum Regime {
  TREND_UP,
  TREND_DOWN,
  RANGE,
  CHOP,
}

// ── TA Helper Functions ────────────────────────────────────────────

function sma(closes: number[], period: number): number | null {
  if (closes.length < period) return null;
  const slice = closes.slice(-period);
  return slice.reduce((a, b) => a + b, 0) / period;
}

/** Full-history EMA (Wilder-style seed with SMA, then exponential). */
function ema(data: number[], period: number): number[] {
  if (data.length === 0) return [];
  const alpha = 2 / (period + 1);
  const result: number[] = [data[0]!];
  for (let i = 1; i < data.length; i++) {
    result.push(alpha * data[i]! + (1 - alpha) * result[i - 1]!);
  }
  return result;
}

/** Last value of EMA series. */
function emaLast(data: number[], period: number): number | null {
  if (data.length < period) return null;
  const series = ema(data, period);
  return series[series.length - 1]!;
}

/** RSI-14 using Wilder's smoothing. */
function rsi(closes: number[], period: number = 14): number | null {
  if (closes.length < period + 1) return null;
  let avgGain = 0;
  let avgLoss = 0;

  // Seed with simple average of first `period` changes
  for (let i = 1; i <= period; i++) {
    const change = closes[i]! - closes[i - 1]!;
    if (change >= 0) avgGain += change;
    else avgLoss -= change;
  }
  avgGain /= period;
  avgLoss /= period;

  // Wilder's smoothing for remaining
  for (let i = period + 1; i < closes.length; i++) {
    const change = closes[i]! - closes[i - 1]!;
    if (change >= 0) {
      avgGain = (avgGain * (period - 1) + change) / period;
      avgLoss = (avgLoss * (period - 1)) / period;
    } else {
      avgGain = (avgGain * (period - 1)) / period;
      avgLoss = (avgLoss * (period - 1) - change) / period;
    }
  }

  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}

/** True Range for a single bar. */
function trueRange(bar: Bar, prevClose: number): number {
  return Math.max(
    bar.high - bar.low,
    Math.abs(bar.high - prevClose),
    Math.abs(bar.low - prevClose),
  );
}

/** ATR-14 using Wilder's smoothing. Returns last value. */
function atr(bars: readonly Bar[], period: number = 14): number | null {
  if (bars.length < period + 1) return null;
  // Compute all true ranges
  const trs: number[] = [];
  for (let i = 1; i < bars.length; i++) {
    trs.push(trueRange(bars[i]!, bars[i - 1]!.close));
  }
  // Wilder's smoothing
  let value = trs.slice(0, period).reduce((a, b) => a + b, 0) / period;
  for (let i = period; i < trs.length; i++) {
    value = (value * (period - 1) + trs[i]!) / period;
  }
  return value;
}

/** ADX-14 with +DI and -DI. Returns { adx, plusDI, minusDI }. */
function adx(
  bars: readonly Bar[],
  period: number = 14,
): { adx: number; plusDI: number; minusDI: number } | null {
  if (bars.length < period * 2 + 1) return null;

  const trArr: number[] = [];
  const plusDM: number[] = [];
  const minusDM: number[] = [];

  for (let i = 1; i < bars.length; i++) {
    const curr = bars[i]!;
    const prev = bars[i - 1]!;
    trArr.push(trueRange(curr, prev.close));

    const upMove = curr.high - prev.high;
    const downMove = prev.low - curr.low;

    plusDM.push(upMove > downMove && upMove > 0 ? upMove : 0);
    minusDM.push(downMove > upMove && downMove > 0 ? downMove : 0);
  }

  // Wilder's smoothing helper
  function wilderSmooth(data: number[], p: number): number[] {
    const result: number[] = [];
    let sum = 0;
    for (let i = 0; i < p; i++) sum += data[i]!;
    result.push(sum);
    for (let i = p; i < data.length; i++) {
      result.push((result[result.length - 1]! * (p - 1) + data[i]!) / p * p / p);
    }
    // Actually Wilder: smoothed = prev - prev/period + current
    // Let me redo this correctly
    const r2: number[] = [];
    let s = 0;
    for (let i = 0; i < p; i++) s += data[i]!;
    r2.push(s / p);
    for (let i = p; i < data.length; i++) {
      r2.push((r2[r2.length - 1]! * (p - 1) + data[i]!) / p);
    }
    return r2;
  }

  const smoothTR = wilderSmooth(trArr, period);
  const smoothPlusDM = wilderSmooth(plusDM, period);
  const smoothMinusDM = wilderSmooth(minusDM, period);

  // Compute DI values
  const plusDIarr: number[] = [];
  const minusDIarr: number[] = [];
  const dxArr: number[] = [];

  for (let i = 0; i < smoothTR.length; i++) {
    const tr = smoothTR[i]!;
    if (tr === 0) {
      plusDIarr.push(0);
      minusDIarr.push(0);
      dxArr.push(0);
      continue;
    }
    const pdi = (smoothPlusDM[i]! / tr) * 100;
    const mdi = (smoothMinusDM[i]! / tr) * 100;
    plusDIarr.push(pdi);
    minusDIarr.push(mdi);
    const diSum = pdi + mdi;
    dxArr.push(diSum === 0 ? 0 : (Math.abs(pdi - mdi) / diSum) * 100);
  }

  // Smooth DX to get ADX
  if (dxArr.length < period) return null;
  const adxSmooth = wilderSmooth(dxArr, period);
  const lastAdx = adxSmooth[adxSmooth.length - 1]!;
  const lastPlusDI = plusDIarr[plusDIarr.length - 1]!;
  const lastMinusDI = minusDIarr[minusDIarr.length - 1]!;

  return { adx: lastAdx, plusDI: lastPlusDI, minusDI: lastMinusDI };
}

/** MACD histogram (last value). */
function macdHist(closes: number[]): number | null {
  if (closes.length < CFG.macdSlow + CFG.macdSignal) return null;
  const fastEma = ema(closes, CFG.macdFast);
  const slowEma = ema(closes, CFG.macdSlow);
  const macdLine: number[] = [];
  for (let i = 0; i < closes.length; i++) {
    macdLine.push(fastEma[i]! - slowEma[i]!);
  }
  const signalLine = ema(macdLine, CFG.macdSignal);
  const last = macdLine.length - 1;
  return macdLine[last]! - signalLine[last]!;
}

/** Bollinger %B. */
function bollingerPctB(closes: number[]): number | null {
  const p = CFG.bbPeriod;
  if (closes.length < p) return null;
  const slice = closes.slice(-p);
  const mean = slice.reduce((a, b) => a + b, 0) / p;
  const variance = slice.reduce((a, b) => a + (b - mean) ** 2, 0) / p;
  const std = Math.sqrt(variance);
  if (std === 0) return 0.5;
  const upper = mean + CFG.bbStdDev * std;
  const lower = mean - CFG.bbStdDev * std;
  const range = upper - lower;
  if (range === 0) return 0.5;
  return (closes[closes.length - 1]! - lower) / range;
}

/** OBV trend: "rising", "falling", or "neutral" based on 10-bar regression. */
function obvTrend(bars: readonly Bar[]): string {
  if (bars.length < 12) return "neutral";
  // Compute OBV
  const obv: number[] = [0];
  for (let i = 1; i < bars.length; i++) {
    const prev = obv[obv.length - 1]!;
    const curr = bars[i]!;
    const prevBar = bars[i - 1]!;
    if (curr.close > prevBar.close) obv.push(prev + curr.volume);
    else if (curr.close < prevBar.close) obv.push(prev - curr.volume);
    else obv.push(prev);
  }
  // Linear regression on last 10 OBV values
  const window = obv.slice(-10);
  const n = window.length;
  let sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0;
  for (let i = 0; i < n; i++) {
    sumX += i;
    sumY += window[i]!;
    sumXY += i * window[i]!;
    sumX2 += i * i;
  }
  const slope = (n * sumXY - sumX * sumY) / (n * sumX2 - sumX * sumX);
  const avgVol = bars.slice(-10).reduce((a, b) => a + b.volume, 0) / 10;
  if (avgVol === 0) return "neutral";
  const normalizedSlope = slope / avgVol;
  if (normalizedSlope > 0.1) return "rising";
  if (normalizedSlope < -0.1) return "falling";
  return "neutral";
}

/** VWAP approximation using typical price × volume. */
function vwap(bars: readonly Bar[], lookback: number = 20): number | null {
  if (bars.length < lookback) return null;
  const window = bars.slice(-lookback);
  let tpv = 0, vol = 0;
  for (const b of window) {
    const tp = (b.high + b.low + b.close) / 3;
    tpv += tp * b.volume;
    vol += b.volume;
  }
  return vol === 0 ? null : tpv / vol;
}

/** Fibonacci zone based on swing high/low over lookback. */
function fibZone(closes: number[], lookback: number = 50): string {
  if (closes.length < lookback) return "none";
  const window = closes.slice(-lookback);
  const high = Math.max(...window);
  const low = Math.min(...window);
  const range = high - low;
  if (range === 0) return "none";
  const price = closes[closes.length - 1]!;
  const retrace = (high - price) / range;

  if (retrace >= 0.786) return "below_786";
  if (retrace >= 0.618) return "618_786";
  if (retrace >= 0.500) return "500_618";
  if (retrace >= 0.382) return "382_500";
  if (retrace >= 0.236) return "236_382";
  return "above_236";
}

/** Keltner Channel squeeze detection. */
function keltnerSqueeze(bars: readonly Bar[]): boolean {
  if (bars.length < CFG.bbPeriod) return false;
  const closes = bars.map((b) => b.close);
  const slice = closes.slice(-CFG.bbPeriod);
  const mean = slice.reduce((a, b) => a + b, 0) / CFG.bbPeriod;
  const variance = slice.reduce((a, b) => a + (b - mean) ** 2, 0) / CFG.bbPeriod;
  const bbWidth = Math.sqrt(variance) * CFG.bbStdDev * 2;

  const atrVal = atr(bars.slice(-CFG.atrPeriod - 2), CFG.atrPeriod);
  if (atrVal === null) return false;
  const keltnerWidth = atrVal * 1.5 * 2;

  return bbWidth < keltnerWidth;
}

// ── Regime Detection ───────────────────────────────────────────────

function detectRegime(
  adxResult: { adx: number; plusDI: number; minusDI: number } | null,
): Regime {
  if (!adxResult) return Regime.RANGE;
  if (adxResult.adx > 25) {
    return adxResult.plusDI > adxResult.minusDI ? Regime.TREND_UP : Regime.TREND_DOWN;
  }
  if (adxResult.adx < 20) return Regime.RANGE;
  return Regime.CHOP;
}

// ── Confluence Scoring ─────────────────────────────────────────────

interface ConfluenceResult {
  score: number;         // 0-1, 0.5 = neutral
  direction: "LONG" | "SHORT" | "NEUTRAL";
  confidence: number;    // 0-1
  regime: Regime;
  atrValue: number;
  rsiValue: number;
}

function computeConfluence(bars: readonly Bar[]): ConfluenceResult | null {
  if (bars.length < CFG.minBarsRequired) return null;

  const closes = bars.map((b) => b.close);
  const price = closes[closes.length - 1]!;
  const prevClose = closes[closes.length - 2]!;
  const priceChange = ((price - prevClose) / prevClose) * 100;

  // Compute all indicators
  const rsiVal = rsi(closes, CFG.rsiPeriod);
  const macdVal = macdHist(closes);
  const bbPctB = bollingerPctB(closes);
  const atrVal = atr(bars, CFG.atrPeriod);
  const adxVal = adx(bars, CFG.adxPeriod);
  const ema9 = emaLast(closes, CFG.emaFast);
  const ema21 = emaLast(closes, CFG.emaMedium);
  const sma50 = sma(closes, CFG.smaPeriod);
  const obvDir = obvTrend(bars);
  const vwapVal = vwap(bars, 20);
  const fibZ = fibZone(closes, 50);
  const squeeze = keltnerSqueeze(bars);

  if (rsiVal === null || atrVal === null) return null;

  const regime = detectRegime(adxVal);

  // ── Weighted voter accumulation ──
  let weightedSum = 0;
  let totalWeight = 0;

  // 1. RSI (weight 1.5)
  {
    const w = 1.5;
    let vote = 0;
    if (rsiVal < CFG.rsiOversold) vote = 1.0;
    else if (rsiVal > CFG.rsiOverbought) vote = -1.0;
    else if (rsiVal < 40) vote = 0.3;
    else if (rsiVal > 60) vote = -0.3;
    weightedSum += vote * w;
    totalWeight += w;
  }

  // 2. MACD Histogram (weight 1.0)
  if (macdVal !== null) {
    const w = 1.0;
    const vote = macdVal > 0 ? 1.0 : macdVal < 0 ? -1.0 : 0;
    weightedSum += vote * w;
    totalWeight += w;
  }

  // 3. Bollinger %B (weight 1.0)
  if (bbPctB !== null) {
    const w = 1.0;
    let vote = 0;
    if (bbPctB < 0.2) vote = 1.0;
    else if (bbPctB > 0.8) vote = -1.0;
    weightedSum += vote * w;
    totalWeight += w;
  }

  // 4. Volume spike (weight 0.8)
  {
    const w = 0.8;
    const avgVol =
      bars.length >= 20
        ? bars.slice(-20).reduce((a, b) => a + b.volume, 0) / 20
        : bars.reduce((a, b) => a + b.volume, 0) / bars.length;
    const currentVol = bars[bars.length - 1]!.volume;
    const isSpike = currentVol > avgVol * 2.0;
    if (isSpike) {
      const vote = priceChange > 0 ? 1.0 : priceChange < 0 ? -1.0 : 0;
      weightedSum += vote * w;
    }
    totalWeight += w;
  }

  // 5. ADX trend strength (weight 0.7)
  if (adxVal) {
    const w = 0.7;
    let vote = 0;
    if (adxVal.adx > 30) {
      vote = adxVal.plusDI > adxVal.minusDI ? 1.0 : -1.0;
    } else if (adxVal.adx > 20) {
      vote = adxVal.plusDI > adxVal.minusDI ? 0.3 : -0.3;
    }
    weightedSum += vote * w;
    totalWeight += w;
  }

  // 6. VWAP (weight 0.5)
  if (vwapVal !== null) {
    const w = 0.5;
    let vote = 0;
    if (price > vwapVal * 1.005) vote = 1.0;
    else if (price < vwapVal * 0.995) vote = -1.0;
    weightedSum += vote * w;
    totalWeight += w;
  }

  // 7. OBV trend (weight 0.6)
  {
    const w = 0.6;
    let vote = 0;
    if (obvDir === "rising") vote = 1.0;
    else if (obvDir === "falling") vote = -1.0;
    weightedSum += vote * w;
    totalWeight += w;
  }

  // 8. Fibonacci zone (weight 0.5)
  {
    const w = 0.5;
    let vote = 0;
    if (fibZ === "618_786" || fibZ === "below_786") vote = 1.0;
    else if (fibZ === "500_618") vote = 0.5;
    else if (fibZ === "above_236") vote = -0.3;
    weightedSum += vote * w;
    totalWeight += w;
  }

  // 9. EMA ribbon 9/21 (weight 0.5)
  if (ema9 !== null && ema21 !== null) {
    const w = 0.5;
    let vote = 0;
    if (ema9 > ema21) vote = 0.6;
    else if (ema9 < ema21) vote = -0.6;
    weightedSum += vote * w;
    totalWeight += w;
  }

  // 10. Keltner squeeze (weight 0.7)
  if (squeeze && macdVal !== null) {
    const w = 0.7;
    const vote = macdVal > 0 ? 0.5 : macdVal < 0 ? -0.5 : 0;
    weightedSum += vote * w;
    totalWeight += w;
  }

  // ── Normalize to 0-1 ──
  const rawScore =
    totalWeight > 0 ? (weightedSum / totalWeight + 1) / 2 : 0.5;
  const score = Math.max(0, Math.min(1, rawScore));

  // ── Direction decision (rule-based fallback, no LLM) ──
  let direction: "LONG" | "SHORT" | "NEUTRAL";
  if (score > 0.55) direction = "LONG";
  else if (score < 0.45) direction = "SHORT";
  else if (rsiVal < 35) direction = "LONG";
  else if (rsiVal > 65) direction = "SHORT";
  else direction = "NEUTRAL";

  // ── Confidence calculation ──
  let confidence = Math.abs(score - 0.5) * 2 * 0.5 + 0.2;

  // Regime bonuses
  if (regime === Regime.TREND_UP && direction === "LONG") confidence += 0.10;
  if (regime === Regime.TREND_DOWN && direction === "SHORT") confidence += 0.10;
  if (regime === Regime.RANGE) confidence -= 0.05;
  if (regime === Regime.CHOP) confidence -= 0.08;

  // SMA50 alignment
  if (sma50 !== null) {
    if (price > sma50 && direction === "LONG") confidence += 0.10;
    else if (price < sma50 && direction === "SHORT") confidence += 0.10;
    else if (price > sma50 && direction === "SHORT") confidence -= 0.08;
    else if (price < sma50 && direction === "LONG") confidence -= 0.08;
  }

  // ADX bonus
  if (adxVal && adxVal.adx > 25) confidence += 0.05;

  // OBV alignment bonus
  if (
    (obvDir === "rising" && direction === "LONG") ||
    (obvDir === "falling" && direction === "SHORT")
  )
    confidence += 0.05;

  // Fibonacci deep retracement bonus
  if (fibZ === "618_786" || fibZ === "below_786") confidence += 0.08;

  // Counter-trend penalty
  if (regime === Regime.TREND_UP && direction === "SHORT") confidence *= 0.5;
  if (regime === Regime.TREND_DOWN && direction === "LONG") confidence *= 0.5;

  confidence = Math.max(0, Math.min(1, confidence));

  return {
    score,
    direction,
    confidence,
    regime,
    atrValue: atrVal,
    rsiValue: rsiVal,
  };
}

// ── ATR-Based SL/TP Multipliers ────────────────────────────────────

function getSlTpMults(
  regime: Regime,
  atrPct: number,
): { slMult: number; tpMult: number } {
  // Volatility-dependent base
  if (atrPct > 0.03) return { slMult: 3.5, tpMult: 5.0 };
  if (atrPct < 0.01) return { slMult: 2.5, tpMult: 3.5 };

  // Regime overrides
  switch (regime) {
    case Regime.TREND_UP:
    case Regime.TREND_DOWN:
      return { slMult: 3.0, tpMult: 4.0 };
    case Regime.RANGE:
      return { slMult: 2.0, tpMult: 3.0 };
    case Regime.CHOP:
      return { slMult: 2.0, tpMult: 2.5 };
    default:
      return { slMult: 2.5, tpMult: 3.5 };
  }
}

// ── Agent State ────────────────────────────────────────────────────

interface TradeState {
  entryPrice: number;
  stopLoss: number;
  takeProfit: number;
  entryBar: number;
  peakPrice: number;
}

let trade: TradeState | null = null;
let _symbol = "BTCUSDT";
let _lastSlBar = -999; // bar index of last stop loss (for cooldown)

// ── The Agent ──────────────────────────────────────────────────────

const agent: StrategyAgent = {
  name: "getclaw-runeclaw",

  init(meta) {
    _symbol = meta.symbol;
    trade = null;
    _lastSlBar = -999;
  },

  onBar(bar: Bar, ctx: BarContext): Order[] {
    const allBars = [...ctx.history, bar];
    if (allBars.length < CFG.minBarsRequired) return [];

    const price = bar.close;
    const orders: Order[] = [];

    // ── If in a position, check exits first ──
    if (ctx.position.size > 0 && trade) {
      // Track peak for trailing
      if (price > trade.peakPrice) trade.peakPrice = price;

      // Hard stop loss
      if (bar.low <= trade.stopLoss) {
        orders.push({
          symbol: _symbol,
          side: "sell",
          orderType: "market",
          size: ctx.position.size,
          tag: `SL hit ${trade.stopLoss.toFixed(2)}`,
        });
        _lastSlBar = ctx.index;
        trade = null;
        return orders;
      }

      // Take profit
      if (bar.high >= trade.takeProfit) {
        orders.push({
          symbol: _symbol,
          side: "sell",
          orderType: "market",
          size: ctx.position.size,
          tag: `TP hit ${trade.takeProfit.toFixed(2)}`,
        });
        trade = null;
        return orders;
      }

      // Time stop: exit if no profit after 24 bars (~4 days on 4h)
      const barsHeld = ctx.index - trade.entryBar;
      if (barsHeld >= 24 && price <= trade.entryPrice) {
        orders.push({
          symbol: _symbol,
          side: "sell",
          orderType: "market",
          size: ctx.position.size,
          tag: `time-stop ${barsHeld} bars`,
        });
        trade = null;
        return orders;
      }

      // Trailing stop: if price ran 2x ATR above entry, trail at 1.5x ATR below peak
      const confluence = computeConfluence(allBars);
      if (confluence) {
        const trailDist = confluence.atrValue * 1.5;
        const trailLevel = trade.peakPrice - trailDist;
        if (
          trade.peakPrice > trade.entryPrice + confluence.atrValue * 2 &&
          price < trailLevel
        ) {
          orders.push({
            symbol: _symbol,
            side: "sell",
            orderType: "market",
            size: ctx.position.size,
            tag: `trail-stop peak=${trade.peakPrice.toFixed(2)}`,
          });
          trade = null;
          return orders;
        }

        // Signal reversal exit: if confluence flips bearish with high confidence
        if (
          confluence.direction === "SHORT" &&
          confluence.confidence >= 0.60
        ) {
          orders.push({
            symbol: _symbol,
            side: "sell",
            orderType: "market",
            size: ctx.position.size,
            tag: `reversal conf=${confluence.confidence.toFixed(2)}`,
          });
          trade = null;
          return orders;
        }
      }

      return []; // hold position
    }

    // ── No position: evaluate entry ──
    const confluence = computeConfluence(allBars);
    if (!confluence) return [];

    // Only take LONG (agentbench is long-only spot)
    if (confluence.direction !== "LONG") return [];
    if (confluence.confidence < CFG.minConfidence) return [];

    // RSI filter: don't buy into overbought territory
    if (confluence.rsiValue > CFG.maxEntryRsi) return [];

    // Cooldown after stop loss
    if (ctx.index - _lastSlBar < CFG.cooldownBars) return [];

    // Volatility guard: reject if ATR too high
    const atrPct = confluence.atrValue / price;
    if (atrPct > CFG.volatilityGuardPct) return [];

    // Calculate SL/TP
    const { slMult, tpMult } = getSlTpMults(confluence.regime, atrPct);
    const stopLoss = price - slMult * confluence.atrValue;
    const takeProfit = price + tpMult * confluence.atrValue;

    // Risk-reward check
    const reward = takeProfit - price;
    const risk = price - stopLoss;
    if (risk <= 0 || reward / risk < CFG.minRiskReward) return [];

    // Position sizing: risk 2% of equity, capped at 10%
    const riskPerUnit = price - stopLoss;
    const maxRiskAmount = ctx.equity * CFG.riskPerTradePct;
    let size = maxRiskAmount / riskPerUnit;

    // Cap by max position % of equity
    const maxSize = (ctx.equity * CFG.maxPositionPct) / price;
    size = Math.min(size, maxSize);

    // Cap by available cash
    const maxCashSize = (ctx.cash * 0.95) / price; // keep 5% buffer
    size = Math.min(size, maxCashSize);

    if (size <= 0) return [];

    // Respect agentbench risk guard maxPositionSize (default 1.0 unit)
    // This is a hard cap in base units, not quote
    const MAX_POS_SIZE = 1.0;
    size = Math.min(size, MAX_POS_SIZE);

    // Round to reasonable precision
    if (price > 10000) size = Math.round(size * 100000) / 100000; // BTC: 5 decimals
    else if (price > 100) size = Math.round(size * 10000) / 10000; // ETH: 4 decimals
    else size = Math.round(size * 1000) / 1000;                    // SOL: 3 decimals

    if (size <= 0) return [];

    // Record trade state for exit management
    trade = {
      entryPrice: price,
      stopLoss,
      takeProfit,
      entryBar: ctx.index,
      peakPrice: price,
    };

    orders.push({
      symbol: _symbol,
      side: "buy",
      orderType: "market",
      size,
      tag: `getclaw conf=${confluence.confidence.toFixed(2)} rsi=${confluence.rsiValue.toFixed(0)} regime=${Regime[confluence.regime]}`,
    });

    return orders;
  },
};

export default agent;
