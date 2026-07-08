# Skill Evolution & Evaluation (Reference)

本文把上传资料里那篇技能综述（*Agent Skill Evaluation and Evolution:
Frameworks and Benchmarks*）压成一张给 `learn-workbuddy` 用的地图。它
回答两个 s16/s17 教程只碰了一半的问题：**技能怎么自动变好**，以及
**怎么判断一个技能是好是坏、是安全还是恶意**。

> 教程代码保持自包含。本文是可选深读，用于指导后续章节和你自己项目的
> skill 子系统设计。相关章节：s16（Skills）、s17（MCP）、s18（Experts）、
> s23（Audit/Sandbox）。

## 一、技能自进化的 4 种范式

`learn-workbuddy` 现在的技能是**静态**的：`SKILL.md` 写好放进目录，被
发现、被加载。真实系统里技能会**演化**。四条主流路线，各有代价：

| 范式 | 做法 | 优点 | 代价 | 对应章节 |
|---|---|---|---|---|
| 执行反馈 | 技能跑错/结果不对 → 自动定位并改 | 修问题准 | 只能被动补漏，没报错就不优化 | s09 transcript、s23 audit 提供反馈信号 |
| 轨迹蒸馏 | 把多次执行的完整流程提炼成通用步骤，固化为新技能 | 总结经验强 | 执行记录杂乱，易冗余、费资源 | s10 记忆蒸馏是同一手法的记忆版 |
| 压缩 & 整合 | 合并重复技能、精简描述、补缺漏 | 瘦身提效率 | 容易删掉关键步骤或安全规则 | s14 context compaction 的技能版 |
| 强化学习 | 任务成败当奖励，反复训练 | 跨任务复用强 | 算力大，且分不清是技能变好还是模型变强 | 超出教学 harness 范围，仅作认知 |

设计启示：前三种在一个纯 harness 里就能做（不训模型），是你项目里"把
用户重复任务一键固化成技能"功能的理论基础。压缩整合那条尤其要小心——
它是**安全规则被静默删除**的高发区，和下面的安全审计直接相关。

## 二、技能评测的 6 类基准

想在 README 里放"我的技能子系统好在哪"，得知道业界从哪 6 个维度量：

| 维度 | 测什么 | 代表基准 |
|---|---|---|
| 技能效用 (Utility) | 技能对任务完成率的实际提升 | SkillsBench（11 大领域）、SkillCraft（长时序工具调用/复用） |
| 技能生成 (Generation) | 自动生成技能的质量与复用能力 | SkillLearnBench |
| 检索与路由 (Retrieval & Routing) | 大规模技能库里的筛选、调度、多技能编排 | SkillRouter、SRA-Bench、AgentSkillOS |
| 安全审计 (Safety Auditing) | 恶意技能、运行时漏洞检测 | SkillTester、SkillGuardBench、SKILL-INJECT |
| 软件工程 (SWE) | 代码开发/运维场景下的技能 | SWE-SkillsBench |
| 真实环境 (Real-World) | 开放动态真实场景 | WildClawBench、SkillForge |

对教程最相关的两条：**检索与路由**直接对应 s16 的"技能多了以后选哪个"
——综述明确指出，仅靠名称/描述检索，准确率会大幅下降，这正是 s03 延迟
加载 + s16 索引匹配要解决的核心难题；**安全审计**对应 s16 现有的
P0/P1/P2 检查，但那套字符串匹配还远不够，见下一节。

## 三、SKILL-INJECT：三类技能注入攻击

s16 的 `audit_skill()` 目前只扫 `rm -rf /`、`sudo`、`pip install` 这类
**直白模式**。综述里的 SKILL-INJECT 基准指出恶意技能有三类更隐蔽的攻击
面，纯字符串黑名单一个都拦不住：

1. **隐藏覆盖 (hidden override)**：技能表面人畜无害，实际悄悄覆盖或重定义
   了 harness 的默认行为/安全规则。对应"压缩整合"范式删掉安全规则的风险。
2. **伪装转移 (disguised transfer)**：把敏感操作伪装成正常步骤，或把数据
   在看似无关的步骤间转移出去（数据泄露）。
3. **远程引导 (remote guidance)**：技能在运行时从外部拉取指令，真正的恶意
   逻辑不在 `SKILL.md` 里，静态扫描当然看不到——这是 prompt injection 在
   技能层的变体。

这三类和 `docs/security-boundaries.md` 里"字符串匹配是安全带不是沙盒"的
结论完全一致：**技能审计必须超越模式匹配**，走向声明式权限 + 沙盒试跑 +
运行时出口管控。更稳妥的表述是：公开 agent / skill 生态里的供应链风险
已经足够说明，技能市场一旦开放，安全审计就必须成为一等能力。

## 四、给 learn-workbuddy 的落地建议

按性价比排序，不必全做：

1. **给 s16 的 `audit_skill` 补三类攻击的说明与检测占位**（已在 s16 README
   补充），让读者知道 P0/P1/P2 只是第一层。
2. **s16/s17 增加"声明式权限"字段**：技能在 frontmatter 里声明它需要哪些
   工具/网络/路径，加载时和实际行为做 diff，这是拦"隐藏覆盖"和"伪装转移"
   最实用的手段。
3. **未来的自进化章节**：如果加第 25 章，优先做"轨迹蒸馏 → 新技能"，因为它
   纯 harness 可实现、演示效果好（用户重复任务被自动固化），且是 Hermes 那
   类"自进化 Agent"卖点的教学版。
4. **评测意识**：README 里引用上面 6 类基准，说明本项目技能子系统对标的是
   哪几维（Utility + Retrieval/Routing + Safety），比空泛说"好用"有说服力。

## 来源

- *Agent Skill Evaluation and Evolution: Frameworks and Benchmarks*（综述）：
  https://arxiv.org/pdf/2606.11435 。基准名称与分类以该综述口径为准；具体数字按
  "综述口径 / 教学抽象"理解，不绑定某个私有实现。
- Voyager（skill library + 终生学习的开山论文，"轨迹蒸馏 → 技能库"路线的源头）：
  https://arxiv.org/abs/2305.16291
- datawhale《如何写出好的 Skill》（技能编写的中文实操指南）：
  https://github.com/datawhalechina/hello-agents/blob/main/Extra-Chapter/Extra08-%E5%A6%82%E4%BD%95%E5%86%99%E5%87%BA%E5%A5%BD%E7%9A%84Skill.md
