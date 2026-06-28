"""
Performance charting page (/chart).

The page itself carries no data — it is served unauthenticated like the
dashboard index, and fetches /api/performance + /api/equitycurve client-side
with the operator's Bearer token (those endpoints stay gated). These tests pin
the route, the served HTML, and that the page is NOT under the /api/ prefix
(so auth_middleware doesn't block the page load itself).
"""

import asyncio
import inspect
from types import SimpleNamespace

from bot.web import dashboard_server as ds


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_chart_route_registered_and_not_api_prefixed():
    src = inspect.getsource(ds.create_app)
    assert 'add_get("/chart"' in src
    assert not "/chart".startswith("/api/")  # page load must not be auth-gated


def test_handler_serves_html():
    req = SimpleNamespace(app={"engine": SimpleNamespace()})
    resp = _run(ds.handle_performance_chart(req))
    assert resp.status == 200


def test_page_references_both_endpoints_and_auth():
    import pathlib
    html = (pathlib.Path(ds.__file__).parent / "performance_chart.html").read_text()
    # Consumes both analytics endpoints...
    assert "/api/performance" in html
    assert "/api/equitycurve" in html
    # ...with the same Bearer-token pattern the dashboard uses.
    assert "Authorization" in html and "Bearer" in html
    # Uses the merged field names so it doesn't render blanks.
    for field in ("current_equity", "initial_balance", "drawdown_pct",
                  "by_symbol", "win_rate", "sharpe_ratio"):
        assert field in html, f"page missing field reference: {field}"


def test_page_degrades_without_chart_library():
    # If the Chart.js CDN is unreachable, the stats/table must still render —
    # the page guards on `typeof Chart === 'undefined'` instead of throwing.
    import pathlib
    html = (pathlib.Path(ds.__file__).parent / "performance_chart.html").read_text()
    assert "typeof Chart === 'undefined'" in html
    assert "chartNote" in html


def test_auth_middleware_does_not_gate_the_page():
    # /chart is not under /api/, so the middleware passes it straight through.
    async def _passthrough(request):
        return ds.web.json_response({"ok": True})
    req = SimpleNamespace(path="/chart", headers={}, method="GET")
    # Even with NO token configured, the page must still be reachable.
    import unittest.mock as mock
    with mock.patch.object(ds, "_DASHBOARD_TOKEN", ""):
        resp = _run(ds.auth_middleware(req, _passthrough))
    assert resp.status == 200
