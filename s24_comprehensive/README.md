# s24: Comprehensive — 机制很多, 循环一个

> *"机制很多, 循环一个"* — 24 个机制全部围绕同一个 `while True`。
>
> **Harness 层**: 综合 — 循环属于 agent, 机制属于 harness。

---

![24 课全景|697](./images/comprehensive-overview.svg)

## 代码架构图

```mermaid
flowchart LR
    A["User Request"] --> B["Mini WorkBuddy Runtime"]
    B --> C["Agent + Tools + Memory"]
    C --> D["Server/API"]
    D --> E["Transcript + Audit"]
    C -. "state" .-> S["runtime state"]
    S -. "recover" .-> C
```

## 学习前置知识

- 完整 harness 是多个小机制组合, 不是一个大框架魔法。
- Python 教学实现复刻的是架构机制, 不是 Electron/Node 源码。
- 最终 demo 应该能展示端到端数据流和安全边界。

## 本章抓住的 WorkBuddy-style 机制

- 串起 agent loop、工具、权限、记忆、压缩、SQLite、审计和 HTTP API。
- 用 clean-room Python 证明 WorkBuddy-style harness 的核心可以从零搭建。
- 把前 23 章收束成一个可运行 mini WorkBuddy。

## 常见误区

- 只做聊天界面没有 sidecar/session/memory/audit, 不算 harness。
- 只堆功能不做验证, 教程无法复现。
- 公开表达混淆源码提取和教学实现, 会带来信任和合规风险。
## 问题

24 课, 24 个机制。看起来很多, 但如果回头看, 每一课都在做同一件事：给 agent loop 加一层能力。

```
s01  agent loop              → 循环本身
s02  tool dispatch           → 循环里的工具分发
s03  deferred loading        → 循环里的工具按需展开
s04  permission hooks        → 循环里的安全门
s05  electron shell          → 循环的进程外壳
s06  sidecar server          → 循环的通信管道
s07  session management      → 循环的生命周期
s08  model routing           → 循环的模型选择
s09  jsonl transcript        → 循环的事件记录
s10  workspace memory        → 循环的工作区记忆
s11  user memory             → 循环的用户级记忆
s12  cloud memory            → 循环的远端召回抽象
s13  output externalization  → 循环的大输出换出
s14  context compact         → 循环的上下文压缩
s15  prompt assembly         → 循环的 prompt 组装
s16  skills system           → 循环的技能加载
s17  mcp connectors          → 循环的外部工具协议
s18  experts system          → 循环的领域专家
s19  visualizer              → 循环的输出可视化
s20  result presentation     → 循环的结果交付
s21  sqlite database         → 循环的持久化层
s22  automation scheduler    → 循环的定时触发
s23  audit sandbox           → 循环的安全审计
s24  comprehensive           → 所有机制回到一个循环
```

没有一个机制替代了循环。每一个机制都是在循环的某个环节插入能力——工具调用前加权限检查, API 调用后加用量记录, 会话结束时加记忆蒸馏, 上下文满时加压缩, 执行命令时加沙盒和审计。

这就是本教程的核心洞察：**循环属于 agent。机制属于 harness。** 模型负责推理和决策, harness 负责提供安全、高效、可追溯的执行环境。24 个机制组合在一起, 把一个 30 行的 `while True` 变成了桌面 AI 助手。

---

## 解决方案

```
                    ┌─────────────────────────────────┐
                    │         System Prompt            │
                    │  ┌─────┐ ┌─────┐ ┌───────────┐  │
                    │  │SOUL │ │USER │ │SKILLS list │  │
                    │  │     │ │MEM  │ │EXPERTS    │  │
                    │  └─────┘ └─────┘ └───────────┘  │
                    └──────────────┬──────────────────┘
                                   │
                    ┌──────────────▼──────────────────┐
                    │       ┌─────────────┐           │
                    │       │  Agent Loop │           │
                    │       │  while True │           │
                    │       └──────┬──────┘           │
     ┌──────────────┼──────────────│──────────────────┼──────────────┐
     │              │              │                  │              │
     │   ┌──────────▼─────────┐   │   ┌──────────────▼───────────┐  │
     │   │  Tool Dispatch     │   │   │  Context Management       │  │
     │   │  ┌──────────────┐  │   │   │  ┌──────────────────────┐ │  │
     │   │  │ Permission   │  │   │   │  │ Compaction (s14)     │ │  │
     │   │  │ Check (s04)  │  │   │   │  │ Prompt Assembly (s15)│ │  │
     │   │  ├──────────────┤  │   │   │  └──────────────────────┘ │  │
     │   │  │ Sandbox      │  │   │   └──────────────────────────┘  │
     │   │  │ Check (s23)  │  │   │              │                  │
     │   │  ├──────────────┤  │   │   ┌──────────▼───────────┐     │
     │   │  │ Execute      │  │   │   │  Memory (s10-s12)     │     │
     │   │  ├──────────────┤  │   │   │  workspace / user /   │     │
     │   │  │ Audit Log    │  │   │   │  cloud                 │     │
     │   │  │ (s23)        │  │   │   └───────────────────────┘     │
     │   │  ├──────────────┤  │   │              │                  │
     │   │  │ Usage Track  │  │   │   ┌──────────▼───────────┐     │
     │   │  │ (s21)        │  │   │   │  SQLite DB (s21)      │     │
     │   │  └──────────────┘  │   │   │  sessions / usage /   │     │
     │   └────────────────────┘   │   │  automations           │     │
     │              │              │   └───────────────────────┘     │
     │   ┌──────────▼─────────┐   │              │                  │
     │   │  Visualizer (s19)  │   │   ┌──────────▼───────────┐     │
     │   │  present_files     │   │   │  Automation (s22)     │     │
     │   │  (s20)             │   │   │  Scheduler            │     │
     │   └────────────────────┘   │   └───────────────────────┘     │
     │              │              │              │                  │
     └──────────────┼──────────────│──────────────│──────────────────┘
                    │              │              │
                    └──────────────▼──────────────┘
                    ┌─────────────────────────────┐
                    │    Electron Shell (s05-s07) │
                    │    main + renderer + sidecar│
                    │    + CLI session            │
                    └─────────────────────────────┘
```

### 24 课全景表

| 课 | 机制 | 格言 | 在循环中的位置 |
|---|---|---|---|
| s01 | Agent Loop | 一个循环就够了 | 循环本身 |
| s02 | Tool Dispatch | 加工具不改循环 | 循环内, 工具分发 |
| s03 | Deferred Loading | 工具不全部加载 | 工具分发后, 按需发现 |
| s04 | Permission Hooks | 先划边界再给自由 | 工具执行前 |
| s05 | Electron Shell | 一个进程不够要三个 | 循环的外壳 |
| s06 | Sidecar Server | 主进程不跑 agent | 进程间通信 |
| s07 | Session Mgmt | 每个会话一个子进程 | 循环的生命周期 |
| s08 | Model Routing | 用 AI 管理 AI | API 调用前, 模型选择 |
| s09 | JSONL Transcript | 对话写盘追加不覆盖 | 每轮循环后, 持久化 |
| s10 | Workspace Memory | 每天的工作记下来 | 会话结束后 |
| s11 | User Memory | 跨项目偏好放用户级 | Prompt 组装时 |
| s12 | Cloud Memory | 有些记忆在云端 | Prompt 组装时 |
| s13 | Output Externalization | 大输出写磁盘留指针 | 工具执行后, 入上下文前 |
| s14 | Context Compact | 上下文总会满 | 循环内, API 调用前 |
| s15 | Prompt Assembly | prompt 是组装出来的 | 每次 API 调用 |
| s16 | Skills System | 技能先列目录 | Prompt 组装 + 工具池 |
| s17 | MCP Connectors | 外接工具标准协议 | 工具池扩展 |
| s18 | Experts System | 领域专家整包加载 | Prompt + 工具 + 记忆 |
| s19 | Visualizer | 不只是文字 | 输出处理 |
| s20 | Result Presentation | 做完要交付 | 输出处理 |
| s21 | SQLite Database | 会话要持久 | 每次操作后 |
| s22 | Automation Scheduler | 到点自动跑 | 循环外触发 |
| s23 | Audit & Sandbox | 每步留痕 | 工具执行前后 |
| **s24** | **Comprehensive** | **机制很多循环一个** | **全部集成** |

---

## 工作原理

### 1. 完整的 Agent Pipeline

一个集成了全部机制的 agent 循环, 每一轮做这些事：

```python
def comprehensive_agent_loop(messages, session):
    while True:
        # ── 1. Prompt Assembly (s15) ──
        system = assemble_prompt(
            soul=get_soul(),           # s11: 身份
            user_mem=get_user_memory(), # s11: 用户偏好
            workspace_mem=get_workspace_log(),  # s10: 工作区日志
            cloud_profile=get_cloud_profile(),  # s12: 云端记忆
            skills=list_skills(),       # s16: 技能目录
            expert=get_expert(),        # s18: 领域专家
            tools_context=get_tools_info()  # s17: MCP 连接器
        )

        # ── 2. Context Compaction (s14) ──
        if token_count(messages) > THRESHOLD:
            messages = compact_context(messages)

        # ── 3. Model Routing (s08) ──
        model = route_model(session.agent_type)  # lite/default/craft

        # ── 4. API Call ──
        response = client.messages.create(
            model=model, system=system,
            messages=messages, tools=session.tools,
        )

        # ── 5. JSONL Transcript (s09) ──
        transcript.append({
            "type": "message",
            "role": "assistant",
            "content": response.content
        })

        # ── 6. Usage Tracking (s21) ──
        db.track_usage(session.id, model, response.usage)

        # ── 7. Check stop ──
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            # ── 8. Result Presentation (s19, s20) ──
            present_result(response.content)
            # ── 9. Memory Update (s10) ──
            update_workspace_memory(session, messages)
            return

        # ── 10. Tool Dispatch (s02) + Deferred Loading (s03) ──
        results = []
        for block in response.content:
            if block.type == "tool_use":
                # ── 10a. Deferred Tool? (s03) ──
                if block.name in DEFERRED_TOOLS:
                    tool_schema = tool_search(block.name)  # ToolSearch
                    result = defer_execute(tool_schema, block.input)  # DeferExecuteTool
                    results.append({"type": "tool_result",
                                    "tool_use_id": block.id, "content": result})
                    continue

                # ── 10b. Permission Check (s04) ──
                if not check_permission(block.name, block.input):
                    results.append(denied_result(block.id))
                    continue

                # ── 10c. Sandbox Check (s23) ──
                if not check_sandbox(block.input):
                    results.append(blocked_result(block.id))
                    continue

                # ── 10d. Execute + Audit (s23) ──
                audit_entry("tool_execute", block.name, block.input)
                output = TOOL_HANDLERS[block.name](**block.input)
                audit_entry("tool_result", block.name, output)

                # ── 10e. Output Externalization (s13) ──
                if should_externalize(output):
                    pointer = write_to_disk(output)
                    output = make_pointer(pointer)  # 上下文只留指针

                # ── 10f. Tool Usage (s21) + JSONL (s09) ──
                db.record_tool_call(session.id, block.name)
                transcript.append({
                    "type": "function_call_result",
                    "tool": block.name, "result": output
                })

                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })

        messages.append({"role": "user", "content": results})
```

### 2. 机制分组

24 个机制可以分为 7 组, 每组服务于循环的一个维度：

```
┌──────────────────────────────────────────────────────────────────┐
│                       Agent Loop (s01)                            │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────┐  │
│  │ 工具层    │  │ 进程层    │  │ 持久层    │  │  记忆层         │  │
│  │ s02 s03  │  │ s05 s06  │  │ s09 s21  │  │  s10 s11 s12  │  │
│  │ s16 s17  │  │ s07 s08  │  │ s22      │  │  s15          │  │
│  │ s18      │  │          │  │          │  │                │  │
│  └──────────┘  └──────────┘  └──────────┘  └────────────────┘  │
│                                                                  │
│  ┌──────────────┐  ┌──────────┐  ┌──────────┐                  │
│  │ 上下文管理层  │  │ 安全层    │  │ 交互层    │                  │
│  │ s13 s14      │  │ s04 s23  │  │ s19 s20  │                  │
│  └──────────────┘  └──────────┘  └──────────┘                  │
└──────────────────────────────────────────────────────────────────┘
```

| 层 | 课 | 职责 |
|---|---|---|
| 工具层 | s02, s03, s16, s17, s18 | 工具分发、延迟加载、技能、连接器、专家 |
| 进程层 | s05, s06, s07, s08 | Electron、Sidecar、会话管理、模型路由 |
| 持久层 | s09, s21, s22 | JSONL 对话日志、SQLite、自动化调度 |
| 记忆层 | s10, s11, s12, s15 | 三层记忆、prompt 组装 |
| 上下文管理层 | s13, s14 | 输出外部化、上下文压缩 |
| 安全层 | s04, s23 | 权限检查、沙盒、审计日志 |
| 交互层 | s19, s20 | 可视化、结果交付 |

### 3. 核心洞察

```
Agency 来自模型。
Harness 让 agency 落地。

模型 = Claude / GPT / GLM (推理 + 决策)
Harness = 24 个机制 (执行环境 + 安全 + 记忆 + 持久化)

Agent = 模型 × Harness
```

同样的模型, 放在 30 行的 CLI harness 里, 就是一个终端工具。放在 24 个机制的 Desktop harness 里, 就是一个桌面 AI 助手。模型没变, 变的是 harness。

---

## WorkBuddy 架构对照

> 基于桌面 agent harness 可观察行为抽象出的 clean-room 对照。

### Agent loop — harness 的核心

生产级桌面 agent bridge 通常会把协议、工具循环和安全治理收束到同一个运行时模块。教学版把它拆成：

- Agent loop 的核心循环（s01）
- 工具注册和分发（s02）
- 延迟加载 ToolSearch（s03）
- 权限检查逻辑（s04）
- 流式响应处理
- 模型路由 lite/default/craft（s08）
- JSONL 对话持久化（s09）
- 输出外部化（s13）
- 上下文压缩管线（s14）
- Prompt 分段组装（s15）
- 技能和专家加载（s16, s18）
- 可视化输出注入（s19）
- 用量追踪（s21）

### 多进程架构

```
Electron Main Process
  ├── SidecarServer
  │     ├── JSON-RPC over Unix Socket (s06)
  │     ├── Model Router lite/default/craft (s08)
  │     ├── SQLite Database (s21)
  │     ├── Automation Scheduler (s22)
  │     └── Audit Log Writer (s23)
  ├── Renderer Process (renderer/)
  │     └── React UI (s05)
  ├── Preload Script (preload/)
  │     └── IPC Bridge (s05)
  └── CLI Session Process (cli/)
        └── Agent Loop (agent bridge module)
              ├── Prompt Assembly (s15)
              ├── Tool Dispatch (s02)
              ├── Deferred Loading (s03)
              ├── JSONL Transcript (s09)
              ├── Output Externalization (s13)
              ├── Context Compaction (s14)
              └── Memory Management (s10-s12)
```

### 一句话总结

```
WorkBuddy-style harness = 一个 agent loop (s01)
                        + 22 个累加机制 (s02-s23)
                        + 一个综合收束 (s24)
```

**循环不变, 机制叠加。** 这就是 harness 工程的本质。

---

## 代码 walkthrough

`code.py` 是终点章的集成示例, 在一个文件中展示了全部 24 个机制的核心模式：

1. **Agent Loop**（s01）— `while True` 核心循环
2. **Tool Dispatch**（s02）— `TOOL_HANDLERS` dispatch map
3. **Deferred Loading**（s03）— ToolSearch + DeferExecuteTool 两步调用
4. **Permission Hooks**（s04）— 工具执行前的权限检查
5. **Electron Shell**（s05）— 三进程架构模拟
6. **Sidecar Server**（s06）— JSON-RPC 路由
7. **Session Management**（s07）— 会话状态机
8. **Model Routing**（s08）— lite/default/craft 三级路由
9. **JSONL Transcript**（s09）— 对话追加持久化
10. **Workspace Memory**（s10）— 项目级工作日志
11. **User Memory**（s11）— 跨项目偏好和长期约束
12. **Cloud Memory**（s12）— 远端 profile / recall 抽象
13. **Output Externalization**（s13）— 大输出写磁盘留指针
14. **Context Compaction**（s14）— 简化版上下文压缩
15. **Prompt Assembly**（s15）— 运行时分段组装 system prompt
16. **Skills**（s16）— 技能目录列表
17. **MCP Connectors**（s17）— 外部工具协议
18. **Experts**（s18）— 领域专家包
19. **Visualizer**（s19）— SVG/HTML 注入
20. **Result Presentation**（s20）— present_files 交付
21. **Database**（s21）— SQLite 会话持久化 + 用量追踪
22. **Automation**（s22）— RRULE 定时调度
23. **Audit & Sandbox**（s23）— 命令安全分级 + 哈希链审计
24. **Comprehensive**（s24）— 全部机制集成到一个循环

这不是生产代码, 而是教学集成——每个机制用最简形式展示其在循环中的位置。

---

## 运行

```bash
python s24_comprehensive/code.py
```

运行后, 你会看到一个集成了全部机制的 agent。试试：

1. 和它聊天, 观察每一轮中各个机制的运作
2. 输入 `/status` 查看 agent 的完整状态（会话、用量、工具、记忆）
3. 输入 `/audit` 查看审计链
4. 输入 `/memory` 查看工作区记忆
5. 输入 `/compact` 手动触发上下文压缩

---

## 练习

1. 给集成 agent 添加 MCP 连接器模拟（s17）：定义一个外部工具, 通过 `tools/list` 发现、`tools/call` 执行
2. 添加 Visualizer 模拟（s19）：当 agent 输出包含 `<svg>` 标签时, 保存为文件并"注入"到 UI
3. 添加 Automation 触发（s22）：实现一个 `/schedule` 命令, 把当前 prompt 注册为定时任务

---

## 24 课完结

```
s01  Agent Loop            ──▶  起点: 一个循环 + 一个工具
s02  Tool Dispatch         ──▶  多个工具, 一个 dispatch map
s03  Deferred Loading      ──▶  ToolSearch + DeferExecuteTool 两步调用
s04  Permission Hooks      ──▶  先划边界, 再给自由
s05  Electron Shell        ──▶  三个进程, 一个应用
s06  Sidecar Server        ──▶  JSON-RPC, RingBuffer
s07  Session Management    ──▶  每个会话一个子进程
s08  Model Routing         ──▶  lite/default/craft 三级路由
s09  JSONL Transcript      ──▶  对话持久化, 追加写入, 崩溃恢复
s10  Workspace Memory      ──▶  每天的工作记下来
s11  User Memory           ──▶  跨项目的偏好
s12  Cloud Memory          ──▶  服务端检索
s13  Output Externalization──▶  大输出写磁盘, 上下文留指针
s14  Context Compact       ──▶  四层压缩管线
s15  Prompt Assembly       ──▶  运行时分段拼接
s16  Skills System         ──▶  按需加载技能
s17  MCP Connectors        ──▶  连接器生态
s18  Experts System        ──▶  领域专家包
s19  Visualizer            ──▶  SVG/HTML 可视化
s20  Result Presentation   ──▶  文件交付
s21  SQLite Database       ──▶  WAL 模式, 7 张表
s22  Automation Scheduler  ──▶  到点自动跑
s23  Audit & Sandbox       ──▶  每步留痕, 不可篡改
s24  Comprehensive         ──▶  终点: 全部归到一个循环
```

从 s01 的 30 行 `while True`, 到 s24 的 500 行集成 agent, 循环本身没有变。变的是围绕循环的 harness 机制——权限、记忆、压缩、审计、持久化、调度、可视化。

**Agency 来自模型。Harness 让 agency 落地。造好 Desktop Harness, 模型会完成剩下的。**
