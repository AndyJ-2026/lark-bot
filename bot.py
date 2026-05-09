"""
Lark Bot v8 — configurable, onboarding-ready:
- First-time setup via chat (no code editing needed)
- @bot in group → AI reply + execute actions
- 1v1 with bot → direct conversation
- 1v1 "帮我转写" → 录音 + ASR 转写 + AI 纪要 + 飞书文档
- 20:00 → daily work report
- NO background queue, NO token waste
"""

import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

BOT_DIR = os.path.dirname(os.path.abspath(__file__))

# Load .env file (no external dependency)
_env_file = os.path.join(BOT_DIR, ".env")
if os.path.isfile(_env_file):
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

CONFIG_FILE = os.path.join(BOT_DIR, "config.json")
ONBOARDED_FILE = os.path.join(BOT_DIR, "onboarded_users.json")
REMINDERS_FILE = os.path.join(BOT_DIR, "reminders.json")
EVENT_DIR = os.environ.get("EVENT_DIR", os.path.join(BOT_DIR, "events"))
AUDIO_CAPTURE_BIN = os.environ.get("AUDIO_CAPTURE_BIN", os.path.expanduser("~/meeting-cli/audio_capture"))
TRANSCRIBE_SCRIPT = os.path.join(BOT_DIR, "transcribe_qwen.py")
VENV_PYTHON = os.path.join(BOT_DIR, ".venv", "bin", "python")
VAULT_DIR = os.environ.get("MEETING_VAULT", os.path.expanduser("~/Documents/Obsidian Vault"))
NOTES_FOLDER = os.environ.get("MEETING_NOTES_FOLDER", "会议纪要")
TMPDIR_MEETING = os.path.join(os.environ.get("TMPDIR", "/tmp"), "meeting-cli")

# Bot's own open_id (to filter out self-messages)
_BOT_OPEN_ID = None


def _init_bot_open_id():
    global _BOT_OPEN_ID
    try:
        result = subprocess.run(
            ["lark-cli", "api", "GET", "/open-apis/bot/v3/info", "--as", "bot"],
            capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            _BOT_OPEN_ID = data.get("bot", {}).get("open_id", "")
            if _BOT_OPEN_ID:
                return
    except Exception:
        pass
    _BOT_OPEN_ID = ""


# ============================================================
# Config
# ============================================================

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return None


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


CONFIG = load_config()

# Derived from config (updated after setup completes)
BOT_NAME = CONFIG["bot_name"] if CONFIG and CONFIG.get("bot_name") else "助手"
OWNER_NAME = CONFIG["owner_name"] if CONFIG and CONFIG.get("owner_name") else ""
OWNER_OPEN_ID = CONFIG["owner_open_id"] if CONFIG and CONFIG.get("owner_open_id") else ""
LLM_MODEL = CONFIG["llm_model"] if CONFIG and CONFIG.get("llm_model") else "MiniMax-M2.5"

# LLM client — initialized lazily after config is ready
_llm_client = None


def _get_llm_client():
    global _llm_client
    if _llm_client:
        return _llm_client
    if not CONFIG:
        return None
    from openai import OpenAI
    api_key = CONFIG.get("llm_api_key") or os.environ.get("MINIMAX_API_KEY", "")
    base_url = CONFIG.get("llm_base_url", "https://api.minimaxi.com/v1")
    if not api_key:
        return None
    _llm_client = OpenAI(api_key=api_key, base_url=base_url)
    return _llm_client


def _reload_config():
    """Reload config and update globals after setup."""
    global CONFIG, BOT_NAME, OWNER_NAME, OWNER_OPEN_ID, LLM_MODEL, _llm_client
    CONFIG = load_config()
    if CONFIG:
        BOT_NAME = CONFIG.get("bot_name", "助手")
        OWNER_NAME = CONFIG.get("owner_name", "")
        OWNER_OPEN_ID = CONFIG.get("owner_open_id", "")
        LLM_MODEL = CONFIG.get("llm_model", "MiniMax-M2.5")
        _llm_client = None  # force re-init


# ============================================================
# Prompts (template functions, use current config values)
# ============================================================

def _reply_prompt():
    return f"""你是 {OWNER_NAME} 的飞书 AI 助手，名叫"{BOT_NAME}"。你在群里代替 {OWNER_NAME} 跟同事互动。

性格：热情、靠谱、略带幽默感，像朋友之间聊天一样说话。

你能执行的操作（通过 action 触发）：
- meeting: 约会议 → params: summary, start(ISO8601+08:00), duration(如1h), people(参会人名字列表,可选,系统自动查找open_id)
- cancel_meeting: 取消会议 → params: keyword
- search_user: 查找同事 → params: query
- digest: 消息汇总或定向分析 → params: query(用户原始问题，通用汇总时留空), days(时间范围天数，AI根据问题推断，默认7)
  - 通用汇总（"汇总一下"、"有什么消息"）→ query留空，不需要days
  - 定向分析（"帮我看看XX提了什么"、"分析一下XX内容"）→ query填用户原始问题，days根据上下文推断
- remind: 设置提醒 → params: time(ISO8601+08:00), message(提醒内容)
  - "明天中午提醒我写周报" → time: 明天12:00的ISO8601, message: "写周报"
  - "下午3点提醒我开会" → time: 今天15:00的ISO8601, message: "开会"
- daily_report: 查看今日工作日报 → params: {{}}
  - "日报"、"今天工作怎么样" → daily_report
- task_create: 创建任务 → params: summary(标题), description(描述,可选), due(截止日期ISO8601,可选), people(涉及的人名列表,可选)
  - "帮我建个任务 XXX"、"记一下 XXX"、"帮我和XX建个任务" → task_create
  - 不需要先 search_user，直接把人名放 people 参数，系统会自动查找
- task_list: 查看待办任务 → params: query(搜索关键词,可选)
  - "我的待办"、"有什么任务" → task_list
- task_complete: 完成任务 → params: task_id
- check_calendar: 查看日程 → params: start(ISO8601,可选), end(ISO8601,可选)
  - "明天有什么会"、"下周三有空吗"、"看看日程" → check_calendar
- read_doc: 读取飞书文档 → params: doc(文档链接或token)
  - 用户发送飞书文档链接或 ID → read_doc
- transcribe_start: 开始会议转写/录音 → params: {{}}
  - "帮我转写"、"开始录音" → transcribe_start
- transcribe_stop: 停止转写/录音并生成纪要 → params: {{}}
  - "结束转写"、"停止录音"、"结束" → transcribe_stop
- none: 不需要操作

原则：
- 能办的直接办，不推给 {OWNER_NAME}，不要问确认，直接执行
- 约会议：推断主题，时间转 ISO8601，未指定时长默认 1h，参会人放 people
- 建任务：直接建，不要问"需要补充吗"，建完说一句就行
- "晚上10点"→ 当天22:00:00+08:00
- 不用 markdown，纯文本
- 失败要说明原因
- 绝对不要在执行 action 之前问用户确认，信息不全就用合理默认值

当前时间：__NOW__

JSON 输出（只输出 JSON）：
{{"reply": "纯文本回复", "action": "meeting/cancel_meeting/search_user/digest/daily_report/task_create/task_list/task_complete/check_calendar/read_doc/remind/transcribe_start/transcribe_stop/none", "params": {{}}}}"""


def _chat_prompt():
    return f"""你是 {OWNER_NAME} 的私人 AI 助手"{BOT_NAME}"。1v1 聊天模式。
- 聪明靠谱，语气轻松
- 不用 markdown，纯文本
- 约会议/设提醒时间用 ISO8601+08:00

你能执行的操作（通过 action 触发）：
- meeting: 约会议 → params: summary, start(ISO8601+08:00), duration(如1h), people(参会人名字列表,可选)
- cancel_meeting: 取消会议 → params: keyword
- search_user: 查找同事 → params: query
- digest: 消息汇总或定向分析 → params: query(用户原始问题，通用汇总时留空), days(时间范围天数，默认7)
  - "汇总一下"、"有什么消息"、"最近有什么" → digest，query留空
  - "帮我看看XX提了什么"、"分析一下XX" → digest，query填原始问题
- daily_report: 查看今日工作日报 → params: {{}}
  - "日报"、"今天工作怎么样"、"今天有什么" → daily_report
- task_create: 创建任务 → params: summary(标题), description(描述,可选), due(截止日期ISO8601,可选), people(涉及的人名列表,可选)
  - "帮我建个任务 XXX"、"记一下 XXX"、"帮我和XX建个任务" → task_create
  - 不需要先 search_user，直接把人名放 people 参数，系统会自动查找
- task_list: 查看待办任务 → params: query(搜索关键词,可选)
  - "我的待办"、"有什么任务" → task_list
- task_complete: 完成任务 → params: task_id
- check_calendar: 查看日程 → params: start(ISO8601,可选), end(ISO8601,可选)
  - "明天有什么会"、"下周三有空吗"、"看看日程" → check_calendar
- read_doc: 读取飞书文档 → params: doc(文档链接或token)
  - 用户发送飞书文档链接或 ID → read_doc
- remind: 设置提醒 → params: time(ISO8601+08:00), message(提醒内容)
- transcribe_start: 开始会议转写/录音 → params: {{}}
  - "帮我转写"、"开始录音" → transcribe_start
- transcribe_stop: 停止转写/录音并生成纪要 → params: {{}}
  - "结束转写"、"停止录音"、"结束" → transcribe_stop
- none: 不需要操作

当前时间：__NOW__

JSON 输出：
{{"reply": "纯文本回复", "action": "meeting/cancel_meeting/search_user/digest/daily_report/task_create/task_list/task_complete/check_calendar/read_doc/remind/transcribe_start/transcribe_stop/none", "params": {{}}}}"""


def _report_prompt():
    return f"""根据以下信息生成简洁的每日工作日报。

今日日程：
__CALENDAR__

今日群消息摘要（与 {OWNER_NAME} 相关的）：
__MESSAGES__

要求：中文口语化，分"今天干了啥"和"还得跟进"两块，每条一句话，结尾来一句轻松的话。"""


def _digest_prompt():
    return f"""以下是最近群聊中与 {OWNER_NAME} 相关的消息。请帮忙汇总：
- 谁找了 {OWNER_NAME}、什么事
- 有哪些需要 {OWNER_NAME} 跟进的
- 简洁明了，纯文本

消息列表：
__MESSAGES__"""


MINUTES_PROMPT = """你是一个专业的会议纪要助手。根据以下会议转写内容，生成一份结构化的会议纪要（Markdown 格式）。

要求：
1. 用中文输出
2. 按以下格式组织：

# 会议纪要

## 基本信息
- 日期：__DATE__
- 时长：__DURATION__

## 会议要点
（按讨论顺序，列出 3-5 个关键议题及结论）

## 决策事项
（明确列出达成的决定）

## 待办事项
（格式：- 事项内容 @负责人）

## 关键讨论记录
（保留重要的讨论细节和观点）
"""

MINUTES_SUMMARY_PROMPT = """根据以下会议纪要，用一两句话概括会议主题和核心结论。纯文本，不要 markdown。

纪要内容：
__MINUTES__"""

ANALYSIS_PROMPT = """你是飞书群消息分析助手。根据用户的问题，从群聊消息中提取相关信息并回答。

用户问题：__QUERY__

以下是群里最近的消息记录：
__MESSAGES__

要求：
- 只回答与用户问题相关的内容
- 引用具体的人和消息内容
- 纯文本输出，禁止使用任何 markdown 语法（不要用 #、>、**、|---|、```等）
- 用换行和短横线分隔内容，用数字编号代替标题
- 如果消息中没有相关内容，如实说明"""


def _owner_pattern():
    """动态生成 owner 名字匹配正则"""
    if not OWNER_NAME:
        return None
    parts = [re.escape(OWNER_NAME), re.escape(f"@{OWNER_NAME}")]
    # Handle names with spaces (e.g. "Jake R" → also match "JakeR")
    if " " in OWNER_NAME:
        parts.append(re.escape(OWNER_NAME.replace(" ", "")))
    return re.compile("|".join(parts), re.IGNORECASE)


# ============================================================
# AI
# ============================================================

# ---- Chat history for context-aware 1v1 conversations ----
_chat_history = {}  # sender_id → [{"role": ..., "content": ...}, ...]
_CHAT_HISTORY_MAX = 10  # max messages per user (5 rounds)
_CHAT_HISTORY_TTL = 3600  # expire after 1 hour of inactivity
_chat_history_ts = {}  # sender_id → last activity timestamp


def _get_chat_history(sender_id):
    # Expire stale history
    if sender_id in _chat_history_ts:
        if time.time() - _chat_history_ts[sender_id] > _CHAT_HISTORY_TTL:
            _chat_history.pop(sender_id, None)
            _chat_history_ts.pop(sender_id, None)
    return _chat_history.get(sender_id, [])


def _append_chat_history(sender_id, role, content):
    if sender_id not in _chat_history:
        _chat_history[sender_id] = []
    _chat_history[sender_id].append({"role": role, "content": content})
    # Trim to max
    if len(_chat_history[sender_id]) > _CHAT_HISTORY_MAX:
        _chat_history[sender_id] = _chat_history[sender_id][-_CHAT_HISTORY_MAX:]
    _chat_history_ts[sender_id] = time.time()


def call_ai(system_prompt, user_msg, history=None):
    client = _get_llm_client()
    if not client:
        log("AI error: no LLM client configured")
        return None
    now = datetime.now().strftime("%Y-%m-%d %H:%M %A")
    prompt = system_prompt.replace("__NOW__", now)
    messages = [{"role": "system", "content": prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_msg})
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=0.5,
        )
        content = resp.choices[0].message.content.strip()
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        content = re.sub(r"<tool_code>.*?</tool_code>", "", content, flags=re.DOTALL).strip()
        # Handle MiniMax tool_call XML format → convert to JSON
        tc_match = re.search(r"<minimax:tool_call>(.*?)</minimax:tool_call>", content, re.DOTALL)
        if tc_match:
            tc_xml = tc_match.group(1)
            action_match = re.search(r'<invoke\s+name="(\w+)"', tc_xml)
            if action_match:
                action = action_match.group(1)
                params = {}
                for pm in re.finditer(r'<parameter\s+name="(\w+)">(.*?)</parameter>', tc_xml, re.DOTALL):
                    params[pm.group(1)] = pm.group(2).strip()
                content = json.dumps({"reply": f"好的，正在处理~", "action": action, "params": params})
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return content
    except Exception as e:
        log(f"AI error: {e}")
        return None


def parse_ai(raw):
    if not raw:
        return {"reply": "稍等哈～", "action": "none", "params": {}}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        for i, ch in enumerate(raw):
            if ch == '{' and '"reply"' in raw[i:]:
                try:
                    return json.loads(raw[i:])
                except json.JSONDecodeError:
                    continue
        return {"reply": raw, "action": "none", "params": {}}


# ============================================================
# Lark CLI
# ============================================================

def lark_cmd(args):
    try:
        result = subprocess.run(
            ["lark-cli"] + args,
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        else:
            log(f"lark-cli err: {result.stderr.strip()[:200]}")
            return None
    except Exception as e:
        log(f"lark-cli exc: {e}")
        return None


def reply_in_chat(chat_id, text):
    result = lark_cmd(["im", "+messages-send", "--as", "bot", "--chat-id", chat_id, "--text", text])
    log("Replied" if result and result.get("ok") else "Reply failed")


def send_dm(text):
    if not OWNER_OPEN_ID:
        log("DM skipped: no owner configured")
        return
    result = lark_cmd(["im", "+messages-send", "--as", "bot", "--user-id", OWNER_OPEN_ID, "--text", text])
    log("DM sent" if result and result.get("ok") else "DM failed")


def send_dm_markdown(text):
    if not OWNER_OPEN_ID:
        return
    result = lark_cmd([
        "im", "+messages-send", "--as", "bot",
        "--user-id", OWNER_OPEN_ID,
        "--markdown", text,
    ])
    log("DM (markdown) sent" if result and result.get("ok") else "DM (markdown) failed")


# ============================================================
# Setup mode (no config.json → interactive setup via chat)
# ============================================================

_setup_state = {
    "step": "init",  # init → wait_bot_name → wait_api_key → done
    "owner_open_id": "",
    "owner_name": "",
    "bot_name": "",
    "chat_id": "",
}


_user_name_cache = {}

def _get_user_name(open_id):
    """通过 lark-cli 获取用户姓名"""
    if open_id in _user_name_cache:
        return _user_name_cache[open_id]
    result = lark_cmd(["api", "GET", f"/open-apis/contact/v3/users/{open_id}",
                       "--as", "bot", "--params", json.dumps({"user_id_type": "open_id"})])
    if result and result.get("code") == 0:
        name = result.get("data", {}).get("user", {}).get("name", "")
        if name:
            _user_name_cache[open_id] = name
            return name
    # Fallback: try search
    result = lark_cmd(["contact", "+search-user", "--query", open_id])
    if result and result.get("ok"):
        users = result.get("data", {}).get("users", [])
        if users:
            name = users[0].get("name", "")
            _user_name_cache[open_id] = name
            return name
    return ""


def _resolve_people(names):
    """Resolve a list of names to open_ids. Returns [(open_id, name), ...]"""
    resolved = []
    if isinstance(names, str):
        names = [names]
    for name in names:
        if not name:
            continue
        result = lark_cmd(["contact", "+search-user", "--query", name])
        if result and result.get("ok"):
            users = result.get("data", {}).get("users", [])
            if users:
                uid = users[0].get("open_id", "")
                uname = users[0].get("name", name)
                if uid:
                    _user_name_cache[uid] = uname
                    resolved.append((uid, uname))
                    log(f"Resolved '{name}' → {uname} ({uid[:16]})")
    return resolved


def process_setup(event):
    """配置模式：硬编码对话流程，不需要 LLM"""
    sender_id = event.get("sender_id", "")
    raw_content = event.get("content") or ""
    chat_id = event.get("chat_id") or ""
    chat_type = event.get("chat_type") or "group"

    # 配置模式只处理私聊
    if chat_type != "p2p":
        return

    text = raw_content.strip()

    if _setup_state["step"] == "init":
        # 第一个私聊的人成为 owner
        _setup_state["owner_open_id"] = sender_id
        _setup_state["chat_id"] = chat_id

        # 尝试获取用户姓名
        name = _get_user_name(sender_id)
        _setup_state["owner_name"] = name

        greeting = f"你好{' ' + name if name else ''}！" if name else "你好！"
        reply_in_chat(chat_id, f"{greeting}我是你的新助手，还没有名字呢。\n你想叫我什么？")
        _setup_state["step"] = "wait_bot_name"
        return

    if _setup_state["step"] == "wait_bot_name":
        if not text:
            reply_in_chat(chat_id, "名字不能为空，再说一次？")
            return
        _setup_state["bot_name"] = text
        reply_in_chat(chat_id,
            f"好的，我叫「{text}」！\n\n"
            "接下来需要配置大模型 API Key，这样我才能理解你说的话。\n"
            "推荐使用 MiniMax（https://www.minimax.chat）\n\n"
            "请发送你的 API Key：")
        _setup_state["step"] = "wait_api_key"
        return

    if _setup_state["step"] == "wait_api_key":
        if not text or len(text) < 10:
            reply_in_chat(chat_id, "这个不像是有效的 API Key，再检查一下？")
            return

        # 保存配置
        cfg = {
            "bot_name": _setup_state["bot_name"],
            "owner_name": _setup_state["owner_name"],
            "owner_open_id": _setup_state["owner_open_id"],
            "llm_api_key": text,
            "llm_base_url": "https://api.minimaxi.com/v1",
            "llm_model": "MiniMax-M2.5",
        }
        save_config(cfg)
        _reload_config()
        _setup_state["step"] = "done"

        reply_in_chat(chat_id,
            f"配置完成！我叫{BOT_NAME}，{OWNER_NAME}你好~")

        # 标记 owner 已引导 + 发送快速开始卡片
        mark_onboarded(sender_id)
        send_welcome_card(chat_id)
        log(f"Setup complete: bot_name={BOT_NAME}, owner={OWNER_NAME}")
        return


# ============================================================
# Onboarding (welcome card + new user detection)
# ============================================================

def load_onboarded():
    if os.path.exists(ONBOARDED_FILE):
        try:
            with open(ONBOARDED_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"users": []}


def save_onboarded(data):
    with open(ONBOARDED_FILE, "w") as f:
        json.dump(data, f, indent=2)


def is_onboarded(user_id):
    data = load_onboarded()
    return user_id in data.get("users", [])


def mark_onboarded(user_id):
    data = load_onboarded()
    if user_id not in data["users"]:
        data["users"].append(user_id)
        save_onboarded(data)


def _get_bot_chats():
    """获取 Bot 已加入的群列表"""
    result = lark_cmd(["im", "chats", "list", "--as", "bot",
                       "--params", json.dumps({"page_size": 20})])
    if not result:
        return []
    items = result.get("data", {}).get("items", [])
    return [c.get("name", "未知群") for c in items if c.get("name")]


def _build_welcome_card(chat_id=None):
    """构建快速开始卡片"""
    chats = _get_bot_chats()
    chat_list = "\n".join(f"• {name}" for name in chats) if chats else "（还没有加入任何群）"

    content = (
        f"👋 你好！我是**{BOT_NAME}**，{OWNER_NAME} 的飞书 AI 助手\n\n"
        "以下是我能做的事：\n\n"
        "✅ 闲聊对话 — 直接跟我说话就行\n"
        "✅ 约会议 — \"帮我约个会\"\n"
        "✅ 取消会议 — \"取消那个会议\"\n"
        "✅ 查同事 — \"查一下 xxx\"\n"
        "✅ 设提醒 — \"明天中午提醒我写周报\"\n"
        "⚠️ 消息汇总 — \"汇总一下\"（需把我拉进群）\n"
        "⚠️ 定向分析 — \"帮我看看 XX 提了什么\"（需把我拉进群）\n"
        "🎙️ 会议转写 — \"帮我转写\"（需本机运行 Bot）\n\n"
        f"**我已加入的群：**\n{chat_list}\n"
        "（没有你需要的群？把我拉进去就行）\n\n"
        f"💡 随时跟我说「帮助」可以再看这张表"
    )

    return json.dumps({"elements": [{"tag": "markdown", "content": content}]})


def send_welcome_card(chat_id):
    """发送快速开始卡片"""
    card = _build_welcome_card(chat_id)
    result = lark_cmd([
        "im", "+messages-send", "--as", "bot",
        "--chat-id", chat_id,
        "--msg-type", "interactive",
        "--content", card,
    ])
    log("Welcome card sent" if result and result.get("ok") else "Welcome card failed")


HELP_TRIGGERS = {"help", "帮助", "你能做什么", "你会什么"}
CHAT_LIST_KEYWORDS = ["哪些群", "加了什么群", "在哪些群", "群列表", "加入的群"]


# ============================================================
# Actions
# ============================================================

def do_meeting(params, chat_id, sender_id=""):
    start = params.get("start", "")
    summary = params.get("summary", "会议")
    if not start:
        return False
    duration = params.get("duration", "1h")
    try:
        dt_start = datetime.fromisoformat(start)
        hours, mins = 0, 0
        h_m = re.search(r"(\d+)h", duration)
        m_m = re.search(r"(\d+)m", duration)
        if h_m: hours = int(h_m.group(1))
        if m_m: mins = int(m_m.group(1))
        if not hours and not mins: hours = 1
        end = (dt_start + timedelta(hours=hours, minutes=mins)).isoformat()
    except Exception:
        end = start
    args = ["calendar", "+create", "--summary", summary, "--start", start, "--end", end]
    attendees = list(params.get("attendees", []))
    # Resolve people names to open_ids
    for uid, _ in _resolve_people(params.get("people", [])):
        if uid not in attendees:
            attendees.append(uid)
    if sender_id and sender_id not in attendees:
        attendees.append(sender_id)
    if OWNER_OPEN_ID and OWNER_OPEN_ID not in attendees:
        attendees.append(OWNER_OPEN_ID)
    if attendees:
        args += ["--attendee-ids", ",".join(attendees)]
    result = lark_cmd(args)
    ok = result and result.get("ok")
    if ok:
        # Build meeting card
        time_str = f"{start[5:16]} ~ {end[11:16]}"
        attendee_names = []
        for aid in attendees:
            name = _get_user_name(aid)
            if name:
                attendee_names.append(name)
        card_content = (
            f"📅 **会议已创建**\n\n"
            f"**{summary}**\n\n"
            f"🕐 {time_str}\n"
        )
        if attendee_names:
            card_content += f"👥 参会人：{'、'.join(attendee_names)}\n"
        card = json.dumps({"elements": [{"tag": "markdown", "content": card_content}]})

        # Send card to the chat where it was requested
        if chat_id:
            _send_card(chat_id, card)

        # DM each attendee (except the sender)
        for aid in attendees:
            if aid != sender_id:
                _send_card_to_user(aid, card)
        log(f"Meeting card sent to chat + {len(attendees)} attendees")
    elif not ok and chat_id:
        reply_in_chat(chat_id, "会议创建失败了，可能是权限问题")
    log(f"Meeting {'ok' if ok else 'failed'}: {summary}")
    return ok


def do_cancel_meeting(params, chat_id):
    keyword = params.get("keyword", "会议")
    result = lark_cmd(["calendar", "+agenda"])
    if not result or not result.get("ok"):
        if chat_id: reply_in_chat(chat_id, "没找到可以取消的会议")
        return False
    events = result.get("data", [])
    target = None
    for e in events:
        if keyword in e.get("summary", "") or "会议" in e.get("summary", ""):
            target = e
            break
    if not target and events:
        target = events[-1]
    if not target:
        if chat_id: reply_in_chat(chat_id, "今天没有匹配的会议")
        return False
    event_id = target.get("event_id", "")
    cal_id = target.get("organizer_calendar_id", "")
    del_result = lark_cmd(["calendar", "events", "delete",
                           "--params", json.dumps({"calendar_id": cal_id or "primary", "event_id": event_id})])
    ok = del_result and del_result.get("code", -1) == 0
    if ok and chat_id:
        reply_in_chat(chat_id, f"已取消: {target.get('summary', '')}")
    elif not ok and chat_id:
        reply_in_chat(chat_id, "取消失败，可能需要手动操作")
    return ok


def do_search_user(params, chat_id):
    query = params.get("query", "")
    if not query:
        return False
    result = lark_cmd(["contact", "+search-user", "--query", query])
    if result and result.get("ok") and chat_id:
        users = result.get("data", {}).get("users", [])
        if users:
            lines = ["找到了:"]
            for u in users[:3]:
                name = u.get("name", "?")
                dept = u.get("department_name", "")
                email = u.get("email") or u.get("enterprise_email", "")
                lines.append(f"- {name} {dept} {email}")
            reply_in_chat(chat_id, "\n".join(lines))
        else:
            reply_in_chat(chat_id, f"没找到「{query}」")
    return True


def _build_task_card(summary, description="", due="", task_id="", assignee_name=""):
    lines = [f"📋 **任务已创建**\n\n**{summary}**"]
    if description:
        lines.append(f"\n{description}")
    if due:
        # Format due date for display
        try:
            dt = datetime.fromisoformat(due)
            lines.append(f"\n⏰ 截止：{dt.strftime('%m月%d日 %H:%M')}")
        except ValueError:
            lines.append(f"\n⏰ 截止：{due}")
    if assignee_name:
        lines.append(f"\n👤 负责人：{assignee_name}")
    if task_id:
        lines.append(f"\n🔗 任务 ID：{task_id}")
    content = "".join(lines)
    return json.dumps({"elements": [{"tag": "markdown", "content": content}]})


def _send_card(chat_id, card):
    result = lark_cmd([
        "im", "+messages-send", "--as", "bot",
        "--chat-id", chat_id,
        "--msg-type", "interactive",
        "--content", card,
    ])
    return result and result.get("ok")


def _send_card_to_user(user_id, card):
    result = lark_cmd([
        "im", "+messages-send", "--as", "bot",
        "--user-id", user_id,
        "--msg-type", "interactive",
        "--content", card,
    ])
    ok = result and result.get("ok")
    if not ok:
        log(f"Cannot DM user {user_id} (user may not have chatted with bot yet)")
    return ok


def do_task_create(params, chat_id, sender_id=""):
    summary = params.get("summary", "")
    if not summary:
        if chat_id: reply_in_chat(chat_id, "任务标题不能为空")
        return False

    is_owner = sender_id == OWNER_OPEN_ID

    # Non-owner can only create tasks that involve the owner
    # (this bot is the owner's assistant, not a generic task tool)
    if not is_owner:
        # Check if they're trying to assign to themselves only
        assignee_param = params.get("assignee", "")
        if assignee_param == sender_id or not assignee_param:
            if chat_id:
                reply_in_chat(chat_id, f"你可以给 {OWNER_NAME} 布置任务，但不能给自己建哦~\n试试：\"帮我给 {OWNER_NAME} 建个任务 XXX\"")
            return False

    # Build members list
    members = []
    member_ids = set()
    # Owner is always an assignee
    members.append({"id": OWNER_OPEN_ID, "role": "assignee", "type": "user"})
    member_ids.add(OWNER_OPEN_ID)
    # If a non-owner creates the task, they're also an assignee
    if not is_owner and sender_id and sender_id not in member_ids:
        members.append({"id": sender_id, "role": "assignee", "type": "user"})
        member_ids.add(sender_id)
    # Resolve people names to open_ids
    for uid, _ in _resolve_people(params.get("people", [])):
        if uid not in member_ids:
            members.append({"id": uid, "role": "assignee", "type": "user"})
            member_ids.add(uid)

    args = ["task", "+create", "--as", "bot", "--summary", summary]
    desc = params.get("description", "")
    if desc:
        args += ["--description", desc]
    due = params.get("due", "")
    if due:
        args += ["--due", due]
    args += ["--data", json.dumps({"members": members})]

    log(f"Task create: {summary}, members: {[m['id'][:16] for m in members]}")
    result = lark_cmd(args)
    if result and result.get("ok"):
        task_data = result.get("data", {})
        task_id = task_data.get("guid", "") or task_data.get("id", "")

        # Build assignee display names
        names = []
        for m in members:
            mid = m["id"]
            if mid == OWNER_OPEN_ID:
                names.append(OWNER_NAME)
            else:
                n = _get_user_name(mid) or mid[:12]
                names.append(n)
        assignee_name = "、".join(names)

        card = _build_task_card(summary, desc, due, task_id, assignee_name)

        # Send card to the chat where it was requested
        # (assignees get notified automatically by Lark's built-in Task Assistant)
        if chat_id:
            _send_card(chat_id, card)

        log(f"Task created: {task_id} {summary}")
        return True
    log(f"Task create failed: {result}")
    if chat_id: reply_in_chat(chat_id, "任务创建失败")
    return False


def do_task_list(params, chat_id):
    args = ["task", "+get-my-tasks"]
    query = params.get("query", "")
    if query:
        args += ["--query", query]
    result = lark_cmd(args)
    if result and result.get("ok"):
        tasks = result.get("data", {}).get("items", [])
        if not tasks:
            if chat_id: reply_in_chat(chat_id, "没有待办任务，清净~")
            return True
        lines = ["你的待办："]
        for t in tasks[:10]:
            summary = t.get("summary", "无标题")
            due = t.get("due", {})
            due_str = ""
            if due and due.get("date"):
                due_str = f" (截止 {due['date']})"
            lines.append(f"- {summary}{due_str}")
        if len(tasks) > 10:
            lines.append(f"...还有 {len(tasks) - 10} 个")
        if chat_id: reply_in_chat(chat_id, "\n".join(lines))
        return True
    if chat_id: reply_in_chat(chat_id, "获取任务列表失败")
    return False


def do_task_complete(params, chat_id):
    task_id = params.get("task_id", "")
    if not task_id:
        if chat_id: reply_in_chat(chat_id, "需要任务 ID 才能完成任务")
        return False
    result = lark_cmd(["task", "+complete", "--task-id", task_id])
    if result and result.get("ok"):
        if chat_id: reply_in_chat(chat_id, "任务已完成!")
        return True
    if chat_id: reply_in_chat(chat_id, "完成任务失败")
    return False


def do_check_calendar(params, chat_id):
    start = params.get("start", "")
    end = params.get("end", "")
    args = ["calendar", "+agenda"]
    if start:
        args += ["--start", start]
    if end:
        args += ["--end", end]
    result = lark_cmd(args)
    if result and result.get("ok"):
        events = result.get("data", [])
        if not events:
            if chat_id: reply_in_chat(chat_id, "那段时间没有日程，空的")
            return True
        lines = []
        for e in events:
            s = e.get("start_time", {}).get("datetime", "?")
            end_t = e.get("end_time", {}).get("datetime", "?")
            summary = e.get("summary", "无标题")
            lines.append(f"- {s[5:16]}~{end_t[11:16]} {summary}")
        if chat_id: reply_in_chat(chat_id, "\n".join(lines))
        return True
    if chat_id: reply_in_chat(chat_id, "日程获取失败")
    return False


def do_read_doc(params, chat_id):
    doc = params.get("doc", "")
    if not doc:
        if chat_id: reply_in_chat(chat_id, "请发送文档链接或 ID")
        return False
    result = lark_cmd(["docs", "+fetch", "--doc", doc])
    if result and result.get("ok"):
        content = result.get("data", {}).get("markdown", "") or result.get("data", {}).get("content", "")
        if not content:
            content = "文档内容为空"
        # Summarize if too long
        if len(content) > 2000:
            summary = call_ai("你是文档摘要助手。用纯文本简要概括以下文档内容，保留关键信息。", content[:8000])
            if summary and chat_id:
                reply_in_chat(chat_id, f"文档概要：\n\n{summary}")
            elif chat_id:
                reply_in_chat(chat_id, content[:2000] + "\n\n...（内容过长，已截断）")
        elif chat_id:
            reply_in_chat(chat_id, content)
        return True
    if chat_id: reply_in_chat(chat_id, "文档读取失败，检查一下链接或 ID？")
    return False


def _pull_chat_messages(chat_id, since):
    all_messages = []
    page_token = ""
    for _ in range(10):
        args = ["im", "+chat-messages-list", "--chat-id", chat_id,
                "--as", "bot", "--start", since, "--page-size", "50"]
        if page_token:
            args += ["--page-token", page_token]
        result = lark_cmd(args)
        if not result or not result.get("ok"):
            break
        data = result.get("data", {})
        all_messages.extend(data.get("messages", []))
        if not data.get("has_more"):
            break
        page_token = data.get("page_token", "")
        if not page_token:
            break
    return all_messages


def _format_message(m, chat_name=""):
    sender_name = m.get("sender", {}).get("name", "未知")
    content = m.get("content", "") or ""
    ts = m.get("create_time", "")
    prefix = f"[{chat_name}] " if chat_name else ""
    return f"{prefix}{ts} {sender_name}: {content[:200]}"


def do_digest(chat_id, query="", days=0, chat_type="group"):
    if query:
        if chat_id:
            reply_in_chat(chat_id, "我去翻翻消息，帮你分析一下~")
        if not days:
            days = 7
        since = (datetime.now() - timedelta(days=days)).isoformat()

        if chat_type == "p2p":
            chats_result = lark_cmd(["im", "chats", "list", "--as", "bot",
                                     "--params", json.dumps({"page_size": 20})])
            messages = []
            if chats_result:
                for c in chats_result.get("data", {}).get("items", []):
                    cid = c.get("chat_id", "")
                    cname = c.get("name", "未知群")
                    if not cid:
                        continue
                    chat_msgs = _pull_chat_messages(cid, since)
                    for m in chat_msgs:
                        m["_chat_name"] = cname
                    messages.extend(chat_msgs)
        else:
            messages = _pull_chat_messages(chat_id, since)

        if not messages:
            if chat_id: reply_in_chat(chat_id, f"最近 {days} 天没找到消息")
            return True
        msg_lines = [_format_message(m, m.get("_chat_name", "")) for m in messages]
        msg_text = "\n".join(msg_lines)
        prompt = ANALYSIS_PROMPT.replace("__QUERY__", query).replace("__MESSAGES__", msg_text)
        raw = call_ai(prompt, "请分析")
        summary = raw or "分析失败，请稍后再试"
        if chat_id:
            reply_in_chat(chat_id, summary)
        log(f"Analysis: {len(messages)} messages, query={query[:40]}")
        return True

    # General summary: owner-related messages
    if chat_id:
        reply_in_chat(chat_id, "我去翻翻最近的消息~")

    chats_result = lark_cmd(["im", "chats", "list", "--as", "bot",
                             "--params", json.dumps({"page_size": 20})])
    if not chats_result:
        if chat_id: reply_in_chat(chat_id, "拉群列表失败了")
        return False

    chat_list = chats_result.get("data", {}).get("items", [])
    if not chat_list:
        if chat_id: reply_in_chat(chat_id, "bot 还没加入任何群")
        return False

    since = (datetime.now() - timedelta(days=2)).isoformat()
    owner_messages = []
    pattern = _owner_pattern()

    for c in chat_list:
        cid = c.get("chat_id", "")
        cname = c.get("name", "未知群")
        if not cid:
            continue
        messages = _pull_chat_messages(cid, since)
        for m in messages:
            content = m.get("content", "") or ""
            if pattern and pattern.search(content):
                owner_messages.append(_format_message(m, cname))

    if not owner_messages:
        if chat_id: reply_in_chat(chat_id, "最近两天没有人提到你，清净~")
        return True

    msg_text = "\n".join(owner_messages)
    raw = call_ai(_digest_prompt().replace("__MESSAGES__", msg_text), "请汇总")
    summary = raw or "\n".join(owner_messages)

    if chat_id:
        reply_in_chat(chat_id, summary)
    log(f"Digest: {len(owner_messages)} owner-related messages")
    return True


# --- Reminders ---

def load_reminders():
    if os.path.exists(REMINDERS_FILE):
        try:
            with open(REMINDERS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def save_reminders(reminders):
    with open(REMINDERS_FILE, "w") as f:
        json.dump(reminders, f, indent=2, ensure_ascii=False)


def do_remind(params, chat_id, sender_id=""):
    time_str = params.get("time", "")
    message = params.get("message", "")
    if not time_str or not message:
        if chat_id: reply_in_chat(chat_id, "时间或内容没说清楚，再说一次？")
        return False
    try:
        remind_time = datetime.fromisoformat(time_str)
        if remind_time.tzinfo and remind_time <= datetime.now(remind_time.tzinfo):
            if chat_id: reply_in_chat(chat_id, "这个时间已经过了哦")
            return False
    except ValueError:
        if chat_id: reply_in_chat(chat_id, "时间格式不对，再说一次？")
        return False
    reminders = load_reminders()
    reminders.append({
        "time": time_str,
        "message": message,
        "chat_id": chat_id,
        "sender_id": sender_id,
        "created": datetime.now().isoformat(),
    })
    save_reminders(reminders)
    display = remind_time.strftime("%m月%d日 %H:%M")
    if chat_id: reply_in_chat(chat_id, f"好的，{display} 提醒你：{message}")
    log(f"Reminder set: {display} → {message}")
    return True


def check_reminders():
    reminders = load_reminders()
    if not reminders:
        return
    now = datetime.now().astimezone()
    remaining = []
    for r in reminders:
        try:
            t = datetime.fromisoformat(r["time"])
            if not t.tzinfo:
                t = t.astimezone()
        except ValueError:
            continue
        if now >= t:
            if (now - t).total_seconds() > 86400:
                log(f"Reminder expired >24h, discarding: {r['message']}")
                continue
            chat_id = r.get("chat_id", "")
            if chat_id:
                reply_in_chat(chat_id, f"⏰ 提醒：{r['message']}")
            log(f"Reminder fired: {r['message']}")
        else:
            remaining.append(r)
    if len(remaining) != len(reminders):
        save_reminders(remaining)


# ============================================================
# Transcribe
# ============================================================

_transcribe_state = {
    "active": False,
    "process": None,
    "audio_file": None,
    "start_time": None,
    "chat_id": None,
    "card_msg_id": None,
}

ASR_ENGINE_LOCAL = "本地（SenseVoice）"
ASR_ENGINE_API = "云端 API"

def _asr_engine_label():
    if CONFIG and CONFIG.get("asr_engine") == "api":
        return ASR_ENGINE_API
    return ASR_ENGINE_LOCAL

# ASR onboarding state (first-time engine selection)
_asr_onboard_state = {
    "active": False,
    "step": None,          # "choose_engine" / "wait_api_key" / "wait_api_url"
    "pending_args": None,  # (audio_file, duration_sec, chat_id, card_msg_id)
}



def _build_transcribe_card(status, **kwargs):
    if status == "recording":
        start_time = kwargs.get("start_time", "")
        content = f"🎙️ **正在录音中...**\n开始时间：{start_time}\n引擎：{_asr_engine_label()}\n\n💡 说「结束转写」即可停止并生成纪要"
    elif status == "transcribing":
        duration = kwargs.get("duration", "")
        content = f"⏳ **转写中...**\n录音时长：{duration}\n引擎：{_asr_engine_label()}"
    elif status == "done":
        duration = kwargs.get("duration", "")
        summary = kwargs.get("summary", "会议纪要已生成")
        doc_url = kwargs.get("doc_url", "")
        content = f"✅ **会议纪要已生成**\n时长：{duration} | 引擎：{_asr_engine_label()}\n\n{summary}"
        if doc_url:
            content += f"\n\n[📄 查看完整纪要]({doc_url})"
    elif status == "error":
        error_msg = kwargs.get("error", "未知错误")
        content = f"❌ **转写失败**\n{error_msg}"
    else:
        content = status
    return json.dumps({"elements": [{"tag": "markdown", "content": content}]})


def _send_transcribe_card(chat_id, status, **kwargs):
    card = _build_transcribe_card(status, **kwargs)
    result = lark_cmd([
        "im", "+messages-send", "--as", "bot",
        "--chat-id", chat_id,
        "--msg-type", "interactive",
        "--content", card,
    ])
    if result and result.get("ok"):
        msg_id = result.get("data", {}).get("message_id", "")
        log(f"Transcribe card sent: {msg_id}")
        return msg_id
    log("Transcribe card send failed")
    return ""


def _update_transcribe_card(msg_id, status, **kwargs):
    if not msg_id:
        return
    card = _build_transcribe_card(status, **kwargs)
    result = lark_cmd([
        "api", "PATCH", f"/open-apis/im/v1/messages/{msg_id}",
        "--as", "bot",
        "--data", json.dumps({"msg_type": "interactive", "content": card}),
    ])
    ok = result and result.get("code") == 0
    log(f"Transcribe card update {'ok' if ok else 'failed'}: {msg_id}")


def do_transcribe_start(chat_id):
    if _transcribe_state["active"]:
        if chat_id:
            reply_in_chat(chat_id, "已经在录音了，说「结束转写」就可以停止~")
        return True

    if not os.path.isfile(AUDIO_CAPTURE_BIN):
        if chat_id:
            reply_in_chat(chat_id, f"audio_capture 没找到: {AUDIO_CAPTURE_BIN}")
        return False

    os.makedirs(TMPDIR_MEETING, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    audio_file = os.path.join(TMPDIR_MEETING, f"bot_{timestamp}.pcm")

    try:
        audio_err = os.path.join(BOT_DIR, "audio_capture.err")
        proc = subprocess.Popen(
            [AUDIO_CAPTURE_BIN, "--auto"],
            stdout=open(audio_file, "wb"),
            stderr=open(audio_err, "w"),
        )
    except Exception as e:
        log(f"audio_capture start failed: {e}")
        if chat_id:
            reply_in_chat(chat_id, "录音启动失败了，检查一下 audio_capture 权限？")
        return False

    _transcribe_state["active"] = True
    _transcribe_state["process"] = proc
    _transcribe_state["audio_file"] = audio_file
    _transcribe_state["start_time"] = time.time()
    _transcribe_state["chat_id"] = chat_id

    card_msg_id = ""
    if chat_id:
        card_msg_id = _send_transcribe_card(chat_id, "recording",
                                             start_time=time.strftime("%H:%M"))
    _transcribe_state["card_msg_id"] = card_msg_id
    log(f"Transcribe started: {audio_file}")
    return True


def do_transcribe_stop(chat_id):
    if not _transcribe_state["active"]:
        if chat_id:
            reply_in_chat(chat_id, "现在没有在录音哦")
        return False

    proc = _transcribe_state["process"]
    audio_file = _transcribe_state["audio_file"]
    start_time = _transcribe_state["start_time"]
    card_msg_id = _transcribe_state["card_msg_id"]
    duration_sec = time.time() - start_time if start_time else 0

    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    _transcribe_state["active"] = False
    _transcribe_state["process"] = None

    duration_str = f"{int(duration_sec // 60)} 分钟"
    _update_transcribe_card(card_msg_id, "transcribing", duration=duration_str)

    # First-time ASR engine selection onboarding
    if CONFIG and not CONFIG.get("asr_engine"):
        _asr_onboard_state["active"] = True
        _asr_onboard_state["step"] = "choose_engine"
        _asr_onboard_state["pending_args"] = (audio_file, duration_sec, chat_id, card_msg_id)
        reply_in_chat(chat_id,
            "录音已结束！在开始转写之前，请选择转写引擎：\n\n"
            "1️⃣ 本地模型（SenseVoice）— 免费离线，但精度一般\n"
            "2️⃣ 云端 API — 精度更高，需要配置 API Key\n\n"
            "请回复 1 或 2：")
        log("ASR onboarding: waiting for engine choice")
        return True

    t = threading.Thread(
        target=_transcribe_pipeline,
        args=(audio_file, duration_sec, chat_id, card_msg_id),
        daemon=True,
    )
    t.start()
    log(f"Transcribe stopped, pipeline started in background")
    return True


def process_asr_onboard(event):
    """Handle ASR engine selection during first transcription."""
    chat_id = event.get("chat_id") or ""
    text = (event.get("content") or "").strip()
    step = _asr_onboard_state["step"]

    if step == "choose_engine":
        if text in ("1", "１"):
            # Local SenseVoice
            CONFIG["asr_engine"] = "local"
            save_config(CONFIG)
            _asr_onboard_state["active"] = False
            _asr_onboard_state["step"] = None
            reply_in_chat(chat_id, "已选择本地模型（SenseVoice），开始转写...")
            _resume_transcribe_pipeline()
        elif text in ("2", "２"):
            _asr_onboard_state["step"] = "wait_api_key"
            reply_in_chat(chat_id,
                "请发送你的语音转写 API Key：\n"
                "（支持 OpenAI Whisper 兼容接口，如 Groq、DeepInfra 等）")
        else:
            reply_in_chat(chat_id, "请回复 1（本地模型）或 2（云端 API）：")

    elif step == "wait_api_key":
        if not text or len(text) < 10:
            reply_in_chat(chat_id, "这个不像是有效的 API Key，再检查一下？")
            return
        _asr_onboard_state["api_key"] = text
        _asr_onboard_state["step"] = "wait_api_url"
        reply_in_chat(chat_id,
            "API Base URL 是什么？\n\n"
            "1️⃣ OpenAI（https://api.openai.com/v1）\n"
            "2️⃣ Groq（https://api.groq.com/openai/v1）\n"
            "3️⃣ 自定义（直接发送 URL）\n\n"
            "请回复 1、2 或完整 URL：")

    elif step == "wait_api_url":
        url_map = {
            "1": "https://api.openai.com/v1",
            "１": "https://api.openai.com/v1",
            "2": "https://api.groq.com/openai/v1",
            "２": "https://api.groq.com/openai/v1",
        }
        base_url = url_map.get(text, text if text.startswith("http") else None)
        if not base_url:
            reply_in_chat(chat_id, "请回复 1、2 或完整的 URL（以 http 开头）：")
            return

        CONFIG["asr_engine"] = "api"
        CONFIG["asr_api_key"] = _asr_onboard_state.get("api_key", "")
        CONFIG["asr_api_url"] = base_url
        CONFIG["asr_model"] = "whisper-large-v3" if "groq" in base_url else "whisper-1"
        save_config(CONFIG)
        _asr_onboard_state["active"] = False
        _asr_onboard_state["step"] = None
        reply_in_chat(chat_id, f"已配置云端 API，开始转写...")
        _resume_transcribe_pipeline()


def _resume_transcribe_pipeline():
    """Resume transcription after ASR onboarding completes."""
    args = _asr_onboard_state.get("pending_args")
    if not args:
        return
    _asr_onboard_state["pending_args"] = None
    t = threading.Thread(target=_transcribe_pipeline, args=args, daemon=True)
    t.start()
    log("Transcribe pipeline resumed after ASR onboarding")


def _transcribe_with_api(audio_file):
    """Transcribe using cloud Whisper-compatible API."""
    import wave
    import struct

    # Convert PCM to WAV in memory
    wav_file = audio_file.replace(".pcm", ".wav")
    with open(audio_file, "rb") as pcm:
        pcm_data = pcm.read()
    with wave.open(wav_file, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)  # 16-bit
        wav.setframerate(16000)
        wav.writeframes(pcm_data)

    api_key = CONFIG.get("asr_api_key", "")
    base_url = CONFIG.get("asr_api_url", "https://api.openai.com/v1")
    model = CONFIG.get("asr_model", "whisper-1")

    import requests
    with open(wav_file, "rb") as f:
        resp = requests.post(
            f"{base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (os.path.basename(wav_file), f, "audio/wav")},
            data={"model": model, "language": "zh"},
            timeout=600,
        )
    try:
        os.remove(wav_file)
    except OSError:
        pass

    if resp.status_code != 200:
        raise RuntimeError(f"ASR API error {resp.status_code}: {resp.text[:200]}")
    return resp.json().get("text", "")


def _transcribe_pipeline(audio_file, duration_sec, chat_id, card_msg_id):
    duration_str = f"{int(duration_sec // 60)} 分钟"
    try:
        use_api = CONFIG and CONFIG.get("asr_engine") == "api"

        if use_api:
            log(f"Running cloud ASR on {audio_file}...")
            try:
                transcript = _transcribe_with_api(audio_file)
            except Exception as e:
                log(f"Cloud ASR failed: {e}")
                _update_transcribe_card(card_msg_id, "error", error=f"云端转写失败：{e}")
                return
            if not transcript or not transcript.strip():
                _update_transcribe_card(card_msg_id, "error", error="转写结果为空，可能会议中没有语音")
                return
            transcript_file = audio_file.replace(".pcm", ".txt")
        else:
            transcript_file = audio_file.replace(".pcm", ".txt")
            log(f"Running SenseVoice on {audio_file}...")
            transcribe_python = VENV_PYTHON if os.path.isfile(VENV_PYTHON) else sys.executable
            result = subprocess.run(
                [transcribe_python, TRANSCRIBE_SCRIPT, "--input", audio_file, "--output", transcript_file],
                capture_output=True, text=True, timeout=1800,
            )
            if result.returncode != 0:
                log(f"Transcribe failed: {result.stderr[:300]}")
                _update_transcribe_card(card_msg_id, "error", error="转写失败，可能是音频太短或模型出错")
                return

            if not os.path.isfile(transcript_file):
                _update_transcribe_card(card_msg_id, "error", error="转写结果为空，可能会议中没有语音")
                return

            with open(transcript_file, encoding="utf-8") as f:
                transcript = f.read()

            if not transcript.strip():
                _update_transcribe_card(card_msg_id, "error", error="转写结果为空")
                return

        log(f"Transcription done: {len(transcript)} chars")

        date_str = time.strftime("%Y-%m-%d")
        prompt = MINUTES_PROMPT.replace("__DATE__", date_str).replace("__DURATION__", duration_str)
        minutes_md = call_ai(prompt, transcript)
        if not minutes_md:
            minutes_md = f"# 会议转写 {date_str}\n\n{transcript}"

        # Save to Obsidian (optional — skip if vault directory doesn't exist)
        if os.path.isdir(VAULT_DIR):
            notes_dir = os.path.join(VAULT_DIR, NOTES_FOLDER)
            os.makedirs(notes_dir, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            note_file = os.path.join(notes_dir, f"{date_str} 会议纪要_{ts}.md")
            with open(note_file, "w", encoding="utf-8") as f:
                f.write(minutes_md)
            log(f"Minutes saved to Obsidian: {note_file}")
        else:
            log(f"Obsidian vault not found at {VAULT_DIR}, skipping local save")

        doc_title = f"{date_str} 会议纪要"
        doc_result = lark_cmd([
            "docs", "+create",
            "--title", doc_title,
            "--markdown", minutes_md,
        ])
        doc_url = ""
        if doc_result and doc_result.get("ok"):
            doc_url = doc_result.get("data", {}).get("url", "")
            log(f"Lark doc created: {doc_url}")
        else:
            log("Lark doc creation failed")

        summary_text = call_ai(
            MINUTES_SUMMARY_PROMPT.replace("__MINUTES__", minutes_md),
            "请概括",
        )
        if not summary_text:
            summary_text = "会议纪要已生成"

        _update_transcribe_card(card_msg_id, "done",
                                duration=duration_str,
                                summary=summary_text,
                                doc_url=doc_url)
        log("Transcribe card updated to done")

        for f in [audio_file, transcript_file]:
            try:
                os.remove(f)
            except OSError:
                pass
        log("Temp files cleaned up")

    except Exception as e:
        log(f"Transcribe pipeline error: {e}")
        _update_transcribe_card(card_msg_id, "error", error=str(e))


# ============================================================
# Action dispatch
# ============================================================

def execute_action(action, params, chat_id, sender_id="", chat_type="group"):
    if action == "meeting":
        return do_meeting(params, chat_id, sender_id)
    elif action == "cancel_meeting":
        return do_cancel_meeting(params, chat_id)
    elif action == "search_user":
        return do_search_user(params, chat_id)
    elif action == "digest":
        return do_digest(chat_id, params.get("query", ""), params.get("days", 0), chat_type)
    elif action == "remind":
        return do_remind(params, chat_id, sender_id)
    elif action == "daily_report":
        send_daily_report()
        return True
    elif action == "task_create":
        return do_task_create(params, chat_id, sender_id)
    elif action == "task_list":
        return do_task_list(params, chat_id)
    elif action == "task_complete":
        return do_task_complete(params, chat_id)
    elif action == "check_calendar":
        return do_check_calendar(params, chat_id)
    elif action == "read_doc":
        return do_read_doc(params, chat_id)
    elif action == "transcribe_start":
        return do_transcribe_start(chat_id)
    elif action == "transcribe_stop":
        return do_transcribe_stop(chat_id)
    return False


def get_today_calendar():
    result = lark_cmd(["calendar", "+agenda"])
    if result and result.get("ok"):
        events = result.get("data", [])
        if not events:
            return "今天没有日程"
        lines = []
        for e in events:
            start = e.get("start_time", {}).get("datetime", "?")
            end = e.get("end_time", {}).get("datetime", "?")
            summary = e.get("summary", "无标题")
            lines.append(f"- {start[11:16]}~{end[11:16]} {summary}")
        return "\n".join(lines)
    return "日历获取失败"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ============================================================
# Detection
# ============================================================

def is_bot_mentioned(content):
    return bool(re.search(r"@LarkCli-", content, re.IGNORECASE))


def extract_text(content):
    return re.sub(r"@\S+[-]\S+(\s+\S+)?\s*", "", content, count=1).strip()


# ============================================================
# Event processing
# ============================================================

def process_event(event):
    # Setup mode: no config → interactive setup
    if not CONFIG:
        if _setup_state["step"] != "done":
            process_setup(event)
            return

    sender_id = event.get("sender_id", "unknown")
    raw_content = event.get("content") or ""
    chat_id = event.get("chat_id") or ""
    chat_type = event.get("chat_type") or "group"
    msg_type = event.get("message_type") or ""

    # Ignore bot's own messages
    if sender_id == _BOT_OPEN_ID:
        return

    if msg_type and msg_type != "text":
        return

    is_owner = sender_id == OWNER_OPEN_ID

    # --- ASR engine onboarding (intercept during first transcription) ---
    if _asr_onboard_state["active"] and chat_type == "p2p" and is_owner:
        process_asr_onboard(event)
        return

    # --- 1v1 direct chat ---
    if chat_type == "p2p":
        text = extract_text(raw_content) if is_bot_mentioned(raw_content) else raw_content
        if not text.strip():
            return

        # New user onboarding
        if not is_onboarded(sender_id):
            mark_onboarded(sender_id)
            send_welcome_card(chat_id)

        # Help command
        if text.strip().lower() in HELP_TRIGGERS:
            send_welcome_card(chat_id)
            return

        # Chat list query (owner only)
        if any(kw in text for kw in CHAT_LIST_KEYWORDS):
            if not is_owner:
                reply_in_chat(chat_id, "这个功能只有管理员能用哦")
                return
            chats = _get_bot_chats()
            if chats:
                reply_in_chat(chat_id, "我加入了这些群：\n" + "\n".join(f"• {name}" for name in chats))
            else:
                reply_in_chat(chat_id, "我还没有加入任何群，把我拉进群就行~")
            return

        # Transcribe keywords — bypass AI to avoid JSON parsing failures
        text_lower = text.strip().lower()
        if any(kw in text_lower for kw in ["结束转写", "停止录音", "停止转写"]):
            reply_in_chat(chat_id, "好的，正在结束转写...")
            do_transcribe_stop(chat_id)
            return
        if any(kw in text_lower for kw in ["帮我转写", "开始录音", "开始转写", "转写会议"]):
            reply_in_chat(chat_id, "好的，开始录音~")
            do_transcribe_start(chat_id)
            return

        log(f"1v1 from {sender_id[:16]}: {text[:80]}")
        history = _get_chat_history(sender_id)
        raw = call_ai(_chat_prompt(), text, history=history)
        log(f"AI raw: {(raw or '')[:200]}")
        result = parse_ai(raw)
        reply_text = result.get("reply", "")
        action = result.get("action", "none")
        if action != "none":
            log(f"Action: {action}, params: {result.get('params', {})}")

        # Owner-only actions (involve owner's private data)
        OWNER_ONLY_ACTIONS = {"digest", "daily_report"}
        if action in OWNER_ONLY_ACTIONS and not is_owner:
            reply_in_chat(chat_id, "这个功能只有管理员能用哦，你可以试试：建任务、约会议、查日程、查人~")
            return

        # Save conversation to history
        _append_chat_history(sender_id, "user", text)
        _append_chat_history(sender_id, "assistant", reply_text)
        reply_in_chat(chat_id, reply_text)
        if action != "none":
            execute_action(action, result.get("params", {}), chat_id, sender_id, chat_type="p2p")
        return

    # --- Group: only respond to @bot ---
    if not is_bot_mentioned(raw_content):
        return

    text = extract_text(raw_content)
    if not text.strip():
        return

    # Help command → welcome card
    if text.strip().lower() in HELP_TRIGGERS:
        if chat_id:
            send_welcome_card(chat_id)
        return

    # Chat list query
    if any(kw in text for kw in CHAT_LIST_KEYWORDS):
        chats = _get_bot_chats()
        if chats:
            reply_in_chat(chat_id, "我加入了这些群：\n" + "\n".join(f"• {name}" for name in chats))
        else:
            reply_in_chat(chat_id, "我还没有加入任何群，把我拉进群就行~")
        return

    log(f"@bot from {sender_id}: {text[:80]}")

    result = parse_ai(call_ai(_reply_prompt(), f"发送人: {sender_id}\n消息: {text}"))
    reply_text = result.get("reply", "稍等哈～")
    action = result.get("action", "none")
    params = result.get("params", {})

    if chat_id:
        reply_in_chat(chat_id, reply_text)

    if action != "none":
        ok = execute_action(action, params, chat_id, sender_id)
        log(f"Action {action}: {'ok' if ok else 'failed'}")


# ============================================================
# Scheduled
# ============================================================

def send_daily_report():
    log("Generating daily report...")
    cal = get_today_calendar()

    since = datetime.now().replace(hour=0, minute=0, second=0).isoformat()
    chats_result = lark_cmd(["im", "chats", "list", "--as", "bot",
                             "--params", json.dumps({"page_size": 20})])
    msg_lines = []
    pattern = _owner_pattern()
    if chats_result and pattern:
        for c in chats_result.get("data", {}).get("items", []):
            cid = c.get("chat_id", "")
            cname = c.get("name", "")
            if not cid: continue
            messages = _pull_chat_messages(cid, since)
            for m in messages:
                content = m.get("content", "") or ""
                if pattern.search(content):
                    sender_name = m.get("sender", {}).get("name", "未知")
                    msg_lines.append(f"[{cname}] {sender_name}: {content[:80]}")

    msg_summary = "\n".join(msg_lines) if msg_lines else "今天群里没人提到你"
    raw = call_ai(_report_prompt().replace("__CALENDAR__", cal).replace("__MESSAGES__", msg_summary),
                  "请生成日报")
    report = raw or "今天暂无数据。"
    send_dm(f"每日工作日报\n\n{report}")
    log("Daily report sent")


def scheduler():
    report_sent = False
    while True:
        now = datetime.now()
        if now.hour == 20 and not report_sent:
            if CONFIG:  # only send if configured
                send_daily_report()
            report_sent = True
        elif now.hour != 20:
            report_sent = False
        try:
            check_reminders()
        except Exception as e:
            log(f"Reminder check error: {e}")
        # Watchdog: check lark-cli health
        try:
            _check_ws_health()
        except Exception as e:
            log(f"WS health check error: {e}")
        time.sleep(30)


# ============================================================
# Main
# ============================================================

def watch_event_dir():
    os.makedirs(EVENT_DIR, exist_ok=True)
    log(f"Watching {EVENT_DIR}")
    processed = set()
    while True:
        files = sorted(glob.glob(os.path.join(EVENT_DIR, "*.json")))
        for f in files:
            if f in processed:
                continue
            try:
                with open(f) as fh:
                    event = json.load(fh)
                process_event(event)
            except Exception as e:
                log(f"Error: {e}")
            finally:
                processed.add(f)
                try:
                    os.remove(f)
                except OSError:
                    pass
        time.sleep(1)


# ============================================================
# lark-cli WebSocket manager (auto-reconnect on network drop)
# ============================================================

_ws_process = None
_ws_last_restart = 0      # timestamp of last restart
_ws_restart_count = 0     # consecutive restarts without staying alive
_WS_COOLDOWN = 60         # min seconds between restarts
_WS_STABLE_TIME = 30      # process must live this long to count as "stable"
_ws_notified = False       # whether we already sent a reconnect card this cycle


def _start_lark_ws():
    """Start lark-cli event subscriber. Returns the process."""
    global _ws_process, _ws_last_restart, _ws_restart_count
    # Kill any existing subscriber
    if _ws_process and _ws_process.poll() is None:
        _ws_process.terminate()
        try:
            _ws_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _ws_process.kill()

    os.makedirs(EVENT_DIR, exist_ok=True)
    # Clear stale event files
    for f in glob.glob(os.path.join(EVENT_DIR, "*.json")):
        try:
            os.remove(f)
        except OSError:
            pass

    # --force: override stale lock from previous instance
    # lark-cli requires relative --output-dir, so cwd must be BOT_DIR
    _ws_process = subprocess.Popen(
        ["lark-cli", "event", "+subscribe", "--as", "bot",
         "--event-types", "im.message.receive_v1",
         "--compact", "--quiet", "--force",
         "--output-dir", "./events"],
        cwd=BOT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=open(os.path.join(BOT_DIR, "lark-ws.err"), "w"),
    )
    _ws_last_restart = time.time()
    _ws_restart_count += 1
    log(f"lark-cli subscriber started (PID: {_ws_process.pid}, attempt: {_ws_restart_count})")
    return _ws_process


def _check_ws_health():
    """Check if lark-cli is alive, restart if dead with cooldown."""
    global _ws_process, _ws_restart_count, _ws_notified
    if _ws_process is None or _ws_process.poll() is not None:
        elapsed = time.time() - _ws_last_restart

        # If process was stable (lived long enough), reset counter
        if elapsed > _WS_STABLE_TIME:
            _ws_restart_count = 0
            _ws_notified = False

        # Cooldown: don't restart too frequently
        if elapsed < _WS_COOLDOWN:
            return False

        exit_code = _ws_process.returncode if _ws_process else "N/A"
        log(f"lark-cli subscriber died (exit={exit_code}), restarting...")
        _start_lark_ws()

        # Only send notification once per disconnect cycle, and only after confirming it stays alive
        if CONFIG and not _ws_notified:
            time.sleep(5)
            if _ws_process and _ws_process.poll() is None:
                _send_online_card(reconnect=True)
                _ws_notified = True
        return True

    # Process is alive — if it's been stable, reset counter
    if time.time() - _ws_last_restart > _WS_STABLE_TIME and _ws_restart_count > 0:
        _ws_restart_count = 0
    return False


def _send_online_card(reconnect=False):
    """Send a status card to owner."""
    now = datetime.now().strftime("%H:%M")
    if reconnect:
        content = f"🔄 **{BOT_NAME} 已恢复连接**\n\n时间：{now}\n网络断开后自动重连成功"
    else:
        content = f"✅ **{BOT_NAME} 已上线**\n\n时间：{now}\n随时可以跟我说话~"
    card = json.dumps({"elements": [{"tag": "markdown", "content": content}]})
    if OWNER_OPEN_ID:
        _send_card_to_user(OWNER_OPEN_ID, card)


LOCK_FILE = os.path.join(BOT_DIR, ".bot.lock")


def _acquire_lock():
    """Ensure only one bot.py instance runs. Exit if another is alive."""
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE) as f:
                old_pid = int(f.read().strip())
            # Check if that process is still alive
            os.kill(old_pid, 0)
            log(f"Another bot.py is running (PID {old_pid}), exiting.")
            sys.exit(0)
        except (ProcessLookupError, ValueError, OSError):
            pass  # stale lock, proceed
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))


def _release_lock():
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass


def main():
    _acquire_lock()
    import atexit
    atexit.register(_release_lock)

    mode = "setup" if not CONFIG else "normal"
    log(f"Lark Bot v8 starting (mode: {mode}, PID: {os.getpid()})")
    if CONFIG:
        log(f"Bot: {BOT_NAME}, Owner: {OWNER_NAME}")
    else:
        log("No config.json — waiting for first message to start setup")
    log("Actions: meeting, cancel_meeting, search_user, digest, remind, transcribe")

    # Get bot's own open_id (for filtering self-messages)
    _init_bot_open_id()
    log(f"Bot open_id: {_BOT_OPEN_ID}")

    # Start lark-cli subscriber (bot manages it directly)
    _start_lark_ws()
    time.sleep(3)

    # Send online notification
    if CONFIG:
        _send_online_card()

    t = threading.Thread(target=scheduler, daemon=True)
    t.start()
    watch_event_dir()


if __name__ == "__main__":
    main()
