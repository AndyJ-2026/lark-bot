#!/usr/bin/env python3
"""在 UV 透视表截图上画红框，圈出指定资源位 + 指定日期列。

用法：
  python3 annotate.py --in <png> --out <png> --rows internalmsg,push,activitycard,sidebysidepopup

行高/列宽根据图像比例自动估算，必要时可改下面常量。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from PIL import Image, ImageDraw

# 两种模板：
#  - "tight"：纯表格截图，2000x605（昨天用的）
#  - "cropped-full"：full-window 截图裁底部表格区后的 2000x705

TEMPLATES: dict[str, dict] = {
    "tight": {
        "W": 2000,
        "H": 605,
        "rows": {
            "header":         (0,   40),
            "total":          (40,  95),
            "NULL":           (95,  145),
            "sidebysidepopup":(145, 195),
            "appspotads":     (195, 245),
            "appquicklink":   (245, 295),
            "activitycard":   (295, 345),
            "activitycenter": (345, 395),
            "bottombanner":   (395, 445),
            "appassetbanner": (445, 495),
            "internalmsg":    (495, 545),
            "push":           (545, 605),
        },
        "col_today": (560, 720),
    },
    "cropped-full": {
        "W": 2000,
        "H": 705,
        "rows": {
            "header":         (0,   60),
            "total":          (60,  115),
            "NULL":           (115, 170),
            "sidebysidepopup":(170, 225),
            "appspotads":     (225, 280),
            "appquicklink":   (280, 335),
            "activitycard":   (335, 390),
            "activitycenter": (390, 445),
            "bottombanner":   (445, 500),
            "appassetbanner": (500, 555),
            "internalmsg":    (555, 610),
            "push":           (610, 665),
        },
        "col_today": (580, 760),
    },
    # 全窗口截图（2000x1236）—— row 居中在视觉单元格中线，row_h≈45
    "full-window": {
        "W": 2000,
        "H": 1236,
        "rows": {
            "total":          (635,  680),
            "NULL":           (680,  723),
            "sidebysidepopup":(723,  767),
            "appspotads":     (767,  811),
            "appquicklink":   (811,  856),
            "activitycard":   (856,  900),
            "activitycenter": (900,  945),
            "bottombanner":   (945,  990),
            "appassetbanner": (990,  1035),
            "internalmsg":    (1035, 1080),
            "push":           (1080, 1125),
        },
        "col_today": (630, 800),
    },
    # 6-28 SPACEX UV 透视表（2000x887）—— grid 实测，每行 50px，总计起始 y=300
    "spacex-0628": {
        "W": 2000,
        "H": 887,
        "rows": {
            "total":          (300, 350),
            "NULL":           (350, 400),
            "sidebysidepopup":(400, 450),
            "appspotads":     (450, 500),
            "appquicklink":   (500, 550),
            "activitycard":   (550, 600),
            "activitycenter": (600, 650),
            "bottombanner":   (650, 700),
            "internalmsg":    (700, 750),
            "appassetbanner": (750, 800),
            "push":           (800, 850),
        },
        "col_today": (620, 780),
    },
    # 6-28 TradFI UV 透视表（2000x887）—— 数据行从 y=300 开始，每行 50px
    "tradfi-0628": {
        "W": 2000,
        "H": 887,
        "rows": {
            "activitycenter": (300, 350),
            "sidebysidepopup":(350, 400),
            "appassetbanner": (400, 450),
            "NULL":           (450, 500),
            "activitycard":   (500, 550),
            "apptopsearch":   (550, 600),
            "icon":           (600, 650),
            "webmenu":        (650, 700),
            "ann":            (700, 750),
            "webassetbanner": (750, 800),
            "webbanner":      (800, 850),
            "tradfi_b":       (850, 887),
        },
        "col_today": (620, 780),
    },
    # 6-28 ZEC UV 透视表（2000x887）—— 数据行从 y=275 开始，每行 50px
    "zec-0628": {
        "W": 2000,
        "H": 887,
        "rows": {
            "total":          (275, 325),
            "NULL":           (325, 375),
            "activitycard":   (375, 425),
            "appquicklink":   (425, 475),
            "appassetbanner": (475, 525),
            "internalmsg":    (525, 575),
            "NULL2":          (575, 625),
            "activitycenter": (625, 675),
            "push":           (675, 725),
            "apptopsearch":   (725, 775),
            "webbanner":      (775, 825),
            "ann":            (825, 875),
        },
        "col_today": (620, 780),
    },
    # 6-29 SPACEX UV 透视表（2000x1239）—— grid 实测，每行 ~45px
    "spacex-0629": {
        "W": 2000,
        "H": 1239,
        "rows": {
            "total":          (618, 663),
            "NULL":           (663, 708),
            "sidebysidepopup":(708, 753),
            "appspotads":     (753, 798),
            "appquicklink":   (798, 843),
            "activitycard":   (843, 888),
            "activitycenter": (888, 933),
            "bottombanner":   (933, 978),
            "internalmsg":    (978, 1023),
            "appassetbanner": (1023, 1068),
            "push":           (1068, 1113),
        },
        "col_today": (620, 790),
    },
    # 6-29 TradFI UV 透视表（2000x1239）—— 顶部只有 NULL（没有 总计 (去重)）
    "tradfi-0629": {
        "W": 2000,
        "H": 1239,
        "rows": {
            "NULL":           (613, 658),
            "appquicklink":   (658, 703),
            "WebTradeProgress":(703, 748),
            "activitycenter": (748, 793),
            "sidebysidepopup":(793, 838),
            "appassetbanner": (838, 883),
            "NULL2":          (883, 928),
            "activitycard":   (928, 973),
            "internalmsg":    (973, 1018),
            "push":           (1018, 1063),
            "icon":           (1063, 1108),
            "apptopsearch":   (1108, 1153),
        },
        "col_today": (620, 790),
    },
    # 6-29 ZEC UV 透视表（2000x1239）
    "zec-0629": {
        "W": 2000,
        "H": 1239,
        "rows": {
            "total":          (623, 668),
            "NULL":           (668, 713),
            "activitycard":   (713, 758),
            "appquicklink":   (758, 803),
            "appassetbanner": (803, 848),
            "internalmsg":    (848, 893),
            "activitycenter": (893, 938),
            "NULL2":          (938, 983),
            "push":           (983, 1028),
            "apptopsearch":   (1028, 1073),
            "webbanner":      (1073, 1118),
            "ann":            (1118, 1163),
        },
        "col_today": (620, 790),
    },
}

LINE_WIDTH = 4
COLOR = (235, 65, 65, 255)


def scale(coord: int, total: int, template: int) -> int:
    return int(coord * total / template)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--rows", required=True, help="逗号分隔，如 internalmsg,push,activitycard")
    ap.add_argument("--full-row", action="store_true", help="圈整行而不是只圈今日列")
    ap.add_argument("--template", default="tight", choices=list(TEMPLATES.keys()),
                    help="坐标模板：tight = 纯表格截图；cropped-full = 全窗口裁出的表格")
    args = ap.parse_args()

    tpl = TEMPLATES[args.template]
    TEMPLATE_W = tpl["W"]
    TEMPLATE_H = tpl["H"]
    ROWS = tpl["rows"]
    COL_TODAY = tpl["col_today"]

    im = Image.open(args.in_path).convert("RGBA")
    w, h = im.size
    draw = ImageDraw.Draw(im)

    rows = [r.strip() for r in args.rows.split(",")]
    x0_tpl, x1_tpl = (0, TEMPLATE_W) if args.full_row else COL_TODAY
    x0 = scale(x0_tpl, w, TEMPLATE_W)
    x1 = scale(x1_tpl, w, TEMPLATE_W)

    for r in rows:
        if r not in ROWS:
            print(f"warn: 没有行配置 {r}，跳过")
            continue
        y0_tpl, y1_tpl = ROWS[r]
        y0 = scale(y0_tpl, h, TEMPLATE_H)
        y1 = scale(y1_tpl, h, TEMPLATE_H)
        draw.rounded_rectangle([x0, y0, x1, y1], radius=6, outline=COLOR, width=LINE_WIDTH)

    im.save(args.out)
    print(f"saved → {args.out}")


if __name__ == "__main__":
    main()
