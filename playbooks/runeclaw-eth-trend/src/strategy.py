"""Nautilus replay strategy for the RUNECLAW ETH pullback backtest.

Self-contained and multi-instrument: it subscribes to ETHUSDT (traded) and
BTCUSDT (regime-gate context only, never traded) bar streams and computes every
decision input internally from rolling buffers, so the managed backtest can
reconstruct it from ``backtest.yaml`` alone. The order-book and cross-sectional
volume dimensions of the live scanner are dropped (not replayable for one
symbol); the remaining three are renormalized to 100.

Instance attributes are ``rc_``-prefixed to avoid colliding with reserved
NautilusTrader ``Component``/``Strategy`` internals such as ``_stop``/``_state``.
"""
from collections import deque
from decimal import Decimal
from typing import Optional

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy

# Live weights momentum 25 + vwap 20 + range 20 = 65 -> renormalize to 100.
_RENORM = 100.0 / 65.0


class _Roller:
    """Rolling OHLCV buffer that derives the per-bar features used by RUNECLAW."""

    def __init__(self, window: int) -> None:
        self.window = window
        self.bars = deque(maxlen=window + 1)  # (high, low, close, volume)

    def push(self, high: float, low: float, close: float, volume: float) -> None:
        self.bars.append((high, low, close, volume))

    def ready(self) -> bool:
        return len(self.bars) >= self.window + 1

    def features(self) -> dict:
        window_bars = list(self.bars)[-self.window:]
        pv = sum(((h + l + c) / 3.0) * v for h, l, c, v in window_bars)
        sv = sum(v for _, _, _, v in window_bars)
        close = window_bars[-1][2]
        vwap = (pv / sv) if sv > 0 else close
        hi = max(h for h, _, _, _ in window_bars)
        lo = min(l for _, l, _, _ in window_bars)
        prior_close = self.bars[0][2]
        chg = (close / prior_close - 1.0) if prior_close else 0.0
        span = hi - lo
        range_pos = ((close - lo) / span) if span > 0 else 0.0
        return {"vwap": vwap, "hi": hi, "lo": lo, "close": close,
                "chg": chg, "atr": span / 2.5, "range_pos": range_pos}


class RuneclawEthStrategyConfig(StrategyConfig):
    instrument_id: Optional[InstrumentId] = None
    bar_type: Optional[BarType] = None
    instrument_ids: tuple[InstrumentId, ...] = ()
    bar_types: tuple[BarType, ...] = ()
    leverage: int = 10
    margin_budget: str = "100"
    max_loss_usdt: str = "15"
    entry_ttl_bars: int = 8
    window_bars_24h: int = 24
    min_score: int = 70
    eth_sl_min_pct: str = "1.5"
    atr_limit_mult: str = "0.5"
    tp1_pct: str = "3.5"


class RuneclawEthStrategy(Strategy):
    def __init__(self, config: RuneclawEthStrategyConfig) -> None:
        super().__init__(config)
        self.cfg = config
        window = int(config.window_bars_24h)
        self._rc_eth_id: Optional[InstrumentId] = None
        self._rc_btc_id: Optional[InstrumentId] = None
        self._rc_eth_instrument: Optional[Instrument] = None
        self._rc_eth = _Roller(window)
        self._rc_btc = _Roller(window)
        self._rc_btc_feat: Optional[dict] = None
        self._rc_phase: str = "FLAT"
        self._rc_pending_order = None
        self._rc_pending_age: int = 0
        self._rc_stop_px: float = 0.0
        self._rc_tp_px: float = 0.0

    def on_start(self) -> None:
        for iid in self.cfg.instrument_ids:
            text = str(iid)
            if "ETHUSDT" in text:
                self._rc_eth_id = iid
            elif "BTCUSDT" in text:
                self._rc_btc_id = iid
        if self._rc_eth_id is None and self.cfg.instrument_id is not None:
            self._rc_eth_id = self.cfg.instrument_id
        if self._rc_eth_id is None:
            raise RuntimeError("ETHUSDT instrument not found in spec")
        self._rc_eth_instrument = self.cache.instrument(self._rc_eth_id)
        for bar_type in self.cfg.bar_types:
            self.subscribe_bars(bar_type)
        if not self.cfg.bar_types and self.cfg.bar_type is not None:
            self.subscribe_bars(self.cfg.bar_type)

    def on_bar(self, bar: Bar) -> None:
        instrument_id = bar.bar_type.instrument_id
        high, low, close, volume = float(bar.high), float(bar.low), float(bar.close), float(bar.volume)

        if self._rc_btc_id is not None and instrument_id == self._rc_btc_id:
            self._rc_btc.push(high, low, close, volume)
            if self._rc_btc.ready():
                self._rc_btc_feat = self._rc_btc.features()
            return

        if instrument_id != self._rc_eth_id:
            return

        # --- ETH bar: manage open / pending state first ---
        if self._rc_phase == "LONG":
            if low <= self._rc_stop_px or high >= self._rc_tp_px:
                self._rc_exit()
            self._rc_eth.push(high, low, close, volume)
            return

        if self._rc_phase == "PENDING":
            self._rc_pending_age += 1
            if self._rc_pending_age > int(self.cfg.entry_ttl_bars):
                if self._rc_pending_order is not None:
                    self.cancel_order(self._rc_pending_order)
                self._rc_pending_order = None
                self._rc_phase = "FLAT"
            self._rc_eth.push(high, low, close, volume)
            return

        # --- FLAT: evaluate a new entry using features that include this bar ---
        self._rc_eth.push(high, low, close, volume)
        if not self._rc_eth.ready() or self._rc_btc_feat is None:
            return

        plan = self._rc_decide()
        if plan is None:
            return
        limit, stop, target, size_factor = plan
        instrument = self._rc_eth_instrument
        if instrument is None:
            return

        stop_pct = (limit - stop) / limit
        leverage = max(int(self.cfg.leverage), 1)
        notional = (float(self.cfg.max_loss_usdt) / stop_pct) * size_factor
        budget = float(self.cfg.margin_budget)
        if budget > 0 and (notional / leverage) > budget:
            notional = budget * leverage
        quantity = self._rc_make_qty(notional / limit, instrument)
        if quantity is None:
            return

        order = self.order_factory.limit(
            instrument_id=instrument.id,
            order_side=OrderSide.BUY,
            quantity=quantity,
            price=self._rc_make_price(limit, instrument),
            time_in_force=TimeInForce.GTC,
        )
        self._rc_pending_order = order
        self._rc_stop_px = stop
        self._rc_tp_px = target
        self._rc_pending_age = 0
        self._rc_phase = "PENDING"
        self.submit_order(order)

    def _rc_decide(self):
        """Return (limit, stop, target, size_factor) or None when no setup."""
        eth = self._rc_eth.features()
        btc = self._rc_btc_feat
        if btc is None:
            return None

        gate_score = (1 if btc["chg"] > 0 else 0) + (1 if btc["close"] > btc["vwap"] else 0)
        if gate_score >= 2:
            size_factor = 1.0
        elif gate_score >= 1:
            size_factor = 0.5
        else:
            return None

        rel = eth["chg"] - btc["chg"]
        momentum = min(max(12.5 + rel * 100.0 * 2.5, 0.0), 25.0)
        vwap = eth["vwap"]
        close = eth["close"]
        if close > vwap * 1.001:
            vwap_score = 20.0
        elif close >= vwap * 0.999:
            vwap_score = 10.0
        else:
            vwap_score = 0.0
        if eth["range_pos"] > 0.66:
            range_score = 20.0
        elif eth["range_pos"] >= 0.33:
            range_score = 10.0
        else:
            range_score = 0.0

        score = (momentum + vwap_score + range_score) * _RENORM
        if score < float(self.cfg.min_score) or close < vwap:
            return None

        limit = vwap - float(self.cfg.atr_limit_mult) * eth["atr"]
        if limit <= 0:
            return None
        sl_min = float(self.cfg.eth_sl_min_pct) / 100.0
        raw_sl_pct = (limit - eth["lo"]) / limit if limit > eth["lo"] else 0.0
        stop_pct = max(raw_sl_pct, sl_min)
        if stop_pct <= 0:
            return None
        stop = limit * (1.0 - stop_pct)
        target = limit * (1.0 + float(self.cfg.tp1_pct) / 100.0)
        if not (stop < limit < target):
            return None
        return limit, stop, target, size_factor

    def on_position_opened(self, event) -> None:
        self._rc_phase = "LONG"
        self._rc_pending_order = None

    def on_position_closed(self, event) -> None:
        self._rc_phase = "FLAT"
        self._rc_stop_px = 0.0
        self._rc_tp_px = 0.0

    def _rc_exit(self) -> None:
        instrument = self._rc_eth_instrument
        if instrument is None:
            return
        for position in self.cache.positions_open(instrument_id=instrument.id):
            order = self.order_factory.market(
                instrument_id=instrument.id,
                order_side=OrderSide.SELL,
                quantity=position.quantity,
                time_in_force=TimeInForce.GTC,
            )
            self.submit_order(order)
        self._rc_phase = "FLAT"
        self._rc_stop_px = 0.0
        self._rc_tp_px = 0.0

    def _rc_make_qty(self, value: float, instrument: Instrument) -> Optional[Quantity]:
        try:
            quantity = Quantity(Decimal(str(round(value, instrument.size_precision))), instrument.size_precision)
        except Exception:
            return None
        return quantity if float(quantity) > 0 else None

    def _rc_make_price(self, value: float, instrument: Instrument) -> Price:
        return Price(Decimal(str(round(value, instrument.price_precision))), instrument.price_precision)

    def on_stop(self) -> None:
        if self._rc_eth_instrument is not None:
            self.cancel_all_orders(self._rc_eth_instrument.id)
            self.close_all_positions(self._rc_eth_instrument.id)
