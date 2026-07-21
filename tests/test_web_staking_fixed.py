"""WEB-2: fixed-term staking on the website (the primary surface).

Pins the hard line as it crosses the gateway: LOCKED staking is
OPERATOR-only behind an explicit double-confirm that shows the lock END
date — and the second confirm is enforced SERVER-side: the execute call
must echo the exact lock end date the UI displayed, or nothing moves.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

import bot.core.yield_radar as yr
from bot.core.yield_radar import YieldReport, YieldRow, lock_end_date
from bot.web import user_gateway as ug

SECRET = "s" * 32
HDRS = {"X-Gateway-Secret": SECRET}
AUTHED = {"7": {"authorized": True, "role": "trader"},
          "9": {"authorized": True, "role": "admin"}}


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


class FakeHandler:
    def __init__(self, users=None, client="CLIENT"):
        self.users = FakeUsers(users)
        self._limiter = SimpleNamespace(allow=lambda uid: True)
        self._client = client

    def _allowlist_ids(self):
        return set()

    def _yield_client(self):
        return self._client

    def _engine_free_usdt(self):
        return 100.0


@contextlib.asynccontextmanager
async def client_for(handler):
    app = ug.build_gateway(SimpleNamespace(_pending_ideas={}), handler)
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        yield c
    finally:
        await c.close()


def _report():
    return YieldReport(rows=[
        YieldRow(coin="USDT", idle_amount=140, idle_usd=140, stakeable_usd=98.0,
                 apy_flexible=8.5, source="futures free", product_id="7001",
                 fixed_terms=[{"days": 90, "apy": 12.0, "product_id": "8090"},
                              {"days": 30, "apy": 10.0, "product_id": "8030"}]),
        YieldRow(coin="ETH", idle_amount=0.5, idle_usd=1500, stakeable_usd=1500,
                 fixed_terms=[{"days": 30, "apy": 3.0, "product_id": "8100"}]),
    ])


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("WEB_GATEWAY_SECRET", SECRET)


async def test_options_and_execute_are_admin_only(monkeypatch):
    monkeypatch.setattr(yr, "build_report", lambda *a, **k: _report())
    async with client_for(FakeHandler(AUTHED)) as c:
        r = await c.get("/staking/fixed?telegram_id=7", headers=HDRS)
        assert r.status == 403
        r = await c.post("/staking/fixed", headers=HDRS, json={
            "telegram_id": "7", "coin": "USDT", "product_id": "8090",
            "days": 90, "confirm_lock_end": lock_end_date(90)})
        assert r.status == 403


async def test_options_list_terms_with_lock_end_dates(monkeypatch):
    monkeypatch.setattr(yr, "build_report", lambda *a, **k: _report())
    async with client_for(FakeHandler(AUTHED)) as c:
        r = await c.get("/staking/fixed?telegram_id=9", headers=HDRS)
        assert r.status == 200
        d = await r.json()
        assert d["available"] is True
        # Stables-only surface: the ETH row (info-only) is never offered.
        assert [row["coin"] for row in d["rows"]] == ["USDT"]
        t90 = next(t for t in d["rows"][0]["terms"] if t["days"] == 90)
        assert t90["lock_end"] == lock_end_date(90)
        assert "NOT redeemable" in d["note"]


async def test_execute_refuses_wrong_lock_end_echo(monkeypatch):
    called = []
    monkeypatch.setattr(yr, "execute_stake_fixed",
                        lambda *a, **k: called.append(a))
    async with client_for(FakeHandler(AUTHED)) as c:
        r = await c.post("/staking/fixed", headers=HDRS, json={
            "telegram_id": "9", "coin": "USDT", "product_id": "8090",
            "days": 90, "confirm_lock_end": "2020-01-01"})
        assert r.status == 409
        d = await r.json()
        assert d["error"] == "lock_end_mismatch"
        assert d["expected_lock_end"] == lock_end_date(90)
        assert not called, "a mismatched confirm must never reach the money path"


async def test_execute_with_correct_echo_runs_the_shared_money_path(monkeypatch):
    called = {}

    def fake_exec(client, coin, product_id, days, futures_free_usdt=0.0):
        called.update(client=client, coin=coin, pid=product_id, days=days,
                      free=futures_free_usdt)
        return yr.ActionResult(True, f"subscribed — LOCKED until {lock_end_date(days)}")

    monkeypatch.setattr(yr, "execute_stake_fixed", fake_exec)
    async with client_for(FakeHandler(AUTHED)) as c:
        r = await c.post("/staking/fixed", headers=HDRS, json={
            "telegram_id": "9", "coin": "USDT", "product_id": "8090",
            "days": 90, "confirm_lock_end": lock_end_date(90)})
        assert r.status == 200
        d = await r.json()
        assert d["ok"] is True and "LOCKED until" in d["detail"]
    assert called == {"client": "CLIENT", "coin": "USDT", "pid": "8090",
                      "days": 90, "free": 100.0}


async def test_no_operator_keys_degrades_honestly(monkeypatch):
    monkeypatch.setattr(yr, "build_report", lambda *a, **k: _report())
    async with client_for(FakeHandler(AUTHED, client=None)) as c:
        r = await c.get("/staking/fixed?telegram_id=9", headers=HDRS)
        assert (await r.json())["available"] is False
        r = await c.post("/staking/fixed", headers=HDRS, json={
            "telegram_id": "9", "coin": "USDT", "product_id": "8090",
            "days": 90, "confirm_lock_end": lock_end_date(90)})
        assert r.status == 503
