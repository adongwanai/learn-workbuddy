# Review & Fixes — 对标 learn-claude-code

这份文档记录本轮对 `learn-workbuddy` 教程的审阅结论、修复项和新增测试。定位与 `docs/code-quality-review.md` 一致：不是营销，是维护清单。

## 总体评价

项目底子很好：24 章渐进链路 + `PROGRESSION` 元数据契约、`mini_workbuddy` 可运行 runtime、clean-room 扫描、CI、27 张 SVG 配图和每章 Mermaid 架构图，工程完整度已经达到甚至超过多数同类"从零构建"教程。对标 learn-claude-code，缺的不是规模而是几个**正确性与诚实性**的细节。本文件记录已经落地的修复，以及仍需单独推进的缺口。

## 修复的 Bug（按严重度）

### 1. [高] mini harness 两处未捕获异常会整轮崩溃

`mini_workbuddy` 号称有错误边界（`MiniAgent.prompt` catch 了 `TimeoutError` 等），但两条路径能绕过它：

- **不平衡引号**：`bash echo "unclosed` → `shlex.split` 抛 `ValueError`，未被捕获，整个 prompt 崩溃。
- **命令超时**：`subprocess.TimeoutExpired` **不是** 内置 `TimeoutError` 的子类，所以 `_bash` 的 30s 超时会穿过 agent 的 `except (... TimeoutError)` 直接崩。

修复：解析失败 → **fail-closed** 转成 `PermissionError`（拒绝执行，而非崩溃）；超时 → 转成内置 `TimeoutError`，让既有错误边界正常兜住，返回 `Tool failed: ...`。
回归测试：`tests/test_security_regressions.py`。

### 2. [高] 审计哈希链防改不防删（截断盲区）

`audit.py` 的哈希链能检测**修改**历史条目，但检测不了**删尾**——因为任何合法链条的前缀本身也是合法链条。攻击者删掉末尾 N 行，`verify()` 依旧返回 `True`。

修复：追加时同步写链外锚点文件 `audit.head`（记录 `count` + 链尾 `hash`），`verify()` 交叉比对。删尾、清空现在都能检出。旧日志（无锚点）保持向后兼容。
回归测试：`test_audit_truncation_is_detected_by_head_anchor` 等 4 个用例。
文档：`s23_audit_sandbox/README.md` 常见误区已补这一条，并指向 `docs/security-boundaries.md`。

### 3. [中] 缺少 `MODEL_ID` 时抛 `KeyError` 而非友好提示

18 个章节用 `MODEL = os.environ["MODEL_ID"]`，新手忘了配 `.env` 会得到一行难懂的 `KeyError: 'MODEL_ID'`。
修复：统一换成 `os.environ.get` + `SystemExit` 友好提示，指向 README quick start。
回归测试：`test_missing_model_id_gives_quickstart_hint`。

### 4. [中] `--mode offline` 其实并不 offline

demo 在模块顶层 `from anthropic import Anthropic`，导致声称"无需 API key、CI 友好"的离线模式在没装 SDK 时直接 `ModuleNotFoundError`。
修复：SDK 改为 `run_real_api_demo()` 内的惰性导入。现在离线 demo 零第三方依赖（除 `python-dotenv`）即可跑完整 harness 并通过审计校验。

### 5. [中] 章节状态写用户真实 home，测试有污染风险

6 个章节硬编码 `Path.home() / ".workbuddy"`，跑教程会往真实 home 写文件，且可能和本机已安装 WorkBuddy 的 SQLite schema 撞库。
修复：改为 `mini_workbuddy.paths.tutorial_workbuddy_home()`，默认写入 `~/.learn_workbuddy/`；仍可用 `WORKBUDDY_HOME` 重定向到临时目录。
回归测试：`test_chapters_do_not_touch_real_home_at_import`、`test_s24_does_not_collide_with_existing_real_workbuddy_db`。

### 6. [低] 文档数字不一致

`docs/code-quality-review.md` 写"37 个架构图"，实际是 27 张 SVG + 每章 mermaid。已更正为准确表述。

## 新增测试（自动化）

| 文件 | 覆盖 | 用例数 |
|---|---|---|
| `tests/test_chapter_imports.py` | 24 章全部离线导入（子进程 + stub SDK），无网络/无 key；MODEL_ID 缺失提示；不写真实 home | 3 |
| `tests/test_security_regressions.py` | 上述 bug 1/2 的崩溃修复、审计截断检测、deny-list 边界契约 | 14 |
| `tests/test_permission_gates.py` | s04 三道权限门的单元测试（离线导入章节），含**明确的绕过边界**用例 | 17 |
| `tests/stubs/anthropic.py` | 离线 stub SDK：`messages.create()` 被调用即报错，防止测试意外走真实模型 | — |

`tests/stubs` 的意义：**之前 20 个 API 章节在 CI 里只做 `py_compile` 语法检查**，能编译但 import 崩溃查不出来（比如本轮的 MODEL_ID 问题）。stub SDK 让这些章节第一次能被真正 import 测试。

## 同步更新

- `.github/workflows/ci.yml`：新增 `python -m pytest -q` 步骤（此前只跑 `verify.py`，而 `verify.py` 内部调 pytest 却没装，导致该步静默失败）。
- `docs/security-boundaries.md`（新增）：诚实声明教学 harness 挡得住什么、**挡不住什么**（间接命令绕过、prompt injection、恶意 skill、密钥外泄、网络出口），并给出上生产的最低门槛。核心结论是：安全层才是产品，字符串匹配只是安全带，不是沙盒。
- `s04` / `s23` README 常见误区补充指向该文档。

## 仍建议后续补（非阻塞）

1. **prompt injection 章节**：目前 24 章覆盖了权限/沙盒/审计，但没有专门讲"工具结果本身是攻击面"。这是当前 agent 安全最热的缺口，加一章会很有辨识度。
2. **skill 安全扫描**：`s16`/`s17` 加载第三方能力时无静态扫描。可作为"最安全的开源桌面 Agent"差异化卖点的代码落点。
3. **24 章统一 `--demo` / `--interactive` 退出协议 + verify 全跑**：让"每章可运行"从章节契约变成 CI 事实。
4. **协议补全**：ACP-like server 可加 `session/cancel`、`session/list`（`docs/code-quality-review.md` 已列）。
5. **传播层**：英文 README、Web 文档站、章节导航页、架构图索引页。
6. **`docs/progression-diffs/`**：每章相对上一章的类/函数/数据流 diff，强化"渐进"体感。
7. **视觉回归**：Playwright/HTML 渲染截图检查 README 与长章节图文布局。

## 第二轮补充（基于完整版 AgentGuide 资料）

- `docs/skill-evolution-and-evaluation.md`（新增）：技能综述精华本地化——4 种自进化范式、6 类评测基准、SKILL-INJECT 三类攻击模式（隐藏覆盖 / 伪装转移 / 远程引导），并给出 4 条按性价比排序的落地建议（含未来 s25"轨迹蒸馏→技能固化"章节的方向）。
- `s16_skills_system/README.md`：安全审计小节补充"P0/P1/P2 只是第一层"警示与三类攻击模式说明，链接到上述文档与 security-boundaries。
- `docs/further-reading.md`：全面重写——原版只有标题没有链接，现在补齐全部官方文章 / 论文 / 工程仓库的真实 URL，新增"Classic Papers"板块（ReAct → Voyager → SWE-bench 时间线，Voyager 正是 s16 技能库路线的学术源头）和技能综述条目。
- `docs/chapter-map.md`：新增"8 个面试考点 ↔ 章节映射"表，把每个考点对到可运行章节和可讲的实现细节，服务于本教程读者的求职场景。

## 第三轮补充（基于本机分析稿评审 + 双 provider 建议）

### clean-room 表达清理（评审问题 #1）
把几处"像源码结论"的措辞改成教学抽象口径，与 README"非源码提取"一致：
- `s08_model_routing/code.py`：将类似"从产品实现抽取"的措辞改成教学抽象说明。
- `s03_deferred_loading/README.md`：把运行时精确数字改成教学估算口径。
- `s06_sidecar_server/code.py`：把产品内部领域数量、RPC 命名等说法改成 domain-based routing / teaching-scale sample。
- 另清理若干章节中过度具体的 bundle 路径、文件大小和内部数量表述。

### 双 Provider 适配层（评审 OpenAI 建议）
不做"一刀切换 OpenAI"，而是按建议做 Provider Adapter——这本身就是 harness 教学的一课（loop 稳定，provider 可换）：
- `mini_workbuddy/providers.py`（新增）：把 Anthropic `tool_use/tool_result` 与 OpenAI Responses `function_call/function_call_output` 归一成 `ToolSpec`/`ToolCall`/`ModelTurn` 三个中性类型；含 `AnthropicProvider`、`OpenAIResponsesProvider`、`OfflineMockProvider` 与 `select_provider`（explicit > PROVIDER env > auto，auto 无 key 时回落 offline）。
- `examples/mini_workbuddy_demo/code.py`：real 循环重构为走适配层，**一套 loop 同时服务两家**；新增 `--provider anthropic|openai|offline`。保留 `--mode real` 无 key 时的原始报错（既有测试契约不破）。
- `.env.example`：新增 `PROVIDER`、`OPENAI_API_KEY`、`OPENAI_MODEL`。
- `requirements.txt`：OpenAI SDK 已纳入默认安装，保证 README 中 `--provider openai` 路径是 clone 后可直接配置运行的正式路径。
- `docs/appendix/provider-adapter.md`（新增）：两种协议逐字段对照表 + 为什么用 Responses API 而非 Agents SDK（教程要自己拥有 loop）+ 运行示例。
- `README.md`：新增"为什么双 provider"小节。

### 真实 API smoke 闭环（评审问题 #2）
- `scripts/run_real_smoke.py`（新增）：一键真实模型冒烟，覆盖 mini demo（及可选 s01/s24），支持 `--provider anthropic|openai --targets ...`。opt-in、需 key、CI 默认不跑，但可被 maintainer 接入带 secret 的 gated job。

### 测试
- `tests/test_providers.py`：provider 选择逻辑（含无 key 时 SystemExit 而非 KeyError）、两家 schema 翻译、结果格式差异、fake client 解析测试、**离线 mock 驱动完整 loop 跑通真实 harness 并通过审计校验**。

### 工程
- 按评审建议在主目录做了 **baseline commit**（此前 `.git` 无任何提交、全部 untracked，改动来源难追溯）。

## 仍未做（评审 #3/#4/#6/#7，按投入产出排序）
这些是"从能发布 → 爆火级"的包装层，非正确性阻塞，建议后续单独推进：
1. **24 章统一 `--demo`/`--interactive` 退出协议 + verify 全跑**（#3）：让"每章可运行"从宣称变成 CI 事实。
2. **传播层**（#6）：英文 README、Web 文档站、章节导航页、架构图索引页。
3. **`docs/progression-diffs/`**（#4）：每章相对上一章的类/函数/数据流 diff，强化"渐进"体感。
4. **视觉回归**（#7）：Playwright/HTML 渲染截图检查 README 与长章节图文布局。

## 第三轮（基于外部评审 + 双 provider 方向）

评审里 6 点里的大部分（provider adapter、.env 的 PROVIDER/OPENAI 项、requirements 的可选 openai、`tests/test_providers.py`、`scripts/run_real_smoke.py`）在上传版本里已经实现且质量不错——`mini_workbuddy/providers.py` 用统一的 `ProviderRequest`/`ModelTurn` 契约把 Anthropic 的 `tool_use/tool_result` 和 OpenAI Responses 的 `function_call/function_call_output` 归一化，agent loop 对三个 provider（anthropic/openai/offline mock）完全一致。本轮继续补齐剩下的缺口：

- **清理残留的过度具体源码表述**（评审 #1）：`s07/s08/s13` 三处 `mirrors WorkBuddy's ...` 注释改为"教学抽象 / 教学估算，非源码派生"；`s03` README 的"Token 节省实测"标题改为"（教学估算）"，与正文口径一致。全仓库 clean-room 扫描无残留（`NOT extracted from` 这类否定式除外）。
- **完整演示 `examples/full_tour/`**（评审 #5，最大缺口）：一条命令走遍全部 harness 层——provider 适配 → session → 工作区记忆 → 工具分发 → 权限拒绝（fail-closed）→ 大输出外部化 → JSONL 转录 + 崩溃式恢复 → HTTP `/api/v1/runs`（ACP-like）→ 审计哈希链 + 锚点校验，最后输出 `full_tour_manifest.json` 列出所有 artifacts。默认离线（mock provider，无 key 无网络），`--provider anthropic|openai` 可切真实模型。退出码仅在每个阶段都通过且审计链校验通过时为 0，因此兼作端到端健康检查。
- **测试与 CI 接线**：新增 `tests/test_full_tour.py`（离线跑通、退出 0、逐阶段 flag 为真、manifest 指向的 artifact 都存在），并把 full tour 加入 `test_lesson_smoke.py` 的离线脚本清单，让既有 CI 覆盖它。
- **README 快速开始**：离线块新增 `python3 examples/full_tour/code.py`，让读者一眼看到"一次跑遍所有层"的入口。

### 评审中属于本机 / 发布层、不在本轮代码范围的

- baseline commit（`.git` 无 commit、全 untracked）：主工作仓已经补过基线提交，后续改动应继续基于该仓库提交，避免来源不可追踪。
- 24 章统一 `--demo/--interactive` 协议全量 verify（评审 #3）：现有 CI 用代表章节 + 全章节离线 import 测试（`test_chapter_imports.py`）已能挡住 import 级崩溃；把 24 章全部改造成统一退出协议是一次较大的接口重构，建议单独一个 PR 做，避免和本轮改动混在一起。
- Web 文档站 / 英文 README / Playwright 视觉回归（评审 #6、#7）：属于传播与发布包装层，是独立工作流，不阻塞当前代码正确性。
