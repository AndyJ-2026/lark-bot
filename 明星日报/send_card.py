#!/usr/bin/env python3
"""把日报作为 Lark interactive card 发到指定群。

流程：
  1. 用 app_id + app_secret 换 tenant_access_token
  2. 直接 POST 到 /open-apis/im/v1/images 上传图，拿 img_key
     （pure server-side，群里啥都不发，没人会看到上传痕迹）
  3. 构造 interactive card JSON（含 header banner + div text + img element 交错）
  4. 用 lark-cli messages-send 发到目标群（或 --webhook 走 webhook）

用法：
  python3 send_card.py --target oc_xxx --title "..." --report-md ./report.md \
      --images uv=./exports/a.png eftd=./exports/b.png anomaly=./exports/c.png

把图按 KEY=PATH 形式给。卡片里图的出现顺序就是命令行里的顺序。
"""

from __future__ import annotations

import argparse
import json
import os
import secrets as _secrets
import subprocess
import sys
import urllib.request
from pathlib import Path


LARK_BASE = "https://open.larksuite.com"
APP_ID = os.environ.get("LARK_APP_ID", "")
APP_SECRET = os.environ.get("LARK_APP_SECRET", "")
if not APP_ID or not APP_SECRET:
    print("请先 export LARK_APP_ID 和 LARK_APP_SECRET（飞书应用凭证）", file=sys.stderr)
    sys.exit(1)

_token_cache: dict[str, object] = {}


def run(cmd: list[str]) -> dict:
    """运行 lark-cli 并解析 JSON 输出。"""
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[CMD FAIL] {' '.join(cmd)}\n{res.stderr}", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError as e:
        print(f"非 JSON 输出: {res.stdout[:400]}", file=sys.stderr)
        raise


def get_tenant_token() -> str:
    """换 tenant_access_token，结果缓存到内存。"""
    if "token" in _token_cache:
        return _token_cache["token"]  # type: ignore
    req = urllib.request.Request(
        f"{LARK_BASE}/open-apis/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        j = json.loads(resp.read())
    if j.get("code") != 0:
        raise RuntimeError(f"换 token 失败: {j}")
    _token_cache["token"] = j["tenant_access_token"]
    return j["tenant_access_token"]


def upload_image(local_path: Path) -> str:
    """裸 POST 上传到 /open-apis/im/v1/images，返回 img_v3_xxx。群里不发任何消息。"""
    rel = os.path.relpath(local_path, start=Path.cwd())
    print(f"  上传：{rel}")
    token = get_tenant_token()

    boundary = "----xingyao" + _secrets.token_hex(12)
    parts: list[bytes] = []
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="image_type"\r\n\r\n')
    parts.append(b"message\r\n")
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="image"; filename="{local_path.name}"\r\n'.encode()
    )
    parts.append(b"Content-Type: image/png\r\n\r\n")
    parts.append(local_path.read_bytes())
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    req = urllib.request.Request(
        f"{LARK_BASE}/open-apis/im/v1/images",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        j = json.loads(resp.read())
    if j.get("code") != 0:
        raise RuntimeError(f"上传图失败: {j}")
    return j["data"]["image_key"]


def build_card(title: str, report_md: str, img_blocks: list[tuple[str, str]]) -> dict:
    """构造 interactive card。img_blocks: [(段落标题, img_key)]"""
    elements: list = [
        {"tag": "div", "text": {"tag": "lark_md", "content": report_md}},
    ]
    for section_title, img_key in img_blocks:
        elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**{section_title}**"}})
        elements.append({
            "tag": "img",
            "img_key": img_key,
            "alt": {"tag": "plain_text", "content": section_title},
            "mode": "fit_horizontal",
        })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "purple",
        },
        "elements": elements,
    }


def send_via_webhook(webhook_url: str, card: dict) -> dict:
    import urllib.request
    payload = json.dumps({"msg_type": "interactive", "card": card}, ensure_ascii=False).encode()
    req = urllib.request.Request(
        webhook_url, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--target", help="目标 chat_id (oc_xxx)，走 lark-cli bot 身份")
    g.add_argument("--webhook", help="飞书自定义机器人 webhook URL")
    ap.add_argument("--title", required=True, help="卡片标题")
    ap.add_argument("--report-md", required=True, type=Path, help="正文 markdown 文件")
    ap.add_argument("--images", nargs="+", required=True,
                    help="图片，按 SECTION_TITLE=./path/to.png 形式给，按顺序排列")
    args = ap.parse_args()

    report_md = args.report_md.read_text(encoding="utf-8").strip()

    print("上传图片到 Lark...")
    img_blocks: list[tuple[str, str]] = []
    for item in args.images:
        if "=" not in item:
            raise SystemExit(f"--images 项格式错: {item}（要 SECTION=PATH）")
        title, path = item.split("=", 1)
        img_key = upload_image(Path(path).resolve())
        img_blocks.append((title, img_key))

    card = build_card(args.title, report_md, img_blocks)

    if args.webhook:
        print(f"通过 webhook 发送...")
        res = send_via_webhook(args.webhook, card)
    else:
        card_json = json.dumps(card, ensure_ascii=False)
        print(f"发送卡片到 {args.target}...")
        res = run([
            "lark-cli", "im", "+messages-send",
            "--chat-id", args.target,
            "--content", card_json,
            "--msg-type", "interactive",
            "--as", "bot",
        ])
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
