"""Pytest fixtures for RUNECLAW test suite."""
import glob
import os
import shutil

import pytest

# Runtime state files written under data/ during a test run. Several tests
# construct a LiveExecutor / RuneClawEngine / PortfolioTracker that persists
# positions, closed trades, risk-breaker state, and learning records to these
# paths. Without per-test cleanup they leak across tests — e.g. a leftover
# data/live_positions.json makes a later test see an "already open" position and
# fail (the root cause of the historical test-isolation failures). The data/
# files are all gitignored runtime artifacts (never committed fixtures), so it is
# safe to remove them between tests.
_STATE_FILES = (
    "data/combined_state.json",
    "data/combined_state.json.bak",
    "data/combined_state.json.tmp",
    "data/live_positions.json",
    "data/live_positions.json.bak",
    "data/live_positions.json.tmp",
    "data/closed_trades.json",
    "data/risk_state.json",
    "data/risk_state.json.bak",
    "data/risk_state.json.tmp",
    # Roadmap P0: the persisted user store leaked across tests. A stale
    # last_seen on a seeded admin tripped the 24h sensitive-command staleness
    # check (user_store.has_permission), so /pause, /resume, emergency-stop and
    # /llmreset tests passed only in suite order (an earlier test refreshed
    # last_seen) and failed in isolation. Cleaning it makes the user store
    # fresh and order-independent.
    "data/users.json",
    "data/users.json.bak",
    "data/users.json.tmp",
)
_STATE_GLOBS = (
    "data/portfolio_*.json",
)
_STATE_DIRS = (
    "data/learning",
)


def _clean_runtime_state() -> None:
    for f in _STATE_FILES:
        try:
            os.remove(f)
        except FileNotFoundError:
            pass
    for pattern in _STATE_GLOBS:
        for f in glob.glob(pattern):
            try:
                os.remove(f)
            except FileNotFoundError:
                pass
    for d in _STATE_DIRS:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean_combined_state():
    """Remove runtime state files before and after each test, preventing
    cross-test state contamination (originally C2-34 for combined_state.json;
    extended to live_positions / closed_trades / risk_state / portfolio_* /
    learning to fix the broader test-isolation failures)."""
    _clean_runtime_state()
    yield
    _clean_runtime_state()
