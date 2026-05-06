#!/bin/bash
set -e

echo "=== Lark Bot Cloud Start ==="

# Configure lark-cli
echo "$LARK_APP_SECRET" | lark-cli config init \
  --app-id "$LARK_APP_ID" \
  --app-secret-stdin \
  --brand "${LARK_BRAND:-lark}"

echo "lark-cli configured for app $LARK_APP_ID"

# Start event subscriber
mkdir -p /app/events
lark-cli event +subscribe --as bot \
  --event-types "im.message.receive_v1" \
  --compact --quiet \
  --output-dir ./events 2>/tmp/lark-ws.log &

sleep 3
if [ -f /tmp/lark-ws.log ] && grep -q "error" /tmp/lark-ws.log 2>/dev/null; then
  echo "WebSocket error:"
  cat /tmp/lark-ws.log
fi

echo "Event subscriber started"

# Start bot
exec python3 -u bot.py
