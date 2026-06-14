"""
Tests for bot.skills.chart_renderer.

The renderer degrades gracefully when matplotlib/mplfinance aren't installed,
so the import-dependent tests are skipped (not failed) in that case. The
fallback paths and async send are tested regardless.
"""
import math

import pytest

from bot.skills import chart_renderer as cr


def _candles(n: int = 60):
    out, t0, price = [], 1_700_000_000_000, 60_000.0
    for i in range(n):
        o = price
        c = price * (1 + 0.004 * math.sin(i / 3.0))
        h = max(o, c) * 1.002
        l = min(o, c) * 0.998
        out.append([t0 + i * 3_600_000, o, h, l, c, 100.0 + i])
        price = c
    return out


needs_charts = pytest.mark.skipif(
    not cr.charts_available(), reason="charting libs (mplfinance) not installed"
)


@needs_charts
def test_indicators_are_sane():
    df = cr.compute_chart_indicators(_candles())
    assert {"Open", "High", "Low", "Close", "Volume", "EMA_9", "EMA_21", "RSI"} <= set(df.columns)
    assert df["RSI"].between(0, 100).all()
    last = df.iloc[-1]
    # EMA9 (faster) tracks price more closely than EMA21
    assert abs(last["EMA_9"] - last["Close"]) <= abs(last["EMA_21"] - last["Close"]) + 1e-9


@needs_charts
def test_render_produces_valid_png():
    png = cr.build_chart_png(_candles(), title="BTC/USDT Test", dpi=120)
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"   # PNG magic header
    assert len(png) > 5000


def test_build_returns_none_on_bad_input():
    # These must never raise — they return None so callers fall back to text.
    assert cr.build_chart_png([], "empty") is None
    assert cr.build_chart_png(_candles(5), "too short") is None


@needs_charts
async def test_send_chart_delivers_photo_off_thread():
    captured = {}

    class FakeBot:
        async def send_photo(self, chat_id, photo, caption, parse_mode):
            data = photo.read()
            assert data[:8] == b"\x89PNG\r\n\x1a\n"
            captured["photo"] = (chat_id, len(caption), parse_mode)

        async def send_message(self, chat_id, text, parse_mode):
            captured["msg"] = (chat_id, text)

    sent = await cr.send_chart(FakeBot(), 12345, _candles(),
                               caption="<b>BTC</b> long", title="BTC")
    assert sent is True
    assert captured["photo"][0] == 12345


@needs_charts
async def test_caption_clamped_to_1024():
    captured = {}

    class FakeBot:
        async def send_photo(self, chat_id, photo, caption, parse_mode):
            captured["len"] = len(caption)
        async def send_message(self, *a, **k):
            pass

    await cr.send_chart(FakeBot(), 1, _candles(), caption="x" * 5000)
    assert captured["len"] == 1024


async def test_send_chart_falls_back_to_text_without_chart():
    captured = {}

    class FakeBot:
        async def send_photo(self, *a, **k):
            raise AssertionError("should not send a photo when there's no chart")
        async def send_message(self, chat_id, text, parse_mode):
            captured["msg"] = text

    # empty candles -> no chart -> text fallback
    sent = await cr.send_chart(FakeBot(), 1, [], caption="<b>no chart</b>")
    assert sent is False
    assert "msg" in captured


@needs_charts
def test_render_with_trade_levels():
    df = cr.compute_chart_indicators(_candles())
    levels = {"entry": 60000.0, "stop_loss": 58800.0, "take_profit": 62400.0}
    for theme in ("dark", "light"):
        png = cr.render_chart_png(df, title="BTC LONG", dpi=110, levels=levels,
                                  theme=theme, subtitle="LONG · conf 78% · R:R 1:2.4")
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
    # zero/missing levels and an unknown theme must not break rendering
    png2 = cr.render_chart_png(df, dpi=110, levels={"entry": 0, "stop_loss": None},
                               theme="nonsense")
    assert png2[:8] == b"\x89PNG\r\n\x1a\n"


def test_levels_from_idea_extracts_fields():
    from types import SimpleNamespace
    idea = SimpleNamespace(entry_price=100.0, stop_loss=95.0, take_profit=110.0)
    lv = cr._levels_from_idea(idea)
    assert lv == {"entry": 100.0, "stop_loss": 95.0, "take_profit": 110.0}
    assert cr._levels_from_idea(None) is None


@needs_charts
def test_vwap_is_computed_and_bounded():
    df = cr.compute_chart_indicators(_candles())
    assert "VWAP" in df.columns
    assert df["VWAP"].notna().all()
    assert df["VWAP"].min() >= df["Low"].min() - 1e-6
    assert df["VWAP"].max() <= df["High"].max() + 1e-6


def test_structure_lines_never_raise_on_short_data():
    # Too-short / unavailable input must return [] rather than raising.
    assert cr._market_structure_lines(None) == []


@needs_charts
def test_render_draws_structure_lines(monkeypatch):
    # Deterministically exercise the BOS/CHoCH drawing path regardless of the
    # detector's thresholds by injecting known structure lines.
    monkeypatch.setattr(cr, "_market_structure_lines", lambda df: [
        {"start": 10, "level": float(df["High"].iloc[10]), "label": "BOS", "color_key": "up"},
        {"start": 30, "level": float(df["Low"].iloc[30]), "label": "CHoCH", "color_key": "choch"},
    ])
    png = cr.build_chart_png(_candles(), title="BTC", dpi=110)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


@needs_charts
def test_smc_helpers_never_raise():
    df = cr.compute_chart_indicators(_candles())
    # all return lists / dict-or-None and never raise on normal data
    assert isinstance(cr._fair_value_gaps(df), list)
    assert isinstance(cr._order_blocks(df), list)
    assert cr._liquidity_sweep(df) is None or isinstance(cr._liquidity_sweep(df), dict)
    assert isinstance(cr._swing_labels(df), list)
    # short data is safe too
    short = cr.compute_chart_indicators(_candles(8))
    assert cr._order_blocks(short) == []


@needs_charts
def test_render_with_smc_off_and_on():
    candles = _candles()
    on = cr.build_chart_png(candles, title="BTC", dpi=110, smc=True)
    off = cr.build_chart_png(candles, title="BTC", dpi=110, smc=False)
    assert on[:8] == b"\x89PNG\r\n\x1a\n"
    assert off[:8] == b"\x89PNG\r\n\x1a\n"


def _idea():
    from types import SimpleNamespace
    return SimpleNamespace(
        asset="BTC/USDT", direction=SimpleNamespace(value="LONG"),
        entry_price=60000.0, stop_loss=58800.0, take_profit=63000.0,
        confidence=0.78, risk_reward_ratio=2.4,
    )


class _FakeBot:
    def __init__(self):
        self.photo_calls = 0
        self.group_sizes = []

    async def send_photo(self, chat_id, photo, caption, parse_mode):
        assert photo.read()[:8] == b"\x89PNG\r\n\x1a\n"
        self.photo_calls += 1

    async def send_media_group(self, chat_id, media):
        self.group_sizes.append(len(media))

    async def send_message(self, **k):
        pass


_have_telegram = False
try:
    import telegram  # noqa: F401
    _have_telegram = True
except Exception:
    pass


@needs_charts
async def test_single_timeframe_sends_one_photo():
    bot = _FakeBot()
    ok = await cr.send_idea_charts_multi(bot, 1, {"1h": _candles()}, _idea())
    assert ok and bot.photo_calls == 1 and bot.group_sizes == []


@needs_charts
@pytest.mark.skipif(not _have_telegram, reason="python-telegram-bot not installed")
async def test_multiple_timeframes_send_album():
    bot = _FakeBot()
    ok = await cr.send_idea_charts_multi(
        bot, 1, {"4h": _candles(), "1h": _candles()}, _idea())
    assert ok and bot.group_sizes == [2]


@needs_charts
async def test_multi_tf_empty_falls_back_to_text():
    bot = _FakeBot()
    ok = await cr.send_idea_charts_multi(bot, 1, {"1h": []}, _idea())
    assert ok is False


@needs_charts
async def test_send_idea_chart_draws_levels_and_sends():
    from types import SimpleNamespace
    captured = {}

    class FakeBot:
        async def send_photo(self, chat_id, photo, caption, parse_mode):
            assert photo.read()[:8] == b"\x89PNG\r\n\x1a\n"
            captured["caption"] = caption
        async def send_message(self, *a, **k):
            pass

    idea = SimpleNamespace(
        asset="BTC/USDT",
        direction=SimpleNamespace(value="LONG"),
        entry_price=60000.0, stop_loss=58800.0, take_profit=62400.0,
    )
    sent = await cr.send_idea_chart(FakeBot(), 42, _candles(), idea)
    assert sent is True
    assert "BTC" in captured["caption"] and "LONG" in captured["caption"]
