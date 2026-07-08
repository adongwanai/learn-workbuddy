# Full Tour：一次跑遍完整 harness

章节 demo 每次只隔离一个机制。`full_tour` 反过来：它把 provider、session、记忆、工具、权限、外部化、JSONL、HTTP 和审计串成一条完整链路，让你看到这些层怎么接在一起。运行后还会留下可检查的 artifacts。

当你选择真实 provider 时，tour 会先做一次 provider probe：强制模型通过统一 adapter 调用 `tool_search`，再把工具结果写入 transcript 和 audit。也就是说，`--provider deepseek|anthropic|openai|openai-chat` 不只是初始化 SDK，而是真的跑过一次 model -> tool -> result 循环。

## 代码架构图

```mermaid
flowchart LR
  CLI["examples/full_tour/code.py"]
  Provider["Provider adapter<br/>offline / deepseek / anthropic / openai / openai-chat"]
  Probe["Provider probe<br/>model -> tool_search -> result"]
  Runtime["mini_workbuddy runtime"]
  Tools["ToolRegistry<br/>bash / read_file / tool_search"]
  Store["Storage<br/>sessions / JSONL / memory / artifacts"]
  Audit["AuditLog<br/>hash chain + head anchor"]
  HTTP["HTTP /api/v1/runs"]
  Manifest["full_tour_manifest.json"]

  CLI --> Provider --> Probe
  CLI --> Runtime
  Probe --> Tools
  Probe --> Store
  Probe --> Audit
  Runtime --> Tools
  Runtime --> Store
  Runtime --> Audit
  Runtime --> HTTP
  Store --> Manifest
  Audit --> Manifest
```

## 运行

```bash
# 离线：确定性 mock provider，无 API key，无网络。
python examples/full_tour/code.py

# 真实 provider：需要 .env 里有对应 key。
python examples/full_tour/code.py --provider deepseek
python examples/full_tour/code.py --provider anthropic
python examples/full_tour/code.py --provider openai
python examples/full_tour/code.py --provider openai-chat

# 指定 artifacts 输出目录，默认是临时目录。
python examples/full_tour/code.py --home /tmp/tour
```

只有所有阶段通过，并且审计链验证通过时，退出码才是 `0`。所以它也可以当成粗粒度端到端健康检查。

## 它会走过哪些层

| 阶段 | 层 | 你会看到什么 |
|---|---|---|
| 1 | Provider adapter | 一个 loop，同时适配 offline / deepseek / anthropic / openai / openai-chat |
| 2 | Session | 工作区 cwd 绑定 transcript 和 audit stream |
| 3 | Provider probe | provider 必须产生至少一次规范化工具调用 |
| 4 | Workspace memory | 写入一条项目级持久记忆 |
| 5 | Tool dispatch | agent 只能通过注册工具影响世界 |
| 6 | Permission denial | 危险命令被 fail-closed 拒绝 |
| 7 | Output externalization | 大输出写入文件，prompt 里只留指针 |
| 8 | Transcript + recovery | 新 `Storage` 从 JSONL 恢复会话事件 |
| 9 | HTTP run endpoint | 调一次 ACP-like `/api/v1/runs` |
| 10 | Audit hash chain | 哈希链 + head anchor 验证 |
| 11 | Artifacts | 写出 `full_tour_manifest.json` |

## Artifacts

运行后打开 `--home` 目录下的 `full_tour_manifest.json`。它会列出 provider、provider probe 工具调用数、每个阶段是否通过，以及 workspace memory、externalized output、JSONL transcript、audit log、audit head anchor 的路径。

这些文件都是普通文本，可以直接打开、diff，或者交给另一个工具继续分析。

## 为什么默认离线

CI 和无 key 读者也必须能跑完整链路。离线 mock provider 是一个脚本化的工具调用 agent：列工具、跑 `pwd`、读 README、总结。它不模拟任何真实模型的“智力”，只用来证明 harness plumbing 是通的。

需要真实模型时，加 `--provider deepseek|anthropic|openai|openai-chat` 即可复用同一条 loop。
