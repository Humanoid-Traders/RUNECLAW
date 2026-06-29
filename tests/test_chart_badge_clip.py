"""
Chart black-space fix (reported from a live ANIME chart).

A pattern badge (e.g. "Fibonacci Extensions") was placed at the midpoint of its
key levels with no clip — for patterns whose levels project well outside the
visible price range, the unclipped label landed far below the panel and
bbox_inches="tight" stretched the saved PNG down to it, leaving a big black gap.
_badge_within_panel gates the badge to the visible range; the draw also sets
clip_on=True as a safety net.
"""

from bot.skills.chart_renderer import _badge_within_panel


class TestBadgeWithinPanel:
    def test_in_range_is_kept(self):
        assert _badge_within_panel(0.5, 0.0, 1.0) is True

    def test_below_range_skipped(self):
        # The live case: a Fibonacci-extension midpoint far below the panel.
        assert _badge_within_panel(-0.3, 0.0, 1.0) is False

    def test_above_range_skipped(self):
        assert _badge_within_panel(1.4, 0.0, 1.0) is False

    def test_within_top_margin_skipped(self):
        # 0.98 is inside the raw range but within the 4% top margin → skipped.
        assert _badge_within_panel(0.99, 0.0, 1.0) is False

    def test_degenerate_range_is_false(self):
        assert _badge_within_panel(0.5, 1.0, 1.0) is False
        assert _badge_within_panel(0.5, 2.0, 1.0) is False

    def test_realistic_price_scale(self):
        # ANIME-scale prices: a level at 0.00287 inside [0.00260, 0.00305].
        assert _badge_within_panel(0.00287, 0.00260, 0.00305) is True
        # A downward fib extension at 0.0021 → skipped.
        assert _badge_within_panel(0.0021, 0.00260, 0.00305) is False


class TestBadgeDrawIsClipped:
    def test_draw_sets_clip_on(self):
        import inspect
        from bot.skills import chart_renderer
        src = inspect.getsource(chart_renderer._pattern_zones_overlay)
        assert "_badge_within_panel(mid_y" in src
        assert "clip_on=True" in src
