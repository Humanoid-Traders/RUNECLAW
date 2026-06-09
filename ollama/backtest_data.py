"""
RUNECLAW Backtest — Data Layer
Loads and validates historical OHLCV, enforces a strict time-based out-of-sample
split, and can synthesize data for testing the harness itself.

The single most important job here: guarantee the model is evaluated ONLY on
data after its decision point, and ideally only on a time period it never saw
during training. Look-ahead bias is the #1 way backtests lie.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from dataclasses import dataclass


REQUIRED_COLS = ["timestamp", "open", "high", "low", "close", "volume"]


@dataclass
class OHLCVData:
    """Validated OHLCV series for one symbol."""
    symbol: str
    df: pd.DataFrame          # indexed by timestamp, sorted ascending
    timeframe: str            # e.g. "1h", "4h"

    def __len__(self):
        return len(self.df)

    def slice_after(self, ts) -> pd.DataFrame:
        """Bars strictly AFTER a timestamp — the forward window for a trade."""
        return self.df[self.df.index > ts]

    def slice_until(self, ts) -> pd.DataFrame:
        """Bars up to and including ts — the only data a decision may use."""
        return self.df[self.df.index <= ts]


def load_ohlcv(path: str, symbol: str, timeframe: str = "1h") -> OHLCVData:
    """
    Load OHLCV from CSV or Parquet. Expects columns:
    timestamp, open, high, low, close, volume
    timestamp may be ISO string, epoch seconds, or epoch ms.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"OHLCV file not found: {path}")

    if p.suffix == ".parquet":
        df = pd.read_parquet(p)
    else:
        df = pd.read_csv(p)

    df.columns = [c.lower().strip() for c in df.columns]
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"{symbol}: missing columns {missing}. Found: {list(df.columns)}")

    # Normalize timestamp
    ts = df["timestamp"]
    if np.issubdtype(ts.dtype, np.number):
        # epoch — detect seconds vs ms by magnitude
        unit = "ms" if ts.iloc[0] > 1e11 else "s"
        df["timestamp"] = pd.to_datetime(ts, unit=unit, utc=True)
    else:
        df["timestamp"] = pd.to_datetime(ts, utc=True)

    df = df.dropna(subset=REQUIRED_COLS).sort_values("timestamp").set_index("timestamp")
    df = df[~df.index.duplicated(keep="first")]

    _validate_bars(df, symbol)
    return OHLCVData(symbol=symbol, df=df, timeframe=timeframe)


def _validate_bars(df: pd.DataFrame, symbol: str) -> None:
    """Catch the data problems that silently corrupt a backtest."""
    issues = []
    # high must be >= low, and high/low must bracket open/close
    bad_hl = (df["high"] < df["low"]).sum()
    if bad_hl:
        issues.append(f"{bad_hl} bars with high < low")
    bad_bracket = (
        (df["high"] < df[["open", "close"]].max(axis=1)) |
        (df["low"]  > df[["open", "close"]].min(axis=1))
    ).sum()
    if bad_bracket:
        issues.append(f"{bad_bracket} bars where high/low don't bracket open/close")
    # non-positive prices
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        issues.append("non-positive prices present")
    # large time gaps (possible missing data)
    if len(df) > 2:
        deltas = df.index.to_series().diff().dropna()
        median_gap = deltas.median()
        big_gaps = (deltas > median_gap * 5).sum()
        if big_gaps:
            issues.append(f"{big_gaps} suspicious time gaps (>5x median bar interval)")

    if issues:
        print(f"  ⚠ {symbol} data warnings: {'; '.join(issues)}")


def out_of_sample_split(data: OHLCVData, holdout_frac: float = 0.2,
                        holdout_start: str = None):
    """
    Split by TIME, never randomly. Returns (in_sample, out_of_sample).

    holdout_start (ISO date) takes precedence — use it to hold out the exact
    period after your training data cutoff. Otherwise the last holdout_frac of
    the series is held out.

    CRITICAL: out-of-sample must be data the model never saw in training.
    If your training data ran through Jan 2026, set holdout_start="2026-02-01".
    """
    df = data.df
    if holdout_start is not None:
        cutoff = pd.to_datetime(holdout_start, utc=True)
        in_df  = df[df.index < cutoff]
        oos_df = df[df.index >= cutoff]
    else:
        n_holdout = int(len(df) * holdout_frac)
        in_df  = df.iloc[:-n_holdout]
        oos_df = df.iloc[-n_holdout:]

    print(f"  {data.symbol}: in-sample {len(in_df)} bars "
          f"({in_df.index.min().date()} → {in_df.index.max().date()}), "
          f"out-of-sample {len(oos_df)} bars "
          f"({oos_df.index.min().date()} → {oos_df.index.max().date()})")

    return (OHLCVData(data.symbol, in_df, data.timeframe),
            OHLCVData(data.symbol, oos_df, data.timeframe))


def synth_ohlcv(symbol: str = "TEST/USDT", n_bars: int = 2000, seed: int = 42,
                start_price: float = 100.0, timeframe: str = "1h",
                regime: str = "mixed") -> OHLCVData:
    """
    Synthetic OHLCV for TESTING THE HARNESS — not for evaluating a model.
    regime: 'trend_up' | 'trend_down' | 'ranging' | 'mixed'
    Uses a geometric random walk with regime-dependent drift/vol.
    """
    rng = np.random.default_rng(seed)
    drift_map = {"trend_up": 0.0004, "trend_down": -0.0004, "ranging": 0.0, "mixed": 0.0}
    vol = 0.012
    drift = drift_map.get(regime, 0.0)

    closes = [start_price]
    for i in range(1, n_bars):
        d = drift
        if regime == "mixed":
            # flip drift every ~250 bars to create distinct regimes
            d = 0.0005 if (i // 250) % 2 == 0 else -0.0005
        ret = rng.normal(d, vol)
        closes.append(max(0.01, closes[-1] * (1 + ret)))
    closes = np.array(closes)

    # Build OHLC around the close path
    opens = np.concatenate([[start_price], closes[:-1]])
    intrabar = np.abs(rng.normal(0, vol * 0.6, n_bars)) * closes
    highs = np.maximum(opens, closes) + intrabar
    lows  = np.minimum(opens, closes) - intrabar
    lows  = np.maximum(lows, 0.01)
    volumes = rng.lognormal(10, 0.5, n_bars)

    start = pd.Timestamp("2024-01-01", tz="UTC")
    freq = {"1h": "1h", "4h": "4h", "15m": "15min", "1d": "1D"}.get(timeframe, "1h")
    idx = pd.date_range(start, periods=n_bars, freq=freq)

    df = pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes
    }, index=idx)
    df.index.name = "timestamp"
    return OHLCVData(symbol=symbol, df=df, timeframe=timeframe)


if __name__ == "__main__":
    # Smoke test the data layer
    d = synth_ohlcv(n_bars=1000, regime="mixed")
    print(f"Synthetic: {len(d)} bars, price {d.df['close'].iloc[0]:.2f} → {d.df['close'].iloc[-1]:.2f}")
    in_s, oos = out_of_sample_split(d, holdout_frac=0.25)
    print(f"Split OK: {len(in_s)} in-sample, {len(oos)} out-of-sample")
