# Appendix: Provider Adapter - DeepSeek / Anthropic `tool_use` vs OpenAI `function_call`

`learn-workbuddy` 教的是桌面 agent harness，不是某一家模型厂商的 API。理想状态下，agent loop 不应该关心模型来自 DeepSeek、Anthropic、OpenAI，还是离线 mock。模型协议可以不同，但 harness 的工具执行、权限、审计、转录和外部化逻辑应该保持一致。

这就是 `mini_workbuddy/providers.py` 的作用：把不同 provider 的工具调用协议归一成同一个内部形状。

## 两种协议的差异

| 关注点 | DeepSeek / Anthropic-compatible Messages API | OpenAI Responses API |
|---|---|---|
| 工具定义 | `{name, description, input_schema}` | `{type:"function", name, description, parameters}` |
| 模型请求工具 | `tool_use` content block，含 `id`、`name`、`input` | `function_call` output item，含 `call_id`、`name`、`arguments` |
| 参数编码 | 已经是 dict | JSON 字符串，需要 `json.loads` |
| 返回工具结果 | `tool_result` block，引用 `tool_use_id` | `function_call_output` item，引用 `call_id` |
| 结果放置位置 | `user` message 的 `content` list | 顶层 typed input item |
| 系统提示 | `system=` | `instructions=` |
| assistant turn | `{role:"assistant", content: response.content}` | `response.output` item list |

最容易踩坑的两点：OpenAI 的工具参数是 JSON 字符串，DeepSeek / Anthropic-compatible 是 dict；两边工具调用 ID 字段也不同。adapter 的意义就是把这些差异从主 loop 里拿掉。

## 归一后的内部契约

harness loop 只看三个中性类型：

```python
ToolSpec(name, description, parameters)
ToolCall(id, name, arguments)
ModelTurn(text, tool_calls, raw_assistant)
```

loop 形状因此保持稳定：

```python
turn = provider.create(ProviderRequest(system, messages, tools))
messages.append(turn.raw_assistant)

if not turn.wants_tools:
    break

results = [(call, run_tool(call)) for call in turn.tool_calls]
messages.append(provider.format_tool_results(results))
```

主 loop 不读取 `tool_use_id`，不直接 `json.loads` provider 参数，也不需要知道哪家模型返回了答案。权限、审计、转录、大输出外部化都运行在归一后的 `ToolCall` 上。

## 为什么用 Responses API，而不是 Agents SDK

OpenAI 有两层抽象：

- **Responses API**：适合你自己拥有 agent loop，自己管理工具执行、消息历史和状态。
- **Agents SDK**：适合让 SDK 管 turns、tools、handoffs、sessions 和 tracing。

这个教程的目标是“从 0 复刻 harness”，所以主线应该保留自己的 loop。否则读者会学会调用框架，却看不到框架内部到底在解决什么问题。Agents SDK 可以作为未来附录：“什么时候应该直接用框架”，但不应该替代核心教学实现。

这个判断也和 OpenAI 官方文档一致：Responses API 支持让模型调用你的函数和内置工具；工具调用流程本质上是“模型请求工具 -> 应用执行工具 -> 把 `function_call_output` 带回下一轮”。Agents SDK 则在 Responses API 之上提供更高层 runtime，让 SDK 管 turns、tools、guardrails、handoffs、sessions 和 tracing。

## Provider 选择

`.env` 配置：

```env
PROVIDER=deepseek|anthropic|openai|openai-chat|offline
DEEPSEEK_API_KEY=...
DEEPSEEK_MODEL=deepseek-v4-pro
ANTHROPIC_API_KEY=...
MODEL_ID=...
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1
OPENAI_CHAT_BASE_URL=https://your-openai-compatible-gateway.example/v1
OPENAI_CHAT_API_KEY=...
OPENAI_CHAT_MODEL=gpt-5.5
```

`auto` 逻辑是：优先 Anthropic key，其次 DeepSeek key，再其次 OpenAI Responses key，再其次 OpenAI-compatible Chat key，都没有则回退离线 mock。离线 mock 是一个脚本化工具调用 agent，用来让无 key 读者和 CI 也能跑完整 harness。

## 运行

```bash
# 离线，无 key，确定性
python examples/mini_workbuddy_demo/code.py --mode real --provider offline

# 真实 DeepSeek，推荐学习路径
python examples/mini_workbuddy_demo/code.py --mode real --provider deepseek
python s01_agent_loop/code.py --provider deepseek

# 章节脚本默认写入 ~/.learn_workbuddy/，避免和真实 WorkBuddy 的 ~/.workbuddy/ 撞库。
# 需要临时目录时可加：WORKBUDDY_HOME=/tmp/learn-workbuddy

# 真实 Anthropic
python examples/mini_workbuddy_demo/code.py --mode real --provider anthropic

# 真实 OpenAI
python examples/mini_workbuddy_demo/code.py --mode real --provider openai

# OpenAI-compatible 网关
python examples/mini_workbuddy_demo/code.py --mode real --provider openai-chat
python examples/full_tour/code.py --provider openai-chat

# 一键真实 API 冒烟，需 key
python scripts/run_real_smoke.py --provider openai --targets mini full
python scripts/run_real_smoke.py --provider openai-chat --targets mini full
python scripts/run_real_smoke.py --provider deepseek --targets all-lessons
```

`full_tour` 会做 provider probe：第一轮强制 provider 调用 `tool_search`，然后把工具结果通过同一个 adapter 回传给模型。这用于证明真实 provider 不只是初始化成功，而是经过了 model -> tool -> result 的 harness 回路。

## 对应代码

- `mini_workbuddy/providers.py`：三个 provider 和 `select_provider`。
- `examples/mini_workbuddy_demo/code.py`：同一个 real loop 跑多个 provider。
- `tests/test_providers.py`：provider 选择、schema 翻译、fake-client 解析、离线完整 loop。
- `scripts/run_real_smoke.py`：手动真实 API 冒烟。

## 官方资料

- OpenAI Responses API overview: https://developers.openai.com/api/reference/responses/overview/
- OpenAI function calling guide: https://developers.openai.com/api/docs/guides/function-calling
- OpenAI Agents SDK: https://openai.github.io/openai-agents-python/
- DeepSeek API docs: https://api-docs.deepseek.com/
- DeepSeek Anthropic-compatible guide: https://api-docs.deepseek.com/guides/anthropic_api
