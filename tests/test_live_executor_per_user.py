"""
Phase 2: LiveExecutor parameterization for per-user accounts.

The non-negotiable here is that the **shared operator executor path stays
byte-identical** — same state files, same credential source (CONFIG.exchange) —
while a per-user executor (user_id + decrypted credentials) is bound to its own
state files and its own keys. Per-user live trading is still gated OFF
(PER_USER_LIVE_ENABLED); this only proves the executor can be constructed
per-user without disturbing the operator.

No network: we never call _get_exchange (which would hit ccxt/Bitget); we assert
on the resolved file paths, stored credentials, and the _user_state_path helper.
"""

from bot.core.live_executor import (
    LiveExecutor, _user_state_path, _POSITIONS_FILE, _CLOSED_TRADES_FILE,
)


def test_operator_executor_is_byte_identical():
    e = LiveExecutor()
    assert e.user_id is None
    assert e._credentials is None
    # Same on-disk layout as before the refactor.
    assert e._positions_file == _POSITIONS_FILE
    assert e._closed_trades_file == _CLOSED_TRADES_FILE


def test_per_user_executor_has_own_files_and_creds():
    creds = {"api_key": "k", "api_secret": "s", "passphrase": "p"}
    e = LiveExecutor(user_id=12345, credentials=creds)
    assert e.user_id == 12345
    assert e._credentials == creds
    # Per-user files are suffixed with the user id and distinct from operator.
    assert e._positions_file != _POSITIONS_FILE
    assert "12345" in e._positions_file
    assert "12345" in e._closed_trades_file


def test_two_users_never_share_state_files():
    a = LiveExecutor(user_id=111, credentials={"api_key": "a", "api_secret": "a", "passphrase": "a"})
    b = LiveExecutor(user_id=222, credentials={"api_key": "b", "api_secret": "b", "passphrase": "b"})
    assert a._positions_file != b._positions_file
    assert a._closed_trades_file != b._closed_trades_file


def test_user_state_path_helper():
    # Operator default (both None) -> unchanged base path.
    assert _user_state_path("data/live_positions.json", None, None) == "data/live_positions.json"
    # user_id suffixes the stem, preserving extension.
    assert _user_state_path("data/live_positions.json", None, 7) == "data/live_positions_7.json"
    # explicit state_dir relocates the file.
    assert _user_state_path("data/closed_trades.json", "/srv/u", 9) == "/srv/u/closed_trades_9.json"
    # state_dir without user_id keeps the base filename.
    assert _user_state_path("data/closed_trades.json", "/srv/u", None) == "/srv/u/closed_trades.json"


def test_state_dir_only_overrides_dir(tmp_path):
    e = LiveExecutor(state_dir=str(tmp_path))
    assert str(tmp_path) in e._positions_file
    assert e._positions_file.endswith("live_positions.json")
