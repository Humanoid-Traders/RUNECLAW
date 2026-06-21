"""Deterministic feature + decision computation for the RUNECLAW ETH backtest.

Everything the strategy needs is precomputed here in pandas from real klines,
using only same-or-past bars (rolling windows), so the Nautilus strategy stays
thin: it just reads the per-bar decision columns and manages orders. The
order-book and cross-sectional volume dimensions of the live scanner are dropped
(not replayable for one symbol); the remaining three are renormalized to 100.
"""
import numpy as np
import pandas as pd

# Live weights: momentum 25 + vwap 20 + range 20 = 65 -> renormalize to 100.
_RAW_MAX = 65.0
_RENORM = 100.0 / _RAW_MAX


def _rolling_vwap(df: pd.DataFrame, window: int) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = (typical * df["volume"]).rolling(window, min_periods=window).sum()
    vol = df["volume"].rolling(window, min_periods=window).sum()
    return pv / vol.replace(0.0, np.nan)


def compute_decision_frame(eth: pd.DataFrame, btc: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    window = int(cfg.get("window_bars_24h", 24))
    atr_mult = float(cfg.get("atr_limit_mult", "0.5"))
    tp1_pct = float(cfg.get("tp1_pct", "3.5")) / 100.0
    sl_min = float(cfg.get("eth_sl_min_pct", "1.5")) / 100.0
    min_score = float(cfg.get("min_score", 70))

    out = eth.copy()

    eth_vwap = _rolling_vwap(out, window)
    eth_high = out["high"].rolling(window, min_periods=window).max()
    eth_low = out["low"].rolling(window, min_periods=window).min()
    eth_chg = out["close"] / out["close"].shift(window) - 1.0
    atr = (eth_high - eth_low) / 2.5

    # BTC context aligned onto ETH timestamps (as-of / forward fill).
    btc_vwap = _rolling_vwap(btc, window)
    btc_chg = btc["close"] / btc["close"].shift(window) - 1.0
    btc_above = (btc["close"] > btc_vwap).astype(float)
    btc_chg_a = btc_chg.reindex(out.index, method="ffill")
    btc_above_a = btc_above.reindex(out.index, method="ffill")

    # BTC regime gate: +1 BTC up on the day, +1 BTC above its VWAP (taker bonus
    # is live-only and unavailable to replay).
    gate_score = (btc_chg_a > 0).astype(float) + (btc_above_a > 0).astype(float)
    size_factor = np.where(gate_score >= 2, 1.0, np.where(gate_score >= 1, 0.5, 0.0))
    gate_open = gate_score >= 1

    # Scored dimensions.
    rel = eth_chg - btc_chg_a
    mom = (12.5 + rel * 100.0 * 2.5).clip(lower=0.0, upper=25.0)
    vwap_score = np.where(out["close"] > eth_vwap * 1.001, 20.0,
                          np.where(out["close"] >= eth_vwap * 0.999, 10.0, 0.0))
    range_pos = (out["close"] - eth_low) / (eth_high - eth_low)
    range_score = np.where(range_pos > 0.66, 20.0, np.where(range_pos >= 0.33, 10.0, 0.0))

    raw = mom.to_numpy() + vwap_score + range_score
    score = raw * _RENORM

    limit = eth_vwap - atr_mult * atr
    raw_sl_pct = (limit - eth_low) / limit
    sl_pct = np.maximum(raw_sl_pct.to_numpy(), sl_min)
    sl_price = limit.to_numpy() * (1.0 - sl_pct)
    tp1 = limit.to_numpy() * (1.0 + tp1_pct)

    entry_ok = (
        gate_open.to_numpy()
        & (score >= min_score)
        & (out["close"].to_numpy() >= eth_vwap.to_numpy())
        & np.isfinite(limit.to_numpy())
        & (limit.to_numpy() > 0)
        & (sl_price < limit.to_numpy())
        & (tp1 > limit.to_numpy())
    )

    out["rc_score"] = np.nan_to_num(score, nan=0.0)
    out["rc_size_factor"] = np.where(entry_ok, size_factor, 0.0)
    out["rc_limit"] = np.nan_to_num(limit.to_numpy(), nan=0.0)
    out["rc_sl"] = np.nan_to_num(sl_price, nan=0.0)
    out["rc_tp1"] = np.nan_to_num(tp1, nan=0.0)
    out["rc_entry"] = entry_ok.astype(int)
    # Helper columns kept for live signal reporting / debugging.
    out["rc_range_pos"] = np.nan_to_num(range_pos.to_numpy(), nan=0.0)
    out["rc_gate_score"] = gate_score.to_numpy()
    return out
