# 明星活动每日播报

## 目标
每天早上一键触发，把现货活动组群里**所有正在跑的明星活动**当日数据拉齐，
生成一张多活动卡片发到飞书「现货活动」组（`oc_66da4b...`）。
活动从上线到下架持续监控，**并行逻辑**，活动数 N 动态变化。

## 监控对象
所有 `config.activities` 列出的活动。

```json
"activities": [
  {"label": "SPACEX (PRE)", "page_name": "...", "uv_target": 12000, "step": 1, "status": "ended"},
  {"label": "TradFI 盛典",   "page_name": "...", "uv_target": 12000, "step": 2},
  {"label": "ZEC",          "page_name": "...", "uv_target": 4500,  "step": 3}
]
```

- **label**：卡片里显示的活动名签
- **page_name**：DataWind 0_流量监控 + 明星项目日报 共用的活动名筛选值
- **uv_target**：达标基准，按活动人为定（无字段可推）
- **step**：对应 DataWind 书签的第几步（页面切换 + 筛选）
- **status**：缺省即 active；`"ended"` 表示已收尾，仍出现在卡片里、带「已收尾」标签

活动增删 = 运营改 config.json + 用 datawind-picker `update-bookmark` 同步书签步骤。

## 数据源（DataWind 书签 `bm-1782218891028`）

| step | 看板 | Tab | 筛选 | 用途 |
|---|---|---|---|---|
| 1..N | 现货活动看板 (39716) | `0_流量监控` | `page_name`=活动 page_name + `view_date`=最近30天 | 该活动每日 UV + 资源位拆分 |
| eftd_overview_step | 现货活动看板 (39716) | `7_异动下钻` | `业务分类`=明星项目 + `eftd_date`=最近30天 | eFTD 趋势 + 各活动占比 |
| daily_new_step | 明星项目日报 (39664) | 明星项目日报 | 无（拉全部活动） | 每日新增 UV/eFTD/eFTTc + Day N |

## 卡片格式

```
📊 明星项目 每日播报 · YYYY-MM-DD

近期活动数据，请大家查看~

达标检查（按 SOP 基准）：
- SPACEX (PRE)：271 / 12,000 = 2.3%（活动已收尾）
- TradFI 盛典：7,207 / 12,000 = 60%
- ZEC：2,338 / 4,500 = 52%

【SPACEX (PRE)】 Day 18 — 已收尾
- UV：271（-92.1%）
- 资源位全线归零（仅 internalmsg 101、NULL 34 残留）
- eFTD：0（-100%）

【TradFI 盛典】 Day 4
- UV：7,207（+19.7%）
- 异动：activitycard +314（+151%）、sidebysidepopup +258（+89%）、internalmsg 0→305、push 0→217
- eFTD：5（+150%）

【ZEC】 Day 12
- UV：2,338（+7.4%）
- 异动：activitycard +123（+17%）、internalmsg +6（+40%）
- eFTD：0

🤖 AI 分析（含资源位调整动作）
- [3 条点评，最后一条带具体调整建议]

[6 张图，嵌入卡片正文 img element]
```

### 6 张图清单
1. 每日新增 - SPACEX（明星项目日报切 SPACEX 截图）
2. 每日新增 - TradFi / ZEC（明星项目日报切 ZEC 截图，含 TradFI 联展）
3. eFTD 总览（明星项目）（现货活动看板 `7_异动下钻` 整 tab）
4. SPACEX UV 异动（现货活动看板 `0_流量监控` 切 SPACEX）
5. TradFI 盛典 UV 异动（同上切 TradFI）
6. ZEC UV 异动（同上切 ZEC）

图来源：datawind-picker `render-chart` 或书签 step 自带截图。
**当前 v1：图缺失时跳过 img element，仅发文字卡片**。下个迭代补图。

## 异动判定
**不卡死阈值**。脚本把每个资源位的日环比明细全部喂给 LLM，由 LLM 决定哪些异动值得报告，
并给出 3 条点评 + 最后一条带"建议调整 XX 资源位"的动作。

## 触发方式
- 手动：`~/lark-bot/明星日报/daily-report.sh`
- 后续可挂 launchd 每天 10:30

## 发送
- 飞书 incoming webhook（现货活动组 `oc_66da4b...`）
- 互动卡片（含 markdown 正文 + 图片 element ×N）

## 目录结构
```
~/lark-bot/明星日报/
├── 需求/spec.md
├── config.json               # activities[] + bookmark_id + webhook + llm_*
├── config.example.json
├── daily-report.sh
├── run.py                    # 主逻辑
├── exports/                  # 本地落地 markdown
└── logs/
```

## 依赖
- datawind-picker（VPN + Chrome 调试端口）
- LLM：lark-bot 同款 OpenAI 兼容 SDK（默认 MiniMax-M2.5，从 lark-bot config 读 llm_api_key）
- Litterbox 图床（图片功能上线后用）
- 飞书 incoming webhook

## 当前已知问题
1. datawind-picker `filter-actions.js:169` 选择器 bug 导致 view_date 设置超时 →
   级联致 page_name 切换在 step 之间不稳定（已在本次修复中解决）。
2. 6 张图自动截图尚未接，v1 文字优先。
