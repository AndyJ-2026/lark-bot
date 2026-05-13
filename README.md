# Lark Bot — 飞书 AI 助手

可配置的飞书 AI 机器人。Clone 后一键安装，聊天内完成配置，零编码上手。

## 快速开始

```bash
git clone https://github.com/AndyJ-2026/lark-bot.git
cd lark-bot
./setup.sh        # 一键安装所有依赖
./start-local.sh  # 启动机器人
```

启动后在飞书私聊机器人，按提示完成配置（取名字、填 API Key）。

## 功能

| 功能 | 触发方式 | 说明 |
|------|----------|------|
| 群聊回复 | 群里 @bot | AI 生成回复，可执行操作 |
| 私聊对话 | 1v1 发消息 | 带上下文，所有人可用 |
| 约会议 | @bot "帮我约个会" | 解析时间/参与者，调日历 API |
| 取消会议 | @bot "取消那个会议" | 按关键词匹配删除 |
| 查人 | @bot "查一下 xxx" | 搜索飞书通讯录 |
| 消息汇总 | @bot "汇总一下" | 拉近 2 天消息，AI 总结 |
| 定向分析 | @bot "帮我看看 XX 提了什么" | 按人/话题分析群消息 |
| 设提醒 | @bot "明天中午提醒我写周报" | AI 解析时间，到时间自动发消息 |
| 会议转写 | 私聊 "帮我转写" | 录音 + ASR 转写 + AI 纪要 + 飞书云文档 |
| 工作日报 | 每天 20:00 自动 | 汇总当天日历+消息，私信 owner |
| 帮助 | "帮助" / "help" | 发送功能清单卡片 |

## 安装要求

- macOS 12.3+
- 飞书开放平台应用（需 App ID 和 Secret）
- 大模型 API Key（推荐 MiniMax，也支持 DeepSeek、通义千问等 OpenAI 兼容 API）

`./setup.sh` 会自动安装：Homebrew、Node.js、lark-cli@1.0.0、Python 3.12、Python 依赖、meeting-cli（会议录音）。

## 首次配置

启动后第一个私聊机器人的人自动成为 owner。机器人会引导你：

1. 给机器人取名字
2. 填写大模型 API Key
3. 配置完成，发送功能清单卡片

所有配置保存在 `config.json`，无需编码。

## 技术栈

- 事件接收：lark-cli@1.0.0 WebSocket
- 消息处理：Python 3.12
- AI：可配置（OpenAI 兼容 API）
- 会议转写：ScreenCaptureKit + FunASR SenseVoice-Small
- 凭证存储：macOS Keychain

## 注意事项

- lark-cli 锁定 1.0.0 版本（新版不兼容海外版 Lark）
- lark-cli 凭证存 macOS Keychain，不要删除相关条目
- 首次会议转写会下载 ASR 模型（约 800MB）
