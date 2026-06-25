"""RUNECLAW ETH pullback - sandbox entry point.

Historical runs go through the Nautilus replay; live runs emit a signal-only
read of the latest bar (this package never places orders).
"""
from getagent import runtime


def run() -> None:
    if runtime.is_historical():
        from . import main_backtest

        main_backtest.run()
    elif runtime.is_live():
        from . import main_live

        main_live.run()
    else:
        runtime.emit_signal(
            action="watch",
            symbol="ETHUSDT",
            confidence=0.0,
            metrics={},
            meta={"reason": f"unsupported evaluation_mode={runtime.evaluation_mode!r}"},
        )


if __name__ == "__main__":
    run()
