"""
Lark Bot v6 — lean version:
- @bot in group → AI reply + execute actions
- @bot "汇总一下" → pull recent messages from API on demand, filter & summarize
- 1v1 with bot → direct conversation
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
- digest: 汇总近期消息（当用户说"汇总"、"有什么消息"、"最近有啥"等）→ 无需 params
- none: 不需要操作

原则：
- 能办的直接办，不推给 Jake
- 约会议：推断主题，时间转 ISO8601，发起人 open_id 放 attendees
- "晚上10点"→ 当天22:00:00+08:00
- 不用 markdown，纯文本
- 失败要说明原因

当前时间：__NOW__

JSON 输出（只输出 JSON）：
{"reply": "纯文本回复", "action": "meeting/cancel_meeting/search_user/digest/none", "params": {}}"""

CHAT_PROMPT = """你是 Jake R 的私人 AI 助手"小J"。1v1 聊天模式。
- 聪明靠谱，语气轻松
- 可以查日历、安排事项、查人、回答问题
- 不用 markdown，纯文本
- 约会议时间用 ISO8601+08:00
当前时间：__NOW__

JSON 输出：
{"reply": "纯文本回复", "action": "meeting/cancel_meeting/search_user/digest/none", "params": {}}"""

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
        return {"reply": raw, "action": "none", "params": {}}


# --- Lark CLI ---
def lark_cmd(args):
    try:
        result = subprocess.run(
            ["lark-cli"] + args,
            capture_output=True, text=True, timeout=30,
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


def do_digest(chat_id):
    """On-demand: pull recent messages from all bot chats, filter Jake-related, summarize."""
    if chat_id:
        reply_in_chat(chat_id, "我去翻翻最近的消息~")

    # Get all chats the bot is in
    chats_result = lark_cmd(["im", "chats", "list", "--as", "bot",
                             "--params", json.dumps({"page_size": 20})])
    if not chats_result:
        if chat_id: reply_in_chat(chat_id, "拉群列表失败了")
        return False

    chat_list = chats_result.get("data", {}).get("items", [])
    if not chat_list:
        if chat_id: reply_in_chat(chat_id, "bot 还没加入任何群")
        return False

    # Pull recent messages (last 2 days) from each chat
    since = (datetime.now() - timedelta(days=2)).isoformat()
    jake_messages = []

    for c in chat_list:
        cid = c.get("chat_id", "")
        cname = c.get("name", "未知群")
        if not cid:
            continue
        msgs_result = lark_cmd(["im", "+chat-messages-list", "--chat-id", cid,
                                "--start", since, "--page-size", "50"])
        if not msgs_result or not msgs_result.get("ok"):
            continue
        messages = msgs_result.get("data", {}).get("messages", [])
        for m in messages:
            content = m.get("body", {}).get("content", "") or ""
            # Check if mentions Jake
            if re.search(r"Jake\s*R|JakeR|@Jake", content, re.IGNORECASE):
                sender = m.get("sender", {}).get("id", "?")
                t = m.get("create_time", "")
                try:
                    ts = datetime.fromtimestamp(int(t) / 1000).strftime("%H:%M") if t else "?"
                except (ValueError, OSError):
                    ts = "?"
                jake_messages.append(f"[{cname}] {ts} {sender}: {content[:100]}")

    if not jake_messages:
        if chat_id: reply_in_chat(chat_id, "最近两天没有人提到你，清净~")
        return True

    # Summarize with AI
    msg_text = "\n".join(jake_messages)
    raw = call_ai(DIGEST_PROMPT.replace("__MESSAGES__", msg_text), "请汇总")
    summary = raw or "\n".join(jake_messages)

    if chat_id:
        reply_in_chat(chat_id, summary)
    log(f"Digest: {len(jake_messages)} jake-related messages")
    return True


def execute_action(action, params, chat_id, sender_id=""):
    if action == "meeting":
        return do_meeting(params, chat_id, sender_id)
    elif action == "cancel_meeting":
        return do_cancel_meeting(params, chat_id)
    elif action == "search_user":
        return do_search_user(params, chat_id)
    elif action == "digest":
        return do_digest(chat_id)
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
            execute_action(action, result.get("params", {}), chat_id, sender_id)
        return

    # --- Group: only respond to @bot ---
    if not is_bot_mentioned(raw_content):
        return

    text = extract_text(raw_content)
    if not text.strip():
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

    # Pull jake-related messages from all chats
    since = datetime.now().replace(hour=0, minute=0, second=0).isoformat()
    chats_result = lark_cmd(["im", "chats", "list", "--as", "bot",
                             "--params", json.dumps({"page_size": 20})])
    msg_lines = []
    if chats_result:
        for c in chats_result.get("data", {}).get("items", []):
            cid = c.get("chat_id", "")
            cname = c.get("name", "")
            if not cid: continue
            msgs = lark_cmd(["im", "+chat-messages-list", "--chat-id", cid,
                             "--start", since, "--page-size", "50"])
            if not msgs or not msgs.get("ok"): continue
            for m in msgs.get("data", {}).get("messages", []):
                content = m.get("body", {}).get("content", "") or ""
                if re.search(r"Jake\s*R|JakeR|@Jake", content, re.IGNORECASE):
                    msg_lines.append(f"[{cname}] {content[:80]}")

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
    log("Lark Bot v6 starting")
    log("Actions: meeting, cancel_meeting, search_user, digest (on-demand)")
    log("Daily report: 20:00")
    log("NO queue, NO background collection — pull on demand only")

    t = threading.Thread(target=scheduler, daemon=True)
    t.start()
    watch_event_dir()


if __name__ == "__main__":
    main()
