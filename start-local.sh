#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PLIST="$HOME/Library/LaunchAgents/com.jaker.lark-bot.plist"
PIDFILE="$SCRIPT_DIR/.bot.pid"

# Pause LaunchAgent to prevent it from respawning processes we're about to kill
if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST" 2>/dev/null
    echo "LaunchAgent 已暂停"
fi

# Kill any existing bot processes
echo "检查残留进程..."
pkill -9 -f "python.*bot\.py" 2>/dev/null
pkill -9 -f "lark-cli.*subscribe" 2>/dev/null
sleep 1

# Clear stale event files
rm -f "$SCRIPT_DIR/events/"*.json 2>/dev/null

# Load .env file
if [ -f .env ]; then
  set -a; source .env; set +a
fi

# Load env vars
export LARK_APP_ID="${LARK_APP_ID:-cli_a97a30b1a70a9db9}"
export LARK_APP_SECRET="${LARK_APP_SECRET}"
export MINIMAX_API_KEY="${MINIMAX_API_KEY}"
export EVENT_DIR="$SCRIPT_DIR/events"

# Start bot (bot.py now manages lark-cli subscriber internally)
python3 -u bot.py &
BOT_PID=$!
echo "$BOT_PID" > "$PIDFILE"
echo "bot.py 已启动 (PID: $BOT_PID)"

# Re-enable LaunchAgent (it won't double-start because KeepAlive watches the PID)
if [ -f "$PLIST" ]; then
    launchctl load "$PLIST" 2>/dev/null
fi

# Cleanup on exit
trap "kill $BOT_PID 2>/dev/null; pkill -f 'lark-cli.*event.*subscribe' 2>/dev/null; rm -f '$PIDFILE'; echo '已停止'" EXIT INT TERM

wait $BOT_PID
