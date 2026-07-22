"""The proactive-alert watch list must survive restarts, and a fresh deploy
with an empty list must auto-enroll the operator — otherwise every restart
silences CRITICAL safety alerts (position unprotected, circuit breaker) until
someone manually re-runs /watch on.
"""
import contextlib


from bot.config import CONFIG
from bot.core.proactive_monitor import ProactiveMonitor


@contextlib.contextmanager
def _cfg(**overrides):
    """Temporarily override frozen CONFIG fields (nested via dotted keys)."""
    saved = {}
    for key, val in overrides.items():
        if "." in key:
            obj_name, attr = key.split(".", 1)
            obj = getattr(CONFIG, obj_name)
            saved[key] = (obj, attr, getattr(obj, attr))
            object.__setattr__(obj, attr, val)
        else:
            saved[key] = (CONFIG, key, getattr(CONFIG, key))
            object.__setattr__(CONFIG, key, val)
    try:
        yield
    finally:
        for obj, attr, old in saved.values():
            object.__setattr__(obj, attr, old)


def _monitor():
    return ProactiveMonitor(engine=None)


def test_bare_construction_is_empty_and_does_not_load(tmp_path):
    # A bare monitor (as tests construct) must be deterministically empty —
    # persistence loads only via hydrate().
    state = tmp_path / "watch.json"
    state.write_text('{"enabled_chats": ["999"]}')
    with _cfg(proactive_watch_state_file=str(state)):
        m = _monitor()
        assert m._enabled_chats == set()


def test_enable_persists_and_survives_restart(tmp_path):
    state = tmp_path / "watch.json"
    with _cfg(proactive_watch_state_file=str(state),
              **{"telegram.chat_id": ""}):
        m1 = _monitor()
        m1.enable_chat("12345")
        assert state.exists()
        # "Restart": a fresh monitor hydrates the persisted list.
        m2 = _monitor()
        m2.hydrate()
        assert "12345" in m2._enabled_chats


def test_disable_persists_removal(tmp_path):
    state = tmp_path / "watch.json"
    with _cfg(proactive_watch_state_file=str(state),
              **{"telegram.chat_id": ""}):
        m = _monitor()
        m.enable_chat("111")
        m.enable_chat("222")
        m.disable_chat("111")
        m2 = _monitor()
        m2.hydrate()
        assert m2._enabled_chats == {"222"}


def test_auto_enrolls_operator_when_empty(tmp_path):
    state = tmp_path / "watch.json"
    with _cfg(proactive_watch_state_file=str(state),
              proactive_auto_enroll_admin=True,
              **{"telegram.chat_id": "77700"}):
        m = _monitor()
        m.hydrate()
        assert m._enabled_chats == {"77700"}
        # And it persisted, so it survives the next restart too.
        assert '"77700"' in state.read_text()


def test_does_not_auto_enroll_when_list_non_empty(tmp_path):
    state = tmp_path / "watch.json"
    state.write_text('{"enabled_chats": ["555"]}')
    with _cfg(proactive_watch_state_file=str(state),
              proactive_auto_enroll_admin=True,
              **{"telegram.chat_id": "77700"}):
        m = _monitor()
        m.hydrate()
        # Existing watcher preserved; operator NOT force-added over their choice.
        assert m._enabled_chats == {"555"}


def test_does_not_re_enroll_after_operator_emptied(tmp_path):
    # Operator turned OFF the last chat -> an EMPTY but EXISTING state file.
    # A restart must respect that, not re-enroll the operator every time.
    state = tmp_path / "watch.json"
    state.write_text('{"enabled_chats": []}')
    with _cfg(proactive_watch_state_file=str(state),
              proactive_auto_enroll_admin=True,
              **{"telegram.chat_id": "77700"}):
        m = _monitor()
        m.hydrate()
        assert m._enabled_chats == set()


def test_auto_enroll_can_be_disabled(tmp_path):
    state = tmp_path / "watch.json"
    with _cfg(proactive_watch_state_file=str(state),
              proactive_auto_enroll_admin=False,
              **{"telegram.chat_id": "77700"}):
        m = _monitor()
        m.hydrate()
        assert m._enabled_chats == set()


def test_corrupt_state_file_is_fail_open(tmp_path):
    state = tmp_path / "watch.json"
    state.write_text("{not valid json")
    with _cfg(proactive_watch_state_file=str(state),
              **{"telegram.chat_id": ""}):
        m = _monitor()
        m.hydrate()  # must not raise
        assert m._enabled_chats == set()
