# Code Quality Review

本文件记录 learn-workbuddy 对标 `shareAI-lab/claw0` 与 `learn-claude-code` 后的代码层结论。它不是营销文案，而是后续维护时的取舍清单。

## 对标结论

| 项目 | 强项 | learn-workbuddy 已有 | learn-workbuddy 还可补 |
|---|---|---|---|
| claw0 | 10 个 section 极清晰，每节只加一个概念，代码和文档共置 | 24 章机制拆解、每章 README + `code.py` + 图 | 可增加多语言版本，但不应现在复制三套代码 |
| learn-claude-code | agents 包、tests、skills、web 站点，工程生态完整 | `mini_workbuddy`、pytest、CI、完整验证脚本 | 可增加 Web 文档站和更多可复用 runtime 包 |
| learn-workbuddy | WorkBuddy-style 桌面 harness 主题更完整，覆盖 sidecar、记忆、审计、自动化、可视化 | clean-room 边界、完整 demo、27 张 SVG 架构图 + 每章 mermaid 代码架构图 | 代码层应继续强化安全边界、恢复能力和协议细节 |

## 已补强的代码项

- `mini_workbuddy.tools` 现在会限制 `read_file` 只能读取 session `cwd` 内的真实文件，拒绝绝对路径逃逸、`..` 逃逸和符号链接逃逸。
- tool call id 从毫秒时间戳改成 `time_ns + counter`，避免高频工具调用写入同名 `tool-results`。
- `mini_workbuddy.audit` 不再静默忽略损坏尾行；日志中出现坏 JSON 或结构错误时，`verify()` 返回 `False`。
- 新增测试覆盖路径逃逸、符号链接逃逸、审计坏行、审计篡改和高频 call id 唯一性。

## 建议补充

1. **协议层更完整**：ACP-like server 可增加 `session/cancel`、`session/list`、`session/history` 的 JSON-RPC 方法，与 REST 能力对齐。
2. **sidecar 可测试化**：现在 `SidecarManager.start()` 是阻塞循环，适合作为教学代码，但不够易测。可拆出单次 request parser 和 process supervisor 状态机。
3. **章节 runner**：增加 `python scripts/run_all_lessons.py --offline`，把离线章节、交互 smoke、mini demo 做成读者可见的命令，而不只在 `verify.py` 里。
4. **Web 文档站**：对标 learn-claude-code 的 web 目录，可以加静态文档站；这属于传播层，不是当前代码正确性的阻塞项。
5. **多语言策略**：claw0 的三语共置很强，但 learn-workbuddy 章节更多，直接复制三套代码会显著增加维护成本。更合适的是先翻译 README/章节 README，不复制 `code.py`。

## 建议不要补或暂缓

| 项 | 原因 |
|---|---|
| 三套语言代码副本 | 维护成本太高，24 章会变成 72 份代码，容易漂移 |
| 真实 Electron app | 会模糊 clean-room 教学目标，且引入大量 UI 维护面 |
| 真实云端记忆服务 | 容易被理解成复刻私有后端，教学版保持抽象更稳 |
| 大规模依赖框架 | 当前标准库 mini harness 更适合读者从 0 理解机制 |

## 冗余观察

- 早期本地观察附录已收口为公开安全的 evidence 摘要和架构文档。公开入口继续以 24 章为主，研究材料只保留可迁移的模式，不保留包体或本机证据细节。
- `s19_visualizer` 与 `s20_result_presentation` 代码形态相近，但语义不同：前者解释“结果如何可视化”，后者解释“完成后如何交付”。可保留。
- `s10_workspace_memory`、`s11_user_memory`、`s12_cloud_memory` 三章不应合并。它们分别对应项目、用户、远端 profile/search 三个所有权边界，合并会削弱 WorkBuddy 记忆系统的核心卖点。

## 当前质量门

交付前必须通过：

```sh
python3 -m pytest -q
python3 scripts/verify.py
```

`scripts/verify.py` 覆盖 Python 语法、pytest、章节结构、README 架构图、SVG 渲染、离线 demo、交互 smoke、HTTP smoke、clean-room 扫描和公开文档过度具体化扫描。
