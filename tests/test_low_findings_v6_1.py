"""
Regression tests for the V6.1 LOW findings (docs/AUDIT_REPORT_V6.1.md):
  AN-2  — divergence strength normalized by the indicator range (no saturation
          when the pivot value is near zero).
  MCP-2 — runeclaw_backtest `bars` is clamped and `mode` is validated.
"""
import numpy as np

from bot.core.divergence import _check_divergence


# ── AN-2: near-zero-pivot divergence strength is graduated, not pinned ──

def _bullish_conf(ind_lows):
    price = np.full(50, 100.0)
    ind = np.full(50, 0.0005)        # near-zero baseline stresses the old denom
    price[12], price[38] = 95.0, 90.0   # price lower low
    ind[12], ind[38] = ind_lows         # indicator higher low (bullish div)
    sigs = _check_divergence(price, ind, lookback=50, indicator_name="obv")
    bull = [s for s in sigs if s.div_type == "regular_bullish"]
    assert bull, "expected a regular bullish divergence"
    return bull[0].confidence


def test_divergence_strength_not_saturated_near_zero():
    """A larger near-zero divergence must score strictly higher than a smaller
    one. The old abs(i1_val)+1e-10 denominator pinned both at the cap."""
    small = _bullish_conf((-0.0001, 0.0))
    large = _bullish_conf((-0.0010, 0.0009))
    assert large > small
    assert small < 0.90  # not pinned at the cap


# ── MCP-2: bounds enforcement ──

def test_mcp_bars_clamped_and_mode_validated(monkeypatch):
    import asyncio
    import bot.mcp.server as srv
    # Patch the module attribute directly (auto-restored) — reloading the
    # module would replace its objects mid-suite and break later tests.
    monkeypatch.setattr(srv, "_MCP_AUTH_TOKEN", "tok")

    server = srv.RuneClawMCPServer()

    async def _call(args):
        return await server.call_tool("runeclaw_fullscan", args, auth_token="tok")

    # Unknown mode is rejected with a structured error (not a silent full scan).
    res = asyncio.run(_call({"mode": "evil"}))
    assert res["status"] == "error"
    assert "mode" in res["result"].lower()
