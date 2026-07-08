#!/usr/bin/env python3
from __future__ import annotations
"""
s15_prompt_assembly.py - System Prompt Assembly: Runtime Segment Concatenation

The system prompt is NOT a static string — it's assembled at runtime
from multiple segments. Each segment is independently maintained,
conditionally included, and concatenated in order.

Ten segment types:
  1. Base instructions (always)
  2. Identity injection (SOUL/IDENTITY/USER files, if exist)
  3. Cloud memory (<memory> block, if profile exists)
  4. Project context (always — file structure, workdir)
  5. Tool descriptions (dynamic from registered tools)
  6. Expert instructions (if expert is active)
  7. Skill instructions (if skills are loaded)
  8. Connector status (if MCP connectors available)
  9. Regional conventions (stock colors, currency)
  10. Working mode (craft/plan/ask)

  ┌─────────┐ ┌──────────┐ ┌────────┐ ┌─────────┐ ┌──────┐
  │  base   │ │ identity │ │ memory │ │ project │ │ tools│
  │ (always)│ │ (if file)│ │(if mem)│ │ (always)│ │(dyn) │
  └────┬────┘ └────┬─────┘ └───┬────┘ └────┬────┘ └──┬───┘
       └───────────┴───────────┴───────────┴─────────┘
                         │ joiner
       ┌─────────────────┼──────────────────┐
       ▼                 ▼                  ▼
  ┌─────────┐  ┌──────────┐  ┌─────────┐  ┌─────────┐
  │ expert  │  │  skills  │  │ region  │  │  mode   │
  │ (if exp)│  │ (if skil)│  │(always) │  │(always) │
  └─────────┘  └──────────┘  └─────────┘  └─────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │ Final system prompt │
              └─────────────────────┘

Reassembled when: skill loaded, expert changed, mode switched,
connector status changed, identity file modified.

Production harnesses often use: same 10-segment assembly in agent bridge, 100KB+ prompts.
Teaching version uses: simplified segments, ~5-20KB prompts.

Usage:
    python s15_prompt_assembly/code.py
"""



# Machine-readable learning path metadata. Tests enforce that every
# chapter declares what it inherits and what it adds.
PROGRESSION = {'chapter': 's15_prompt_assembly',
 'builds_on': ['s14_context_compact'],
 'adds': ['runtime prompt segments', 'budgeted context blocks', 'assembly order'],
 'preserves': ['memory and compaction inputs']}
import os, sys, time, json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable

try:
    import readline
    readline.parse_and_bind('set bind-tty-special-chars off')
    readline.parse_and_bind('set input-meta on')
    readline.parse_and_bind('set output-meta on')
    readline.parse_and_bind('set convert-meta off')
except ImportError:
    pass

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"): os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path.cwd()
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ.get("MODEL_ID")
if not MODEL:
    raise SystemExit(
        "MODEL_ID is not set. Copy .env.example to .env and fill in "
        "ANTHROPIC_API_KEY and MODEL_ID (see README quick start)."
    )


# ======================================================================
# Prompt Segment system
# ======================================================================

@dataclass
class PromptSegment:
    """A single segment of the system prompt.

    Each segment has:
    - name: identifier for debugging
    - builder: function that returns str | None
    - condition: function that returns bool (default: always True)
    - priority: lower = earlier in the prompt (default: 50)

    If builder returns None, or condition returns False,
    the segment is not included.
    """
    name: str
    builder: Callable[[], str | None]
    condition: Callable[[], bool] = field(default=lambda: True)
    priority: int = 50

    def build(self) -> str | None:
        if not self.condition():
            return None
        return self.builder()


# ======================================================================
# Runtime state (changes trigger reassembly)
# ======================================================================

active_expert: dict | None = None
loaded_skills: list[dict] = []
work_mode: str = "craft"  # craft | plan | ask
region: str = "CN"
connectors: list[dict] = []


# ======================================================================
# Segment builders
# ======================================================================

def build_base_instructions() -> str:
    """Segment 1: Base instructions — always included."""
    return f"""你是一个桌面 AI 助手 (WorkBuddy 教学版)。

工作目录: {WORKDIR}

核心规则:
- 使用工具解决问题, 不要只说不做
- 遵循权限系统, 危险操作需用户确认
- 工具执行前后有 hooks 扩展点
- 回答简洁, 先行动后解释"""


def build_identity() -> str | None:
    """Segment 2: Identity injection — SOUL/IDENTITY/USER files.

    In real WorkBuddy, these files live at ~/.workbuddy/.
    Teaching version: check if they exist, read and inject.
    """
    parts = []
    identity_dir = Path(os.environ.get("WORKBUDDY_HOME", Path.home() / ".workbuddy"))

    for name, filename in [("SOUL", "persona/core.md"),
                            ("IDENTITY", "persona/identity.md"),
                            ("USER", "persona/user.md")]:
        filepath = identity_dir / filename
        if filepath.exists():
            content = filepath.read_text().strip()
            if content:
                parts.append(f"## {name}\n{content}")

    if not parts:
        # Simulated identity (in real usage, user creates these files)
        parts.append("## SOUL\n你是 CodeBuddy, 一个务实高效的编程助手。")
        parts.append("## USER\n用户是开发者, 偏好简洁直接的回答。")

    return "\n\n".join(parts)


def build_cloud_memory() -> str | None:
    """Segment 3: Cloud memory — <memory> block from s12."""
    # Simulated profile (s12 handles real implementation)
    profile = (
        "偏好: Python, TypeScript, 函数式风格, 简洁回答\n"
        "工具: zsh, git, vscode\n"
        "项目: learn-workbuddy 教程"
    )
    return f"<memory>\n{profile}\n</memory>"


def build_project_context() -> str:
    """Segment 4: Project context — file structure, working directory."""
    # List top-level files/dirs in workdir
    try:
        entries = sorted(WORKDIR.iterdir(), key=lambda p: (p.is_file(), p.name))
        tree_lines = []
        for e in entries[:20]:  # Limit to 20 entries
            if e.name.startswith('.'):
                continue
            tree_lines.append(f"  {'📁' if e.is_dir() else '📄'} {e.name}")
        tree = "\n".join(tree_lines) if tree_lines else "  (empty)"
    except Exception:
        tree = "  (unable to read)"

    return f"""## 项目上下文

工作目录: {WORKDIR}
目录结构:
{tree}"""


def build_tool_descriptions() -> str:
    """Segment 5: Tool descriptions — dynamically generated from TOOLS."""
    if not TOOLS:
        return ""
    lines = ["## 可用工具"]
    for tool in TOOLS:
        params = tool.get("input_schema", {}).get("properties", {})
        param_str = ", ".join(params.keys()) if params else "none"
        lines.append(f"- **{tool['name']}**({param_str}): {tool['description']}")
    return "\n".join(lines)


def build_expert_instructions() -> str | None:
    """Segment 6: Expert instructions — only when expert is active."""
    if not active_expert:
        return None
    return f"""## 专家模式: {active_expert['name']}

{active_expert['instructions']}"""


def build_skill_instructions() -> str | None:
    """Segment 7: Skill instructions — loaded skills' SKILL.md content."""
    if not loaded_skills:
        return None
    parts = []
    for skill in loaded_skills:
        parts.append(f"## 技能: {skill['title']}\n{skill['content']}")
    return "\n\n".join(parts)


def build_connector_status() -> str | None:
    """Segment 8: Connector status — available MCP connectors."""
    if not connectors:
        return None
    lines = ["## 连接器状态"]
    for conn in connectors:
        status = "已连接" if conn.get("connected") else "未连接"
        lines.append(f"- {conn['name']}: {status} ({len(conn.get('tools', []))} tools)")
    return "\n".join(lines)


def build_regional_conventions() -> str:
    """Segment 9: Regional conventions — stock colors, currency, dates."""
    if region == "CN":
        return """## 区域约定
- 股市涨跌颜色: 红涨绿跌 (中国市场惯例)
- 货币: CNY (¥)
- 日期格式: YYYY-MM-DD
- 语言: 简体中文"""
    else:
        return """## Regional Conventions
- Stock colors: green up, red down
- Currency: USD ($)
- Date format: MM/DD/YYYY
- Language: English"""


def build_work_mode() -> str:
    """Segment 10: Working mode — craft / plan / ask."""
    modes = {
        "craft": "## 工作模式: Craft\n直接动手, 使用工具完成任务。默认模式。",
        "plan": "## 工作模式: Plan\n先制定计划, 列出步骤, 等用户确认后再执行。",
        "ask": "## 工作模式: Ask\n只回答问题, 不主动执行操作。适合咨询场景。",
    }
    return modes.get(work_mode, modes["craft"])


# ======================================================================
# Segment registry
# ======================================================================

SEGMENTS: list[PromptSegment] = [
    PromptSegment("base", build_base_instructions, priority=10),
    PromptSegment("identity", build_identity, priority=20),
    PromptSegment("memory", build_cloud_memory, priority=25),
    PromptSegment("project", build_project_context, priority=30),
    PromptSegment("tools", build_tool_descriptions, priority=40),
    PromptSegment("expert", build_expert_instructions,
                  condition=lambda: active_expert is not None, priority=50),
    PromptSegment("skills", build_skill_instructions,
                  condition=lambda: len(loaded_skills) > 0, priority=55),
    PromptSegment("connectors", build_connector_status,
                  condition=lambda: len(connectors) > 0, priority=60),
    PromptSegment("region", build_regional_conventions, priority=70),
    PromptSegment("mode", build_work_mode, priority=80),
]


def assemble_system_prompt(verbose: bool = False) -> str:
    """Assemble system prompt from segments.

    1. Sort segments by priority
    2. Build each segment (check condition)
    3. Filter out None results
    4. Join with separator
    """
    sorted_segments = sorted(SEGMENTS, key=lambda s: s.priority)

    parts = []
    segment_info = []
    for seg in sorted_segments:
        content = seg.build()
        included = content is not None
        segment_info.append((seg.name, seg.priority, included,
                            len(content) if content else 0))
        if content:
            parts.append(content)

    if verbose:
        print(f"\n\033[90m{'片段':<15} {'优先级':>6} {'包含':>6} {'长度':>8}\033[0m")
        print(f"\033[90m{'─'*15} {'─'*6} {'─'*6} {'─'*8}\033[0m")
        for name, pri, inc, length in segment_info:
            mark = "✓" if inc else "✗"
            print(f"\033[90m{name:<15} {pri:>6} {mark:>6} {length:>8}\033[0m")
        total = sum(l for _, _, i, l in segment_info if i)
        print(f"\033[90m{'总计':<15} {'':>6} {'':>6} {total:>8}\033[0m\n")

    return "\n\n---\n\n".join(parts)


# ======================================================================
# Reassembly triggers
# ======================================================================

SYSTEM_PROMPT = ""


def reassemble_prompt():
    """Reassemble the system prompt and update the global."""
    global SYSTEM_PROMPT
    SYSTEM_PROMPT = assemble_system_prompt()
    print(f"\033[90m[prompt] 重新组装, 长度: {len(SYSTEM_PROMPT):,} 字符\033[0m")


def load_skill(title: str, content: str):
    """Load a skill — triggers prompt reassembly."""
    loaded_skills.append({"title": title, "content": content})
    reassemble_prompt()
    print(f"\033[32m[prompt] 技能 '{title}' 已加载\033[0m")


def set_expert(name: str, instructions: str):
    """Set active expert — triggers prompt reassembly."""
    global active_expert
    active_expert = {"name": name, "instructions": instructions}
    reassemble_prompt()
    print(f"\033[32m[prompt] 专家 '{name}' 已激活\033[0m")


def switch_mode(mode: str):
    """Switch work mode — triggers prompt reassembly."""
    global work_mode
    if mode not in ("craft", "plan", "ask"):
        print(f"未知模式: {mode}")
        return
    work_mode = mode
    reassemble_prompt()
    print(f"\033[32m[prompt] 工作模式切换为 '{mode}'\033[0m")


def add_connector(name: str, tool_count: int = 5):
    """Add a connector — triggers prompt reassembly."""
    connectors.append({
        "name": name,
        "connected": True,
        "tools": [f"{name}_tool_{i}" for i in range(tool_count)],
    })
    reassemble_prompt()
    print(f"\033[32m[prompt] 连接器 '{name}' 已连接 ({tool_count} tools)\033[0m")


# ======================================================================
# Tools (simplified)
# ======================================================================

def run_bash(command: str) -> str:
    import subprocess
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True, timeout=30)
        return (r.stdout + r.stderr).strip()[:3000] or "(no output)"
    except Exception as e:
        return f"Error: {e}"

def run_read(path: str) -> str:
    try:
        p = (WORKDIR / path).resolve()
        if not p.is_relative_to(WORKDIR):
            return "Error: path escapes workspace"
        return p.read_text()[:3000]
    except Exception as e:
        return f"Error: {e}"

def run_glob(pattern: str) -> str:
    import glob as g
    results = sorted(g.glob(str(WORKDIR / pattern)))[:20]
    return "\n".join(Path(r).name for r in results) if results else "(no matches)"


TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object",
         "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object",
         "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "glob", "description": "Find files matching a pattern.",
     "input_schema": {"type": "object",
         "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}},
]

TOOL_HANDLERS = {"bash": run_bash, "read_file": run_read, "glob": run_glob}


# ======================================================================
# Agent Loop
# ======================================================================

def agent_loop(messages: list):
    """Agent loop using the assembled system prompt."""
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM_PROMPT, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            handler = TOOL_HANDLERS.get(block.name)
            output = handler(**block.input) if handler else f"Unknown: {block.name}"
            display = str(output)[:100].replace('\n', ' ')
            print(f"  \033[36m> {block.name}\033[0m {display}")
            results.append({"type": "tool_result",
                            "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})


# ======================================================================
# Main
# ======================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("s15: Prompt Assembly — 运行时分段拼接")
    print("=" * 60)

    # Initial assembly
    reassemble_prompt()
    assemble_system_prompt(verbose=True)

    print("\033[90m命令:\033[0m")
    print("\033[90m  prompt   — 查看系统提示片段结构\033[0m")
    print("\033[90m  skill    — 模拟加载技能 (触发重新组装)\033[0m")
    print("\033[90m  expert   — 模拟切换专家 (触发重新组装)\033[0m")
    print("\033[90m  mode X   — 切换工作模式 craft/plan/ask\033[0m")
    print("\033[90m  conn     — 模拟连接器上线 (触发重新组装)\033[0m")
    print("\033[90m  stats    — 查看系统提示统计\033[0m")
    print()

    history = []
    while True:
        try:
            query = input("\033[36ms15 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit"):
            break

        cmd = query.strip().lower()

        if cmd == "prompt":
            assemble_system_prompt(verbose=True)
            print(f"\033[90m系统提示总长度: {len(SYSTEM_PROMPT):,} 字符\033[0m")
            print(f"\033[90m前 500 字符预览:\n{SYSTEM_PROMPT[:500]}\033[0m")
            continue

        if cmd == "skill":
            load_skill(
                "git-commit",
                "当用户要求提交代码时, 使用 /commit 命令。\n"
                "1. 检查 git status\n"
                "2. 暂存相关文件\n"
                "3. 生成规范的 commit message"
            )
            continue

        if cmd == "expert":
            set_expert("SoftwareCompany",
                       "你是软件公司架构师。专注于:\n"
                       "- 技术选型和架构设计\n"
                       "- 代码质量和可维护性\n"
                       "- 团队协作和工程实践")
            continue

        if cmd.startswith("mode "):
            switch_mode(query.strip().split(" ", 1)[1])
            continue

        if cmd == "conn":
            add_connector("github", tool_count=8)
            continue

        if cmd == "stats":
            print(f"\033[90m系统提示长度: {len(SYSTEM_PROMPT):,} 字符\033[0m")
            print(f"\033[90m估算 token: {len(SYSTEM_PROMPT)//4:,}\033[0m")
            print(f"\033[90m已加载技能: {len(loaded_skills)}\033[0m")
            print(f"\033[90m激活专家: {active_expert['name'] if active_expert else '无'}\033[0m")
            print(f"\033[90m工作模式: {work_mode}\033[0m")
            print(f"\033[90m连接器: {len(connectors)}\033[0m")
            continue

        # Normal query — send to agent
        history.append({"role": "user", "content": query})
        agent_loop(history)

        for block in history[-1]["content"]:
            if getattr(block, "type", None) == "text":
                print(block.text)
        print()
