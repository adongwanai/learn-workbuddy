# 贡献指南

感谢你对 learn-workbuddy 的兴趣！这个仓库是一个 0→1 的 harness 工程学习项目，欢迎贡献。

## 如何贡献

### 报告问题

如果你发现内容有误、代码不能运行、或某个概念解释不清楚，请 [提交 Issue](https://github.com/adongwanai/learn-workbuddy/issues)，附上：

- 哪一章、哪个文件
- 你期望的内容
- 实际看到的内容

### 提交改进

1. Fork 仓库
2. 创建分支：`git checkout -b fix/s01-typo`
3. 修改内容
4. 运行测试：`python3 -m pytest -q`
5. 运行总验证：`python3 scripts/verify.py`
6. 提交 PR，说明改了什么、为什么改

### 贡献方向

我们特别欢迎以下贡献：

- **翻译** — 将中文教程翻译成英文 / 日文 / 韩文
- **新模型适配** — 在 `.env.example` 中添加更多 Anthropic-compatible 模型
- **SVG 图表改进** — 让架构图更清晰、更美观
- **代码示例** — 为现有章节添加更多实战示例
- **新章节提案** — 如果你发现桌面 agent harness 有未被覆盖的机制，欢迎提议新章节

### 写作规范

- 每章遵循统一结构：`问题 → 解决方案 → 工作原理 → 试一下 → 架构对照 → 下一课`
- 中文叙事，代码注释也用中文
- 离线章节必须能独立运行；需要 API key 的章节必须至少通过语法检查
- 代码是 clean-room 教学版——简化但保留可迁移的 harness 机制
- SVG 图表放在 `images/` 目录，使用统一的配色方案
- 不提交闭源代码、私有 prompt、用户路径、token、日志原文或未脱敏证据

### 测试要求

- `tests/test_project_structure.py` 保护 24 章主线、配图引用和 clean-room 边界。
- `tests/test_mini_workbuddy.py` 覆盖最小 harness 的存储、工具、权限、外部化、agent transcript 和事件流。
- `tests/test_server_protocol.py` 覆盖 REST/ACP-like 协议、请求头门禁和会话恢复。
- `tests/test_lesson_smoke.py` 跑离线章节，并保证需要 API key 的章节至少能通过语法检查。

本地最小检查：

```sh
python3 -m pytest -q
```

发布前完整检查：

```sh
python3 scripts/verify.py
```

### 分支命名

```
fix/s01-typo          # 修复 typo
improve/s08-diagram   # 改进图表
add/s25-new-chapter   # 新增章节
translate/en-s01      # 翻译
```

## 许可证

提交的内容将遵循项目的 [MIT 许可证](./LICENSE)。
