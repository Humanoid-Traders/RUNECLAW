"""Pytest fixtures for RUNECLAW test suite."""
import os
import pytest


@pytest.fixture(autouse=True)
def _clean_combined_state():
    """Remove combined_state.json before and after each test that creates
    a RuneClawEngine, preventing cross-test state contamination from C2-34."""
    combined = "data/combined_state.json"
    combined_bak = "data/combined_state.json.bak"
    combined_tmp = "data/combined_state.json.tmp"
    for f in (combined, combined_bak, combined_tmp):
        try:
            os.remove(f)
        except FileNotFoundError:
            pass
    yield
    for f in (combined, combined_bak, combined_tmp):
        try:
            os.remove(f)
        except FileNotFoundError:
            pass
