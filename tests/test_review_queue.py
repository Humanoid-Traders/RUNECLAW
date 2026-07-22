"""Guardian pre-trade review queue — the tighten-only operation + the queue.

The safety spine of this slice is one property: TIGHTENING NEVER GRANTS AUTHORITY.
For every action, if the tightened envelope authorizes it, the original envelope
must have authorized it too. This is proven here against a broad random action
space, plus the per-field tighten rules, the fail-closed monotonicity guard, the
persisted append-only queue, and the gateway registration/invariants.
"""

import inspect
import random

from bot.guardian.authority import authorize, compile_envelope
from bot.guardian import review_queue as rq
from bot.web import user_gateway


def _env(**over):
    spec = {
        "mode": "enforce",
        "allowed_venues": ["bitget", "hyperliquid"],
        "allowed_market_types": ["perp", "spot"],
        "max_notional_per_trade_usd": 1000,
        "max_notional_daily_usd": 5000,
        "withdraw_allowed": True,
        "withdraw_allowlist": ["0xAAA", "0xBBB"],
        "symbol_allowlist": ["BTC", "ETH", "SOL"],
        "symbol_blocklist": ["DOGE"],
    }
    spec.update(over)
    return compile_envelope(spec)


# ── the hard invariant: tightening never grants authority ──────────────

def _authorizes_subset(cur, tightened, rounds=6000, seed=0):
    now = 2_000_000_000
    kinds = ["trade", "withdraw", "transfer", "bogus"]
    venues = ["bitget", "hyperliquid", "binance", ""]
    mts = ["perp", "spot", "margin", ""]
    assets = ["BTC", "ETH", "SOL", "DOGE", "PEPE", ""]
    dests = ["0xAAA", "0xBBB", "0xCCC", ""]
    notion = [0, 50, 100, 500, 1000, 6000, None]
    r = random.Random(seed)
    for _ in range(rounds):
        a = {"kind": r.choice(kinds), "venue": r.choice(venues),
             "market_type": r.choice(mts), "asset": r.choice(assets),
             "dest": r.choice(dests), "notional_usd": r.choice(notion)}
        spent = r.choice([0, 1000, 4000, 5000])
        at = authorize(tightened, a, now_ts=now, spent_today_usd=spent)["decision"]
        ac = authorize(cur, a, now_ts=now, spent_today_usd=spent)["decision"]
        if at == "allow" and ac != "allow":
            return False, a
    return True, None


def test_tighten_never_grants_authority():
    cur = _env()
    specs = [
        {"max_notional_per_trade_usd": 100},
        {"max_notional_daily_usd": 1000},
        {"allowed_venues": ["bitget"]},
        {"allowed_market_types": ["spot"]},
        {"symbol_allowlist": ["BTC"]},
        {"symbol_blocklist": ["PEPE", "SOL"]},
        {"withdraw_allowed": False},
        {"withdraw_allowlist": ["0xAAA"]},
        {"expiry_ts": 1_900_000_000},
        {"revoked": True},
        {"max_notional_per_trade_usd": 10, "allowed_venues": ["bitget"],
         "symbol_allowlist": ["BTC"], "withdraw_allowed": False},
    ]
    for i, s in enumerate(specs):
        t = rq.tighten_envelope(cur, s)
        ok, bad = _authorizes_subset(cur, t, seed=i)
        assert ok, f"tighten {s} granted new authority for {bad}"


def test_tighten_on_unrestricted_current():
    # current is unrestricted on market types + symbols (empty = allow-all)
    cur = _env(allowed_market_types=[], symbol_allowlist=[], symbol_blocklist=[],
               max_notional_per_trade_usd=None, max_notional_daily_usd=None,
               withdraw_allowed=False, withdraw_allowlist=[])
    t = rq.tighten_envelope(cur, {"allowed_market_types": ["spot"],
                                  "symbol_allowlist": ["BTC"],
                                  "max_notional_per_trade_usd": 50})
    assert t["allowed_market_types"] == ["spot"]
    assert t["symbol_allowlist"] == ["BTC"]
    assert t["max_notional_per_trade_usd"] == 50.0
    ok, bad = _authorizes_subset(cur, t, seed=99)
    assert ok, f"granted new authority for {bad}"


def test_tighten_rehashes_and_keeps_mode():
    cur = _env()
    t = rq.tighten_envelope(cur, {"max_notional_per_trade_usd": 100})
    assert t["envelope_id"] != cur["envelope_id"]      # new identity
    assert t["compiled_hash"] != cur["compiled_hash"]
    assert t["mode"] == cur["mode"]                    # enforcement mode unchanged
    assert t["max_notional_per_trade_usd"] == 100.0


def test_tighten_ceiling_cannot_be_raised():
    cur = _env(max_notional_per_trade_usd=100)
    # asking for a LARGER cap must not loosen — stays at 100
    t = rq.tighten_envelope(cur, {"max_notional_per_trade_usd": 999999})
    assert t["max_notional_per_trade_usd"] == 100.0


def test_tighten_never_enables_withdrawal():
    cur = _env(withdraw_allowed=False, withdraw_allowlist=[])
    # a truthy withdraw flag in the tighten spec must NOT turn withdrawal on
    t = rq.tighten_envelope(cur, {"withdraw_allowed": True,
                                  "withdraw_allowlist": ["0xNEW"]})
    assert t["withdraw_allowed"] is False


def test_guard_raises_on_a_loosening_bug():
    cur = _env(max_notional_per_trade_usd=100)
    out = dict(cur)
    out["max_notional_per_trade_usd"] = 500      # a hypothetical loosening bug
    try:
        rq._assert_tighter(cur, out)
        raised = False
    except AssertionError:
        raised = True
    assert raised, "the monotonicity guard must reject a raised cap"


# ── the append-only queue ──────────────────────────────────────────────

def test_queue_records_and_lists(tmp_path):
    q = rq.ReviewQueue(path=str(tmp_path / "rq.json"))
    a = q.record({"user_id": "u1", "kind": "web3_transfer", "network": "sepolia",
                  "action": {"side": "swap", "amount_usd": 5}, "envelope_id": "env_x"})
    assert a["status"] == "pending" and a["id"].startswith("rq_")
    q.record({"user_id": "u2", "action": {}})
    assert q.pending_count() == 2
    assert q.pending_count(user_id="u1") == 1
    lst = q.list(limit=10)
    assert lst[0]["user_id"] == "u2"             # newest-first
    # persistence: a fresh instance reloads the same items
    q2 = rq.ReviewQueue(path=str(tmp_path / "rq.json"))
    assert q2.pending_count() == 2


def test_queue_mark_reviewed(tmp_path):
    q = rq.ReviewQueue(path=str(tmp_path / "rq.json"))
    q.record({"user_id": "u1", "action": {}})
    q.record({"user_id": "u1", "action": {}})
    q.record({"user_id": "u2", "action": {}})
    n = q.mark_reviewed("u1", note="tightened")
    assert n == 2
    assert q.pending_count(user_id="u1") == 0
    assert q.pending_count(user_id="u2") == 1


def test_queue_record_never_raises(tmp_path):
    q = rq.ReviewQueue(path=str(tmp_path / "rq.json"))
    # a malformed entry must still record (observe-only never breaks the caller)
    item = q.record("not a dict")
    assert item["status"] == "pending"


# ── gateway wiring + invariants ────────────────────────────────────────

def test_web3_handler_records_to_review_queue():
    src = inspect.getsource(user_gateway.handle_web3_execute)
    assert "get_review_queue" in src and ".record(" in src
    # still preview-only: the review hook must not have added a signer.
    for forbidden in ("eth_sendRawTransaction", "signTransaction", "private_key"):
        assert forbidden not in src


def test_tighten_handler_is_admin_only_and_never_signs():
    src = inspect.getsource(user_gateway.handle_guardian_review_tighten)
    assert "_is_admin_id" in src
    assert "tighten_envelope(" in src and "store.bind(" in src
    for forbidden in ("eth_sendRawTransaction", "signTransaction", "send_transaction",
                      "private_key", "eth_sendTransaction", "Wallet"):
        assert forbidden not in src


def test_review_routes_registered():
    src = inspect.getsource(user_gateway.build_gateway)
    assert 'add_get("/guardian/review", handle_guardian_review)' in src
    assert 'add_post("/guardian/review/tighten", handle_guardian_review_tighten)' in src
