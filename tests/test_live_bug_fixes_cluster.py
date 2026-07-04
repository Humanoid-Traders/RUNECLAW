"""Regression tests for a cluster of live bugs reported from the running bot:

1. bitget 45115 — the futures ENTRY order was placed against the spot symbol,
   so its price was rounded to the spot tick (finer than the perp tick for
   sub-cent tokens) → off the perp grid → order rejected.
2. STALE_DATA — a confirmed trade was rejected because idea.timestamp was
   stamped at scan time and never refreshed, even though the confirm path
   re-validates against a live price. The wide ~200-symbol scan aged ideas
   past the window.
3. Latest Signal freeze — the button ran a full ~200-symbol force_scan inline
   with no cap/timeout.
4. Daily PnL display — a dollar figure was rendered with a '%' suffix and, in
   LIVE, summed ALL closed trades ever (never reset daily).
"""
import inspect
from datetime import datetime, timedelta, timezone

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LiveExecutor
from bot.skills.telegram_handler import TelegramHandler, _closed_on_utc_date

UTC = timezone.utc


# ── 1. bitget 45115: futures entry uses the swap symbol ──────────────────
def test_futures_entry_normalizes_to_swap_symbol():
    src = inspect.getsource(LiveExecutor.execute)
    # The entry path must convert idea.asset to the perp/swap form when futures,
    # so price_to_precision / create_order use the FUTURES market's real tick.
    assert 'if is_futures:' in src
    assert 'f"{idea.asset}:USDT"' in src
    # And it must happen before the order price pipeline (create_order).
    conv = src.index('f"{idea.asset}:USDT"')
    # the old buggy line (bare assignment with the misleading comment) is gone
    assert 'Convert symbol to the perpetual/swap format' in src
    assert conv < src.index('active_exchange = exchange')


# ── 2. STALE_DATA: confirm refreshes idea.timestamp before the re-check ──
def test_confirm_refreshes_timestamp_before_risk_recheck():
    src = inspect.getsource(RuneClawEngine._confirm_trade_inner)
    # The refresh is gated on a successful live fetch and happens before the
    # risk re-check (which runs the STALE_DATA guard).
    assert 'idea = idea.model_copy(update={"timestamp": datetime.now(UTC)})' in src
    refresh = src.index('model_copy(update={"timestamp"')
    recheck = src.index('recheck_engine.evaluate(')
    assert refresh < recheck
    assert 'if current_price > 0:' in src


# ── 3. Freeze: force_scan caps the interactive universe ──────────────────
def test_force_scan_accepts_max_symbols():
    sig = inspect.signature(RuneClawEngine.force_scan)
    assert 'max_symbols' in sig.parameters
    locked = inspect.signature(RuneClawEngine._force_scan_locked)
    assert 'max_symbols' in locked.parameters
    body = inspect.getsource(RuneClawEngine._force_scan_locked)
    assert 'signals = signals[:max_symbols]' in body


def test_latest_signal_uses_interactive_cap_and_timeout():
    src = inspect.getsource(TelegramHandler._cmd_latest_signal)
    assert 'CONFIG.interactive_scan_count' in src
    assert 'asyncio.wait_for' in src
    assert 'interactive_scan_timeout_sec' in src
    assert 'asyncio.TimeoutError' in src


def test_interactive_scan_config_defaults():
    assert CONFIG.interactive_scan_count == 40
    assert CONFIG.interactive_scan_timeout_sec == 45
    # Still smaller than the full universe so the cap actually bites.
    assert CONFIG.interactive_scan_count < CONFIG.top_movers_count


# ── 4. Daily PnL: today-filter helper + percent conversion ───────────────
class _Pos:
    def __init__(self, closed_at, pnl_usd=1.0):
        self.closed_at = closed_at
        self.pnl_usd = pnl_usd


def test_closed_on_utc_date_matches_only_today():
    today = datetime.now(UTC).date()
    now = datetime.now(UTC)
    assert _closed_on_utc_date(_Pos(now), today) is True
    assert _closed_on_utc_date(_Pos(now - timedelta(days=1)), today) is False
    # ISO string form
    assert _closed_on_utc_date(_Pos(now.isoformat()), today) is True
    # naive datetime is treated as UTC
    assert _closed_on_utc_date(_Pos(now.replace(tzinfo=None)), today) is True
    # dict row form
    assert _closed_on_utc_date({"closed_at": now}, today) is True
    # missing / bad
    assert _closed_on_utc_date(_Pos(None), today) is False
    assert _closed_on_utc_date(_Pos("not-a-date"), today) is False


def test_status_card_converts_daily_pnl_to_percent_and_filters_today():
    src = inspect.getsource(TelegramHandler._cmd_status)
    # LIVE daily must be filtered to today's UTC close date (was all-time).
    assert '_closed_on_utc_date(t, _today)' in src
    # And dollars are converted to percent-of-equity before rendering.
    assert 'daily_pnl_pct = (daily_pnl / equity * 100.0)' in src
    assert 'daily_pnl=round(daily_pnl_pct, 2)' in src
