# Further Reading Map

This map turns a curated resource list into a reading path for this tutorial. These links are optional; the code in this repo remains self-contained.

## Core Harness Design

| Read This | Why It Helps | Related Chapters |
|---|---|---|
| [Anthropic: Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents) | Agent loop, tool use, workflows vs agents. Read this first. | s01, s02, s24 |
| [Anthropic: Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) | Long-running sessions, persistence, recovery. | s06, s07, s09, s14 |
| [Anthropic: Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-for-long-running-app-dev) | Coding-agent harness concerns. | s04, s07, s13, s23 |
| [OpenAI: A practical guide to building agents](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf) | Agent components and product framing. | s01, s02, s24 |

## Context Engineering

| Read This | Why It Helps | Related Chapters |
|---|---|---|
| [Anthropic: Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | Context selection, isolation, compaction. | s10-s15 |
| [Claude Code best practices](https://www.anthropic.com/engineering/claude-code-best-practices) | Practical coding-agent workflows. | s04, s07, s16 |
| [Lilian Weng: LLM Powered Autonomous Agents](https://lilianweng.github.io/posts/2023-06-23-agent/) | Planning, memory, reflection overview. | s07, s10-s14 |

## Tools, Skills, and Protocols

| Read This | Why It Helps | Related Chapters |
|---|---|---|
| [Anthropic: Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) | Tool schema design and affordances. | s02, s03, s17 |
| [OpenAI: Responses API overview](https://developers.openai.com/api/reference/responses/overview/) | Direct model interface for stateful interactions, tools, function calling, and built-in capabilities. | s01, s02, s24 |
| [OpenAI: Function calling](https://developers.openai.com/api/docs/guides/function-calling) | Tool-call flow and `function_call_output` shape used by the OpenAI provider adapter. | s02, provider appendix |
| [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) | Higher-level runtime for managed turns, tools, guardrails, handoffs, sessions, and tracing. Useful as a contrast to owning the loop. | provider appendix, future |
| [Model Context Protocol](https://modelcontextprotocol.io) | External tool connector protocol. | s17 |
| [Agent Client Protocol](https://agentclientprotocol.com) | UI/client to agent communication model. | s07, s24 |
| [Claude Code Skills](https://docs.claude.com/en/docs/claude-code/skills) / [Agent Skills](https://www.anthropic.com/news/skills) | Skill packaging patterns. | s16 |
| [datawhale: 如何写出好的 Skill](https://github.com/datawhalechina/hello-agents/blob/main/Extra-Chapter/Extra08-%E5%A6%82%E4%BD%95%E5%86%99%E5%87%BA%E5%A5%BD%E7%9A%84Skill.md) | Practical Chinese guide to writing skills. | s16 |
| [Skill survey: Evaluation and Evolution](https://arxiv.org/pdf/2606.11435) | 4 evolution paradigms, 6 benchmark families, SKILL-INJECT attack taxonomy. Local digest: [skill-evolution-and-evaluation.md](./skill-evolution-and-evaluation.md) | s16, s17, s23 |

## Memory Systems

| Read This | Why It Helps | Related Chapters |
|---|---|---|
| [mem0](https://github.com/mem0ai/mem0) | Memory extraction and retrieval patterns. | s10-s12 |
| [Letta (MemGPT)](https://github.com/letta-ai/letta) | Stateful agents and memory management. | s10-s14 |
| [Generative Agents paper](https://arxiv.org/abs/2304.03442) | Memory, reflection, planning loop. | s10-s14 |

## Multi-Agent Systems

| Read This | Why It Helps | Related Chapters |
|---|---|---|
| [Anthropic: How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) | Context isolation and subagent coordination. | s07, s08, s14 |
| [Cognition: Don't Build Multi-Agents](https://cognition.ai/blog/dont-build-multi-agents) | A useful skeptical counterweight. | s07, s18 |
| [AutoGen](https://github.com/microsoft/autogen) / [CrewAI](https://github.com/crewAIInc/crewAI) | Alternative multi-agent abstractions. | s07, s18 |

## Classic Papers (chronological)

| Paper | Why It Matters Here | Related Chapters |
|---|---|---|
| [ReAct (2022)](https://arxiv.org/abs/2210.03629) | The reasoning + acting loop s01 implements. | s01 |
| [Toolformer (2023)](https://arxiv.org/abs/2302.04761) | Learning when to call tools. | s02, s03 |
| [Reflexion (2023)](https://arxiv.org/abs/2303.11366) | Self-reflection from execution feedback. | s09, s14 |
| [Generative Agents (2023)](https://arxiv.org/abs/2304.03442) | Memory / reflection / planning. | s10-s12 |
| [Voyager (2023)](https://arxiv.org/abs/2305.16291) | Skill library + lifelong learning — the ancestor of skill self-evolution. | s16 |
| [AgentBench (2023)](https://arxiv.org/abs/2308.03688) / [WebArena (2023)](https://arxiv.org/abs/2307.13854) / [SWE-bench (2023)](https://arxiv.org/abs/2310.06770) | How agent capability is measured. | s20, s24 |

## Evals and Observability

| Read This | Why It Helps | Related Chapters |
|---|---|---|
| [Anthropic: Demystifying Evals for AI Agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) | How to test agent behavior. | s20, s23, s24 |
| [Langfuse](https://github.com/langfuse/langfuse) / [Phoenix](https://github.com/Arize-ai/phoenix) / [Helicone](https://github.com/Helicone/helicone) | Tracing and observability. | s09, s21, s23 |
| SWE-bench / τ-bench / WebArena / AgentBench / GAIA | Must-know agent benchmarks. | s20, s24 |

## Similar Build-From-Scratch Tutorials

| Project | Why It Helps | Related Chapters |
|---|---|---|
| [shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) | Gradual Claude Code-style rebuild. | Whole repo |
| [shareAI-lab/claw0](https://github.com/shareAI-lab/claw0) | Agent loop to always-on gateway, ~10 sections. | s01-s24 |
| [datawhalechina/hello-agents](https://github.com/datawhalechina/hello-agents) | Chinese agent fundamentals. | s01-s04 |
| [walkinglabs/learn-harness-engineering](https://github.com/walkinglabs/learn-harness-engineering) | Harness-oriented learning path. | s05-s24 |
| [HKUDS/nanobot](https://github.com/HKUDS/nanobot) | Ultra-light personal agent baseline (Python). | s01-s04, s10 |
| [HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness) | Open harness reference direction. | s06-s24 |
| [lukilabs/craft-agents-oss](https://github.com/lukilabs/craft-agents-oss) | Full-stack agent app architecture sample. | s05-s07, s24 |

## How To Use This Map

Do not read everything first. The best order is:

1. Build chapters s01-s04.
2. Read the core harness design pieces.
3. Build s05-s09.
4. Read context engineering and memory resources.
5. Build s10-s18.
6. Read tools/protocols and multi-agent pieces.
7. Build s19-s24.
8. Read evals and observability resources.
