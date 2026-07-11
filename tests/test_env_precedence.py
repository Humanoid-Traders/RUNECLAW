"""
.env precedence flip (live incident 2026-07-11).

The operator edited the LLM_TIER_* model ids in .env, but stale exports
inherited from the shell/supervisor silently WON under the old
load_dotenv(override=False) — every tier kept calling a dead model id and the
bot ran on the rule engine while looking configured. .env is now the source of
truth (override=True); RUNECLAW_ENV_INHERIT=1 restores the old behaviour.

The subprocess tests run a fresh interpreter in a tmp CWD containing a .env
(the repo checkout has no root .env, so bare load_dotenv finds the tmp one) —
end-to-end proof of both modes without touching this process's environment.
"""
import os
import subprocess
import sys

import bot.config as config

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNIPPET = "import bot.config, os; print(os.environ.get('RC_TEST_PRECEDENCE'))"


def _run(tmp_path, inherit_mode: bool) -> str:
    (tmp_path / ".env").write_text("RC_TEST_PRECEDENCE=from_dotenv\n")
    env = dict(os.environ)
    env["RC_TEST_PRECEDENCE"] = "from_process"
    env["PYTHONPATH"] = REPO
    if inherit_mode:
        env["RUNECLAW_ENV_INHERIT"] = "1"
    else:
        env.pop("RUNECLAW_ENV_INHERIT", None)
    out = subprocess.run(
        [sys.executable, "-c", SNIPPET], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout.strip().splitlines()[-1]


# ── end-to-end: which source wins? ───────────────────────────────────
def test_dotenv_beats_inherited_env_by_default(tmp_path):
    """The exact live incident: a stale inherited export must LOSE to .env."""
    assert _run(tmp_path, inherit_mode=False) == "from_dotenv"


def test_inherit_mode_keeps_old_precedence(tmp_path):
    assert _run(tmp_path, inherit_mode=True) == "from_process"


# ── pure helper ──────────────────────────────────────────────────────
def test_replaced_keys_detected():
    pre = {"A": "old", "B": "same", "C": "x"}
    post = {"A": "new", "B": "same", "C": "x", "D": "added"}
    assert config._detect_replaced_inherited_keys(pre, post) == ["A"]


def test_no_replacements_is_empty():
    env = {"A": "1", "B": "2"}
    assert config._detect_replaced_inherited_keys(env, dict(env)) == []


def test_removed_key_is_not_flagged():
    # A key that vanished (never happens via load_dotenv) must not crash.
    assert config._detect_replaced_inherited_keys({"A": "1"}, {}) == []


# ── safety-switch warning still fires when inherited value governs ───
def test_inherited_safety_switch_detection_unchanged():
    """The RC-AUD-019 pure helper keeps working (pinned by existing tests);
    the warning now skips switches that .env replaced."""
    assert config._detect_inherited_safety_switches(
        {"SIMULATION_MODE", "PATH"}) == ["SIMULATION_MODE"]
