"""
Tiered-pipeline savings derive from configured cost, docs are provider-agnostic
(deep-audit low #44).

The TieredPipeline docstrings described the tiers as gpt-4o-mini → gpt-4o, and the
OptimizationStats savings used hardcoded gpt-4o-era dollar constants, even though
routing uses the configured provider (qwen / sonnet / etc.). The tier-1/2 saved
cost now derives from CONFIG.llm.est_cost_per_analysis, and the docstrings name
tiers by role.
"""


from bot.config import CONFIG
from bot.core.token_optimizer import OptimizationStats, TieredPipeline


class TestSavingsTrackConfig:
    def test_tier1_saving_equals_configured_full_cost(self):
        s = OptimizationStats()
        s.record_tier(1)
        assert s.estimated_cost_saved_usd == CONFIG.llm.est_cost_per_analysis

    def test_tier2_saving_is_fraction_of_full_cost(self):
        s = OptimizationStats()
        s.record_tier(2)
        expected = CONFIG.llm.est_cost_per_analysis * (16.0 / 17.0)
        assert abs(s.estimated_cost_saved_usd - expected) < 1e-9

    def test_tier3_records_no_saving(self):
        s = OptimizationStats()
        s.record_tier(3)
        assert s.estimated_cost_saved_usd == 0.0
        assert s.tier3_full_calls == 1


class TestDocsAreProviderAgnostic:
    def test_class_docstring_not_hardcoded_to_openai_only(self):
        doc = TieredPipeline.__doc__
        # Tiers are now named by role; the model-agnostic note is present.
        assert "cheap-tier" in doc and "full-tier" in doc
        assert "model-agnostic" in doc
