"""
Position-card symbol button vs stale local tracking.

Operator report: clicking the symbol button (e.g. "AMDUSDT") on a live position
card returned "position closed" while /livepositions (which reads the exchange)
still showed it OPEN. The pos_details_<trade_id> handler looked the position up
in LOCAL state; when local tracking had gone stale (booked closed while the
exchange still held it), it fell through to "position closed" — because its
exchange fallback matched by SYMBOL while the button passes a TRADE_ID, so the
fallback never matched.

Fix: resolve the SYMBOL from any local record (open OR closed) for that trade_id
and let the exchange fallback match it by symbol. This test guards that logic is
present in the handler (the handler is a single ~400KB method; its behaviours
are guarded by source inspection here, matching the existing test style).
"""

import inspect

from bot.skills.telegram_handler import TelegramHandler


def _pos_details_block() -> str:
    src = inspect.getsource(TelegramHandler._handle_callback)
    i = src.index('pos_details_')
    j = src.index('pos_close_', i)   # next callback branch
    return src[i:j]


class TestPosDetailsStaleSync:
    def test_resolves_symbol_from_local_record(self):
        block = _pos_details_block()
        # Resolve the symbol from open OR closed local records for a trade_id.
        assert "_resolved_sym" in block
        assert "closed_positions" in block

    def test_exchange_fallback_matches_by_resolved_symbol(self):
        block = _pos_details_block()
        # The exchange fallback must also match on the resolved symbol, not only
        # the raw ident (which is a trade_id and never equals an exchange sym).
        assert "ep_clean == _rs_clean" in block

    def test_exchange_fallback_still_present(self):
        block = _pos_details_block()
        # The exchange-direct fallback (reconcile against the venue) must remain.
        assert "fetch_positions" in block
