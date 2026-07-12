"""
Catalog watch (2026-07-12) — "smart periodic update check" for new listings.

Coverage map this round formalizes:
  - New CRYPTO perps + *STOCK-suffix equity perps + HL builder markets
    already enter the scan universe automatically (futures-first discovery,
    suffix classification, venue-native overlay).
  - The gap: bare-ticker TradFi listings classify as Crypto (wrong volume
    floor / no session sizing) and NOTHING told the operator the catalog
    changed. Bitget metadata has no class-distinguishing field (verified
    live 2026-07-12), so the durable fix is detect-and-alert.

The watch diffs the futures ticker map the scanner already fetched — zero
extra API calls — seeds silently on first sight, persists across restarts,
and hands events to the proactive monitor as one INFO alert.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

from bot.core.catalog_watch import CatalogWatch


def _watch(tmp_path):
    return CatalogWatch(state_file=str(tmp_path / "catalog_seen.json"))


# ── seeding + diffing ────────────────────────────────────────────────
def test_first_observation_seeds_silently(tmp_path):
    w = _watch(tmp_path)
    events = w.observe({"BTC/USDT:USDT", "ETH/USDT:USDT"})
    assert events == []              # no alert flood on first boot
    assert w.drain_pending() == []


def test_new_listing_detected_with_class_and_volume(tmp_path):
    w = _watch(tmp_path)
    w.observe({"BTC/USDT:USDT"})
    tickers = {"KOSTOCK/USDT:USDT": {"quoteVolume": "2500000"},
               "WIF/USDT:USDT": {"quoteVolume": 900000.0}}
    events = w.observe({"BTC/USDT:USDT", "KOSTOCK/USDT:USDT",
                        "WIF/USDT:USDT"}, tickers)
    by_sym = {e["symbol"]: e for e in events}
    assert set(by_sym) == {"KOSTOCK/USDT:USDT", "WIF/USDT:USDT"}
    assert by_sym["KOSTOCK/USDT:USDT"]["category"] == "Stock"
    assert by_sym["KOSTOCK/USDT:USDT"]["vol_usd"] == 2500000.0
    assert by_sym["WIF/USDT:USDT"]["category"] == "Crypto"
    # Same catalog next cycle -> quiet.
    assert w.observe({"BTC/USDT:USDT", "KOSTOCK/USDT:USDT",
                      "WIF/USDT:USDT"}, tickers) == []


def test_empty_catalog_never_reseeds_or_alerts(tmp_path):
    """A failed/empty fetch must not wipe the seen-set (else the next good
    fetch would alert on the entire catalog)."""
    w = _watch(tmp_path)
    w.observe({"BTC/USDT:USDT"})
    assert w.observe(set()) == []
    assert w.observe({"BTC/USDT:USDT", "NEW/USDT:USDT"})[0]["symbol"] == \
        "NEW/USDT:USDT"           # only the truly-new one


def test_drain_clears_pending(tmp_path):
    w = _watch(tmp_path)
    w.observe({"BTC/USDT:USDT"})
    w.observe({"BTC/USDT:USDT", "NEW/USDT:USDT"})
    drained = w.drain_pending()
    assert [e["symbol"] for e in drained] == ["NEW/USDT:USDT"]
    assert w.drain_pending() == []


# ── persistence ──────────────────────────────────────────────────────
def test_state_survives_restart(tmp_path):
    w = _watch(tmp_path)
    w.observe({"BTC/USDT:USDT"})
    w.observe({"BTC/USDT:USDT", "NEW/USDT:USDT"})
    # Fresh instance = process restart: seen-set AND pending queue persist.
    w2 = _watch(tmp_path)
    assert w2.observe({"BTC/USDT:USDT", "NEW/USDT:USDT"}) == []  # not re-alerted
    assert [e["symbol"] for e in w2.drain_pending()] == ["NEW/USDT:USDT"]


def test_corrupt_state_fails_open(tmp_path):
    state = tmp_path / "catalog_seen.json"
    state.write_text("{not json")
    w = CatalogWatch(state_file=str(state))
    assert w.observe({"BTC/USDT:USDT"}) == []   # reseeds silently
    # And the reseed was persisted as valid JSON again.
    data = json.loads(state.read_text())
    assert data["seen"] == ["BTC/USDT:USDT"]


def test_unwritable_state_dir_never_raises():
    w = CatalogWatch(state_file="/proc/definitely/not/writable.json")
    assert w.observe({"BTC/USDT:USDT"}) == []
    assert w.observe({"BTC/USDT:USDT", "NEW/USDT:USDT"}) != []  # still works in-memory


# ── wiring pins ──────────────────────────────────────────────────────
def test_scanner_observes_futures_catalog_each_cycle():
    from bot.core.market_scanner import MarketScanner
    src = inspect.getsource(MarketScanner._scan_all_markets)
    assert "_catalog_watch.observe" in src
    assert "catalog_watch_enabled" in src
    init_src = inspect.getsource(MarketScanner.__init__)
    assert "CatalogWatch()" in init_src


def test_monitor_check_registered():
    from bot.core.proactive_monitor import ProactiveMonitor
    src = inspect.getsource(ProactiveMonitor._check_all)
    assert "_check_new_listings" in src


def test_config_flag_exists():
    from bot.config import CONFIG
    assert isinstance(CONFIG.catalog_watch_enabled, bool)


# ── monitor alert rendering ──────────────────────────────────────────
def _monitor_with_events(events):
    from bot.core.proactive_monitor import ProactiveMonitor
    engine = SimpleNamespace(
        scanner=SimpleNamespace(
            _catalog_watch=SimpleNamespace(drain_pending=lambda: events)))
    return ProactiveMonitor(engine)


def test_new_listings_alert_rendering():
    m = _monitor_with_events([
        {"symbol": "KO/USDT:USDT", "category": "Crypto", "vol_usd": 3.2e6},
        {"symbol": "RTXSTOCK/USDT:USDT", "category": "Stock", "vol_usd": 0.0},
    ])
    alerts = m._check_new_listings()
    assert len(alerts) == 1
    a = alerts[0]
    assert a.alert_type == "NEW_LISTINGS" and a.severity == "INFO"
    assert "KO/USDT:USDT" in a.body and "$3.2M/day" in a.body
    assert "RTXSTOCK/USDT:USDT" in a.body and "Stock" in a.body
    assert "config entry" in a.body            # the human-glance nudge
    assert a.dedup_key.startswith("new_listings_")


def test_new_listings_quiet_when_no_events():
    assert _monitor_with_events([])._check_new_listings() == []


def test_new_listings_defensive_without_scanner():
    from bot.core.proactive_monitor import ProactiveMonitor
    m = ProactiveMonitor(SimpleNamespace())     # engine with no scanner attr
    assert m._check_new_listings() == []
