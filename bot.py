"""
Lark Bot v7 — lean version:
- @bot in group → AI reply + execute actions
- @bot "汇总一下" → pull recent messages from API on demand, filter & summarize
- 1v1 with bot → direct conversation
- 1v1 "帮我转写" → 录音 + Qwen3-ASR 转写 + AI 纪要 + 飞书文档
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
from openai import OpenAI

# --- Config ---
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
JAKE_OPEN_ID = os.environ.get("JAKE_OPEN_ID", "ou_19af5476f7712283c2bc5f6554d4818b")
EVENT_DIR = os.environ.get("EVENT_DIR", "/Users/jaker/lark-bot/events")
AUDIO_CAPTURE_BIN = os.environ.get("AUDIO_CAPTURE_BIN", os.path.expanduser("~/meeting-cli/audio_capture"))
TRANSCRIBE_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "transcribe_qwen.py")
VENV_PYTHON = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python")
VAULT_DIR = os.environ.get("MEETING_VAULT", os.path.expanduser("~/Documents/Obsidian Vault"))
NOTES_FOLDER = os.environ.get("MEETING_NOTES_FOLDER", "会议纪要")
TMPDIR_MEETING = os.path.join(os.environ.get("TMPDIR", "/tmp"), "meeting-cli")

minimax = OpenAI(
    api_key=MINIMAX_API_KEY,
    base_url=os.environ.get("LLM_BASE_URL", "https://api.minimaxi.com/v1"),
)

REPLY_PROMPT = """你是 Jake R 的飞书 AI 助手，名叫"小J"。你在群里代替 Jake 跟同事互动。

性格：热情、靠谱、略带幽默感，像朋友之间聊天一样说话。

你能执行的操作（通过 action 触发）：
- meeting: 约会议 → params: summary, start(ISO8601+08:00), duration(如1h), attendees(open_id列表，发起人必须包含)
- cancel_meeting: 取消会议 → params: keyword
- search_user: 查找同事 → params: query
- digest: 消息汇总或定向分析 → params: query(用户原始问题，通用汇总时留空), days(时间范围天数，AI根据问题推断，默认7)
  - 通用汇总（"汇总一下"、"有什么消息"）→ query留空，不需要days
  - 定向分析（"帮我看看XX提了什么"、"分析一下XX内容"）→ query填用户原始问题，days根据上下文推断
- remind: 设置提醒 → params: time(ISO8601+08:00), message(提醒内容)
  - "明天中午提醒我写周报" → time: 明天12:00的ISO8601, message: "写周报"
  - "下午3点提醒我开会" → time: 今天15:00的ISO8601, message: "开会"
- transcribe_start: 开始会议转写/录音 → params: {}
  - "帮我转写"、"开始录音"、"转写会议" → transcribe_start
- transcribe_stop: 停止转写/录音并生成纪要 → params: {}
  - "结束转写"、"停止录音"、"结束" → transcribe_stop
- none: 不需要操作

原则：
- 能办的直接办，不推给 Jake
- 约会议：推断主题，时间转 ISO8601，发起人 open_id 放 attendees
- "晚上10点"→ 当天22:00:00+08:00
- 不用 markdown，纯文本
- 失败要说明原因

当前时间：__NOW__

JSON 输出（只输出 JSON）：
{"reply": "纯文本回复", "action": "meeting/cancel_meeting/search_user/digest/remind/transcribe_start/transcribe_stop/none", "params": {}}"""

CHAT_PROMPT = """你是 Jake R 的私人 AI 助手"小J"。1v1 聊天模式。
- 聪明靠谱，语气轻松
- 可以查日历、安排事项、查人、设提醒、会议转写、回答问题
- 不用 markdown，纯文本
- 约会议/设提醒时间用 ISO8601+08:00
- "帮我转写"/"开始录音" → transcribe_start
- "结束转写"/"停止录音"/"结束" → transcribe_stop
当前时间：__NOW__

JSON 输出：
{"reply": "纯文本回复", "action": "meeting/cancel_meeting/search_user/digest/remind/transcribe_start/transcribe_stop/none", "params": {}}"""

REPORT_PROMPT = """根据以下信息生成简洁的每日工作日报。

今日日程：
__CALENDAR__

今日群消息摘要（与 Jake 相关的）：
__MESSAGES__

要求：中文口语化，分"今天干了啥"和"还得跟进"两块，每条一句话，结尾来一句轻松的话。"""

DIGEST_PROMPT = """以下是最近群聊中与 Jake R 相关的消息。请帮忙汇总：
- 谁找了 Jake、什么事
- 有哪些需要 Jake 跟进的
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


# --- AI ---
def call_ai(system_prompt, user_msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M %A")
    prompt = system_prompt.replace("__NOW__", now)
    try:
        resp = minimax.chat.completions.create(
            model="MiniMax-M2.5",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.5,
        )
        content = resp.choices[0].message.content.strip()
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        content = re.sub(r"<tool_code>.*?</tool_code>", "", content, flags=re.DOTALL).strip()
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
        # AI sometimes returns explanation text before/after JSON — extract it
        for i, ch in enumerate(raw):
            if ch == '{' and '"reply"' in raw[i:]:
                try:
                    return json.loads(raw[i:])
                except json.JSONDecodeError:
                    continue
        return {"reply": raw, "action": "none", "params": {}}


# --- Lark CLI ---
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
    result = lark_cmd(["im", "+messages-send", "--as", "bot", "--user-id", JAKE_OPEN_ID, "--text", text])
    log("DM sent" if result and result.get("ok") else "DM failed")


# --- Actions ---
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
    if sender_id and sender_id not in attendees:
        attendees.append(sender_id)
    if JAKE_OPEN_ID not in attendees:
        attendees.append(JAKE_OPEN_ID)
    if attendees:
        args += ["--attendee-ids", ",".join(attendees)]
    result = lark_cmd(args)
    ok = result and result.get("ok")
    if ok and chat_id:
        reply_in_chat(chat_id, f"会议已创建~ {summary} {start[11:16]}-{end[11:16]}")
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


def _pull_chat_messages(chat_id, since):
    """Pull all messages from a single chat since a given time, auto-paginating."""
    all_messages = []
    page_token = ""
    for _ in range(10):  # max 10 pages = 500 messages
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
    """Format a single message into a readable line."""
    sender_name = m.get("sender", {}).get("name", "未知")
    content = m.get("content", "") or ""
    ts = m.get("create_time", "")
    prefix = f"[{chat_name}] " if chat_name else ""
    return f"{prefix}{ts} {sender_name}: {content[:200]}"


def do_digest(chat_id, query="", days=0, chat_type="group"):
    """Message digest with two modes: targeted analysis (with query) or general summary."""

    if query:
        # --- Targeted analysis ---
        if chat_id:
            reply_in_chat(chat_id, "我去翻翻消息，帮你分析一下~")
        if not days:
            days = 7
        since = (datetime.now() - timedelta(days=days)).isoformat()

        if chat_type == "p2p":
            # Triggered from private chat — scan all group chats
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

    # --- General summary: all chats, Jake-related, last 2 days ---
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
    jake_messages = []

    for c in chat_list:
        cid = c.get("chat_id", "")
        cname = c.get("name", "未知群")
        if not cid:
            continue
        messages = _pull_chat_messages(cid, since)
        for m in messages:
            content = m.get("content", "") or ""
            if re.search(r"Jake\s*R|JakeR|@Jake", content, re.IGNORECASE):
                jake_messages.append(_format_message(m, cname))

    if not jake_messages:
        if chat_id: reply_in_chat(chat_id, "最近两天没有人提到你，清净~")
        return True

    msg_text = "\n".join(jake_messages)
    raw = call_ai(DIGEST_PROMPT.replace("__MESSAGES__", msg_text), "请汇总")
    summary = raw or "\n".join(jake_messages)

    if chat_id:
        reply_in_chat(chat_id, summary)
    log(f"Digest: {len(jake_messages)} jake-related messages")
    return True


REMINDERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reminders.json")


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


# --- Transcribe ---
_transcribe_state = {
    "active": False,
    "process": None,
    "audio_file": None,
    "start_time": None,
    "chat_id": None,
    "card_msg_id": None,
}

ASR_ENGINE = "本地（SenseVoice）"


def _build_card(status, **kwargs):
    """构建不同状态的卡片 JSON"""
    if status == "recording":
        start_time = kwargs.get("start_time", "")
        content = f"🎙️ **正在录音中...**\n开始时间：{start_time}\n引擎：{ASR_ENGINE}\n\n💡 说「结束转写」即可停止并生成纪要"
    elif status == "transcribing":
        duration = kwargs.get("duration", "")
        content = f"⏳ **转写中...**\n录音时长：{duration}\n引擎：{ASR_ENGINE}"
    elif status == "done":
        duration = kwargs.get("duration", "")
        summary = kwargs.get("summary", "会议纪要已生成")
        doc_url = kwargs.get("doc_url", "")
        content = f"✅ **会议纪要已生成**\n时长：{duration} | 引擎：{ASR_ENGINE}\n\n{summary}"
        if doc_url:
            content += f"\n\n[📄 查看完整纪要]({doc_url})"
    elif status == "error":
        error_msg = kwargs.get("error", "未知错误")
        content = f"❌ **转写失败**\n{error_msg}"
    else:
        content = status

    card = json.dumps({"elements": [{"tag": "markdown", "content": content}]})
    return card


def _send_card(chat_id, status, **kwargs):
    """发送卡片消息，返回 message_id"""
    card = _build_card(status, **kwargs)
    result = lark_cmd([
        "im", "+messages-send", "--as", "bot",
        "--chat-id", chat_id,
        "--msg-type", "interactive",
        "--content", card,
    ])
    if result and result.get("ok"):
        msg_id = result.get("data", {}).get("message_id", "")
        log(f"Card sent: {msg_id}")
        return msg_id
    log("Card send failed")
    return ""


def _update_card(msg_id, status, **kwargs):
    """更新已发送的卡片"""
    if not msg_id:
        return
    card = _build_card(status, **kwargs)
    result = lark_cmd([
        "api", "PATCH", f"/open-apis/im/v1/messages/{msg_id}",
        "--as", "bot",
        "--data", json.dumps({"msg_type": "interactive", "content": card}),
    ])
    ok = result and result.get("code") == 0
    log(f"Card update {'ok' if ok else 'failed'}: {msg_id}")


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
        proc = subprocess.Popen(
            [AUDIO_CAPTURE_BIN],
            stdout=open(audio_file, "wb"),
            stderr=subprocess.DEVNULL,
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

    # 发送录音状态卡片
    card_msg_id = ""
    if chat_id:
        card_msg_id = _send_card(chat_id, "recording",
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

    # 停止录音
    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    _transcribe_state["active"] = False
    _transcribe_state["process"] = None

    duration_str = f"{int(duration_sec // 60)} 分钟"

    # 更新卡片为转写中状态
    _update_card(card_msg_id, "transcribing", duration=duration_str)

    # 后台线程处理转写流水线
    t = threading.Thread(
        target=_transcribe_pipeline,
        args=(audio_file, duration_sec, chat_id, card_msg_id),
        daemon=True,
    )
    t.start()
    log(f"Transcribe stopped, pipeline started in background")
    return True


def _transcribe_pipeline(audio_file, duration_sec, chat_id, card_msg_id):
    """后台转写流水线：转写 → 纪要 → Obsidian → 飞书文档 → 更新卡片"""
    duration_str = f"{int(duration_sec // 60)} 分钟"
    try:
        # 1. 转写
        transcript_file = audio_file.replace(".pcm", ".txt")
        log(f"Running SenseVoice on {audio_file}...")
        transcribe_python = VENV_PYTHON if os.path.isfile(VENV_PYTHON) else sys.executable
        result = subprocess.run(
            [transcribe_python, TRANSCRIBE_SCRIPT, "--input", audio_file, "--output", transcript_file],
            capture_output=True, text=True, timeout=1800,
        )
        if result.returncode != 0:
            log(f"Transcribe failed: {result.stderr[:300]}")
            _update_card(card_msg_id, "error", error="转写失败，可能是音频太短或模型出错")
            return

        if not os.path.isfile(transcript_file):
            _update_card(card_msg_id, "error", error="转写结果为空，可能会议中没有语音")
            return

        with open(transcript_file, encoding="utf-8") as f:
            transcript = f.read()

        if not transcript.strip():
            _update_card(card_msg_id, "error", error="转写结果为空")
            return

        log(f"Transcription done: {len(transcript)} chars")

        # 2. AI 生成纪要
        date_str = time.strftime("%Y-%m-%d")
        prompt = MINUTES_PROMPT.replace("__DATE__", date_str).replace("__DURATION__", duration_str)
        minutes_md = call_ai(prompt, transcript)
        if not minutes_md:
            minutes_md = f"# 会议转写 {date_str}\n\n{transcript}"

        # 3. 保存到 Obsidian
        notes_dir = os.path.join(VAULT_DIR, NOTES_FOLDER)
        os.makedirs(notes_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        note_file = os.path.join(notes_dir, f"{date_str} 会议纪要_{ts}.md")
        with open(note_file, "w", encoding="utf-8") as f:
            f.write(minutes_md)
        log(f"Minutes saved to Obsidian: {note_file}")

        # 4. 创建飞书云文档
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

        # 5. 生成会议概括
        summary_text = call_ai(
            MINUTES_SUMMARY_PROMPT.replace("__MINUTES__", minutes_md),
            "请概括",
        )
        if not summary_text:
            summary_text = "会议纪要已生成"

        # 6. 更新卡片为完成状态
        _update_card(card_msg_id, "done",
                     duration=duration_str,
                     summary=summary_text,
                     doc_url=doc_url)
        log("Card updated to done")

        # 7. 清理临时文件
        for f in [audio_file, transcript_file]:
            try:
                os.remove(f)
            except OSError:
                pass
        log("Temp files cleaned up")

    except Exception as e:
        log(f"Transcribe pipeline error: {e}")
        _update_card(card_msg_id, "error", error=str(e))


def send_dm_markdown(text):
    """发送 Markdown 格式私聊消息"""
    result = lark_cmd([
        "im", "+messages-send", "--as", "bot",
        "--user-id", JAKE_OPEN_ID,
        "--markdown", text,
    ])
    log("DM (markdown) sent" if result and result.get("ok") else "DM (markdown) failed")


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


# --- Detection ---
def is_bot_mentioned(content):
    return bool(re.search(r"@LarkCli-", content, re.IGNORECASE))

def extract_text(content):
    return re.sub(r"@\S+[-]\S+(\s+\S+)?\s*", "", content, count=1).strip()


# --- Event processing ---
def process_event(event):
    sender_id = event.get("sender_id", "unknown")
    raw_content = event.get("content") or ""
    chat_id = event.get("chat_id") or ""
    chat_type = event.get("chat_type") or "group"
    msg_type = event.get("message_type") or ""

    if msg_type and msg_type != "text":
        return

    is_jake = sender_id == JAKE_OPEN_ID

    # --- 1v1 direct chat ---
    if chat_type == "p2p":
        if not is_jake:
            return
        text = extract_text(raw_content) if is_bot_mentioned(raw_content) else raw_content
        if not text.strip():
            return
        log(f"1v1: {text[:80]}")
        result = parse_ai(call_ai(CHAT_PROMPT, text))
        reply_in_chat(chat_id, result.get("reply", ""))
        action = result.get("action", "none")
        if action != "none":
            execute_action(action, result.get("params", {}), chat_id, sender_id, chat_type="p2p")
        return

    # --- Group: only respond to @bot ---
    if not is_bot_mentioned(raw_content):
        return

    text = extract_text(raw_content)
    if not text.strip():
        return

    # --- Help command: respond with feature list, skip AI ---
    if text.strip().lower() in ("help", "帮助"):
        help_text = (
            "小J 能做这些事：\n\n"
            "📅 约会议 — @我 说\"帮我约个会\"\n"
            "❌ 取消会议 — @我 说\"取消那个会议\"\n"
            "🔍 查人 — @我 说\"查一下 xxx\"\n"
            "📋 消息汇总 — @我 说\"汇总一下\"\n"
            "🔎 定向分析 — @我 说\"帮我看看 XX 提了什么需求\"、\"分析一下 trigger 的内容\"\n"
            "⏰ 设提醒 — @我 说\"明天中午提醒我写周报\"\n"
            "🎙️ 会议转写 — 私聊说\"帮我转写\"开始，\"结束转写\"停止\n"
            "💬 闲聊 — @我 随便说点什么\n\n"
            "📊 每天 20:00 自动私信 Jake 工作日报"
        )
        if chat_id:
            reply_in_chat(chat_id, help_text)
        return

    log(f"@bot from {sender_id}: {text[:80]}")

    result = parse_ai(call_ai(REPLY_PROMPT, f"发送人: {sender_id}\n消息: {text}"))
    reply_text = result.get("reply", "稍等哈～")
    action = result.get("action", "none")
    params = result.get("params", {})

    if chat_id:
        reply_in_chat(chat_id, reply_text)

    if action != "none":
        ok = execute_action(action, params, chat_id, sender_id)
        log(f"Action {action}: {'ok' if ok else 'failed'}")


# --- Scheduled ---
def send_daily_report():
    log("Generating daily report...")
    cal = get_today_calendar()

    # Pull jake-related messages from all chats (today only)
    since = datetime.now().replace(hour=0, minute=0, second=0).isoformat()
    chats_result = lark_cmd(["im", "chats", "list", "--as", "bot",
                             "--params", json.dumps({"page_size": 20})])
    msg_lines = []
    if chats_result:
        for c in chats_result.get("data", {}).get("items", []):
            cid = c.get("chat_id", "")
            cname = c.get("name", "")
            if not cid: continue
            messages = _pull_chat_messages(cid, since)
            for m in messages:
                content = m.get("content", "") or ""
                if re.search(r"Jake\s*R|JakeR|@Jake", content, re.IGNORECASE):
                    sender_name = m.get("sender", {}).get("name", "未知")
                    msg_lines.append(f"[{cname}] {sender_name}: {content[:80]}")

    msg_summary = "\n".join(msg_lines) if msg_lines else "今天群里没人提到你"
    raw = call_ai(REPORT_PROMPT.replace("__CALENDAR__", cal).replace("__MESSAGES__", msg_summary),
                  "请生成日报")
    report = raw or "今天暂无数据。"
    send_dm(f"每日工作日报\n\n{report}")
    log("Daily report sent")


def scheduler():
    report_sent = False
    while True:
        now = datetime.now()
        if now.hour == 20 and now.minute == 0 and not report_sent:
            send_daily_report()
            report_sent = True
        elif now.hour == 0 and now.minute == 0:
            report_sent = False
        try:
            check_reminders()
        except Exception as e:
            log(f"Reminder check error: {e}")
        time.sleep(30)


# --- Main ---
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


def main():
    log("Lark Bot v7 starting")
    log("Actions: meeting, cancel_meeting, search_user, digest, remind, transcribe")
    log("Daily report: 20:00")
    log("NO queue, NO background collection — pull on demand only")

    t = threading.Thread(target=scheduler, daemon=True)
    t.start()
    watch_event_dir()


if __name__ == "__main__":
    main()
