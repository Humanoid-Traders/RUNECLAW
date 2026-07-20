"""Proof-of-PnL publisher scheduler — SC1-SC8.

The scheduler is DEFAULT-OFF, FAIL-SAFE, and DETERMINISTIC: it publishes
nothing unless PROOFOFPNL_PUBLISH_ENABLED is set, never raises into its caller,
and seals the same bundle+stamp to the same publish_hash. It gathers no fills
itself — the caller passes CCXT trades in — so it is fully testable without an
exchange.
"""
from bot.proofofpnl import scheduler as sch
from bot.proofofpnl.publish import PublicationStore, verify_publication


class _MemStore(PublicationStore):
    """In-memory PublicationStore — no disk."""

    def __init__(self) -> None:
        self._pub = None

    def write(self, publication: dict) -> bool:
        self._pub = publication
        return True

    def read(self):
        return self._pub


def _trade(**over):
    d = {"id": "t1", "order": "o1", "symbol": "BTC/USDT:USDT", "side": "buy",
         "price": 50000.0, "amount": 0.01, "timestamp": 1_700_000_000_000,
         "fee": {"cost": 0.25, "currency": "USDT"}}
    d.update(over)
    return d


def _pub(**over):
    kw = {"account_ids": ["operator"], "store": _MemStore(),
          "env": {"PROOFOFPNL_PUBLISH_ENABLED": "true"}, "interval_s": 3600}
    kw.update(over)
    return sch.ProofOfPnLPublisher(**kw)


def test_sc1_default_off_blocks_publishing():
    p = sch.ProofOfPnLPublisher(account_ids=["operator"], store=_MemStore(),
                                env={})   # flag unset
    assert p.enabled() is False
    assert p.should_publish(1_700_000_100) is False


def test_sc2_publish_persists_a_verifiable_public_safe_publication():
    store = _MemStore()
    p = _pub(store=store)
    pub = p.publish(1_700_000_100, [_trade(), _trade(id="t2", side="sell", timestamp=1_700_000_050_000)],
                    range_start=1_699_990_000, range_end=1_700_000_100)
    assert pub is not None
    assert pub["publish_hash"]
    # re-verification (hash re-derive + public-safety) passes
    ok, problems = verify_publication(pub)
    assert ok, problems
    # persisted as the latest
    assert store.read() is pub
    # the anchor is honest — never fabricated
    assert (pub.get("anchor") or {}).get("status", "UNVERIFIED") == "UNVERIFIED" or pub.get("anchor") is None


def test_sc3_deterministic_same_fills_and_stamp_same_hash():
    trades = [_trade()]
    h1 = _pub().publish(1_700_000_100, trades)["publish_hash"]
    h2 = _pub().publish(1_700_000_100, trades)["publish_hash"]
    assert h1 == h2


def test_sc4_cadence_gate_blocks_until_interval_elapses():
    p = _pub(interval_s=3600)
    assert p.due(1_700_000_000) is True            # never published yet
    p.publish(1_700_000_000, [_trade()])
    assert p.due(1_700_000_000 + 100) is False     # too soon
    assert p.due(1_700_000_000 + 3600) is True     # interval elapsed


def test_sc5_should_publish_needs_enabled_even_when_forced():
    off = sch.ProofOfPnLPublisher(account_ids=["operator"], store=_MemStore(), env={})
    assert off.should_publish(1_700_000_000, force=True) is False   # disabled wins
    on = _pub()
    assert on.should_publish(1_700_000_000, force=True) is True     # forced past cadence


def test_sc6_publish_is_fail_safe_on_bad_input():
    store = _MemStore()
    p = _pub(store=store)
    # A trade whose price is a non-number can trip assembly; publish must swallow
    # it and leave the store untouched, never raising.
    out = p.publish(1_700_000_100, [{"symbol": object(), "side": "buy"}])
    # Either it published an honest (possibly incomplete) bundle, or it failed
    # closed to None — but it must NEVER raise and must not corrupt the store.
    assert out is None or store.read() is out


def test_sc7_interval_parsing_and_floor(monkeypatch):
    assert sch.publish_interval_s({}) == sch.DEFAULT_INTERVAL_S
    assert sch.publish_interval_s({"PROOFOFPNL_PUBLISH_INTERVAL_S": "120"}) == 120
    assert sch.publish_interval_s({"PROOFOFPNL_PUBLISH_INTERVAL_S": "5"}) == sch.DEFAULT_INTERVAL_S  # floored
    assert sch.publish_interval_s({"PROOFOFPNL_PUBLISH_INTERVAL_S": "junk"}) == sch.DEFAULT_INTERVAL_S


def test_sc8_operator_publisher_is_cached_and_inert_until_flag():
    sch.reset_operator_publisher()
    p1 = sch.get_operator_publisher(env={"PROOFOFPNL_ACCOUNT_ID": "acct-9"})
    p2 = sch.get_operator_publisher(env={"PROOFOFPNL_ACCOUNT_ID": "acct-9"})
    assert p1 is p2                                # built once, cached
    assert p1 is not None
    assert p1.enabled() is False                   # inert until the flag is set
    sch.reset_operator_publisher()


def test_sc9_engine_run_loop_wires_the_publisher_failopen():
    """The engine's run loop must call the publisher tick, and the tick must be
    cadence-gated, live-only, and fail-open (source invariant)."""
    import inspect
    from bot.core.engine import RuneClawEngine
    run_src = inspect.getsource(RuneClawEngine.run)
    assert "_maybe_publish_proofofpnl" in run_src
    m_src = inspect.getsource(RuneClawEngine._maybe_publish_proofofpnl)
    assert "should_publish" in m_src            # cadence + enabled gate
    assert "CONFIG.is_live()" in m_src          # only REAL live fills are publishable
    assert "fetch_my_trades" in m_src           # gathers real fills
