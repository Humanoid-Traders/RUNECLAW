"""Intent Compiler web authoring — the gateway compile/bind glue (PR-9).

The web operator authors a policy through the bot gateway: a preview step
(compile → clamp → human-readable, no write) and an apply step (recompile from
the TEXT authoritatively, then bind). These tests pin that glue against a minimal
fake engine — the compile/clamp math itself is covered by the intent_policy
tests; here we lock the web contract: preview never binds, apply recompiles from
text (never a client blob) and writes, and empty/garbage never raises.
"""
import bot.web.user_gateway as gw


class _FakeEngine:
    """Just the two hooks the authoring helpers touch."""
    def __init__(self):
        self.written = []

    def _intent_engine_caps(self):
        # Realistic authoritative caps the policy clamps against.
        return {"max_position_pct": 20.0, "min_confidence": 0.6, "max_daily_loss_pct": 10.0}

    def write_intent_policy(self, policy):
        self.written.append(policy)
        return policy            # pretend it bound

    def _intent_policy_summary(self):
        return self.written[-1] if self.written else None


def test_preview_compiles_but_never_binds():
    eng = _FakeEngine()
    out = gw._compile_policy_preview(eng, "only majors, max 5% per trade, min confidence 70%")
    assert out["ok"] is True
    assert out["rules"]                      # produced typed rules
    assert out["human_readable"]             # renderable preview
    assert eng.written == []                 # PREVIEW WROTE NOTHING


def test_preview_unrecognised_text_returns_a_hint():
    eng = _FakeEngine()
    out = gw._compile_policy_preview(eng, "please be nice to me")
    assert out["ok"] is True and out["rules"] == []
    assert out["note"]                        # guidance, not a crash
    assert eng.written == []


def test_apply_recompiles_from_text_and_binds():
    eng = _FakeEngine()
    out = gw._compile_and_bind_policy(eng, "max 5% per trade, no shorts", "enforce")
    assert out["ok"] is True and out["mode"] == "enforce" and out["bound"] is True
    assert len(eng.written) == 1
    bound = eng.written[0]
    assert bound["mode"] == "enforce"
    assert bound.get("rules")                # the bound artifact carries the compiled rules
    # The bound value came from recompiling the TEXT, not from any client-sent blob.
    assert bound.get("source_text") == "max 5% per trade, no shorts"


def test_apply_with_no_rules_does_not_bind():
    eng = _FakeEngine()
    out = gw._compile_and_bind_policy(eng, "hello there", "shadow")
    assert out["ok"] is False and out["error"] == "no_rules"
    assert eng.written == []                  # nothing bound when nothing compiled


def test_clamp_is_tighten_only_through_the_web_path():
    # Ask for a LOOSER cap than the engine's (30% > 20%): the compiler clamps it
    # down, so the web can never author a looser-than-cap rule.
    eng = _FakeEngine()
    out = gw._compile_and_bind_policy(eng, "max 30% per trade", "shadow")
    assert out["ok"] is True
    rule = next(r for r in eng.written[0]["rules"] if r["type"] == "max_position_pct")
    assert float(rule["value"]) <= 20.0       # clamped to the engine cap, never looser
