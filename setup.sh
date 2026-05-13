#!/bin/bash
# Lark Bot — 一键安装脚本
# 自动检测并安装所有依赖，用户只需运行 ./setup.sh
# 仅支持 macOS

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MEETING_CLI_DIR="$SCRIPT_DIR/meeting-cli"
VENV_DIR="$SCRIPT_DIR/.venv"
LARK_CLI_VERSION="1.0.0"
REQUIRED_PYTHON="3.12"

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

STEP=0
TOTAL=8

step() {
    STEP=$((STEP + 1))
    echo ""
    printf "[${STEP}/${TOTAL}] %s " "$1"
}

ok() {
    printf "${GREEN}✓${NC} %s\n" "$1"
}

warn() {
    printf "${YELLOW}⚠${NC} %s\n" "$1"
}

fail() {
    printf "${RED}✗${NC} %s\n" "$1"
    exit 1
}

installing() {
    printf "${YELLOW}⬇${NC} 安装中... "
}

echo ""
echo "========================================"
echo "  Lark Bot — 一键安装"
echo "========================================"

# ======================================
# 1. macOS 版本检查
# ======================================
step "macOS 版本"

if [[ "$(uname)" != "Darwin" ]]; then
    fail "仅支持 macOS"
fi

MACOS_VERSION=$(sw_vers -productVersion)
MACOS_MAJOR=$(echo "$MACOS_VERSION" | cut -d. -f1)
ARCH=$(uname -m)

# ScreenCaptureKit 需要 macOS 12.3+
if [[ "$MACOS_MAJOR" -lt 12 ]]; then
    fail "需要 macOS 12.3+，当前 $MACOS_VERSION"
fi

ok "$MACOS_VERSION ($ARCH)"

# ======================================
# 2. Xcode CLI Tools
# ======================================
step "Xcode CLI Tools"

if xcode-select -p &>/dev/null; then
    ok "已安装"
else
    installing
    echo ""
    echo "    需要安装 Xcode 命令行工具，请在弹窗中点击「安装」"
    xcode-select --install 2>/dev/null || true
    echo "    等待安装完成后，请重新运行 ./setup.sh"
    exit 0
fi

# ======================================
# 3. Homebrew
# ======================================
step "Homebrew"

if command -v brew &>/dev/null; then
    ok "已安装"
else
    installing
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Apple Silicon brew 路径
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    ok "安装完成"
fi

# 确保 brew 在 PATH 中
if [[ -f /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# ======================================
# 4. Node.js
# ======================================
step "Node.js"

if command -v node &>/dev/null; then
    NODE_VER=$(node --version)
    ok "$NODE_VER"
else
    installing
    brew install node
    NODE_VER=$(node --version)
    ok "$NODE_VER"
fi

# ======================================
# 5. lark-cli (锁定 1.0.0)
# ======================================
step "lark-cli"

NEED_INSTALL=false
if command -v lark-cli &>/dev/null; then
    CURRENT_VER=$(lark-cli --version 2>&1 | grep -o '[0-9]\+\.[0-9]\+\.[0-9]\+' | head -1)
    if [[ "$CURRENT_VER" == "$LARK_CLI_VERSION" ]]; then
        ok "$CURRENT_VER"
    else
        warn "当前 $CURRENT_VER，需要 $LARK_CLI_VERSION，正在降级..."
        NEED_INSTALL=true
    fi
else
    NEED_INSTALL=true
fi

if [[ "$NEED_INSTALL" == "true" ]]; then
    installing
    npm install -g "@larksuite/cli@${LARK_CLI_VERSION}" 2>/dev/null
    ok "$LARK_CLI_VERSION"
fi

# ======================================
# 6. Python 3.12
# ======================================
step "Python $REQUIRED_PYTHON"

PYTHON_BIN=""

# 检查是否已有 Python 3.12
if command -v python3.12 &>/dev/null; then
    PYTHON_BIN=$(which python3.12)
    ok "$(python3.12 --version)"
elif command -v python3 &>/dev/null; then
    PY_VER=$(python3 --version | grep -o '[0-9]\+\.[0-9]\+')
    PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
    if [[ "$PY_MAJOR" -ge 3 && "$PY_MINOR" -ge 12 ]]; then
        PYTHON_BIN=$(which python3)
        ok "Python $PY_VER"
    fi
fi

if [[ -z "$PYTHON_BIN" ]]; then
    installing
    brew install python@$REQUIRED_PYTHON
    # brew 安装的路径
    if [[ -f /opt/homebrew/opt/python@${REQUIRED_PYTHON}/bin/python3.12 ]]; then
        PYTHON_BIN="/opt/homebrew/opt/python@${REQUIRED_PYTHON}/bin/python3.12"
    elif [[ -f /usr/local/opt/python@${REQUIRED_PYTHON}/bin/python3.12 ]]; then
        PYTHON_BIN="/usr/local/opt/python@${REQUIRED_PYTHON}/bin/python3.12"
    else
        PYTHON_BIN=$(which python3.12 2>/dev/null || echo "")
    fi

    if [[ -z "$PYTHON_BIN" ]]; then
        fail "Python $REQUIRED_PYTHON 安装失败"
    fi
    ok "$($PYTHON_BIN --version)"
fi

# ======================================
# 7. Python venv + 依赖
# ======================================
step "Python 依赖"

if [[ -f "$VENV_DIR/bin/python" ]]; then
    # 检查关键依赖是否已装
    if "$VENV_DIR/bin/python" -c "import funasr; import torch" 2>/dev/null; then
        ok "已安装（venv）"
    else
        installing
        "$VENV_DIR/bin/pip" install -q funasr modelscope torch torchaudio numpy soundfile openai 2>/dev/null
        ok "安装完成"
    fi
else
    installing
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install -q --upgrade pip 2>/dev/null
    "$VENV_DIR/bin/pip" install -q funasr modelscope torch torchaudio numpy soundfile openai 2>/dev/null
    ok "安装完成（venv: .venv/）"
fi

# ======================================
# 8. audio_capture (meeting-cli)
# ======================================
step "audio_capture（会议录音）"

AUDIO_BIN="$MEETING_CLI_DIR/audio_capture"
AUDIO_SRC="$MEETING_CLI_DIR/audio_capture.swift"

if [[ -x "$AUDIO_BIN" ]]; then
    ok "已编译"
else
    # 如果 meeting-cli 目录不存在，从 GitHub clone
    if [[ ! -d "$MEETING_CLI_DIR" ]]; then
        installing
        echo ""
        printf "    克隆 meeting-cli... "
        git clone -q https://github.com/AndyJ-2026/meeting-cli.git "$MEETING_CLI_DIR" 2>/dev/null
        echo "完成"
    fi

    if [[ ! -f "$AUDIO_SRC" ]]; then
        fail "audio_capture.swift 未找到: $AUDIO_SRC"
    fi

    printf "    编译 audio_capture... "
    swiftc -O -o "$AUDIO_BIN" "$AUDIO_SRC" \
        -framework ScreenCaptureKit -framework AVFoundation \
        -framework CoreMedia -framework CoreAudio
    ok "编译成功"
fi

# 更新 bot 的 audio_capture 路径（写入 .env）
if ! grep -q "AUDIO_CAPTURE_BIN" "$SCRIPT_DIR/.env" 2>/dev/null; then
    echo "AUDIO_CAPTURE_BIN=$AUDIO_BIN" >> "$SCRIPT_DIR/.env"
fi

# ======================================
# 完成
# ======================================
echo ""
echo "========================================"
echo "  安装完成！"
echo "========================================"
echo ""
echo "  下一步："
echo "  1. 配置 lark-cli（如未做过）："
echo "     lark-cli config init"
echo "     按提示输入 App ID 和 App Secret（从飞书开放平台获取）"
echo ""
echo "  2. 启动机器人："
echo "     ./start-local.sh"
echo ""
echo "  3. 在飞书私聊机器人，完成初始配置"
echo ""
echo "  首次运行时："
echo "  • 系统会弹出屏幕录制和麦克风权限请求"
echo "  • ASR 模型会自动下载（约 800MB，仅首次）"
echo ""
