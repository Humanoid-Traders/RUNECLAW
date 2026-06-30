"""
Directory fsync after atomic rename (deep-audit medium).

os.replace(tmp, target) is atomic, but the rename can be lost on a crash unless
the PARENT DIRECTORY is fsync'd — the save paths already fsync the tmp file's
contents, not the directory entry. fsync_dir closes that gap (best-effort), and
the three state-save paths (portfolio / combined engine state / risk engine) now
call it after os.replace.
"""

import inspect
import os

import bot.utils.durable_io as durable_io
from bot.utils.durable_io import fsync_dir


class TestFsyncDir:
    def test_real_file_fsyncs_parent(self, tmp_path):
        target = tmp_path / "state.json"
        target.write_text("{}")
        calls = {"fsync": 0}
        real_fsync = os.fsync

        def _spy(fd):
            calls["fsync"] += 1
            return real_fsync(fd)

        # Patch on the module so we observe the directory fsync.
        orig = durable_io.os.fsync
        durable_io.os.fsync = _spy
        try:
            fsync_dir(str(target))
        finally:
            durable_io.os.fsync = orig
        assert calls["fsync"] == 1

    def test_missing_dir_is_swallowed(self):
        # Parent directory does not exist → best-effort no-op, no raise.
        fsync_dir("/nonexistent-xyz-123/deeper/state.json")

    def test_bare_filename_uses_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "x.json").write_text("{}")
        fsync_dir("x.json")  # dirname == "" → "." → cwd; must not raise

    def test_open_failure_is_swallowed(self, monkeypatch):
        def _boom(*a, **k):
            raise OSError("no dir fsync on this platform")
        monkeypatch.setattr(durable_io.os, "open", _boom)
        fsync_dir("/tmp/whatever.json")  # swallowed → returns cleanly


class TestWiring:
    def _src(self, mod, qualname):
        obj = mod
        for part in qualname.split("."):
            obj = getattr(obj, part)
        return inspect.getsource(obj)

    def test_portfolio_save_calls_fsync_dir(self):
        import bot.risk.portfolio as p
        src = self._src(p, "PortfolioTracker._save_state_locked")
        assert "fsync_dir(" in src

    def test_risk_engine_save_calls_fsync_dir(self):
        import bot.risk.risk_engine as r
        src = self._src(r, "RiskEngine._save_state_individual")
        assert "fsync_dir(" in src

    def test_engine_combined_save_calls_fsync_dir(self):
        import bot.core.engine as e
        # The combined-state saver lives on the engine; assert the call exists
        # somewhere in the module's save path.
        src = inspect.getsource(e)
        assert "fsync_dir(str(combined_path))" in src
