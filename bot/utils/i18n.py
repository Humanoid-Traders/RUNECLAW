"""
Bilingual support: English + Traditional Chinese (繁體中文)

Usage:
    from bot.utils.i18n import t

    # Get translated string
    msg = t("welcome", lang)          # returns EN or 繁中 version
    msg = t("welcome", lang, name="Trader")  # with placeholders
"""

from typing import Optional

# Default language
DEFAULT_LANG = "en"
SUPPORTED_LANGS = {"en": "English", "zh": "繁體中文"}

# ═══════════════════════════════════════════════════════════
#  TRANSLATIONS
#  Each key maps to {"en": "...", "zh": "..."}
#  Use {placeholder} for dynamic values
# ═══════════════════════════════════════════════════════════

_STRINGS: dict[str, dict[str, str]] = {
    # ── /start ──
    "welcome_pending": {
        "en": (
            "<b>Hey {name}!</b>\n\n"
            "I'm RUNECLAW, your AI trading assistant.\n"
            "I scan the market, find setups, and manage risk for you.\n\n"
            "Your account is pending approval.\n"
            "ID: <code>{tg_id}</code>\n\n"
            "An admin will get you set up soon.\n"
            "Once approved, just chat with me like normal."
        ),
        "zh": (
            "<b>嗨 {name}！</b>\n\n"
            "我是 RUNECLAW，你的 AI 交易助手。\n"
            "我會掃描市場、尋找交易機會，並為你管理風險。\n\n"
            "你的帳號正在等待審核。\n"
            "ID: <code>{tg_id}</code>\n\n"
            "管理員會盡快為你設定。\n"
            "審核通過後，直接跟我對話就行了。"
        ),
    },
    "welcome_ready": {
        "en": (
            "Hey {name}, here's where things stand:\n\n"
            "{status_icon} <b>{status_label}</b> | {mode}\n"
            "Equity: <code>${equity}</code>\n"
            "Open positions: <code>{filled}</code>{pending_str}\n"
            "Win rate: <code>{win_rate}</code>\n"
            "Tier: {tier} | Trading: {trade_mode}\n\n"
            "<b>Talk to me:</b>\n"
            "<i>\"scan BTC\" - \"show my positions\" - \"what's the risk?\"</i>\n"
            "<i>\"analyze SOL\" - \"how's my PnL?\" - \"pause the bot\"</i>\n\n"
            "Just type what you need, no commands required.\n\n"
            "<i>{time}</i>"
        ),
        "zh": (
            "嗨 {name}，以下是目前的狀態：\n\n"
            "{status_icon} <b>{status_label_zh}</b> | {mode}\n"
            "權益: <code>${equity}</code>\n"
            "持倉數量: <code>{filled}</code>{pending_str_zh}\n"
            "勝率: <code>{win_rate}</code>\n"
            "等級: {tier} | 交易模式: {trade_mode_zh}\n\n"
            "<b>跟我說：</b>\n"
            "<i>\"掃描 BTC\" - \"顯示持倉\" - \"風險如何？\"</i>\n"
            "<i>\"分析 SOL\" - \"損益如何？\" - \"暫停交易\"</i>\n\n"
            "直接輸入你的需求，不需要指令。\n\n"
            "<i>{time}</i>"
        ),
    },

    # ── /help sections ──
    "help_title": {
        "en": "\u2694\ufe0f <b>RUNECLAW \u2014 Command Guide</b>",
        "zh": "\u2694\ufe0f <b>RUNECLAW \u2014 指令指南</b>",
    },
    "help_tip": {
        "en": (
            "\U0001f4ac <i>You can also just type naturally:</i>\n"
            "<i>\"scan BTC\" \u2014 \"how's my PnL?\" \u2014 \"what's moving?\"</i>"
        ),
        "zh": (
            "\U0001f4ac <i>你也可以直接用自然語言：</i>\n"
            "<i>\"掃描 BTC\" \u2014 \"損益如何？\" \u2014 \"什麼在漲？\"</i>"
        ),
    },
    "help_market": {
        "en": (
            "\U0001f50d <b>Market Analysis</b>\n"
            "/scan \u2014 quick market scan\n"
            "/deepscan \u2014 deep multi-timeframe scan\n"
            "/fullscan \u2014 full scan (all pairs)\n"
            "/analyze <i>BTC</i> \u2014 detailed coin analysis\n"
            "/macro \u2014 macro outlook\n"
            "/patterns \u2014 chart pattern detection"
        ),
        "zh": (
            "\U0001f50d <b>市場分析</b>\n"
            "/scan \u2014 快速掃描\n"
            "/deepscan \u2014 多時段深度掃描\n"
            "/fullscan \u2014 全幣種掃描\n"
            "/analyze <i>BTC</i> \u2014 詳細分析\n"
            "/macro \u2014 宏觀展望\n"
            "/patterns \u2014 圖表形態偵測"
        ),
    },
    "help_trading": {
        "en": (
            "\U0001f4b9 <b>Trading</b>\n"
            "/trade <i>BTC</i> \u2014 generate trade idea\n"
            "/latest_signal \u2014 last signal\n"
            "/signals \u2014 signal history & stats\n"
            "/proposals \u2014 pending trade proposals"
        ),
        "zh": (
            "\U0001f4b9 <b>交易</b>\n"
            "/trade <i>BTC</i> \u2014 產生交易建議\n"
            "/latest_signal \u2014 最新信號\n"
            "/signals \u2014 信號紀錄\n"
            "/proposals \u2014 待確認交易"
        ),
    },
    "help_portfolio": {
        "en": (
            "\U0001f4ca <b>Portfolio & Risk</b>\n"
            "/portfolio \u2014 holdings & PnL\n"
            "/open_positions \u2014 current positions\n"
            "/risk \u2014 risk dashboard\n"
            "/performance \u2014 performance stats\n"
            "/daily_report \u2014 daily summary\n"
            "/journal \u2014 trade journal\n"
            "/costs \u2014 fee breakdown"
        ),
        "zh": (
            "\U0001f4ca <b>投資組合與風險</b>\n"
            "/portfolio \u2014 持倉與損益\n"
            "/open_positions \u2014 目前持倉\n"
            "/risk \u2014 風險控制面板\n"
            "/performance \u2014 績效統計\n"
            "/daily_report \u2014 每日報告\n"
            "/journal \u2014 交易日誌\n"
            "/costs \u2014 手續費明細"
        ),
    },
    "help_strategy": {
        "en": (
            "\U0001f3af <b>Strategy Presets</b>\n"
            "/momentum \u2014 trend following\n"
            "/swing \u2014 swing trades\n"
            "/scalp \u2014 quick scalps\n"
            "/dip \u2014 dip buying\n"
            "/intraday \u2014 intraday setups\n"
            "/strategy \u2014 current strategy info\n"
            "/mode \u2014 switch strategy mode\n"
            "/playbook \u2014 strategy playbook"
        ),
        "zh": (
            "\U0001f3af <b>策略預設</b>\n"
            "/momentum \u2014 趨勢跟蹤\n"
            "/swing \u2014 波段交易\n"
            "/scalp \u2014 短線搶帽\n"
            "/dip \u2014 逢低買入\n"
            "/intraday \u2014 日內交易\n"
            "/strategy \u2014 目前策略\n"
            "/mode \u2014 切換策略\n"
            "/playbook \u2014 策略手冊"
        ),
    },
    "help_tools": {
        "en": (
            "\U0001f6e0 <b>Tools</b>\n"
            "/backtest \u2014 run backtest\n"
            "/walkforward \u2014 walk-forward test\n"
            "/optimize \u2014 parameter optimization\n"
            "/watch <i>BTC 65000</i> \u2014 price alert\n"
            "/learn \u2014 trading lessons\n"
            "/montecarlo \u2014 Monte Carlo risk simulation\n"
            "/attribution \u2014 signal performance attribution\n"
            "/equitycurve \u2014 equity curve health status"
        ),
        "zh": (
            "\U0001f6e0 <b>工具</b>\n"
            "/backtest \u2014 回測\n"
            "/walkforward \u2014 前推測試\n"
            "/optimize \u2014 參數優化\n"
            "/watch <i>BTC 65000</i> \u2014 價格提醒\n"
            "/learn \u2014 交易課程\n"
            "/montecarlo \u2014 蒙地卡羅風險模擬\n"
            "/attribution \u2014 訊號績效歸因\n"
            "/equitycurve \u2014 權益曲線健康狀態"
        ),
    },
    "help_controls": {
        "en": (
            "\u2699\ufe0f <b>Controls</b>\n"
            "/dashboard \u2014 overview panel\n"
            "/status \u2014 engine status\n"
            "/health \u2014 system health\n"
            "/pause \u2014 pause trading\n"
            "/resume \u2014 resume trading\n"
            "/halt \u2014 halt engine\n"
            "/emergency_stop \u2014 kill switch\n"
            "/rejected \u2014 rejected trades\n"
            "/whynot \u2014 why last trade was rejected\n"
            "/reset \u2014 reset engine state"
        ),
        "zh": (
            "\u2699\ufe0f <b>控制</b>\n"
            "/dashboard \u2014 總覽面板\n"
            "/status \u2014 引擎狀態\n"
            "/health \u2014 系統健康度\n"
            "/pause \u2014 暫停交易\n"
            "/resume \u2014 恢復交易\n"
            "/halt \u2014 停止引擎\n"
            "/emergency_stop \u2014 緊急停止\n"
            "/rejected \u2014 被拒絕的交易\n"
            "/whynot \u2014 為什麼被拒絕\n"
            "/reset \u2014 重設引擎"
        ),
    },
    "help_account": {
        "en": (
            "\U0001f464 <b>Account</b>\n"
            "/me \u2014 your profile\n"
            "/link \u2014 link exchange account\n"
            "/start \u2014 refresh session\n"
            "/lang \u2014 switch language"
        ),
        "zh": (
            "\U0001f464 <b>帳號</b>\n"
            "/me \u2014 你的資料\n"
            "/link \u2014 連結交易所\n"
            "/start \u2014 重新整理\n"
            "/lang \u2014 切換語言"
        ),
    },
    "help_ai": {
        "en": (
            "\U0001f916 <b>AI Settings</b>\n"
            "/llmstatus \u2014 current AI model\n"
            "/llmtiers \u2014 available models\n"
            "/setllm \u2014 change AI model\n"
            "/llmreset \u2014 reset to default"
        ),
        "zh": (
            "\U0001f916 <b>AI 設定</b>\n"
            "/llmstatus \u2014 目前 AI 模型\n"
            "/llmtiers \u2014 可用模型\n"
            "/setllm \u2014 更換 AI 模型\n"
            "/llmreset \u2014 重設為預設"
        ),
    },
    "help_live": {
        "en": (
            "\U0001f525 <b>Live Trading</b>\n"
            "/golive \u2014 enable live execution\n"
            "/livebalance \u2014 exchange balance\n"
            "/livepositions \u2014 exchange positions\n"
            "/liveclose <i>id</i> \u2014 close position\n"
            "/buy <i>BTC 5</i> \u2014 spot buy\n"
            "/sell <i>BTC</i> \u2014 spot sell"
        ),
        "zh": (
            "\U0001f525 <b>實盤交易</b>\n"
            "/golive \u2014 啟用實盤\n"
            "/livebalance \u2014 交易所餘額\n"
            "/livepositions \u2014 交易所持倉\n"
            "/liveclose <i>id</i> \u2014 平倉\n"
            "/buy <i>BTC 5</i> \u2014 現貨買入\n"
            "/sell <i>BTC</i> \u2014 現貨賣出"
        ),
    },
    "help_admin": {
        "en": (
            "\U0001f6e1 <b>Admin</b>\n"
            "/users \u2014 all users\n"
            "/approve <i>ID</i> \u2014 approve user\n"
            "/revoke <i>ID</i> \u2014 revoke access\n"
            "/set_tier <i>ID tier</i> \u2014 change tier\n"
            "/grant_live <i>ID</i> \u2014 enable live trading\n"
            "/revoke_live <i>ID</i> \u2014 disable live trading"
        ),
        "zh": (
            "\U0001f6e1 <b>管理員</b>\n"
            "/users \u2014 所有用戶\n"
            "/approve <i>ID</i> \u2014 批准用戶\n"
            "/revoke <i>ID</i> \u2014 撤銷權限\n"
            "/set_tier <i>ID tier</i> \u2014 更改等級\n"
            "/grant_live <i>ID</i> \u2014 啟用實盤\n"
            "/revoke_live <i>ID</i> \u2014 停用實盤"
        ),
    },

    # ── Trade flow messages ──
    "trade_proposed": {
        "en": "New trade idea for {asset}",
        "zh": "{asset} 新交易建議",
    },
    "trade_confirmed": {
        "en": "\u2705 Confirmed \u2014 executing...",
        "zh": "\u2705 已確認 \u2014 執行中...",
    },
    "trade_rejected": {
        "en": "Trade REJECTED on re-check: {reason}",
        "zh": "交易在複查時被拒絕: {reason}",
    },
    "trade_executed": {
        "en": "\u2705 Trade executed: {direction} {asset} at ${price}",
        "zh": "\u2705 交易已執行: {direction} {asset} 於 ${price}",
    },
    "trade_expired": {
        "en": "Trade expired (not confirmed in time)",
        "zh": "交易已過期（未及時確認）",
    },
    "trade_not_found": {
        "en": "Trade not found or expired.",
        "zh": "找不到交易或已過期。",
    },

    # ── Risk messages ──
    "risk_approved": {
        "en": "Risk check: APPROVED ({passed} checks passed)",
        "zh": "風險檢查: 通過 ({passed} 項檢查通過)",
    },
    "risk_rejected": {
        "en": "Risk check: REJECTED \u2014 {reason}",
        "zh": "風險檢查: 未通過 \u2014 {reason}",
    },

    # ── Position messages ──
    "position_opened": {
        "en": "Position opened: {direction} {asset}",
        "zh": "已開倉: {direction} {asset}",
    },
    "position_closed": {
        "en": "Position closed: {asset} | PnL: {pnl}",
        "zh": "已平倉: {asset} | 損益: {pnl}",
    },
    "no_open_positions": {
        "en": "No open positions.",
        "zh": "目前無持倉。",
    },

    # ── Limit order flow ──
    "limit_prompt": {
        "en": (
            "\U0001f4b0 Set limit price for {asset} {direction}\n\n"
            "Current entry: <code>${entry}</code>\n"
            "SL: <code>${sl}</code> | TP: <code>${tp}</code>\n\n"
            "Type your limit price (e.g. <code>{example1}</code> or <code>{example2}</code>):"
        ),
        "zh": (
            "\U0001f4b0 設定 {asset} {direction} 的限價\n\n"
            "目前入場價: <code>${entry}</code>\n"
            "止損: <code>${sl}</code> | 止盈: <code>${tp}</code>\n\n"
            "請輸入限價 (例如 <code>{example1}</code> 或 <code>{example2}</code>):"
        ),
    },
    "limit_set": {
        "en": "\U0001f4b0 Limit set: {asset} {direction}\nEntry: ${old_entry} \u2192 ${new_entry}",
        "zh": "\U0001f4b0 限價已設定: {asset} {direction}\n入場價: ${old_entry} \u2192 ${new_entry}",
    },

    # ── /lang command ──
    "lang_switched": {
        "en": "Language set to <b>English</b>.",
        "zh": "語言已切換為<b>繁體中文</b>。",
    },
    "lang_prompt": {
        "en": "Select language / 選擇語言:",
        "zh": "選擇語言 / Select language:",
    },

    # ── Status labels ──
    "status_active": {"en": "Active", "zh": "運作中"},
    "status_paused": {"en": "Paused", "zh": "已暫停"},
    "mode_live": {"en": "\U0001f525 Live", "zh": "\U0001f525 實盤"},
    "mode_paper": {"en": "\U0001f4dd Paper", "zh": "\U0001f4dd 模擬"},

    # ── Common ──
    "direction_long": {"en": "LONG", "zh": "做多"},
    "direction_short": {"en": "SHORT", "zh": "做空"},
    "confirm": {"en": "Confirm", "zh": "確認"},
    "reject": {"en": "Reject", "zh": "拒絕"},
    "cancel": {"en": "Cancel", "zh": "取消"},
    "entry": {"en": "Entry", "zh": "入場價"},
    "stop_loss": {"en": "Stop Loss", "zh": "止損"},
    "take_profit": {"en": "Take Profit", "zh": "止盈"},
    "confidence": {"en": "Confidence", "zh": "信心度"},
    "position_size": {"en": "Position Size", "zh": "倉位大小"},
    "risk_reward": {"en": "Risk/Reward", "zh": "風險/報酬"},
    "scanning": {"en": "Scanning market...", "zh": "掃描市場中..."},
    "analyzing": {"en": "Analyzing {asset}...", "zh": "分析 {asset} 中..."},
    "no_setups": {"en": "No trade setups found.", "zh": "未找到交易機會。"},
}


def t(key: str, lang: str = DEFAULT_LANG, **kwargs) -> str:
    """
    Get translated string.

    Args:
        key: Translation key (e.g. "welcome_pending")
        lang: Language code ("en" or "zh")
        **kwargs: Placeholder values for {name}, {asset}, etc.

    Returns:
        Translated string with placeholders filled in.
        Falls back to English if key or language not found.
    """
    entry = _STRINGS.get(key)
    if entry is None:
        return key  # return the key itself as fallback

    text = entry.get(lang, entry.get("en", key))

    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass  # return template with unfilled placeholders

    return text


def get_user_lang(users_db, tg_id: str) -> str:
    """Get language preference for a user. Defaults to 'en'."""
    if users_db is None:
        return DEFAULT_LANG
    user = users_db.get(tg_id)
    if user and isinstance(user, dict):
        return user.get("lang", DEFAULT_LANG)
    return DEFAULT_LANG


def set_user_lang(users_db, tg_id: str, lang: str) -> bool:
    """Set language preference for a user. Returns True if successful."""
    if lang not in SUPPORTED_LANGS:
        return False
    if users_db is None:
        return False
    user = users_db.get(tg_id)
    if user and isinstance(user, dict):
        user["lang"] = lang
        # UserStore uses _users dict + _save()
        import threading
        with users_db._lock:
            users_db._users[str(tg_id)] = user
            users_db._save()
        return True
    return False
