"""Contract Studio core — the §4-compliant Solidity security-flag scanner.

The product drafts Solidity with the tier-routed LLM, but AI misses economic
exploits — so the scanner raises FLAGS (look here), never a verdict (it's safe).
These lock: the heuristics fire on the classic footguns, an empty list is
reported as "no flags" and NEVER as "safe", and the audit disclaimer is always
present. Pure text analysis — no network, no LLM, no money-path.
"""

from bot.core import contract_studio as cs


_UNSAFE = """
pragma solidity ^0.8.0;
contract Vault {
    address owner;
    function withdraw() public {
        require(tx.origin == owner);            // tx.origin auth
        (bool ok, ) = msg.sender.call{value: address(this).balance}("");  // unchecked
        selfdestruct(payable(owner));           // selfdestruct
    }
    function rand() public view returns (uint) {
        return uint(keccak256(abi.encodePacked(block.timestamp)));  // weak randomness
    }
}
"""

_CLEAN = """// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;
contract Counter {
    uint256 public count;
    function increment() external { count += 1; }
}
"""


def _ids(flags):
    return {f.id for f in flags}


def test_flags_the_classic_footguns():
    ids = _ids(cs.scan_security_flags(_UNSAFE))
    for expected in ("tx-origin-auth", "selfdestruct", "weak-randomness",
                     "unchecked-lowlevel-call", "floating-pragma"):
        assert expected in ids, expected


def test_flags_carry_line_reason_and_hint():
    flags = cs.scan_security_flags(_UNSAFE)
    tx = next(f for f in flags if f.id == "tx-origin-auth")
    assert tx.severity == "high"
    assert tx.line >= 1                       # anchored to the matched line
    assert "msg.sender" in tx.hint            # actionable fix
    assert tx.detail                          # says why it matters


def test_flags_sorted_high_severity_first():
    flags = cs.scan_security_flags(_UNSAFE)
    sev_rank = {"high": 0, "medium": 1, "low": 2, "info": 3}
    ranks = [sev_rank[f.severity] for f in flags]
    assert ranks == sorted(ranks)


def test_missing_spdx_and_pragma_are_file_level_flags():
    ids = _ids(cs.scan_security_flags("contract X {}"))
    assert "missing-spdx" in ids and "no-pragma" in ids


def test_clean_contract_reports_no_flags_but_never_calls_it_safe():
    flags = cs.scan_security_flags(_CLEAN)
    # a well-formed counter trips none of the footgun rules…
    assert _ids(flags) & {"tx-origin-auth", "selfdestruct", "delegatecall",
                          "weak-randomness", "missing-spdx", "no-pragma"} == set()
    summary = cs.flags_summary(flags)
    assert summary["clean"] is True
    # …but the summary still carries the disclaimer — "clean" is never "safe".
    assert "audit" in summary["disclaimer"].lower()
    assert "not" in cs.AUDIT_DISCLAIMER.lower() or "before" in cs.AUDIT_DISCLAIMER.lower()


def test_scanner_never_raises_on_bad_input():
    for bad in ("", "   ", None, 12345):
        assert cs.scan_security_flags(bad) == []


def test_summary_counts_by_severity():
    s = cs.flags_summary(cs.scan_security_flags(_UNSAFE))
    assert s["count"] >= 4
    assert s["by_severity"]["high"] >= 3
    assert s["clean"] is False


def test_generation_prompt_bakes_in_the_compliance_posture():
    p = cs.build_generation_prompt("an ERC-20 with a cap", license="MIT", pragma="0.8.24")
    assert "SPDX-License-Identifier: MIT" in p
    assert "pragma solidity 0.8.24" in p
    assert "tx.origin" in p                    # tells the model to avoid it
    assert "DRAFT" in p
    assert cs.AUDIT_DISCLAIMER in p            # disclaimer travels with the draft
    assert "an ERC-20 with a cap" in p
