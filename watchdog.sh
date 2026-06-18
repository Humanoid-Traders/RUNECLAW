#!/bin/bash
# RUNECLAW Watchdog — ensures the bot stays alive
# Install: crontab -e → */1 * * * * /home/mulerun/runeclaw/watchdog.sh >> /tmp/watchdog.log 2>&1

PIDFILE="${RUNECLAW_STATE_DIR:-data}/runeclaw.pid"
LOGFILE="/tmp/runeclaw.log"
BOTDIR="$(cd "$(dirname "$0")" && pwd)"

cd "$BOTDIR" || exit 1

# Check if bot process is alive
is_running() {
    if [ -f "$PIDFILE" ]; then
        pid=$(cat "$PIDFILE" 2>/dev/null)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    # Also check by process name
    pgrep -f "python.*bot\.main.*telegram" > /dev/null 2>&1
    return $?
}

if is_running; then
    exit 0
fi

# Bot is dead — restart it
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Watchdog: bot not running, restarting..."

# Clean up any zombie processes
pkill -9 -f "python.*bot\.main" 2>/dev/null
sleep 2

# Start the bot
nohup python3 -m bot.main --mode telegram >> "$LOGFILE" 2>&1 &
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Watchdog: started PID $!"
