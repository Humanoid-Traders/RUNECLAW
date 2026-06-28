"""
Read-only analytics endpoints: GET /api/performance and /api/equitycurve.

These aggregate operator-level performance from the engine's portfolios. The
engine keeps no persistent equity series, so the curve is RECONSTRUCTED from
realized closed-trade PnL (start at initial balance, step by each net PnL in
close-time order). Both live under /api/* so the existing fail-closed Bearer
auth gates them — that gating is pinned here too.

Mirrors test_ops_endpoints.py: call handlers directly with a SimpleNamespace
fake engine, drive coroutines with a private loop.
"""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from bot.compat import UTC
from bot.utils.models import Direction, TradeExecution, TradeStatus
from bot.web import dashboard_server as ds


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _req(engine, path="/api/performance"):
    return SimpleNamespace(app={"engine": engine}, path=path, headers={}, method="GET")


_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _trade(idx, pnl, *, asset="BTC/USDT", closed=True):
    return TradeExecution(
        trade_id=f"t{idx}",
        asset=asset,
        direction=Direction.LONG,
        entry_price=100.0,
        quantity=1.0,
        stop_loss=95.0,
        take_profit=110.0,
        status=TradeStatus.EXECUTED,
        pnl=pnl,
        exit_price=100.0 + pnl,
        strategy_type="swing",
        opened_at=_T0 + timedelta(hours=idx),
        closed_at=(_T0 + timedelta(hours=idx + 1)) if closed else None,
    )


def _portfolio(trades, initial=10_000.0):
    p = SimpleNamespace()
    p.trade_history = list(trades)
    p._initial_balance = initial
    return p


def _engine(portfolios=None, base_trades=None, base_initial=10_000.0):
    """Build a fake engine. If portfolios given -> multi-user; else base portfolio."""
    if portfolios is not None:
        ups = {str(i): p for i, p in enumerate(portfolios)}
        user_portfolios = SimpleNamespace(all_portfolios=lambda: ups)
    else:
        user_portfolios = SimpleNamespace(all_portfolios=lambda: {})
    return SimpleNamespace(
        user_portfolios=user_portfolios,
        portfolio=_portfolio(base_trades or [], base_initial),
    )


# ── Pure reconstruction ───────────────────────────────────────────────────

class TestReconstructEquityCurve:
    def test_steps_by_pnl_in_close_order(self):
        # Out-of-order input; reconstruction must sort by close time.
        trades = [_trade(2, 50.0), _trade(0, 100.0), _trade(1, -30.0)]
        initial, points, closed = ds._reconstruct_equity_curve([_portfolio(trades)])
        assert initial == 10_000.0
        equities = [round(e, 2) for _, e in points]
        # seed 10000, then +100, -30, +50
        assert equities == [10_000.0, 10_100.0, 10_070.0, 10_120.0]
        assert len(closed) == 3

    def test_open_trades_excluded(self):
        trades = [_trade(0, 100.0), _trade(1, 999.0, closed=False)]
        _, points, closed = ds._reconstruct_equity_curve([_portfolio(trades)])
        assert len(closed) == 1
        assert round(points[-1][1], 2) == 10_100.0

    def test_aggregates_across_portfolios(self):
        a = _portfolio([_trade(0, 100.0)], initial=10_000.0)
        b = _portfolio([_trade(1, 200.0)], initial=5_000.0)
        initial, points, closed = ds._reconstruct_equity_curve([a, b])
        assert initial == 15_000.0
        assert len(closed) == 2
        assert round(points[-1][1], 2) == 15_300.0

    def test_empty_is_safe(self):
        initial, points, closed = ds._reconstruct_equity_curve([_portfolio([])])
        assert initial == 10_000.0
        assert points == []
        assert closed == []


# ── /api/performance ───────────────────────────────────────────────────────

class TestPerformance:
    def test_shape_and_values(self):
        eng = _engine(base_trades=[_trade(0, 100.0), _trade(1, -40.0), _trade(2, 60.0)])
        resp = _run(ds.handle_performance(_req(eng)))
        assert resp.status == 200
        import json
        body = json.loads(resp.body.decode())
        assert body["initial_balance"] == 10_000.0
        assert body["current_equity"] == 10_120.0
        m = body["metrics"]
        assert m["total_trades"] == 3
        assert m["winning_trades"] == 2
        assert m["losing_trades"] == 1
        assert "sharpe_ratio" in m and "max_drawdown_pct" in m
        assert "by_symbol" in body["breakdown"]

    def test_drawdown_is_populated_from_reconstructed_curve(self):
        # A loss after a peak must produce a non-zero max drawdown.
        eng = _engine(base_trades=[_trade(0, 500.0), _trade(1, -300.0)])
        resp = _run(ds.handle_performance(_req(eng)))
        import json
        m = json.loads(resp.body.decode())["metrics"]
        assert m["max_drawdown_pct"] > 0.0

    def test_empty_portfolio_returns_valid_zeros(self):
        resp = _run(ds.handle_performance(_req(_engine(base_trades=[]))))
        assert resp.status == 200
        import json
        body = json.loads(resp.body.decode())
        assert body["metrics"]["total_trades"] == 0
        assert body["current_equity"] == 10_000.0

    def test_multi_user_aggregation(self):
        eng = _engine(portfolios=[
            _portfolio([_trade(0, 100.0)], 10_000.0),
            _portfolio([_trade(1, 250.0)], 5_000.0),
        ])
        resp = _run(ds.handle_performance(_req(eng)))
        import json
        body = json.loads(resp.body.decode())
        assert body["initial_balance"] == 15_000.0
        assert body["metrics"]["total_trades"] == 2


# ── /api/equitycurve ───────────────────────────────────────────────────────

class TestEquityCurve:
    def test_points_and_drawdown(self):
        eng = _engine(base_trades=[_trade(0, 200.0), _trade(1, -100.0)])
        resp = _run(ds.handle_equitycurve(_req(eng, path="/api/equitycurve")))
        assert resp.status == 200
        import json
        body = json.loads(resp.body.decode())
        assert body["count"] == 3  # seed + 2 trades
        pts = body["points"]
        assert pts[0]["equity"] == 10_000.0
        assert pts[1]["equity"] == 10_200.0
        assert pts[2]["equity"] == 10_100.0
        # Peak was 10_200; dd at last point = (10200-10100)/10200*100.
        assert pts[2]["drawdown_pct"] > 0.0
        assert pts[1]["drawdown_pct"] == 0.0
        # Timestamps are ISO strings.
        assert "T" in pts[0]["timestamp"]

    def test_empty_curve_is_safe(self):
        resp = _run(ds.handle_equitycurve(_req(_engine(base_trades=[]), path="/api/equitycurve")))
        assert resp.status == 200
        import json
        body = json.loads(resp.body.decode())
        assert body["count"] == 0
        assert body["points"] == []


# ── Auth + routing ─────────────────────────────────────────────────────────

class TestAuthGating:
    async def _passthrough(self, request):
        return ds.web.json_response({"ok": True})

    def test_no_token_configured_fails_closed(self, monkeypatch):
        monkeypatch.setattr(ds, "_DASHBOARD_TOKEN", "")
        for path in ("/api/performance", "/api/equitycurve"):
            req = SimpleNamespace(path=path, headers={}, method="GET")
            resp = _run(ds.auth_middleware(req, self._passthrough))
            assert resp.status == 403

    def test_wrong_token_rejected(self, monkeypatch):
        monkeypatch.setattr(ds, "_DASHBOARD_TOKEN", "secret")
        req = SimpleNamespace(path="/api/performance",
                              headers={"Authorization": "Bearer nope"}, method="GET")
        resp = _run(ds.auth_middleware(req, self._passthrough))
        assert resp.status == 401

    def test_correct_token_passes(self, monkeypatch):
        monkeypatch.setattr(ds, "_DASHBOARD_TOKEN", "secret")
        req = SimpleNamespace(path="/api/performance",
                              headers={"Authorization": "Bearer secret"}, method="GET")
        resp = _run(ds.auth_middleware(req, self._passthrough))
        assert resp.status == 200


class TestRoutesRegistered:
    def test_analytics_routes_under_api_prefix(self):
        import inspect
        src = inspect.getsource(ds.create_app)
        for route in ("/api/performance", "/api/equitycurve"):
            assert f'add_get("{route}"' in src
            assert route.startswith("/api/")  # so auth_middleware gates them
