#!/bin/bash
#
# 通过 LaunchAgent 管理 bot 生命周期，确保只有一个实例运行。
# 用法：
#   ./start-local.sh          重启 bot
#   ./start-local.sh stop     停止 bot
#   ./start-local.sh status   查看状态
#

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_SRC="$SCRIPT_DIR/com.jaker.lark-bot.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.jaker.lark-bot.plist"

case "${1:-restart}" in
  stop)
    launchctl unload "$PLIST_DST" 2>/dev/null
    pkill -9 -f "python.*bot\.py" 2>/dev/null
    pkill -9 -f "lark-cli.*subscribe" 2>/dev/null
    rm -f "$SCRIPT_DIR/.bot.lock"
    echo "已停止"
    ;;

  status)
    if pgrep -f "python.*bot\.py" >/dev/null 2>&1; then
      PID=$(cat "$SCRIPT_DIR/.bot.lock" 2>/dev/null || echo "?")
      echo "运行中 (PID: $PID)"
      pgrep -f "lark-cli.*subscribe" >/dev/null 2>&1 && echo "lark-cli: 在线" || echo "lark-cli: 离线"
    else
      echo "未运行"
    fi
    ;;

  restart|"")
    echo "重启 bot..."

    # 1. 卸载 LaunchAgent（停止 KeepAlive 自动拉起）
    launchctl unload "$PLIST_DST" 2>/dev/null

    # 2. 杀掉所有残留
    pkill -9 -f "python.*bot\.py" 2>/dev/null
    pkill -9 -f "lark-cli.*subscribe" 2>/dev/null
    rm -f "$SCRIPT_DIR/.bot.lock"
    sleep 1

    # 3. 清理旧事件
    rm -f "$SCRIPT_DIR/events/"*.json 2>/dev/null

    # 4. 安装最新 plist（替换路径占位符后复制）
    sed "s|__BOT_DIR__|$SCRIPT_DIR|g" "$PLIST_SRC" > "$PLIST_DST"

    # 5. 加载 LaunchAgent（会自动启动 bot.py）
    launchctl load "$PLIST_DST"
    sleep 3

    # 6. 确认
    if pgrep -f "python.*bot\.py" >/dev/null 2>&1; then
      PID=$(cat "$SCRIPT_DIR/.bot.lock" 2>/dev/null || echo "?")
      echo "bot.py 已启动 (PID: $PID)"
      pgrep -f "lark-cli.*subscribe" >/dev/null 2>&1 && echo "lark-cli: 在线" || echo "lark-cli: 等待连接..."
    else
      echo "启动失败，查看 bot.err:"
      tail -5 "$SCRIPT_DIR/bot.err" 2>/dev/null
    fi
    ;;

  *)
    echo "用法: $0 [restart|stop|status]"
    ;;
esac
