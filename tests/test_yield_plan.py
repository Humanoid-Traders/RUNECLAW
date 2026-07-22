"""
CROSS-2 — guided cross-chain yield execution (the single-move triple-gate).

The evaluator is PURE and fail-closed: a move executes only when the scanner
rates it worth, the deterministic yield policy passes, AND the Authority
Envelope authorizes the first-leg transfer — plus the locked hard-gates
(stables-only, non-custodial, recallable). Anything missing → skip with a
reason, never an exception, never a guess. Nothing here signs or moves funds.
"""

from bot.guardian import yield_plan as yp


def _worth_move(**over):
    # A scanner move that IS worth moving (worth='yes', positive net-of-cost).
    m = {
        "asset": "USDC", "amount_usd": 40.0, "from_chain": "sepolia",
        "current_apy": 3.0, "best_apy": 6.0, "delta_apy": 3.0,
        "custodial": False, "lockup_days": 0,
        "breakeven_days": 12, "net_horizon_usd": 1.20, "worth": "yes",
    }
    m.update(over)
    return m


def _envelope(**over):
    # An enforcing envelope that allows a transfer to one recallable address.
    e = {
        "envelope_id": "env-1", "revoked": False,
        "withdraw_allowed": True,
        "withdraw_allowlist": ["0x" + "ab" * 20],
        "max_notional_daily_usd": 150.0,
    }
    e.update(over)
    return e


DEST = "0x" + "ab" * 20


class TestYieldPolicy:
    def test_a_worth_move_passes_the_default_policy(self):
        r = yp.evaluate_yield_policy(yp.DEFAULT_YIELD_POLICY, _worth_move())
        assert r["verdict"] == "pass", r["reasons"]

    def test_marginal_apy_is_rejected(self):
        r = yp.evaluate_yield_policy(yp.DEFAULT_YIELD_POLICY,
                                     _worth_move(delta_apy=0.4))
        assert r["verdict"] == "fail"
        assert any("below the 1.00% minimum" in x for x in r["reasons"])

    def test_custodial_and_lockup_are_rejected(self):
        r1 = yp.evaluate_yield_policy(yp.DEFAULT_YIELD_POLICY, _worth_move(custodial=True))
        r2 = yp.evaluate_yield_policy(yp.DEFAULT_YIELD_POLICY, _worth_move(lockup_days=30))
        assert r1["verdict"] == "fail" and any("custodial" in x for x in r1["reasons"])
        assert r2["verdict"] == "fail" and any("lockup" in x for x in r2["reasons"])

    def test_per_move_and_daily_caps(self):
        big = yp.evaluate_yield_policy(yp.DEFAULT_YIELD_POLICY, _worth_move(amount_usd=80.0))
        assert big["verdict"] == "fail" and any("per-move cap" in x for x in big["reasons"])
        daily = yp.evaluate_yield_policy(yp.DEFAULT_YIELD_POLICY, _worth_move(amount_usd=40.0),
                                         spent_today_usd=120.0)
        assert daily["verdict"] == "fail" and any("daily total" in x for x in daily["reasons"])

    def test_unknown_rule_type_is_skipped_not_blocking(self):
        r = yp.evaluate_yield_policy([{"type": "made_up_rule", "value": 1}], _worth_move())
        assert r["verdict"] == "pass"      # fail-open: unknown rule never blocks
        assert r["checked"] == 0


class TestTripleGate:
    def test_worth_move_with_allowing_envelope_executes(self):
        d = yp.evaluate_yield_move(move=_worth_move(), to_chain="base-sepolia", dest=DEST,
                                   envelope=_envelope(), now_ts=1000.0)
        assert d["verdict"] == "execute", d["reasons"]
        assert d["gates"] == {"scanner": True, "policy": True, "authority": True}
        assert d["first_leg"]["kind"] == "transfer"
        assert d["first_leg"]["asset"] == "USDC"
        assert d["first_leg"]["network"] == "sepolia"       # source chain
        assert d["first_leg"]["notional_usd"] == 40.0

    def test_non_stable_is_skipped(self):
        d = yp.evaluate_yield_move(move=_worth_move(asset="WETH"), to_chain="base-sepolia",
                                   dest=DEST, envelope=_envelope(), now_ts=1000.0)
        assert d["verdict"] == "skip"
        assert d["stables_only_ok"] is False
        assert any("stablecoin" in x for x in d["reasons"])

    def test_not_worth_is_skipped(self):
        d = yp.evaluate_yield_move(move=_worth_move(worth="marginal", net_horizon_usd=0.0),
                                   to_chain="base-sepolia", dest=DEST,
                                   envelope=_envelope(), now_ts=1000.0)
        assert d["verdict"] == "skip"
        assert d["gates"]["scanner"] is False

    def test_dest_not_on_allowlist_is_denied_by_authority(self):
        d = yp.evaluate_yield_move(move=_worth_move(), to_chain="base-sepolia",
                                   dest="0x" + "cd" * 20, envelope=_envelope(), now_ts=1000.0)
        assert d["verdict"] == "skip"
        assert d["gates"]["authority"] is False

    def test_revoked_envelope_denies(self):
        d = yp.evaluate_yield_move(move=_worth_move(), to_chain="base-sepolia", dest=DEST,
                                   envelope=_envelope(revoked=True), now_ts=1000.0)
        assert d["verdict"] == "skip"
        assert d["gates"]["authority"] is False

    def test_no_envelope_fails_closed(self):
        d = yp.evaluate_yield_move(move=_worth_move(), to_chain="base-sepolia", dest=DEST,
                                   envelope=None, now_ts=1000.0)
        assert d["verdict"] == "skip"
        assert d["gates"]["authority"] is False

    def test_missing_dest_is_skipped(self):
        d = yp.evaluate_yield_move(move=_worth_move(), to_chain="base-sepolia", dest="",
                                   envelope=_envelope(), now_ts=1000.0)
        assert d["verdict"] == "skip"
        assert any("destination" in x for x in d["reasons"])

    def test_never_raises_on_junk_move(self):
        d = yp.evaluate_yield_move(move={}, to_chain="", dest=DEST,
                                   envelope=_envelope(), now_ts=1000.0)
        assert d["verdict"] == "skip"      # empty move → skipped, not crashed
        assert isinstance(d["reasons"], list)
