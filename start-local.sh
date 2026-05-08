#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Kill any existing bot processes
echo "检查残留进程..."
pkill -f "python.*bot\.py" 2>/dev/null
pkill -f "lark-cli.*event.*subscribe.*output-dir.*events" 2>/dev/null
sleep 1

# Double check — force kill if still alive
if pgrep -f "python.*bot\.py" >/dev/null 2>&1; then
    pkill -9 -f "python.*bot\.py" 2>/dev/null
    sleep 1
fi

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

# Start event subscriber
mkdir -p "$EVENT_DIR"
lark-cli event +subscribe --as bot \
  --event-types "im.message.receive_v1" \
  --compact --quiet \
  --output-dir ./events 2>/tmp/lark-ws.log &
WS_PID=$!

sleep 3

# Verify subscriber is running
if ! kill -0 $WS_PID 2>/dev/null; then
    echo "错误: lark-cli 事件订阅启动失败"
    exit 1
fi

echo "lark-cli 事件订阅已启动 (PID: $WS_PID)"

# Start bot
python3 -u bot.py &
BOT_PID=$!
echo "bot.py 已启动 (PID: $BOT_PID)"

# Cleanup on exit
trap "kill $WS_PID $BOT_PID 2>/dev/null; echo '已停止'" EXIT INT TERM

wait $BOT_PID
