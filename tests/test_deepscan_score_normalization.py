"""
DeepScan's raw "score" is an unbounded point count (len(chart_patterns)*2 +
len(candle_patterns)*1 + RSI/volume bonuses -- up to 16 chart-pattern
detectors and 14 candlestick patterns can each independently contribute).

Reported live: the "RUNECLAW DEEP SCAN 4H" score-card showed SCORE 100% for
all six top signals, despite their underlying pattern confidences (from the
companion patterns card) being mixed and in the 63-94% range with conflicting
bullish/bearish signals -- not a plausible uniform 100% confluence. Root
cause: the raw score was divided by a fixed constant (10.0) and clamped to
1.0, but real raw scores routinely exceed 10 once several detectors fire at
once, so every hit saturated at the cap regardless of how it compared to the
rest of the scan.
"""

from bot.skills.skill_registry import normalize_deepscan_scores


class TestNormalizeDeepscanScores:
    def test_scores_above_old_divisor_no_longer_saturate_uniformly(self):
        # Mirrors the reported incident: six hits with raw scores that would
        # all have hit the old /10.0 cap (>= 10), but differ from each other.
        hits = [
            {"symbol": "CL", "score": 15},
            {"symbol": "NATGAS", "score": 14},
            {"symbol": "XPD", "score": 16},
            {"symbol": "BZ", "score": 15},
            {"symbol": "ETH", "score": 12},
            {"symbol": "BNB", "score": 12},
        ]
        hits.sort(key=lambda h: h["score"], reverse=True)
        normalize_deepscan_scores(hits)
        # The best hit in the batch is the reference point -- exactly 1.0.
        assert hits[0]["score"] == 16
        assert hits[0]["score_norm"] == 1.0
        # Not every hit collapses to the same value anymore.
        norms = {h["score_norm"] for h in hits}
        assert len(norms) > 1
        # Relative ordering preserved and proportionally correct.
        eth = next(h for h in hits if h["symbol"] == "ETH")
        assert abs(eth["score_norm"] - 12 / 16) < 1e-9

    def test_single_hit_scores_full_confidence(self):
        hits = [{"symbol": "BTC", "score": 7}]
        normalize_deepscan_scores(hits)
        assert hits[0]["score_norm"] == 1.0

    def test_all_zero_scores_do_not_divide_by_zero(self):
        hits = [{"symbol": "A", "score": 0}, {"symbol": "B", "score": 0}]
        normalize_deepscan_scores(hits)
        assert hits[0]["score_norm"] == 0.0
        assert hits[1]["score_norm"] == 0.0

    def test_empty_list_is_a_noop(self):
        hits = []
        normalize_deepscan_scores(hits)  # must not raise
        assert hits == []

    def test_low_raw_scores_still_spread_correctly(self):
        # Confirms the fix doesn't just shift the saturation point -- small
        # batches with modest scores (e.g. a quiet market) still spread out
        # relative to their own best hit, not an arbitrary fixed scale.
        hits = [{"symbol": "A", "score": 3}, {"symbol": "B", "score": 1}]
        normalize_deepscan_scores(hits)
        assert hits[0]["score_norm"] == 1.0
        assert abs(hits[1]["score_norm"] - 1 / 3) < 1e-9
