#!/usr/bin/env python3
"""
RUNECLAW v2 self-test — offline, no API keys required.

Tests the three new capability modules:
  1. Macro-event intelligence (MacroEventProvider)
  2. Compliance-first authorization (ComplianceEngine)
  3. Tamper-evident audit chain (AuditChain)
  4. Macro risk gate integration
  5. v2 skills instantiation

32 assertions total.
"""

import os
import sys
import json
import tempfile

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timedelta, timezone

UTC = timezone.utc
_passed = 0
_failed = 0


def check(condition: bool, label: str) -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✓ {label}")
    else:
        _failed += 1
        print(f"  ✗ FAIL: {label}")


# ═══════════════════════════════════════════════════════════════
# 1. MACRO-EVENT INTELLIGENCE
# ═══════════════════════════════════════════════════════════════
print("\n1. MACRO-EVENT GATE")

from bot.core.macro_events import MacroEventProvider, MacroContext, MacroSeverity

# 1a. Blind provider (no calendar) → BLOCK
blind = MacroEventProvider()
ctx = blind.get_context()
check(ctx.risk_state == "BLOCK_NEW_ENTRIES", "blind → BLOCK_NEW_ENTRIES")
check(ctx.is_blind is True, "blind flag set")
check(ctx.size_multiplier == 0.0, "blind → size=0")

# 1b. Live feed with event in BLACKOUT window → BLOCK
fomc_time = datetime.now(UTC) + timedelta(minutes=30)  # 30 min from now
feed_events = [{
    "event_type": "FOMC_DECISION",
    "scheduled_utc": fomc_time.isoformat(),
    "label": "FOMC Rate Decision — Test",
}]
provider = MacroEventProvider(live_feed=lambda: feed_events)
ctx = provider.get_context(now=fomc_time - timedelta(minutes=10))  # 10 min before
check(ctx.risk_state == "BLOCK_NEW_ENTRIES", "in-blackout → BLOCK")
check(ctx.window == "BLACKOUT", "window=BLACKOUT")
check(ctx.size_multiplier == 0.0, "blackout → size=0")

# 1c. Live feed with event in REDUCE window → REDUCE
ctx_reduce = provider.get_context(now=fomc_time - timedelta(minutes=120))  # 2h before
check(ctx_reduce.risk_state == "REDUCE", "pre-event → REDUCE")
check(ctx_reduce.size_multiplier == 0.5, "reduce → size=0.5")

# 1d. Live feed with event far away → CLEAR
ctx_clear = provider.get_context(now=fomc_time - timedelta(hours=12))  # 12h before
check(ctx_clear.risk_state == "CLEAR", "far away → CLEAR")
check(ctx_clear.size_multiplier == 1.0, "clear → size=1.0")

# 1e. Stale seed → BLOCK
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
    json.dump({
        "generated_utc": (datetime.now(UTC) - timedelta(hours=200)).isoformat(),
        "events": feed_events,
    }, f)
    stale_path = f.name

stale_prov = MacroEventProvider(seed_path=stale_path, max_stale_hours=72)
ctx_stale = stale_prov.get_context()
check(ctx_stale.risk_state == "BLOCK_NEW_ENTRIES", "stale → BLOCK")
check(ctx_stale.is_stale is True, "stale flag set")
os.unlink(stale_path)

# 1f. Funding rate synthetic event → BLOCK
from datetime import datetime as _dt2
_far_future = [(datetime.now(UTC) + timedelta(days=30)).isoformat()]
funding_prov = MacroEventProvider(
    live_feed=lambda: [{
        "event_type": "CPI",
        "scheduled_utc": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        "label": "Future CPI",
    }],
    funding_provider=lambda sym: 0.002,  # 0.2% per 8h — extreme
)
ctx_fund = funding_prov.get_context(symbol="BTC/USDT")
check(ctx_fund.risk_state == "BLOCK_NEW_ENTRIES", "extreme funding → BLOCK")
check(ctx_fund.severity == MacroSeverity.CRITICAL, "funding severity=CRITICAL")

print(f"  ... {_passed} passed so far")

# ═══════════════════════════════════════════════════════════════
# 2. MACRO RISK GATE (#16 integration shape)
# ═══════════════════════════════════════════════════════════════
print("\n2. MACRO RISK GATE (provider-based)")

# 2a. No provider → context not checked (original calendar fallback)
check(blind.get_context().risk_state == "BLOCK_NEW_ENTRIES",
      "no provider wired → fail-closed BLOCK from blind provider")

# 2b. In-blackout → should produce BLOCK (already tested above, verify via provider)
ctx_bb = provider.get_context(now=fomc_time - timedelta(minutes=10))
check(ctx_bb.size_multiplier == 0.0, "blackout → multiplier=0")

# 2c. Pre-event → size × 0.5
ctx_pe = provider.get_context(now=fomc_time - timedelta(minutes=120))
check(ctx_pe.size_multiplier == 0.5, "pre-event → multiplier=0.5")

# ═══════════════════════════════════════════════════════════════
# 3. COMPLIANCE ENGINE (five-lock)
# ═══════════════════════════════════════════════════════════════
print("\n3. COMPLIANCE (five-lock)")

from bot.compliance.compliance_engine import (
    ComplianceEngine, Permission, SubjectProfile,
    ApprovalToken, AuthorizationDecision, default_demo_profile,
)

ce = ComplianceEngine()
demo = default_demo_profile()

# 3a. Paper trade with demo profile → GRANTED
dec = ce.authorize(
    action=Permission.PAPER_TRADE, profile=demo,
    live_mode=False, risk_passed=True, macro_ok=True,
    notional_usd=100.0,
)
check(dec.granted is True, "paper trade → GRANTED")

# 3b. Demo profile attempting live → DENIED (no LIVE_TRADE permission)
dec_live = ce.authorize(
    action=Permission.LIVE_TRADE, profile=demo,
    live_mode=True, risk_passed=True, macro_ok=True,
    notional_usd=100.0,
)
check(dec_live.granted is False, "demo live → DENIED")
check(any("LIVE_TRADE" in r for r in dec_live.reasons), "denial names missing lock")

# 3c. Full live profile with all five locks → needs token
live_profile = SubjectProfile(
    subject_id="live-trader",
    permissions={Permission.READ_ONLY, Permission.ANALYSIS, Permission.PAPER_TRADE, Permission.LIVE_TRADE},
    jurisdiction="DE",
    max_notional_usd=50_000.0,
    kyc_verified=True,
)

# Without token → DENIED
dec_no_token = ce.authorize(
    action=Permission.LIVE_TRADE, profile=live_profile,
    live_mode=True, risk_passed=True, macro_ok=True,
    notional_usd=100.0, trade_id="TI-001",
)
check(dec_no_token.granted is False, "live without token → DENIED")

# With token → GRANTED
token = ce.issue_approval_token("TI-001", "live-trader")
dec_with_token = ce.authorize(
    action=Permission.LIVE_TRADE, profile=live_profile,
    live_mode=True, risk_passed=True, macro_ok=True,
    notional_usd=100.0, trade_id="TI-001",
    approval_token=token,
)
check(dec_with_token.granted is True, "live with all five locks → GRANTED")

# Reuse same token → DENIED (single-use)
dec_reuse = ce.authorize(
    action=Permission.LIVE_TRADE, profile=live_profile,
    live_mode=True, risk_passed=True, macro_ok=True,
    notional_usd=100.0, trade_id="TI-001",
    approval_token=token,
)
check(dec_reuse.granted is False, "reused token → DENIED")

# 3d. Restricted jurisdiction → DENIED
restricted = SubjectProfile(
    subject_id="bad-jurisdiction",
    permissions={Permission.LIVE_TRADE, Permission.PAPER_TRADE},
    jurisdiction="KP",
)
dec_jurisdiction = ce.authorize(
    action=Permission.PAPER_TRADE, profile=restricted,
    live_mode=False, risk_passed=True, macro_ok=True,
    notional_usd=100.0,
)
check(dec_jurisdiction.granted is False, "restricted jurisdiction → DENIED")

# 3e. Consent ledger populated
check(len(ce.get_consent_ledger()) >= 5, "consent ledger has entries")

print(f"  ... {_passed} passed so far")

# ═══════════════════════════════════════════════════════════════
# 4. TAMPER-EVIDENT AUDIT CHAIN
# ═══════════════════════════════════════════════════════════════
print("\n4. AUDIT CHAIN")

from bot.utils.audit_chain import AuditChain, DecisionRecord

with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
    chain_path = f.name

chain = AuditChain(chain_path)

# 4a. Append entries
chain.append("TRADE_SIGNAL", {"symbol": "BTC/USDT", "direction": "LONG"})
chain.append("RISK_CHECK", {"verdict": "APPROVED", "checks": 18})
chain.seal_decision(DecisionRecord(
    decision_id="DEC-001", symbol="BTC/USDT",
    outcome="EXECUTED_PAPER",
))
check(chain.get_chain_length() == 3, "chain has 3 entries")

# 4b. Verify intact chain
ok, problems = AuditChain.verify(chain_path)
check(ok is True, "intact chain verifies")
check(len(problems) == 0, "no problems in intact chain")

# 4c. Tamper with a line and verify detection
with open(chain_path, "r") as f:
    lines = f.readlines()

# Modify the second line
tampered = json.loads(lines[1])
tampered["payload"]["verdict"] = "REJECTED"  # change data
lines[1] = json.dumps(tampered) + "\n"

with open(chain_path, "w") as f:
    f.writelines(lines)

ok2, problems2 = AuditChain.verify(chain_path)
check(ok2 is False, "tampered chain detected")
check(len(problems2) > 0, "tampering problems reported")

os.unlink(chain_path)

# ═══════════════════════════════════════════════════════════════
# 5. v2 SKILLS
# ═══════════════════════════════════════════════════════════════
print("\n5. SKILLS")

from bot.skills.macro_skills import build_v2_skills

skills = build_v2_skills()
check(len(skills) == 5, "5 v2 skills built")
skill_names = {s.name for s in skills}
check("macro_brief" in skill_names, "macro_brief skill present")
check("kill_switch" in skill_names, "kill_switch skill present")
check("compliance_status" in skill_names, "compliance_status skill present")

# ═══════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════
total = _passed + _failed
print(f"\n{'='*50}")
print(f"  {_passed} passed, {_failed} failed  (total: {total})")
print(f"{'='*50}")

sys.exit(0 if _failed == 0 else 1)
