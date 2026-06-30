#!/usr/bin/env python3
"""明星活动每日播报 — N 个明星活动并行监控。

前置：先在 Claude 里跑 DataWind 书签 bm-1782218891028，
导出按 step 号落在 ~/.datawind-picker/exports/*_stepN/。

闭环：N 个活动 = config.activities 列表，每个绑一个 step（0_流量监控筛 page_name），
共享 eftd_overview_step（7_异动下钻）和 daily_new_step（明星项目日报）。
活动从上线持续监控到下架；下架后保留 status:"ended" 标签继续出现在卡片里。
"""

from __future__ import annotations

import csv
import json
import os
import re
import secrets
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path

# ---------- 配置 ----------
ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
EXPORTS_ROOT = Path.home() / ".datawind-picker" / "exports"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)


# ---------- 工具 ----------
def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")
    with (LOG_DIR / "run.log").open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")


def find_export_for_step(step: int) -> Path:
    """按 step 号找最新导出目录。不依赖书签名，只看 `_stepN` 后缀。"""
    suffix = f"_step{step}"
    candidates = sorted(
        [p for p in EXPORTS_ROOT.glob(f"*{suffix}") if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(
            f"找不到 step {step} 的导出。请先跑书签 {CONFIG.get('bookmark_id', '')}。"
        )
    return candidates[0]


def find_csv(export_dir: Path, keyword: str) -> Path | None:
    for p in export_dir.iterdir():
        if p.suffix == ".csv" and keyword in p.name:
            return p
    return None


def _clean_cell(s: str) -> str:
    s = s.strip()
    if s.startswith('="') and s.endswith('"'):
        s = s[2:-1]
    return s


def _normalize_activity_name(page_name: str) -> str:
    """page_name `明星活动_SPACEX(PRE)_0612_20260612` → `明星活动_SPACEX(PRE)_0612`
    （明星项目日报 CSV 的「活动名称」列去掉了末尾 _YYYYMMDD）。"""
    return re.sub(r"_\d{8}$", "", page_name)


# ---------- CSV 解析 ----------
def parse_traffic_trend(csv_path: Path) -> tuple[dict, dict]:
    """流量趋势-广告系列 CSV → (by_medium, grand_total)。
    by_medium: { utm_medium: { 'YYYY-MM-DD': uv } }，NULL campaign + 小计 source。
    grand_total: { 'YYYY-MM-DD': uv }，顶部「总计（去重）」行。
    """
    by_medium: dict[str, dict[str, int]] = defaultdict(dict)
    grand_total: dict[str, int] = {}
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader)  # header
        for row in reader:
            if len(row) < 5:
                continue
            view_time = _clean_cell(row[0])
            try:
                uv = int(row[1]) if row[1] else 0
            except ValueError:
                continue
            campaign = _clean_cell(row[2])
            medium = _clean_cell(row[3])
            source = _clean_cell(row[4])

            if campaign == "总计（去重）" and not medium and not source:
                if view_time.startswith("2"):
                    grand_total[view_time] = uv
                continue
            if campaign != "NULL" or source != "小计" or not medium:
                continue
            if not view_time.startswith("2"):
                continue
            by_medium[medium][view_time] = uv
    return by_medium, grand_total


def parse_daily_new(csv_path: Path) -> dict[str, dict[str, dict]]:
    """每日新增情况 CSV → { 活动名称: { 'YYYY-MM-DD': {day_n, uv, eftd, efttc} } }。

    CSV 列：日环比, 日环比, 活动名称, 活动上线第X天, 统计日期, 访问UV, 新增eFTD, 新增eFTTc合约交易额, 日环比
    每个 (活动, 日期) 有 3 行，主数据在第一行（其余两行只填日环比）。
    """
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if len(row) < 8:
                continue
            act = _clean_cell(row[2])
            day_n = _clean_cell(row[3])
            d = _clean_cell(row[4])
            if not act or not d.startswith("2"):
                continue
            # 只挑主数据行（UV/eFTD/eFTTc 至少一项非空）
            try:
                uv = int(row[5]) if row[5] else None
                eftd = int(row[6]) if row[6] else None
                efttc = float(row[7]) if row[7] else None
            except ValueError:
                continue
            if uv is None and eftd is None and efttc is None:
                continue
            out[act][d] = {
                "day_n": day_n,
                "uv": uv if uv is not None else 0,
                "eftd": eftd if eftd is not None else 0,
                "efttc": efttc if efttc is not None else 0.0,
            }
    return out


def parse_eftd_overview_trend(csv_path: Path) -> dict[str, int]:
    """eFTD_趋势_-_daily_副本 CSV → { 'YYYY-MM-DD': eftd_total }。
    取 deposit_first_currency_v1 == '总计' 的行。"""
    daily: dict[str, int] = {}
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if len(row) < 3:
                continue
            d = _clean_cell(row[0])
            try:
                v = int(row[1])
            except ValueError:
                continue
            currency = _clean_cell(row[2])
            if currency != "总计" or not d.startswith("2"):
                continue
            daily[d] = v
    return daily


def parse_eftd_breakdown(csv_path: Path) -> list[dict]:
    """efttc 异动下钻 副本 CSV → 各活动 eFTD 排名。
    列：business_type, display_activity_name, eFTD, 占比。"""
    rows = []
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader)
        for r in reader:
            if len(r) < 4:
                continue
            bt = _clean_cell(r[0])
            act = _clean_cell(r[1])
            try:
                eftd = int(r[2])
            except ValueError:
                continue
            if bt == "总计":
                continue
            rows.append({"business_type": bt, "activity": act, "eftd": eftd})
    rows.sort(key=lambda x: -x["eftd"])
    return rows


# ---------- 计算 ----------
def compute_activity_uv(activity: dict, today: str, yesterday: str) -> dict | None:
    """跑该活动绑定 step 的 流量趋势-广告系列 CSV，返回 today/yesterday 总 UV 和资源位明细。"""
    step = activity["step"]
    try:
        step_dir = find_export_for_step(step)
    except FileNotFoundError as e:
        log(f"  ⚠ {activity['label']} step{step} 缺失：{e}")
        return None

    trend_csv = find_csv(step_dir, "流量趋势-广告系列")
    if not trend_csv:
        log(f"  ⚠ {activity['label']} step{step} 找不到 流量趋势-广告系列 CSV")
        return None

    by_medium, grand_total = parse_traffic_trend(trend_csv)
    t_uv = grand_total.get(today, 0)
    y_uv = grand_total.get(yesterday, 0)

    # 资源位（utm_medium）日环比明细 — 不卡阈值，全量给 LLM
    rows = []
    for medium, daily in by_medium.items():
        t = daily.get(today, 0)
        y = daily.get(yesterday, 0)
        delta = t - y
        pct = (delta / y * 100) if y else (float("inf") if t else 0.0)
        rows.append({"medium": medium, "today": t, "yesterday": y, "delta": delta, "pct": pct})
    rows.sort(key=lambda r: -abs(r["delta"]))

    return {
        "label": activity["label"],
        "page_name": activity["page_name"],
        "uv_target": activity["uv_target"],
        "status": activity.get("status", "active"),
        "today_uv": t_uv,
        "yesterday_uv": y_uv,
        "uv_pct": ((t_uv - y_uv) / y_uv * 100) if y_uv else 0.0,
        "rate_pct": (t_uv / activity["uv_target"] * 100) if activity["uv_target"] else 0.0,
        "mediums": rows,
        "step_dir": str(step_dir),
    }


def attach_daily_new(activity_view: dict, daily_new: dict, today: str) -> None:
    """从「每日新增情况」CSV 补 Day N + eFTD + eFTTc 给 activity_view。"""
    norm = _normalize_activity_name(activity_view["page_name"])
    rec = daily_new.get(norm, {}).get(today)
    if rec:
        activity_view["day_n"] = rec["day_n"]
        activity_view["eftd_today"] = rec["eftd"]
        activity_view["efttc_today"] = rec["efttc"]
    # 昨日 eFTD 也要（算环比）
    # 从 daily_new 拿 yesterday
    dates = sorted(daily_new.get(norm, {}).keys())
    if len(dates) >= 2:
        y_rec = daily_new[norm][dates[-2]]
        activity_view["eftd_yesterday"] = y_rec["eftd"]


# ---------- 渲染 ----------
def fmt_pct(pct: float) -> str:
    if pct == float("inf"):
        return "新增"
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def render_activity_section(av: dict) -> str:
    label = av["label"]
    status_tag = " — 已收尾" if av.get("status") == "ended" else ""
    day_str = av.get("day_n", "Day ?")
    lines = [f"【{label}】 {day_str}{status_tag}"]
    lines.append(f"- UV：{av['today_uv']:,}（{fmt_pct(av['uv_pct'])}）")

    # 资源位明细 — top 6 by abs delta
    top = av["mediums"][:6]
    if top:
        parts = []
        for m in top:
            if m["delta"] == 0 and m["today"] == 0:
                continue
            if m["yesterday"] == 0 and m["today"] > 0:
                parts.append(f"{m['medium']} 0→{m['today']:,}")
            elif m["today"] == 0 and m["yesterday"] > 0:
                parts.append(f"{m['medium']} {m['yesterday']:,}→0")
            else:
                parts.append(f"{m['medium']} {fmt_pct(m['pct'])}（{m['delta']:+,}）")
        if parts:
            lines.append("- 异动：" + "、".join(parts))

    # eFTD
    if "eftd_today" in av:
        ef_t = av["eftd_today"]
        ef_y = av.get("eftd_yesterday", 0)
        if ef_y:
            ef_pct = (ef_t - ef_y) / ef_y * 100
            lines.append(f"- eFTD：{ef_t}（{fmt_pct(ef_pct)}）")
        else:
            lines.append(f"- eFTD：{ef_t}")
    return "\n".join(lines)


def render_qualification_check(views: list[dict]) -> str:
    lines = ["**达标检查（按 SOP 基准）**"]
    for av in views:
        tag = "（活动已收尾）" if av.get("status") == "ended" else ""
        lines.append(
            f"- {av['label']}：{av['today_uv']:,} / {av['uv_target']:,} = {av['rate_pct']:.1f}%{tag}"
        )
    return "\n".join(lines)


# ---------- LLM 分析 ----------
def call_llm_analysis(views: list[dict], eftd_overview: dict | None) -> str:
    """把全部活动数据 + 资源位明细 + eFTD 喂给 LLM，要求 3 条点评 + 资源位调整建议。"""
    api_key = os.environ.get(CONFIG.get("llm_api_key_env", "MINIMAX_API_KEY"), "")
    if not api_key:
        # Fall back to lark-bot config（避免重复配置 API key）
        try:
            bot_cfg = json.loads(Path.home().joinpath("lark-bot", "config.json").read_text())
            api_key = bot_cfg.get("llm_api_key", "")
        except Exception:
            pass
    if not api_key:
        log("LLM API key 未配置，跳过 AI 分析")
        return "_（未配置 LLM API key，跳过 AI 分析）_"

    try:
        from openai import OpenAI
    except ImportError:
        log("openai SDK 未装，跳过 AI 分析")
        return "_（openai SDK 未装，跳过 AI 分析）_"

    client = OpenAI(api_key=api_key, base_url=CONFIG.get("llm_base_url", "https://api.minimax.io/v1"))

    payload = {
        "activities": [
            {
                "label": v["label"],
                "status": v.get("status", "active"),
                "day_n": v.get("day_n"),
                "uv_today": v["today_uv"],
                "uv_yesterday": v["yesterday_uv"],
                "uv_pct": round(v["uv_pct"], 1),
                "uv_target": v["uv_target"],
                "rate_pct": round(v["rate_pct"], 1),
                "eftd_today": v.get("eftd_today"),
                "eftd_yesterday": v.get("eftd_yesterday"),
                "mediums_top10": [
                    {
                        "medium": m["medium"],
                        "today": m["today"],
                        "yesterday": m["yesterday"],
                        "delta": m["delta"],
                        "pct": round(m["pct"], 1) if m["pct"] != float("inf") else "新增",
                    }
                    for m in v["mediums"][:10]
                ],
            }
            for v in views
        ],
        "eftd_overview": eftd_overview,
    }

    prompt = (
        "你是明星项目流量运营分析助手。下面是今日所有在跑明星活动的数据快照，"
        "请输出**恰好 3 条**点评，每条一行用「- 」开头，简练专业，不超过 30 个汉字。\n"
        "- 第 1 条：综述今日各活动 UV 达标情况\n"
        "- 第 2 条：点出最关键的资源位异动信号\n"
        "- 第 3 条：给出**具体的资源位调整动作**（建议加大/减少哪个资源位、为什么）\n\n"
        f"数据：\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
    )

    try:
        resp = client.chat.completions.create(
            model=CONFIG.get("llm_model", "MiniMax-M2.5"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log(f"LLM 调用失败：{e}")
        return f"_（AI 分析调用失败：{e}）_"


# ---------- 图床 ----------
def upload_litterbox(img_path: Path) -> str:
    boundary = "----xingyao" + secrets.token_hex(12)
    parts = []
    for name, val in [("reqtype", "fileupload"), ("time", "72h")]:
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(f"{val}\r\n".encode())
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="fileToUpload"; filename="{img_path.name}"\r\n'.encode()
    )
    parts.append(b"Content-Type: image/png\r\n\r\n")
    parts.append(img_path.read_bytes())
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        "https://litterbox.catbox.moe/resources/internals/api.php",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        url = resp.read().decode().strip()
    if not url.startswith("http"):
        raise RuntimeError(f"图床返回异常: {url!r}")
    return url


def collect_screenshots(views: list[dict], eftd_step_dir: Path | None, daily_new_dir: Path | None) -> list[tuple[str, Path]]:
    """收集 6 张图（活动数变化时图数也变）。每个 step 目录里找 .png。"""
    imgs: list[tuple[str, Path]] = []
    # 各活动 UV 异动图
    for v in views:
        step_dir = Path(v["step_dir"])
        for p in step_dir.glob("*.png"):
            imgs.append((f"{v['label']} UV 异动", p))
            break  # 每活动取 1 张
    # eFTD 总览
    if eftd_step_dir:
        for p in eftd_step_dir.glob("*.png"):
            imgs.append(("eFTD 总览（明星项目）", p))
            break
    # 每日新增
    if daily_new_dir:
        for p in daily_new_dir.glob("*.png"):
            imgs.append(("每日新增情况", p))
            break
    return imgs


# ---------- 飞书 ----------
def send_lark(text: str, img_urls: list[tuple[str, str]]) -> dict:
    webhook = CONFIG.get("webhook_url", "").strip()
    if not webhook:
        log("webhook_url 未配置，跳过发送")
        return {"skipped": True}

    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": text}}]
    for title, url in img_urls:
        elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**{title}**"}})
        elements.append({
            "tag": "img",
            "img_key": url,  # 注意：incoming webhook 直接外链需用 image_key，没有 key 就用按钮兜底
            "alt": {"tag": "plain_text", "content": title},
        })

    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"📊 明星项目 每日播报 · {date.today().isoformat()}"},
                "template": "blue",
            },
            "elements": elements,
        },
    }
    req = urllib.request.Request(
        webhook,
        data=json.dumps(card).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


# ---------- 主流程 ----------
def main() -> int:
    activities = CONFIG.get("activities", [])
    if not activities:
        log("config.activities 为空，无活动可监控")
        return 1
    log(f"开始：{len(activities)} 个活动并行")

    # Step 5（明星项目日报）先解：要从中拿 today/yesterday 日期
    daily_new_dir = None
    daily_new: dict = {}
    try:
        daily_new_dir = find_export_for_step(CONFIG.get("daily_new_step", 5))
        log(f"daily_new 导出：{daily_new_dir}")
        dn_csv = find_csv(daily_new_dir, "每日新增情况")
        if dn_csv:
            daily_new = parse_daily_new(dn_csv)
            log(f"  - {dn_csv.name}（{len(daily_new)} 个活动）")
    except FileNotFoundError as e:
        log(f"daily_new 跳过：{e}")

    # 算 today/yesterday：从首个活动的 流量趋势 CSV grand_total 推
    today = yesterday = None
    for act in activities:
        try:
            sd = find_export_for_step(act["step"])
            tc = find_csv(sd, "流量趋势-广告系列")
            if tc:
                _, gt = parse_traffic_trend(tc)
                dates = sorted(d for d in gt.keys() if d.startswith("2"))
                if len(dates) >= 2:
                    today, yesterday = dates[-1], dates[-2]
                    break
        except FileNotFoundError:
            continue
    if not today or not yesterday:
        log("无法从任何 step 推出 today/yesterday 日期")
        return 1
    log(f"对比窗口：{yesterday} → {today}")

    # 每个活动算 UV + 资源位
    views: list[dict] = []
    for act in activities:
        log(f"活动「{act['label']}」 step{act['step']}")
        av = compute_activity_uv(act, today, yesterday)
        if not av:
            continue
        if daily_new:
            attach_daily_new(av, daily_new, today)
        views.append(av)
        log(f"  UV {av['today_uv']:,} ({fmt_pct(av['uv_pct'])})，达标 {av['rate_pct']:.1f}%")

    if not views:
        log("所有活动都拉数失败")
        return 1

    # eFTD 总览
    eftd_overview = None
    eftd_step_dir = None
    try:
        eftd_step_dir = find_export_for_step(CONFIG.get("eftd_overview_step", 4))
        log(f"eFTD overview 导出：{eftd_step_dir}")
        trend_csv = find_csv(eftd_step_dir, "eFTD_趋势")
        bd_csv = find_csv(eftd_step_dir, "efttc异动下钻_副本")
        if trend_csv:
            daily = parse_eftd_overview_trend(trend_csv)
            if daily:
                dates = sorted(daily.keys())
                eftd_overview = {
                    "today": daily[dates[-1]],
                    "yesterday": daily[dates[-2]] if len(dates) >= 2 else None,
                }
        if bd_csv:
            bd = parse_eftd_breakdown(bd_csv)
            eftd_overview = eftd_overview or {}
            eftd_overview["breakdown"] = bd[:10]
    except FileNotFoundError as e:
        log(f"eFTD overview 跳过：{e}")

    # LLM 分析
    ai_text = call_llm_analysis(views, eftd_overview)

    # 渲染 markdown
    sections = [
        f"近期活动数据，请大家查看~",
        "",
        render_qualification_check(views),
        "",
    ]
    for av in views:
        sections.append(render_activity_section(av))
        sections.append("")
    sections.append("**🤖 AI 分析（含资源位调整动作）**")
    sections.append(ai_text)
    report_md = "\n".join(sections)

    print()
    print("====== 日报正文 ======")
    print(report_md)
    print("======================")
    print()

    (ROOT / "exports").mkdir(exist_ok=True)
    (ROOT / "exports" / f"report_{date.today().isoformat()}.md").write_text(report_md, encoding="utf-8")

    # 截图收集 + 上传
    img_urls: list[tuple[str, str]] = []
    if "--no-upload" not in sys.argv:
        imgs = collect_screenshots(views, eftd_step_dir, daily_new_dir)
        log(f"待上传图片：{len(imgs)} 张")
        for title, path in imgs:
            try:
                url = upload_litterbox(path)
                img_urls.append((title, url))
                log(f"  {title}: {url}")
            except Exception as e:
                log(f"  {title} 上传失败：{e}")

    if "--dry-run" in sys.argv:
        log("dry-run：跳过发送")
        return 0

    resp = send_lark(report_md, img_urls)
    log(f"飞书返回：{resp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
