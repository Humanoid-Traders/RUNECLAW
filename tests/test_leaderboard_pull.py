"""leaderboard_pull — the desired-state opt-in fetch (community C2).

The tri-state contract is the point: None = channel unconfigured/FAILED (leave
the board untouched — a transport blip must never read as a mass opt-out);
[] = the website POSITIVELY reported nobody is opted in (reconcile-remove is
correct); rows = the current opt-in set.
"""
import bot.utils.leaderboard_pull as lp


def test_unconfigured_returns_none(monkeypatch):
    monkeypatch.setattr(lp, "SYNC_SECRET", "")
    assert lp.fetch_leaderboard_optins() is None


def test_transport_failure_returns_none(monkeypatch):
    monkeypatch.setattr(lp, "SYNC_SECRET", "s" * 48)
    monkeypatch.setattr(lp, "_request", lambda path, body=None: None)
    assert lp.fetch_leaderboard_optins() is None


def test_malformed_response_returns_none(monkeypatch):
    monkeypatch.setattr(lp, "SYNC_SECRET", "s" * 48)
    monkeypatch.setattr(lp, "_request", lambda path, body=None: {"error": "x"})
    assert lp.fetch_leaderboard_optins() is None


def test_positive_empty_returns_empty_list(monkeypatch):
    # {"optins": []} is a REAL everyone-opted-out state, distinct from failure.
    monkeypatch.setattr(lp, "SYNC_SECRET", "s" * 48)
    monkeypatch.setattr(lp, "_request", lambda path, body=None: {"optins": []})
    assert lp.fetch_leaderboard_optins() == []


def test_rows_parsed_and_non_dicts_dropped(monkeypatch):
    monkeypatch.setattr(lp, "SYNC_SECRET", "s" * 48)
    calls = []

    def _fake(path, body=None):
        calls.append(path)
        return {"optins": [
            {"user_id": 5, "telegram_id": "111", "handle": "runefox"},
            "junk", None,
            {"user_id": 6, "telegram_id": "222", "handle": "wolf_7"},
        ]}

    monkeypatch.setattr(lp, "_request", _fake)
    rows = lp.fetch_leaderboard_optins()
    assert rows == [
        {"user_id": 5, "telegram_id": "111", "handle": "runefox"},
        {"user_id": 6, "telegram_id": "222", "handle": "wolf_7"},
    ]
    assert calls == ["/api/bot/sync/leaderboard/pending"]
