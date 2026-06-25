# RUNECLAW ETH Pullback (Backtest)

A backtestable, long-only RUNECLAW strategy on **ETHUSDT** perpetual futures. It
is the historically-replayable sibling of the RUNECLAW live scanner: the live
order-book dimension cannot be replayed, so this package drops it and keeps only
the parts that reconstruct cleanly from historical klines, renormalized to a
0–100 score. BTCUSDT is used purely as a regime-gate context series and is never
traded.

## 策略 / Strategy

Each bar the strategy reads two things: the market leader's regime and the
quality of ETH itself. The BTC gate opens only when BTC is up on the rolling day
and trading above its rolling average price. With the gate open, ETH is scored
across three replayable dimensions — relative strength versus BTC, position
versus its rolling VWAP, and where price sits within the rolling-day range —
renormalized to 0–100. The order-book and cross-sectional volume dimensions from
the live scanner are intentionally omitted here because they cannot be replayed
for a single symbol.

## 开仓 / Entry

When the gate is open and ETH clears the minimum score while holding above VWAP,
the strategy rests a **limit buy** at `VWAP − 0.5 × ATR14`, where `ATR14` is
estimated as the rolling-day range divided by 2.5. The order waits for a
pullback; if it is not filled within a few bars it is cancelled and the strategy
re-evaluates. Position size is solved backward from the per-trade dollar risk:
`notional = max_loss_usdt / stop_distance%`, then `margin = notional / leverage`,
capped by `margin_budget`.

## 平仓 / Exit

- **Stop loss** sits just below the rolling-day low, floored at the ETH tier
  minimum (1.5%). It is checked against each bar's low.
- **Take profit** is a defined target at +3.5% above entry, checked against each
  bar's high.

The backtest models a single protective stop and a single profit target per
trade (the live scanner additionally layers a second target and a trailing
runner; those are not part of this replayable evidence).

## Parameters

- **leverage** — amplifies gains and drawdowns; also feeds the sizing math.
- **margin_budget** — capital cap and the denominator for the displayed return.
- **max_loss_usdt** — hard per-trade dollar risk; drives position size.
- **min_score** — the 0–100 quality bar ETH must clear to enter.

## How to read the backtest

`total_return_pct` is the strategy-budget return (`net_pnl / margin_budget`);
`account_total_return_pct` is the raw account-level number. Read `max_drawdown_pct`,
`win_rate`, and `total_trades` together — a high return on very few trades is not
robust evidence. The replay covers roughly the most recent ~1000 hourly bars of
ETH on Bitget.

## 风险 / Risk

This is a momentum-continuation strategy and it underperforms when BTC is choppy
or falling, when ETH spends the session below its average price, or when fast
declines slice straight through the stop. It can sit idle for long stretches by
design. A historical backtest is evidence, not a guarantee — it omits the live
order-book confirmation, and live trading pays fees and slippage that erode edge.
Past performance never guarantees future results; size every trade to a drawdown
you can actually tolerate and do not use leverage you cannot afford to lose.
