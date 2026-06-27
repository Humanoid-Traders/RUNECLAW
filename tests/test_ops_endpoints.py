"""
Operational endpoints (roadmap ops item): liveness /health, readiness /ready,
and a hand-rolled Prometheus /metrics.

/health always answers 200 (the server is up). /ready reflects real engine
health and FAILS CLOSED (503) when the exchange is down, health is CRITICAL, or
health can't be read. /metrics exposes ONLY non-financial operational signals,
so it is safe to expose to an unauthenticated scraper.
"""

import asyncio
from types import SimpleNamespace

from bot.core.system_health import HealthSnapshot
from bot.web import dashboard_server as ds


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _req(engine):
    return SimpleNamespace(app={"engine": engine})


def _engine(snapshot=None, *, open_positions=(), cb=False, losses=0, stats=None):
    health = SimpleNamespace(snapshot=lambda: snapshot) if snapshot is not None else None
    eng = SimpleNamespace(
        portfolio=SimpleNamespace(open_positions=list(open_positions)),
        risk=SimpleNamespace(circuit_breaker_active=cb, consecutive_losses=losses,
                             stats=stats if stats is not None else {}),
    )
    if health is not None:
        eng.health = health
    return eng


_HEALTHY = HealthSnapshot(uptime_seconds=120.0, exchange_connected=True, ws_connected=True,
                          status="HEALTHY", total_api_calls=10, total_errors=0, api_latency_ms=50.0)
_CRITICAL = HealthSnapshot(exchange_connected=False, status="CRITICAL")
_DEGRADED_DISCONNECTED = HealthSnapshot(exchange_connected=False, status="DEGRADED")


class TestHealth:
    def test_health_is_always_200(self):
        resp = _run(ds.handle_health(_req(_engine(_HEALTHY))))
        assert resp.status == 200

    def test_health_does_not_touch_engine_internals(self):
        # Even a totally empty engine must not break liveness.
        resp = _run(ds.handle_health(_req(SimpleNamespace(app={}))))
        assert resp.status == 200


class TestReady:
    def test_ready_when_healthy(self):
        resp = _run(ds.handle_ready(_req(_engine(_HEALTHY))))
        assert resp.status == 200

    def test_not_ready_when_critical(self):
        resp = _run(ds.handle_ready(_req(_engine(_CRITICAL))))
        assert resp.status == 503

    def test_not_ready_when_exchange_disconnected(self):
        resp = _run(ds.handle_ready(_req(_engine(_DEGRADED_DISCONNECTED))))
        assert resp.status == 503

    def test_fails_closed_without_health(self):
        # engine has no .health attribute -> readiness can't be determined -> 503.
        resp = _run(ds.handle_ready(_req(_engine(snapshot=None))))
        assert resp.status == 503


class TestMetrics:
    def test_prometheus_text_and_core_series(self):
        resp = _run(ds.handle_metrics(_req(
            _engine(_HEALTHY, open_positions=[1, 2], cb=False, losses=0,
                    stats={"total_checks": 5, "total_rejections": 1}))))
        assert resp.content_type == "text/plain"
        txt = resp.text
        assert "runeclaw_up 1" in txt
        assert "runeclaw_ready 1" in txt
        assert "runeclaw_open_positions 2" in txt
        assert "runeclaw_exchange_connected 1" in txt
        assert "runeclaw_risk_rejections_total 1" in txt
        # Each emitted series carries HELP + TYPE.
        assert "# HELP runeclaw_up" in txt and "# TYPE runeclaw_up gauge" in txt

    def test_ready_zero_when_critical(self):
        resp = _run(ds.handle_metrics(_req(_engine(_CRITICAL))))
        assert "runeclaw_ready 0" in resp.text
        # runeclaw_up still 1 — the server itself is serving.
        assert "runeclaw_up 1" in resp.text

    def test_no_financial_data_leaked(self):
        resp = _run(ds.handle_metrics(_req(
            _engine(_HEALTHY, open_positions=[1], stats={"total_checks": 3}))))
        low = resp.text.lower()
        for forbidden in ("equity", "pnl", "balance", "usd", "profit"):
            assert forbidden not in low, f"leaked '{forbidden}' into /metrics"

    def test_metrics_survives_a_broken_engine(self):
        # No health, no risk stats — still returns valid text with runeclaw_up.
        resp = _run(ds.handle_metrics(_req(_engine(snapshot=None))))
        assert resp.status == 200
        assert "runeclaw_up 1" in resp.text


class TestRoutesRegistered:
    def test_routes_present(self):
        import inspect
        src = inspect.getsource(ds.create_app)
        for route in ("/health", "/ready", "/metrics"):
            assert f'add_get("{route}"' in src

    def test_probes_are_not_under_api_prefix(self):
        # auth_middleware only guards /api/* — probes must stay outside it.
        for route in ("/health", "/ready", "/metrics"):
            assert not route.startswith("/api/")
