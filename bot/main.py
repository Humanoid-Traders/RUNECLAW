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
import os
import signal
import sys

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.skills.skill_registry import build_default_registry
from bot.skills.telegram_handler import TelegramHandler
from bot.utils.logger import audit, system_log

_PID_FILE = os.path.join(os.environ.get("RUNECLAW_STATE_DIR", "data"), "runeclaw.pid")


def _banner() -> str:
    return (
        "\n"
        "  ╔══════════════════════════════════════╗\n"
        "  ║   RUNECLAW  --  AI Trading Core      ║\n"
        "  ║   Bitget AI Base Camp · S1           ║\n"
        "  ╚══════════════════════════════════════╝\n"
        f"  Mode: {'SIMULATION' if CONFIG.simulation_mode else 'LIVE'}\n"
        f"  Live Trading: {'ENABLED' if CONFIG.live_trading_enabled else 'DISABLED'}\n"
        f"  Paper Balance: ${CONFIG.paper_balance_usd:,.2f}\n"
    )


def run_telegram() -> None:
    """Start the Telegram bot with the trading engine."""
    # ── PID-file guard: prevent duplicate instances ──
    os.makedirs(os.path.dirname(_PID_FILE) or ".", exist_ok=True)
    if os.path.exists(_PID_FILE):
        try:
            old_pid = int(open(_PID_FILE).read().strip())
            # Check if the old process is still running
            os.kill(old_pid, 0)  # signal 0 = just check, don't kill
            # Process exists — kill it before starting fresh
            print(f"  Killing stale bot instance (PID {old_pid})...")
            os.kill(old_pid, signal.SIGTERM)
            import time
            time.sleep(3)
            # Force kill if still alive
            try:
                os.kill(old_pid, 0)
                os.kill(old_pid, signal.SIGKILL)
                time.sleep(1)
            except ProcessLookupError:
                pass
        except (ProcessLookupError, ValueError):
            pass  # Process already dead, stale PID file
        except PermissionError:
            print(f"  WARNING: Cannot kill existing process. May cause Telegram conflicts.")

    # Write our PID
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    def _cleanup_pid():
        try:
            if os.path.exists(_PID_FILE):
                stored = int(open(_PID_FILE).read().strip())
                if stored == os.getpid():
                    os.unlink(_PID_FILE)
        except Exception:
            pass

    import atexit
    atexit.register(_cleanup_pid)

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

        # Start live dashboard web server
        dashboard_runner = None
        try:
            from aiohttp import web as _web
            from bot.web.dashboard_server import create_app as _create_dash
            dashboard_app = _create_dash(engine)
            dashboard_runner = _web.AppRunner(dashboard_app)
            await dashboard_runner.setup()
            _site = _web.TCPSite(dashboard_runner, "0.0.0.0", 8080)
            await _site.start()
            print("  Live Dashboard: http://0.0.0.0:8080\n")
        except Exception as exc:
            print(f"  Dashboard server skipped: {exc}\n")

        try:
            await app.initialize()
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            # Start proactive alert monitor (Move 2)
            await handler.start_monitor(app.bot)

            # Keep running forever — restart engine if it crashes
            while True:
                try:
                    await engine_task
                    # Engine exited cleanly — shouldn't happen, restart
                    audit(system_log, "Engine loop exited unexpectedly, restarting",
                          action="engine_restart", result="RESTARTING")
                    print("  WARNING: Engine exited, restarting in 5s...")
                    await asyncio.sleep(5)
                    engine_task = asyncio.create_task(engine.run())
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    audit(system_log, f"Engine crashed: {exc}",
                          action="engine_crash", result="ERROR")
                    print(f"  ERROR: Engine crashed: {exc}. Restarting in 10s...")
                    await asyncio.sleep(10)
                    engine_task = asyncio.create_task(engine.run())
        finally:
            await handler.stop_monitor()
            if dashboard_runner:
                await dashboard_runner.cleanup()
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
