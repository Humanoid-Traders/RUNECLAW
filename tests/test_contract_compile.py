"""
Contract Studio compile-check — fail-soft solc wrapper + pure summary.

The compiler (py-solc-x + a solc binary) is an optional operator-installed
extra, exactly like the eth-account signer. These tests pin the behaviour that
must hold with or WITHOUT it installed: compile_source never raises, reports
available=False when the toolchain is absent, and summarize_compile shapes the
result for the UI while always carrying the audit disclaimer (compiling ≠ safe).
"""

import sys

from bot.core import contract_studio as cs


def _solcx_installed() -> bool:
    try:
        import solcx  # noqa: F401
        return True
    except Exception:
        return False


class TestCompileSource:
    def test_empty_source_is_rejected_without_touching_the_compiler(self):
        r = cs.compile_source("   ")
        assert r["ok"] is False
        assert r["error"] == "empty_source"
        assert r["contracts"] == []

    def test_never_raises_on_junk(self):
        # Garbage in → a structured failure, never an exception.
        r = cs.compile_source("this is not solidity {{{")
        assert isinstance(r, dict)
        assert r["ok"] is False
        assert "error" in r and "diagnostics" in r

    def test_reports_unavailable_when_solcx_absent(self, monkeypatch):
        # Simulate the compiler not being installed: block the import.
        monkeypatch.setitem(sys.modules, "solcx", None)
        r = cs.compile_source("pragma solidity 0.8.24; contract C {}")
        assert r["available"] is False
        assert r["error"] == "compiler_unavailable"
        assert r["ok"] is False

    def test_pragma_version_is_detected(self):
        assert cs._detect_pragma_version("pragma solidity 0.8.24;") == "0.8.24"
        # Floating / caret pragmas are not pinned — fall back to installed max.
        assert cs._detect_pragma_version("pragma solidity ^0.8.0;") is None
        assert cs._detect_pragma_version("no pragma here") is None


class TestSummarizeCompile:
    def test_summary_counts_diagnostics_and_carries_disclaimer(self):
        result = {
            "ok": False, "available": True, "error": "compile_failed",
            "contracts": [{"name": "C", "bytecode": "", "abi": []}],
            "diagnostics": [
                {"severity": "error", "message": "boom"},
                {"severity": "warning", "message": "meh"},
                {"severity": "warning", "message": "meh2"},
            ],
        }
        s = cs.summarize_compile(result)
        assert s["ok"] is False
        assert s["error_count"] == 1
        assert s["warning_count"] == 2
        assert s["contract_names"] == ["C"]
        # Compiling is never a safety claim — the disclaimer always travels.
        assert s["disclaimer"] == cs.AUDIT_DISCLAIMER

    def test_summary_is_fail_soft_on_empty_input(self):
        s = cs.summarize_compile({})
        assert s["ok"] is False and s["available"] is False
        assert s["error_count"] == 0 and s["warning_count"] == 0
        assert s["contract_names"] == []
        assert s["disclaimer"] == cs.AUDIT_DISCLAIMER
