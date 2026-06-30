#!/usr/bin/env python3
"""校验本次书签导出的 CSV 数据准确性。

策略：
  1. 找到今天所有 step1-5 的导出目录
  2. 每个活动的 UV 透视表里抽出"总计 (去重)"行的 6-28 列数值
  3. 对照已知正确值（来自 2026-06-28 的截图人工核对）
  4. 检查关键资源位（sidebysidepopup/internalmsg/push）的 6-28 值是否一致

不通过任意一项 → 退出非零，调用方应放弃发送。
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

EXPORTS_ROOT = Path.home() / ".datawind-picker" / "exports"
TODAY = date.today().isoformat()

# 已知基准值（来自 6-28 人工核对的截图）
KNOWN_0628 = {
    "SPACEX": {
        "total": 3418,
        "sidebysidepopup": 188,
        "internalmsg": 473,
        "push": 16,
    },
    "TradFI": {
        "total": 6023,
        "sidebysidepopup": 289,
        "tradfi_b": 746,
    },
    "ZEC": {
        "total": 2177,
        "appquicklink": 1077,
        "appassetbanner": 177,
    },
}

# 书签 step ↔ 活动映射（按 update-bookmark 时的顺序）
STEP_TO_ACTIVITY = {
    "step1": "SPACEX",
    "step2": "TradFI",
    "step3": "ZEC",
    # step4 是 eFTD（异动下钻），step5 是 每日新增
}


def _clean(s: str) -> str:
    s = s.strip()
    if s.startswith('="') and s.endswith('"'):
        s = s[2:-1]
    return s


def find_step_dir(step_idx: int) -> Path | None:
    """找今天的某一 step 导出目录。"""
    pattern = f"{TODAY}_*_step{step_idx}"
    candidates = sorted(EXPORTS_ROOT.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def parse_uv_csv(csv_path: Path) -> dict[str, dict[str, int]]:
    """返回 { medium: { date: uv } }，包括 'total' 表示总计去重行。"""
    out: dict[str, dict[str, int]] = defaultdict(dict)
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if len(row) < 5:
                continue
            date_ = _clean(row[0])
            try:
                uv = int(row[1]) if row[1] else 0
            except ValueError:
                continue
            camp = _clean(row[2])
            med = _clean(row[3])
            src = _clean(row[4])

            if camp == "总计（去重）" and not med and not src:
                if date_.startswith("2"):
                    out["total"][date_] = uv
            elif camp == "NULL" and src == "小计" and med:
                if date_.startswith("2"):
                    out[med][date_] = uv
    return out


def find_trend_csv(d: Path) -> Path | None:
    for p in d.iterdir():
        if "流量趋势-广告系列" in p.name and p.suffix == ".csv":
            return p
    return None


def verify():
    print(f"=== 校验 {TODAY} 导出数据 ===\n")
    all_ok = True
    summary = {}

    for step_idx in [1, 2, 3]:
        activity = STEP_TO_ACTIVITY[f"step{step_idx}"]
        known = KNOWN_0628[activity]

        d = find_step_dir(step_idx)
        if not d:
            print(f"✗ {activity} (step{step_idx}): 找不到导出目录")
            all_ok = False
            continue

        csv_path = find_trend_csv(d)
        if not csv_path:
            print(f"✗ {activity}: {d} 里没找到流量趋势 CSV")
            all_ok = False
            continue

        data = parse_uv_csv(csv_path)
        print(f"--- {activity} ({d.name}) ---")

        # 校验 6-28 已知值
        local_ok = True
        for key, expected in known.items():
            got = data.get(key, {}).get("2026-06-28")
            if got is None:
                print(f"  ✗ {key} 6-28: 数据缺失")
                local_ok = False
            elif got != expected:
                print(f"  ✗ {key} 6-28: got={got}, expected={expected}（差 {got - expected:+d}）")
                local_ok = False
            else:
                print(f"  ✓ {key} 6-28: {got}")

        # 抓今日数据（6-29）
        today_data = {k: data.get(k, {}).get("2026-06-29", "缺失") for k in known.keys()}
        print(f"  今日 6-29: {today_data}")

        summary[activity] = {
            "ok_0628": local_ok,
            "today_0629": today_data,
            "csv": str(csv_path),
        }
        all_ok = all_ok and local_ok

    print(f"\n=== 总结 ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if all_ok:
        print("\n✓ 全部校验通过，数据可信，可以生成日报")
        return 0
    else:
        print("\n✗ 校验失败，请检查 filter 是否生效或活动名是否正确")
        return 1


if __name__ == "__main__":
    sys.exit(verify())
