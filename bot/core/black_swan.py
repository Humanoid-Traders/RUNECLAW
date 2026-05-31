"""Black Swan Detector — statistical anomaly detection for RUNECLAW trading bot.

Monitors market microstructure for early warning signs of extreme events,
triggering pre-emptive halts BEFORE the circuit breaker (which only fires
after 5 % daily loss or 10 % drawdown) would react.

Anomaly types tracked:
    - Correlation breakdown between correlated assets
    - Volume collapse / liquidity evaporation
    - Price acceleration (flash-crash detection)
    - Volatility explosion (ATR spike)
    - Bid-ask spread widening (simulated)
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Optional

import numpy as np
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class AnomalyType(str, Enum):
    """Categories of structural market anomalies."""

    CORRELATION_BREAKDOWN = "CORRELATION_BREAKDOWN"
    VOLUME_COLLAPSE = "VOLUME_COLLAPSE"
    PRICE_ACCELERATION = "PRICE_ACCELERATION"
    VOLATILITY_EXPLOSION = "VOLATILITY_EXPLOSION"
    SPREAD_WIDENING = "SPREAD_WIDENING"


class AnomalyAlert(BaseModel):
    """Immutable record of a detected anomaly."""

    anomaly_type: AnomalyType
    severity: float = Field(ge=0.0, le=1.0)  # 0 = minor, 1 = extreme
    symbol: str
    description: str
    metric_value: float  # the actual measured value
    threshold: float  # the threshold it exceeded
    recommended_action: str  # e.g. "HALT_NEW_TRADES", "REDUCE_POSITION_SIZE", "MONITOR"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Thresholds / constants
# ---------------------------------------------------------------------------

_MAX_HISTORY = 100
_CORRELATION_WINDOW = 20
_CORRELATION_THRESHOLD = 0.3
_VOLUME_WINDOW = 20
_VOLUME_COLLAPSE_RATIO = 0.30
_PRICE_ACCEL_WINDOW = 5
_PRICE_ACCEL_LOOKBACK = 20
_PRICE_ACCEL_SIGMA = 3.0
_ATR_WINDOW = 20
_ATR_EXPLOSION_FACTOR = 3.0
_SPREAD_FACTOR = 2.0
_HALT_SEVERITY = 0.8


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class BlackSwanDetector:
    """Statistical anomaly detector that pre-empts the circuit breaker.

    While the circuit breaker reacts to losses (5 % daily, 10 % drawdown),
    the BlackSwanDetector watches market microstructure for signs of danger
    BEFORE positions take damage.

    Usage::

        detector = BlackSwanDetector()

        # Each scan cycle, feed data for every monitored symbol:
        alerts = detector.update("BTC/USDT", price=67_400.0, volume=1_200.0, atr=350.0)

        if detector.halt_recommended:
            # ... pause the strategy ...
            pass
    """

    def __init__(self) -> None:
        # Rolling price history per symbol: list of close prices
        self._price_history: dict[str, list[float]] = {}
        # Rolling volume history per symbol
        self._volume_history: dict[str, list[float]] = {}
        # Rolling ATR history per symbol
        self._atr_history: dict[str, list[float]] = {}
        # Alert state
        self._active_alerts: list[AnomalyAlert] = []
        self._halt_recommended: bool = False

    # -- public properties --------------------------------------------------

    @property
    def halt_recommended(self) -> bool:
        """``True`` if any active alert recommends halting new trades."""
        return self._halt_recommended

    @property
    def active_alerts(self) -> list[AnomalyAlert]:
        """Return a shallow copy of current active alerts."""
        return list(self._active_alerts)

    # -- primary interface --------------------------------------------------

    def update(
        self,
        symbol: str,
        price: float,
        volume: float,
        atr: float = 0.0,
    ) -> list[AnomalyAlert]:
        """Feed new market data and check for anomalies.

        Call this every scan cycle for each symbol being monitored.

        Args:
            symbol: Trading pair identifier (e.g. ``"BTC/USDT"``).
            price: Latest close / last price.
            volume: Latest period volume.
            atr: Current Average True Range value.  Pass ``0.0`` if unavailable.

        Returns:
            List of *new* alerts triggered during this update (empty when
            market conditions are normal).
        """
        # 1. Append to rolling histories (capped at _MAX_HISTORY)
        self._append_history(self._price_history, symbol, price)
        self._append_history(self._volume_history, symbol, volume)
        if atr > 0.0:
            self._append_history(self._atr_history, symbol, atr)

        # 2. Run all five anomaly checks for this symbol
        new_alerts: list[AnomalyAlert] = []
        for check in (
            self._check_correlation_breakdown,
            self._check_volume_collapse,
            self._check_price_acceleration,
            self._check_volatility_explosion,
            self._check_spread_widening,
        ):
            alert = check(symbol)
            if alert is not None:
                new_alerts.append(alert)

        # 3. Merge into active alerts
        self._active_alerts.extend(new_alerts)

        # 4. Escalate to halt if any alert is severe enough
        if any(a.severity >= _HALT_SEVERITY for a in new_alerts):
            self._halt_recommended = True

        return new_alerts

    def check_all(self) -> list[AnomalyAlert]:
        """Run all anomaly checks across every tracked symbol.

        Useful for a periodic sweep rather than per-update checking.
        """
        all_symbols = set(self._price_history.keys())
        new_alerts: list[AnomalyAlert] = []
        for symbol in all_symbols:
            for check in (
                self._check_correlation_breakdown,
                self._check_volume_collapse,
                self._check_price_acceleration,
                self._check_volatility_explosion,
                self._check_spread_widening,
            ):
                alert = check(symbol)
                if alert is not None:
                    new_alerts.append(alert)

        self._active_alerts.extend(new_alerts)
        if any(a.severity >= _HALT_SEVERITY for a in new_alerts):
            self._halt_recommended = True
        return new_alerts

    def clear_alerts(self) -> None:
        """Reset all active alerts and lift halt recommendation.

        This is a manual action analogous to resetting the circuit breaker.
        """
        self._active_alerts.clear()
        self._halt_recommended = False

    # -- individual anomaly checks ------------------------------------------

    def _check_correlation_breakdown(self, symbol: str) -> Optional[AnomalyAlert]:
        """Detect divergence between assets that normally move together.

        Computes pairwise rolling correlation (window = 20) between *symbol*
        and every other tracked symbol.  If the minimum correlation drops
        below ``_CORRELATION_THRESHOLD`` (0.3), an alert is raised.
        """
        prices = self._price_history.get(symbol)
        if prices is None or len(prices) < _CORRELATION_WINDOW:
            return None

        target = np.array(prices[-_CORRELATION_WINDOW:])
        # Need at least one other symbol to compare against
        other_symbols = [s for s in self._price_history if s != symbol]
        if not other_symbols:
            return None

        min_corr = 1.0
        worst_peer = symbol
        for peer in other_symbols:
            peer_prices = self._price_history[peer]
            if len(peer_prices) < _CORRELATION_WINDOW:
                continue
            peer_arr = np.array(peer_prices[-_CORRELATION_WINDOW:])
            # Guard against constant series (zero std)
            if np.std(target) == 0 or np.std(peer_arr) == 0:
                continue
            corr = float(np.corrcoef(target, peer_arr)[0, 1])
            if corr < min_corr:
                min_corr = corr
                worst_peer = peer

        if min_corr < _CORRELATION_THRESHOLD:
            severity = self._severity_from_ratio(
                _CORRELATION_THRESHOLD - min_corr,
                floor=0.0,
                ceiling=_CORRELATION_THRESHOLD,
            )
            action = "HALT_NEW_TRADES" if severity >= _HALT_SEVERITY else "REDUCE_POSITION_SIZE"
            return AnomalyAlert(
                anomaly_type=AnomalyType.CORRELATION_BREAKDOWN,
                severity=severity,
                symbol=symbol,
                description=(
                    f"Correlation between {symbol} and {worst_peer} collapsed to "
                    f"{min_corr:.3f} (threshold {_CORRELATION_THRESHOLD})"
                ),
                metric_value=min_corr,
                threshold=_CORRELATION_THRESHOLD,
                recommended_action=action,
            )
        return None

    def _check_volume_collapse(self, symbol: str) -> Optional[AnomalyAlert]:
        """Detect sudden liquidity evaporation.

        Fires when the latest volume is below 30 % of the 20-period rolling
        average, indicating extreme slippage risk.
        """
        volumes = self._volume_history.get(symbol)
        if volumes is None or len(volumes) < _VOLUME_WINDOW + 1:
            return None

        window = np.array(volumes[-(_VOLUME_WINDOW + 1): -1])
        avg_volume = float(np.mean(window))
        if avg_volume == 0:
            return None

        current_volume = volumes[-1]
        ratio = current_volume / avg_volume  # e.g. 0.25 means 25 % of average

        if ratio < _VOLUME_COLLAPSE_RATIO:
            # Severity increases as ratio drops toward 0
            severity = self._severity_from_ratio(
                _VOLUME_COLLAPSE_RATIO - ratio,
                floor=0.0,
                ceiling=_VOLUME_COLLAPSE_RATIO,
            )
            action = "HALT_NEW_TRADES" if severity >= _HALT_SEVERITY else "REDUCE_POSITION_SIZE"
            return AnomalyAlert(
                anomaly_type=AnomalyType.VOLUME_COLLAPSE,
                severity=severity,
                symbol=symbol,
                description=(
                    f"{symbol} volume collapsed to {ratio:.1%} of 20-period average "
                    f"(threshold {_VOLUME_COLLAPSE_RATIO:.0%})"
                ),
                metric_value=ratio,
                threshold=_VOLUME_COLLAPSE_RATIO,
                recommended_action=action,
            )
        return None

    def _check_price_acceleration(self, symbol: str) -> Optional[AnomalyAlert]:
        """Detect moves exceeding 3 standard deviations within a short window.

        Computes log-returns over the last 20 periods to establish a
        volatility baseline, then checks whether the absolute price change
        over the most recent 5 bars exceeds ``3 * std_dev``.  This catches
        flash crashes and cascade liquidations.
        """
        prices = self._price_history.get(symbol)
        if prices is None or len(prices) < _PRICE_ACCEL_LOOKBACK + 1:
            return None

        arr = np.array(prices[-(max(_PRICE_ACCEL_LOOKBACK, _PRICE_ACCEL_WINDOW) + 1):])
        log_returns = np.diff(np.log(arr))

        if len(log_returns) < _PRICE_ACCEL_LOOKBACK:
            return None

        std_dev = float(np.std(log_returns[-_PRICE_ACCEL_LOOKBACK:]))
        if std_dev == 0:
            return None

        # Absolute return over the most recent PRICE_ACCEL_WINDOW bars
        recent_move = abs(float(np.sum(log_returns[-_PRICE_ACCEL_WINDOW:])))
        sigma_multiple = recent_move / std_dev

        if sigma_multiple > _PRICE_ACCEL_SIGMA:
            severity = self._severity_from_ratio(
                sigma_multiple,
                floor=_PRICE_ACCEL_SIGMA,
                ceiling=_PRICE_ACCEL_SIGMA * 3,  # 9-sigma → severity 1.0
            )
            action = "HALT_NEW_TRADES" if severity >= _HALT_SEVERITY else "REDUCE_POSITION_SIZE"
            return AnomalyAlert(
                anomaly_type=AnomalyType.PRICE_ACCELERATION,
                severity=severity,
                symbol=symbol,
                description=(
                    f"{symbol} moved {sigma_multiple:.1f} sigma in {_PRICE_ACCEL_WINDOW} bars "
                    f"(threshold {_PRICE_ACCEL_SIGMA:.0f} sigma)"
                ),
                metric_value=sigma_multiple,
                threshold=_PRICE_ACCEL_SIGMA,
                recommended_action=action,
            )
        return None

    def _check_volatility_explosion(self, symbol: str) -> Optional[AnomalyAlert]:
        """Detect ATR spiking to 3x or more of its 20-period average.

        When the market enters a volatility regime that is 3x above its
        recent norm, standard position-sizing assumptions break down and
        the bot should reduce exposure or halt.
        """
        atrs = self._atr_history.get(symbol)
        if atrs is None or len(atrs) < _ATR_WINDOW + 1:
            return None

        window = np.array(atrs[-(_ATR_WINDOW + 1): -1])
        avg_atr = float(np.mean(window))
        if avg_atr == 0:
            return None

        current_atr = atrs[-1]
        ratio = current_atr / avg_atr

        if ratio > _ATR_EXPLOSION_FACTOR:
            severity = self._severity_from_ratio(
                ratio,
                floor=_ATR_EXPLOSION_FACTOR,
                ceiling=_ATR_EXPLOSION_FACTOR * 3,  # 9x → severity 1.0
            )
            action = "HALT_NEW_TRADES" if severity >= _HALT_SEVERITY else "REDUCE_POSITION_SIZE"
            return AnomalyAlert(
                anomaly_type=AnomalyType.VOLATILITY_EXPLOSION,
                severity=severity,
                symbol=symbol,
                description=(
                    f"{symbol} ATR spiked to {ratio:.1f}x its 20-period average "
                    f"(threshold {_ATR_EXPLOSION_FACTOR:.0f}x)"
                ),
                metric_value=ratio,
                threshold=_ATR_EXPLOSION_FACTOR,
                recommended_action=action,
            )
        return None

    def _check_spread_widening(self, symbol: str) -> Optional[AnomalyAlert]:
        """Detect (simulated) bid-ask spread exceeding 2x normal.

        Real spread data requires Level-2 order-book feeds.  As a proxy we
        estimate the spread from recent price volatility: the spread is
        modelled as the standard deviation of tick-to-tick returns over the
        last 20 bars scaled by a noise factor.  When the latest estimated
        spread exceeds ``_SPREAD_FACTOR`` (2x) its rolling baseline, market
        makers are likely pulling liquidity.
        """
        prices = self._price_history.get(symbol)
        if prices is None or len(prices) < _CORRELATION_WINDOW + 1:
            return None

        arr = np.array(prices[-(max(_CORRELATION_WINDOW, 1) + 1):])
        returns = np.abs(np.diff(arr) / arr[:-1])
        if len(returns) < _CORRELATION_WINDOW:
            return None

        baseline = float(np.mean(returns[:-1])) if len(returns) > 1 else 0.0
        if baseline == 0:
            return None

        latest = float(returns[-1])
        ratio = latest / baseline

        if ratio > _SPREAD_FACTOR:
            severity = self._severity_from_ratio(
                ratio,
                floor=_SPREAD_FACTOR,
                ceiling=_SPREAD_FACTOR * 4,  # 8x → severity 1.0
            )
            action = "HALT_NEW_TRADES" if severity >= _HALT_SEVERITY else "MONITOR"
            return AnomalyAlert(
                anomaly_type=AnomalyType.SPREAD_WIDENING,
                severity=severity,
                symbol=symbol,
                description=(
                    f"{symbol} estimated spread widened to {ratio:.1f}x baseline "
                    f"(threshold {_SPREAD_FACTOR:.0f}x)"
                ),
                metric_value=ratio,
                threshold=_SPREAD_FACTOR,
                recommended_action=action,
            )
        return None

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _severity_from_ratio(ratio: float, floor: float = 0.0, ceiling: float = 1.0) -> float:
        """Map a ratio to a 0-1 severity score with linear interpolation.

        ``floor`` is the ratio value that maps to severity 0.
        ``ceiling`` is the ratio value that maps to severity 1.
        Values outside the range are clamped.
        """
        if ceiling == floor:
            return 1.0 if ratio >= ceiling else 0.0
        raw = (ratio - floor) / (ceiling - floor)
        return float(np.clip(raw, 0.0, 1.0))

    def _append_history(
        self,
        store: dict[str, list[float]],
        symbol: str,
        value: float,
    ) -> None:
        """Append *value* to the per-symbol rolling buffer, capping at ``_MAX_HISTORY``."""
        buf = store.setdefault(symbol, [])
        buf.append(value)
        if len(buf) > _MAX_HISTORY:
            del buf[: len(buf) - _MAX_HISTORY]
