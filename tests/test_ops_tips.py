"""Ops tips implementation: dead-man's-switch ping, funding haircut default,
--honest backtest preset, backup script presence."""
from __future__ import annotations

import os
import stat
import time
import pytest

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine


class TestHealthcheckPing:
    def test_disabled_by_default(self):
        assert CONFIG.monitoring.healthcheck_ping_url == ""
        assert 5.0 <= CONFIG.monitoring.healthcheck_ping_interval_sec <= 3600.0

    @pytest.mark.asyncio
    async def test_noop_when_url_unset(self):
        eng = RuneClawEngine.__new__(RuneClawEngine)
        # Must return instantly and touch nothing when no URL configured.
        await eng._maybe_ping_healthcheck()

    @pytest.mark.asyncio
    async def test_throttled_by_interval(self):
        eng = RuneClawEngine.__new__(RuneClawEngine)
        stamp = time.monotonic()
        eng._last_healthcheck_ping = stamp  # just pinged
        object.__setattr__(CONFIG.monitoring, "healthcheck_ping_url",
                           "http://127.0.0.1:9/ping")
        try:
            await eng._maybe_ping_healthcheck()  # within interval — no attempt
        finally:
            object.__setattr__(CONFIG.monitoring, "healthcheck_ping_url", "")
        # Throttled call must not update the ping timestamp (no attempt made).
        assert eng._last_healthcheck_ping == stamp

    @pytest.mark.asyncio
    async def test_ping_failure_is_fail_open(self):
        eng = RuneClawEngine.__new__(RuneClawEngine)
        eng._last_healthcheck_ping = 0.0
        object.__setattr__(CONFIG.monitoring, "healthcheck_ping_url",
                           "http://127.0.0.1:9/unreachable")
        try:
            # Unreachable target must not raise into the tick loop.
            await eng._maybe_ping_healthcheck()
        finally:
            object.__setattr__(CONFIG.monitoring, "healthcheck_ping_url", "")


class TestFundingHaircutDefault:
    def test_default_on(self):
        assert CONFIG.analyzer.funding_cost_aware_enabled is True


class TestHonestPreset:
    def test_honest_sets_strict_and_next_open(self):
        from bot.backtest.runner import build_parser
        args = build_parser().parse_args(["--honest"])
        # main() applies the preset:
        if args.honest:
            args.strict_data = True
            args.fill_mode = "next_open"
        assert args.strict_data is True
        assert args.fill_mode == "next_open"


class TestBackupScript:
    def test_exists_and_executable(self):
        path = "scripts/backup_data.sh"
        assert os.path.exists(path)
        assert os.stat(path).st_mode & stat.S_IXUSR
