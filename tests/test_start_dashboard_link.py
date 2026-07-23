"""The /start (welcome_ready) message links to the web dashboard.

Pins two things: the bilingual template carries the {web_url} deep-link, and
_dashboard_url() builds it from the same WEBSITE_URL env the rest of the bot
uses (so bot + web never drift to different origins), defaulting sensibly.
"""


def test_welcome_template_carries_the_dashboard_link():
    from bot.utils.i18n import t
    kw = dict(name="X", status_icon="🟢", status_label="Active",
              status_label_zh="運行中", mode="LIVE", equity="$540", filled=1,
              pending_str="", pending_str_zh="", win_rate="50%", tier="Admin",
              trade_mode="Live", trade_mode_zh="實盤",
              web_url="https://example.test/dashboard#home", time="06:13")
    for lang in ("en", "zh"):
        msg = t("welcome_ready", lang, **kw)
        # the link rides as a real clickable anchor, in the caller's language
        assert 'href="https://example.test/dashboard#home"' in msg, lang


def test_dashboard_url_uses_website_env_and_deeplinks_home(monkeypatch):
    from bot.skills.telegram_handler import _dashboard_url
    monkeypatch.setenv("WEBSITE_URL", "https://my.site/")   # trailing slash tolerated
    assert _dashboard_url() == "https://my.site/dashboard#home"
    monkeypatch.delenv("WEBSITE_URL", raising=False)
    # default origin still yields a valid dashboard deep-link
    assert _dashboard_url().endswith("/dashboard#home")
    assert _dashboard_url().startswith("https://")
