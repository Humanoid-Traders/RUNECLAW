"""QC-2b: order-book wall entry gate (pure decision function).

book_wall_verdict flags entries where a dominant opposing wall sits in the
entry→TP path. It must be fail-open (degraded book → no flag) and never raise.
"""

from bot.core.entry_quality import book_wall_verdict


def _flat_book(center=100.0, step=0.1, size=10.0, n=20):
    """A balanced ladder: uniform size on both sides around `center`."""
    bids = [(round(center - step * (i + 1), 6), size) for i in range(n)]
    asks = [(round(center + step * (i + 1), 6), size) for i in range(n)]
    return bids, asks


class TestFailOpen:
    def test_empty_book_never_flags(self):
        v = book_wall_verdict("LONG", 100.0, 105.0, [], [])
        assert v["flag"] is False and v["reason"] == "book-unavailable"

    def test_one_sided_book_never_flags(self):
        bids, _ = _flat_book()
        v = book_wall_verdict("LONG", 100.0, 105.0, bids, [])
        assert v["flag"] is False

    def test_malformed_levels_are_ignored_not_raised(self):
        bids = [("junk", "x"), (99.9, 10), (None, 5)]
        asks = [(100.1, 10), [100.2], (100.3, "n/a")]
        v = book_wall_verdict("LONG", 100.0, 105.0, bids, asks)
        assert isinstance(v, dict) and "flag" in v

    def test_bad_direction_or_entry(self):
        bids, asks = _flat_book()
        assert book_wall_verdict("SIDEWAYS", 100, 105, bids, asks)["flag"] is False
        assert book_wall_verdict("LONG", 0, 105, bids, asks)["flag"] is False


class TestBalancedBookPasses:
    def test_flat_book_long_no_flag(self):
        bids, asks = _flat_book()
        v = book_wall_verdict("LONG", 100.0, 101.0, bids, asks)
        assert v["flag"] is False, v

    def test_flat_book_short_no_flag(self):
        bids, asks = _flat_book()
        v = book_wall_verdict("SHORT", 100.0, 99.0, bids, asks)
        assert v["flag"] is False, v


class TestWallDetection:
    def test_long_blocked_by_ask_wall_in_path(self):
        bids, asks = _flat_book()
        # A huge ask wall just above entry, inside the entry→TP band.
        asks = [(100.1, 10), (100.2, 10), (100.3, 400.0)] + asks[3:]
        v = book_wall_verdict("LONG", 100.0, 101.0, bids, asks)
        assert v["flag"] is True
        assert "wall" in v["reason"]

    def test_short_blocked_by_bid_wall_in_path(self):
        bids, asks = _flat_book()
        bids = [(99.9, 10), (99.8, 10), (99.7, 400.0)] + bids[3:]
        v = book_wall_verdict("SHORT", 100.0, 99.0, bids, asks)
        assert v["flag"] is True
        assert "wall" in v["reason"]

    def test_wall_outside_the_band_is_ignored(self):
        bids, asks = _flat_book()
        # Wall sits far above entry (5%), well outside the 1.5% path band.
        asks = asks + [(105.0, 400.0)]
        v = book_wall_verdict("LONG", 100.0, 101.0, bids, asks, band_pct=1.5)
        assert v["flag"] is False, v

    def test_wall_beyond_tp_is_ignored(self):
        bids, asks = _flat_book()
        # Wall at 100.9 but TP is 100.4 — the wall is past our target, so it
        # can't block the move we care about.
        asks = [(100.1, 10), (100.2, 10), (100.9, 400.0)] + asks[3:]
        v = book_wall_verdict("LONG", 100.0, 100.4, bids, asks)
        assert v["flag"] is False, v


class TestImbalanceDetection:
    def test_adverse_shelf_flags(self):
        # Heavy resting asks vs thin bids in the band → opposing shelf.
        bids = [(99.9, 2.0), (99.8, 2.0), (99.7, 2.0)]
        asks = [(100.1, 60.0), (100.2, 60.0), (100.3, 60.0)]
        v = book_wall_verdict("LONG", 100.0, 101.0, bids, asks,
                              wall_ratio=99.0, imbalance_ratio=3.0)
        assert v["flag"] is True
        assert "shelf" in v["reason"]

    def test_supportive_book_does_not_flag(self):
        # Thin asks, heavy bids — the path is clear for a LONG.
        bids = [(99.9, 60.0), (99.8, 60.0), (99.7, 60.0)]
        asks = [(100.1, 2.0), (100.2, 2.0), (100.3, 2.0)]
        v = book_wall_verdict("LONG", 100.0, 101.0, bids, asks)
        assert v["flag"] is False, v
