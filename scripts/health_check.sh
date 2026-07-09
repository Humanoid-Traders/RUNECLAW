#!/usr/bin/env bash
# RUNECLAW process health check (deploy-report recommendation).
#
# Verifies the bot process is alive; optionally restarts it and/or alerts via
# Telegram if it has disappeared. Designed to run from cron, e.g. every 5 min:
#
#   */5 * * * * /home/mulerun/runeclaw/scripts/health_check.sh >> /home/mulerun/runeclaw/data/logs/health_check.log 2>&1
#
# Env / flags:
#   RUNECLAW_DIR      repo dir (default: the script's parent dir)
#   RUNECLAW_MODE     bot mode for restart (default: telegram)
#   RUNECLAW_RESTART  1 = auto-restart when down (default: 0 = report only)
#   TELEGRAM_BOT_TOKEN / TELEGRAM_ALERT_CHAT_ID  optional down-alert
#
# Exit code: 0 = healthy (or restarted), 1 = down and not restarted.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNECLAW_DIR="${RUNECLAW_DIR:-$(dirname "$SCRIPT_DIR")}"
RUNECLAW_MODE="${RUNECLAW_MODE:-telegram}"
RUNECLAW_RESTART="${RUNECLAW_RESTART:-0}"
STAMP="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# Match the bot entrypoint specifically (python -m bot.main), not this script
# or an editor, so a false "alive" reading is impossible.
if pgrep -f "bot\.main" >/dev/null 2>&1; then
  echo "$STAMP OK: bot.main is running (pid $(pgrep -f 'bot\.main' | tr '\n' ' '))"
  exit 0
fi

echo "$STAMP DOWN: no bot.main process found"

# Optional Telegram alert (best-effort; never fails the check).
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_ALERT_CHAT_ID:-}" ]; then
  curl -sS -m 10 -o /dev/null \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_ALERT_CHAT_ID}" \
    --data-urlencode "text=🚨 RUNECLAW is DOWN (${STAMP}) — no bot.main process" \
    || echo "$STAMP WARN: telegram alert failed"
fi

if [ "$RUNECLAW_RESTART" = "1" ]; then
  echo "$STAMP RESTART: launching bot.main --mode $RUNECLAW_MODE"
  cd "$RUNECLAW_DIR"
  # nohup + disown so the restarted bot survives this cron shell exiting.
  nohup python -m bot.main --mode "$RUNECLAW_MODE" \
    >> "$RUNECLAW_DIR/data/logs/bot_restart.log" 2>&1 &
  disown || true
  echo "$STAMP RESTART: launched (pid $!)"
  exit 0
fi

exit 1
