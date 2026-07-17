"""
Cross-venue net worth (PR X, bot side).

Pins the trust model of GET /gateway/networth:
  * caller auth runs the same _guard_user gate as every other endpoint
  * paper equity and the connected-CEX snapshot are reported per-section,
    each failing soft on its own
  * the CEX read is ONE read-only balance fetch on the user's stored venue —
    and the decrypted credential fields NEVER appear in the response
  * _balance_total is defensive over ccxt's balance shapes
"""

import contextlib
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

import bot.core.exchange_credentials as xc
from bot.core.exchange_credentials import _balance_total
from bot.web import user_gateway as ug

SECRET = "s" * 32
HDRS = {"X-Gateway-Secret": SECRET}
AUTHED = {"7": {"authorized": True, "role": "trader"}}


class FakeUsers:
    def __init__(self, users=None):
        self._users = dict(users or {})

    def get(self, tg):
        return self._users.get(str(tg))

    def register(self, tg, name="", auto_role=""):
        return self._users.setdefault(
            str(tg), {"authorized": True, "role": "trader"})

    def has_permission(self, tg, cmd):
        return True

    def can_trade_live(self, tg):
        return False


class FakeHandler:
    def __init__(self, users=None):
        self.users = FakeUsers(users)
        self._limiter = SimpleNamespace(allow=lambda uid: True)
        self.intent_router = SimpleNamespace(classify_rules=lambda t: None)
        self.registry = SimpleNamespace(get=lambda name: None)
        self.conversations = SimpleNamespace(
            append=lambda *a, **k: None, get_recent=lambda *a, **k: [])

    def _allowlist_ids(self):
        return set()

    def _can_trade_live(self, tg):
        return False


class FakePortfolio:
    def snapshot(self):
        return SimpleNamespace(equity_usd=10_250.5, total_pnl=250.5)


class FakeEngine:
    def __init__(self):
        self._pending_ideas = {}
        self.user_portfolios = SimpleNamespace(get=lambda uid: FakePortfolio())


class FakeStore:
    """Credential store double: one user connected to bybit."""

    def __init__(self, connected=True, fields=None):
        self._connected = connected
        self._fields = fields if fields is not None else {
            "api_key": "k" * 16, "api_secret": "s" * 16}

    def has(self, tg):
        return self._connected

    def get_venue(self, tg):
        return "bybit"

    def get(self, tg):
        return self._fields


@contextlib.asynccontextmanager
async def client_for(engine, handler):
    app = ug.build_gateway(engine, handler)
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        yield c
    finally:
        await c.close()


# ── Pure helper ──────────────────────────────────────────────────────────────

def test_balance_total_prefers_total_then_free_plus_used():
    assert _balance_total({"USDT": {"total": 123.45}}, "USDT") == 123.45
    assert _balance_total({"USDT": {"free": 100, "used": 25}}, "USDT") == 125.0
    assert _balance_total({}, "USDT") == 0.0
    assert _balance_total({"USDT": {"total": "garbage"}}, "USDT") == 0.0
    assert _balance_total(None or {}, "USDT") == 0.0


async def test_balance_snapshot_unknown_venue_fails_soft():
    snap = await xc.balance_snapshot("kraken", {"api_key": "x", "api_secret": "y"})
    assert snap["ok"] is False
    assert "unknown venue" in snap["detail"]


# ── Gateway endpoint ─────────────────────────────────────────────────────────

async def test_networth_connected_venue_and_paper(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setattr(xc, "get_credential_store", lambda: FakeStore())

    seen = {}

    async def fake_snapshot(venue, fields, sandbox=False):
        seen["venue"] = venue
        seen["fields"] = fields
        return {"ok": True, "venue": venue, "currency": "USDT",
                "equity_usd": 512.34, "detail": "512.34 USDT total"}

    monkeypatch.setattr(xc, "balance_snapshot", fake_snapshot)

    async with client_for(FakeEngine(), FakeHandler(AUTHED)) as c:
        r = await c.get("/networth?telegram_id=7", headers=HDRS)
        assert r.status == 200
        data = await r.json()
        assert data["read_only"] is True
        assert data["paper"] == {"equity_usd": 10250.5, "total_pnl": 250.5,
                                 "simulated": True}
        cex = data["cex"]
        assert cex["connected"] is True
        assert cex["venue"] == "bybit"
        assert cex["equity_usd"] == 512.34
        # THE invariant: decrypted credentials were used in-process but never
        # serialized into the response.
        blob = str(data)
        assert "k" * 16 not in blob and "s" * 16 not in blob
        assert seen["venue"] == "bybit"


async def test_networth_not_connected_and_unreadable(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)

    monkeypatch.setattr(xc, "get_credential_store",
                        lambda: FakeStore(connected=False))
    async with client_for(FakeEngine(), FakeHandler(AUTHED)) as c:
        r = await c.get("/networth?telegram_id=7", headers=HDRS)
        data = await r.json()
        assert data["cex"] == {"connected": False}
        assert data["paper"]["equity_usd"] == 10250.5

    # Connected but the record can't decrypt (master key changed).
    monkeypatch.setattr(xc, "get_credential_store",
                        lambda: FakeStore(connected=True, fields=None))
    async with client_for(FakeEngine(), FakeHandler(AUTHED)) as c:
        r = await c.get("/networth?telegram_id=7", headers=HDRS)
        data = await r.json()
        assert data["cex"]["connected"] is True
        assert data["cex"]["ok"] is False
        assert data["cex"]["equity_usd"] is None


async def test_networth_guards_unauthorized_callers(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    async with client_for(FakeEngine(), FakeHandler(users={})) as c:
        # Telegram-shaped id that is NOT allowlisted/registered → rejected by
        # the same guard every other gateway endpoint uses.
        r = await c.get("/networth?telegram_id=999", headers=HDRS)
        assert r.status in (401, 403)


async def test_networth_wrong_secret_is_403(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    async with client_for(FakeEngine(), FakeHandler(AUTHED)) as c:
        r = await c.get("/networth?telegram_id=7",
                        headers={"X-Gateway-Secret": "x" * 32})
        assert r.status == 403
