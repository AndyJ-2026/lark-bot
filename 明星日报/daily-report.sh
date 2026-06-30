#!/bin/bash
# 明星活动每日播报 一键触发
#
# 流程：
#  1. 提示在 Claude 里跑书签（如果今天没跑过）
#  2. 跑 run.py：读最新 CSV → 生成日报 → 上传图床 → 发飞书活动组
#
# 用法：
#  ./daily-report.sh             # 完整跑
#  ./daily-report.sh --dry-run   # 不发送，只看效果
#  ./daily-report.sh --no-upload # 不上传图床（debug 用）

set -euo pipefail
cd "$(dirname "$0")"

BOOKMARK_ID=$(python3 -c "import json; print(json.load(open('config.json'))['bookmark_id'])")
EXPORTS_DIR="$HOME/.datawind-picker/exports"
TODAY=$(date +%Y-%m-%d)

# 检查今天是否已经跑过书签
LATEST=$(ls -1dt "$EXPORTS_DIR"/*零流量监控*_step1 2>/dev/null | head -1 || true)
if [[ -z "$LATEST" ]] || ! [[ "$(basename "$LATEST")" =~ ^$TODAY ]]; then
  cat <<EOF
⚠ 今天还没跑过书签。请在 Claude Code 里执行：

    /mcp__datawind-picker__run bookmarkId=$BOOKMARK_ID

跑完再回来 ./daily-report.sh
EOF
  exit 1
fi

echo "✓ 检测到今日导出：$LATEST"
python3 run.py "$@"
