"""
RUNECLAW -- AI Trading Command Core
Entry point for both Telegram bot mode and CLI testing mode.

Usage:
    python -m bot.main --mode telegram   # Start Telegram bot
    python -m bot.main --mode cli        # Interactive CLI for testing
    python -m bot.main --mode scan       # One-shot market scan
    python -m bot.main --mode backtest   # Run backtest with synthetic data
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.skills.skill_registry import build_default_registry
from bot.skills.telegram_handler import TelegramHandler
from bot.utils.logger import audit, system_log


def _banner() -> str:
    return (
        "\n"
        "  ╔══════════════════════════════════════╗\n"
        "  ║   RUNECLAW  --  AI Trading Core      ║\n"
        "  ║   Bitget / GetClaw Hackathon 2025    ║\n"
        "  ╚══════════════════════════════════════╝\n"
        f"  Mode: {'SIMULATION' if CONFIG.simulation_mode else 'LIVE'}\n"
        f"  Live Trading: {'ENABLED' if CONFIG.live_trading_enabled else 'DISABLED'}\n"
        f"  Paper Balance: ${CONFIG.paper_balance_usd:,.2f}\n"
    )


def run_telegram() -> None:
    """Start the Telegram bot with the trading engine."""
    engine = RuneClawEngine()
    handler = TelegramHandler(engine)
    app = handler.build_app()

    audit(system_log, "Starting Telegram bot", action="startup", result="TELEGRAM")
    print(_banner())
    print("  Telegram bot is running. Press Ctrl+C to stop.\n")

    # Start the background engine loop alongside the Telegram polling
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _run_all() -> None:
        engine_task = asyncio.create_task(engine.run())
        try:
            await app.initialize()
            await app.start()
            await app.updater.start_polling()
            await engine_task
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            await engine.stop()

    try:
        loop.run_until_complete(_run_all())
    except KeyboardInterrupt:
        print("\nShutting down...")


async def run_cli() -> None:
    """Interactive CLI for testing skills without Telegram."""
    engine = RuneClawEngine()
    registry = build_default_registry()

    print(_banner())
    print("  CLI Mode -- type a skill name or 'quit' to exit.")
    print(f"  Available: {', '.join(s.split(' --')[0] for s in registry.list_skills())}\n")

    while True:
        try:
            raw = input("runeclaw> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if raw in ("quit", "exit", "q"):
            break

        parts = raw.split()
        skill_name = parts[0] if parts else ""
        kwargs = {}
        if len(parts) > 1:
            # Simple key=value or positional arg as symbol
            if "=" in parts[1]:
                for p in parts[1:]:
                    k, v = p.split("=", 1)
                    kwargs[k] = v
            else:
                kwargs["symbol"] = f"{parts[1].upper()}/USDT"

        skill = registry.get(skill_name)
        if skill is None:
            print(f"  Unknown skill: {skill_name}")
            continue

        try:
            result = await skill.execute(engine, **kwargs)
            print(f"\n{result}\n")
        except Exception as exc:
            print(f"  Error: {exc}")

    await engine.stop()
    print("Goodbye.")


async def run_scan() -> None:
    """One-shot market scan for quick testing."""
    engine = RuneClawEngine()
    registry = build_default_registry()
    print(_banner())
    result = await registry.get("scan_market").execute(engine)  # type: ignore
    print(result)
    await engine.stop()


async def run_backtest() -> None:
    """Run a backtest with synthetic data (or CSV/API via the runner module)."""
    from bot.backtest.data_loader import DataLoader
    from bot.backtest.engine import BacktestEngine
    from bot.backtest.models import BacktestConfig
    from bot.backtest.runner import _format_result_summary

    print(_banner())
    print("  Backtest Mode -- generating synthetic data and replaying...\n")

    config = BacktestConfig(
        symbol="BTC/USDT",
        timeframe="1h",
        initial_balance=CONFIG.paper_balance_usd,
    )

    bars = DataLoader.generate_synthetic(bars=720, seed=42)
    print(f"  Generated {len(bars)} synthetic 1h bars (30 days)")
    print(f"  Price range: ${min(b.close for b in bars):,.2f} – ${max(b.close for b in bars):,.2f}\n")

    engine = BacktestEngine(config)
    result = await engine.run(bars)
    print(_format_result_summary(result))


def main() -> None:
    parser = argparse.ArgumentParser(description="RUNECLAW AI Trading Command Core")
    parser.add_argument(
        "--mode", choices=["telegram", "cli", "scan", "backtest"], default="cli",
        help="Run mode: telegram (bot), cli (interactive), scan (one-shot), backtest")
    args = parser.parse_args()

    if args.mode == "telegram":
        if not CONFIG.telegram.bot_token:
            print("ERROR: TELEGRAM_BOT_TOKEN not set. Cannot start bot.")
            sys.exit(1)
        run_telegram()
    elif args.mode == "scan":
        asyncio.run(run_scan())
    elif args.mode == "backtest":
        asyncio.run(run_backtest())
    else:
        asyncio.run(run_cli())


if __name__ == "__main__":
    main()
