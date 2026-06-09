"""
RUNECLAW Keep Awake — Prevents laptop sleep/lock during training.
Simulates tiny mouse movement every 60 seconds.
No admin required. Run in a separate CMD window alongside training.

Usage:
  python keep_awake.py

Press Ctrl+C to stop.
"""

import ctypes
import time
import sys

def keep_awake():
    # Prevent Windows from sleeping (no admin needed)
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ES_DISPLAY_REQUIRED = 0x00000002
    ctypes.windll.kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    )

    print("=" * 45)
    print("  RUNECLAW KEEP AWAKE")
    print("=" * 45)
    print("  GPU stays at full speed.")
    print("  Screen stays on. No sleep.")
    print("  Press Ctrl+C to stop.")
    print("=" * 45)

    try:
        tick = 0
        while True:
            # Tiny mouse nudge (1px right, then 1px left)
            ctypes.windll.user32.mouse_event(0x0001, 1, 0, 0, 0)
            time.sleep(0.05)
            ctypes.windll.user32.mouse_event(0x0001, -1, 0, 0, 0)

            # Also simulate a keypress (shift key — does nothing visible)
            ctypes.windll.user32.keybd_event(0x10, 0, 0, 0)       # shift down
            ctypes.windll.user32.keybd_event(0x10, 0, 0x0002, 0)  # shift up

            # Re-assert no-sleep flag each cycle
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            )

            tick += 1
            if tick % 5 == 0:
                mins = tick
                print(f"  Awake for {mins} min | GPU should be at full clock", end="\r")

            time.sleep(60)

    except KeyboardInterrupt:
        # Restore default power behavior
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        print("\n\n  Stopped. Power settings restored.")

if __name__ == "__main__":
    keep_awake()
