# Learning Guide

这份指南回答三个问题：谁适合读、怎么跑、每个阶段怎么确认自己真的学会了。

## 适合谁

- 你已经能写一个简单 tool-calling agent，但不知道桌面 agent harness 怎么分层。
- 你想理解 session、sidecar、memory、compaction、audit、automation 这些工程机制。
- 你希望像 learn-claude-code 一样，一章一章跑代码，而不是只看架构图。

不需要先会 Electron。这个仓库用 Python 教学实现解释架构边界，Electron 只作为桌面产品分层的概念背景。

## 30 分钟快速路线

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python3 examples/full_tour/code.py
python3 s01_agent_loop/code.py --demo
python3 s04_permission_hooks/code.py --demo
python3 s10_workspace_memory/code.py --demo
python3 s14_context_compact/code.py --demo
python3 s24_comprehensive/code.py --demo
```

这条路线不需要 API key。你会看到完整 harness 的离线链路，以及 agent loop、安全门、记忆、压缩、综合集成这些关键机制。

## 深度路线

| 阶段 | 章节 | 目标 | 验收命令 |
|---|---|---|---|
| Agent Core | s01-s04 | 理解模型、工具、权限之间的反馈环。 | `python3 s04_permission_hooks/code.py --demo` |
| Desktop Runtime | s05-s09 | 理解 UI、sidecar、session、transcript 的分层。 | `python3 s09_jsonl_transcript/code.py --demo` |
| Context & Memory | s10-s15 | 理解记忆选择、输出外部化、压缩和 prompt 组装。 | `python3 s15_prompt_assembly/code.py --demo` |
| Extensions | s16-s20 | 理解 skills、connectors、experts 和交付层。 | `python3 s20_result_presentation/code.py --demo` |
| Production Harness | s21-s24 | 理解 SQLite、自动化、审计和综合 loop。 | `python3 s24_comprehensive/code.py --demo` |

跑完整套：

```sh
python3 scripts/verify.py
```

## 真实模型路线

推荐先用 DeepSeek，因为它能复用 Anthropic-compatible `tool_use/tool_result` 形状，章节脚本和 mini harness 都能跑。

```sh
cp .env.example .env
# 编辑 .env，填 DEEPSEEK_API_KEY

python3 examples/mini_workbuddy_demo/code.py --mode real --provider deepseek
python3 examples/full_tour/code.py --provider deepseek
python3 scripts/run_real_smoke.py --provider deepseek --targets mini full s01 s24
```

全章节真实模型 smoke：

```sh
python3 scripts/run_real_smoke.py --provider deepseek --targets all-lessons
```

OpenAI 路线分两种：

- `--provider openai`：OpenAI Responses API，覆盖 mini harness / full tour。
- `--provider openai-chat`：OpenAI-compatible Chat Completions 网关，覆盖 mini harness / full tour。

章节脚本本身使用 Anthropic-compatible message shape；GPT 网关适配主要在 `mini_workbuddy.providers` 里教学。

## 卡住时看哪里

| 问题 | 看这里 |
|---|---|
| 没有 API key | 先跑 `--demo` 或 `--provider offline`。 |
| DeepSeek 跑不起来 | 检查 `.env` 里的 `DEEPSEEK_API_KEY` 和 `DEEPSEEK_MODEL`。 |
| OpenAI provider 报错 | 确认 `pip install -r requirements.txt` 已安装 `openai`。 |
| 章节跑到真实家目录 | 设置 `WORKBUDDY_HOME=/tmp/learn-workbuddy`。 |
| 安全边界困惑 | 先读 `docs/security-boundaries.md`。 |
| clean-room 边界困惑 | 先读 `docs/legal/clean-room.md` 和 `NOTICE.md`。 |

## 最后验收

学完后，你应该能画出并解释这条链路：

```text
UI shell -> sidecar/session runtime -> agent loop -> tool registry
         -> permission/audit -> transcript/memory/artifacts -> prompt assembly
```

你也应该能说清楚三大矛盾：

- 上下文有限 vs 信息无限
- 自主执行 vs 安全可控
- 模型成本 vs 任务复杂度

这就是 WorkBuddy-style desktop agent harness 的核心。
