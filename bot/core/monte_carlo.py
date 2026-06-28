"""
RUNECLAW Monte Carlo Risk Simulation — statistical risk assessment.

Takes historical trade results and runs Monte Carlo simulations to:
  - Estimate worst-case drawdown at various confidence levels
  - Project expected equity growth with confidence bands
  - Calculate probability of ruin (account going below threshold)
  - Recommend position size adjustments based on tail risk

Methodology:
  - Reshuffle trade sequence randomly N times (default 10,000)
  - For each shuffle, compute equity curve and max drawdown
  - Build distribution of max drawdowns
  - Report percentile-based risk metrics
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class MonteCarloResult:
    """Results of Monte Carlo simulation."""
    num_simulations: int
    num_trades: int

    # Drawdown percentiles
    dd_50th: float    # median max drawdown %
    dd_75th: float    # 75th percentile
    dd_90th: float    # 90th percentile
    dd_95th: float    # 95th percentile — key risk metric
    dd_99th: float    # 99th percentile — tail risk

    # Return percentiles (final equity as % of starting)
    return_median: float
    return_5th: float   # worst 5% outcome
    return_95th: float  # best 5% outcome

    # Risk metrics
    probability_of_ruin: float  # P(equity < ruin_threshold)
    recommended_size_mult: float  # suggested position size multiplier
    risk_rating: str  # "LOW", "MEDIUM", "HIGH", "EXTREME"

    description: str


def run_monte_carlo(
    trade_pnls: list[float],
    starting_equity: float = 10000.0,
    num_simulations: int = 10000,
    ruin_threshold_pct: float = 50.0,
    max_acceptable_dd_pct: float = 20.0,
    target_confidence: float = 95.0,
) -> Optional[MonteCarloResult]:
    """Run Monte Carlo simulation on trade PnL history.

    Args:
        trade_pnls: list of net PnL values from closed trades
        starting_equity: starting account balance
        num_simulations: number of random reshuffles
        ruin_threshold_pct: equity drawdown % that defines "ruin"
        max_acceptable_dd_pct: max drawdown you're willing to accept
        target_confidence: confidence level for risk assessment (e.g., 95 = 95th percentile)

    Returns:
        MonteCarloResult or None if insufficient trades.
    """
    if len(trade_pnls) < 5:
        return None

    pnls = np.array(trade_pnls, dtype=float)
    n_trades = len(pnls)

    # Cap simulations for performance
    num_simulations = min(num_simulations, 50000)

    max_drawdowns = np.zeros(num_simulations)
    final_equities = np.zeros(num_simulations)
    ruin_count = 0
    ruin_level = starting_equity * (1 - ruin_threshold_pct / 100.0)

    for sim in range(num_simulations):
        # Random shuffle of trade order
        shuffled = np.random.permutation(pnls)

        # Compute equity curve
        equity = starting_equity
        peak = equity
        max_dd = 0.0
        hit_ruin = False

        for pnl in shuffled:
            equity += pnl
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
            if equity <= ruin_level:
                hit_ruin = True

        max_drawdowns[sim] = max_dd
        final_equities[sim] = equity
        if hit_ruin:
            ruin_count += 1

    # Compute percentiles
    dd_percentiles = np.percentile(max_drawdowns, [50, 75, 90, 95, 99])
    return_pcts = (final_equities / starting_equity - 1) * 100
    return_percentiles = np.percentile(return_pcts, [5, 50, 95])

    prob_ruin = ruin_count / num_simulations

    # Risk rating based on 95th percentile drawdown
    dd_95 = dd_percentiles[3]
    if dd_95 < 10:
        risk_rating = "LOW"
    elif dd_95 < 20:
        risk_rating = "MEDIUM"
    elif dd_95 < 35:
        risk_rating = "HIGH"
    else:
        risk_rating = "EXTREME"

    # Recommended size multiplier
    # If 95th percentile DD exceeds acceptable DD, scale down proportionally
    if dd_95 > max_acceptable_dd_pct and dd_95 > 0:
        recommended_mult = max_acceptable_dd_pct / dd_95
        recommended_mult = max(0.25, min(1.0, recommended_mult))
    else:
        recommended_mult = 1.0

    # Build description
    desc_parts = [
        f"{num_simulations:,} sims on {n_trades} trades",
        f"95th DD: {dd_95:.1f}%",
        f"Median return: {return_percentiles[1]:.1f}%",
        f"Ruin prob: {prob_ruin:.1%}",
        f"Risk: {risk_rating}",
    ]
    if recommended_mult < 1.0:
        desc_parts.append(f"Suggest {recommended_mult:.0%} size reduction")

    return MonteCarloResult(
        num_simulations=num_simulations,
        num_trades=n_trades,
        dd_50th=round(dd_percentiles[0], 2),
        dd_75th=round(dd_percentiles[1], 2),
        dd_90th=round(dd_percentiles[2], 2),
        dd_95th=round(dd_percentiles[3], 2),
        dd_99th=round(dd_percentiles[4], 2),
        return_median=round(return_percentiles[1], 2),
        return_5th=round(return_percentiles[0], 2),
        return_95th=round(return_percentiles[2], 2),
        probability_of_ruin=round(prob_ruin, 4),
        recommended_size_mult=round(recommended_mult, 2),
        risk_rating=risk_rating,
        description=" | ".join(desc_parts),
    )


def monte_carlo_size_adjustment(
    trade_pnls: list[float],
    starting_equity: float,
    max_acceptable_dd_pct: float = 20.0,
) -> float:
    """Quick Monte Carlo check that returns only the position size multiplier.

    Uses fewer simulations (2000) for speed. Called by risk engine
    to dynamically adjust position sizing based on tail risk.

    Returns:
        Multiplier between 0.25 and 1.0 (1.0 = no adjustment needed).
    """
    result = run_monte_carlo(
        trade_pnls=trade_pnls,
        starting_equity=starting_equity,
        num_simulations=2000,
        max_acceptable_dd_pct=max_acceptable_dd_pct,
    )
    if result is None:
        return 1.0
    return result.recommended_size_mult
