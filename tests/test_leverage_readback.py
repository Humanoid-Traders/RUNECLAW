"""QC-LEV: robust leverage read-back (live incident, ETHFI 2026-07-21).

A live LONG on ETHFI was ABORTED with "Cannot confirm 5x leverage … aborting
order rather than trading at the exchange's sticky default." That message is
the fail-closed guard's `not _lev_verified` path — it fires when the leverage
READ-BACK couldn't be parsed, NOT when 5x is genuinely impossible (that is a
different "exchange stuck at Nx" abort). The exchange had applied 5x; the bot
just couldn't read the confirmation because ccxt returned the value only in the
raw `info` payload, while the parser looked at the unified fields alone.

`_parse_leverage_readback` fixes the READ without relaxing the standard: a
value is only accepted if it parses to a positive int, and callers still
require it to EQUAL the target (a wrong leverage aborts exactly as before).
"""

from __future__ import annotations

from bot.core.live_executor import _parse_leverage_readback


class TestUnifiedFields:
    def test_reads_long_leverage(self):
        assert _parse_leverage_readback({"longLeverage": 5}) == 5

    def test_reads_plain_leverage(self):
        assert _parse_leverage_readback({"leverage": "10"}) == 10

    def test_position_dict_leverage_field(self):
        # ccxt position dicts carry a unified `leverage` too.
        assert _parse_leverage_readback({"symbol": "ETHFI/USDT:USDT",
                                         "leverage": 5, "contracts": 0}) == 5


class TestRawInfoPayload:
    def test_ethfi_shape_info_only(self):
        # The exact failure shape: unified fields None, real value in info as a
        # STRING. Previously → None → false "Cannot confirm" abort.
        payload = {"symbol": "ETHFI/USDT:USDT", "longLeverage": None,
                   "leverage": None,
                   "info": {"symbol": "ETHFIUSDT", "marginCoin": "USDT",
                            "longLeverage": "5", "shortLeverage": "5",
                            "marginMode": "isolated"}}
        assert _parse_leverage_readback(payload) == 5

    def test_crossed_margin_leverage_in_info(self):
        payload = {"leverage": None,
                   "info": {"crossMarginLeverage": "20", "marginMode": "crossed"}}
        assert _parse_leverage_readback(payload) == 20


class TestFailClosedPreserved:
    def test_unparseable_returns_none(self):
        # No leverage field anywhere → None → the caller fails CLOSED (unchanged).
        assert _parse_leverage_readback({"symbol": "X", "info": {"marginCoin": "USDT"}}) is None

    def test_non_dict_returns_none(self):
        assert _parse_leverage_readback(None) is None
        assert _parse_leverage_readback([{"longLeverage": 5}]) is None

    def test_zero_or_negative_rejected(self):
        # A bogus 0/negative must NOT count as a verified leverage.
        assert _parse_leverage_readback({"leverage": 0}) is None
        assert _parse_leverage_readback({"longLeverage": -5}) is None

    def test_garbage_string_returns_none(self):
        assert _parse_leverage_readback({"leverage": "n/a"}) is None
