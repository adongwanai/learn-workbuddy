#!/usr/bin/env python3
from __future__ import annotations
"""
s18_experts_system.py - Experts System: Domain Knowledge Packages

Simulates WorkBuddy's expert package lifecycle:

    select expert ──► load package ──► inject system prompt ──► active for session

Key mechanisms simulated:
  1. Expert package structure (system_prompt + tools_config + guidelines + skills)
  2. Expert loading and caching (metadata.json pattern)
  3. System prompt injection with <expert_specialization> block
  4. Session-level persistence (expert_id field)
  5. Expert switching at runtime

Production harnesses often use:
  - Expert Center (专家) with 100+ domain experts
  - Cache at ~/.workbuddy/app/cache/experts/metadata.json
  - expert_id stored in sessions table (SQLite)
  - expert-manager skill for package lifecycle (create/edit/review/convert)
  - Expert marketplace hint for package resolution
  - Instructions wrapped in <expert_specialization> tags in system prompt

Teaching version uses:
  - In-memory expert packages (3 hardcoded experts)
  - Simulated cache (dict, no file persistence)
  - Same prompt injection pattern
  - Same session-level activation concept

Usage:
    python s18_experts_system/code.py
"""



# Machine-readable learning path metadata. Tests enforce that every
# chapter declares what it inherits and what it adds.
PROGRESSION = {'chapter': 's18_experts_system',
 'builds_on': ['s17_mcp_connectors'],
 'adds': ['expert packages', 'expert prompt injection', 'session-level expert state'],
 'preserves': ['external capability model']}
import os, sys, json, time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

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

BASE_SYSTEM = f"""You are a coding agent operating at {WORKDIR}.
You help users with software engineering tasks.
When an expert is active, follow the expert's specialization and guidelines."""


# ============================================================
# Expert Package Definition
# ============================================================

@dataclass
class ExpertPackage:
    """A domain expert package.

    Unlike a skill (which adds one capability), an expert reshapes
    the agent's entire persona for a specific domain.
    """
    expert_id: str
    name: str
    category: str
    description: str
    system_prompt: str           # Specialized system prompt
    guidelines: list[str]         # Behavior guidelines
    preferred_tools: list[str]    # Tools this expert prefers
    skills: list[str]             # Bundled skill names
    icon: str = "📋"

    def to_dict(self) -> dict:
        return {
            "expert_id": self.expert_id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "guidelines": self.guidelines,
            "preferred_tools": self.preferred_tools,
            "skills": self.skills,
            "icon": self.icon,
        }


# ============================================================
# Built-in Expert Packages (simulated marketplace)
# ============================================================

EXPERT_PACKAGES: dict[str, ExpertPackage] = {
    "SoftwareCompany": ExpertPackage(
        expert_id="SoftwareCompany",
        name="软件公司专家",
        category="软件开发",
        description="资深软件架构师，擅长系统设计、技术选型、代码审查",
        icon="🏗️",
        system_prompt="""You are a senior software architect at a leading tech company.
You approach problems with deep technical insight:

- Start with architecture analysis before diving into code
- Consider scalability, maintainability, and trade-offs explicitly
- Reference design patterns by name (Strategy, Observer, Factory, etc.)
- Draw system boundaries and interface contracts
- Always consider error handling, edge cases, and operational concerns
- When reviewing code, focus on: coupling, cohesion, SOLID principles
- Propose solutions with clear pros/cons analysis""",
        guidelines=[
            "Always consider scalability implications",
            "Prefer documented patterns over ad-hoc solutions",
            "Identify trade-offs explicitly — no solution is perfect",
            "Consider operational concerns: monitoring, deployment, rollback",
            "Review code for SOLID principles and design pattern misuse",
        ],
        preferred_tools=["bash", "read_file", "write_file", "edit_file"],
        skills=["code_review", "arch_design", "tech_doc", "api_design"],
    ),
    "UiDesigner": ExpertPackage(
        expert_id="UiDesigner",
        name="UI 设计师",
        category="设计",
        description="资深 UI/UX 设计师，擅长界面设计、设计系统、用户体验",
        icon="🎨",
        system_prompt="""You are a senior UI/UX designer with 10+ years of experience.
You approach problems with a designer's eye:

- Start with user flow analysis before visual design
- Consider accessibility (WCAG), responsive design, and edge states
- Reference design systems: Material, Human Interface, Fluent
- Use precise design terminology: spacing scale, type ramp, elevation
- Always consider the design context: who, what, where, when
- When reviewing UIs, focus on: hierarchy, consistency, affordance, feedback
- Propose solutions with clear rationale tied to user outcomes""",
        guidelines=[
            "Always consider accessibility (WCAG 2.1 AA minimum)",
            "Design for edge cases: empty states, error states, loading states",
            "Maintain consistent spacing, typography, and color systems",
            "Consider mobile-first responsive breakpoints",
            "Provide clear visual hierarchy and information architecture",
        ],
        preferred_tools=["read_file", "write_file"],
        skills=["design_review", "design_system", "prototype", "accessibility_audit"],
    ),
    "TrendResearcher": ExpertPackage(
        expert_id="TrendResearcher",
        name="趋势研究员",
        category="研究",
        description="行业趋势研究员，擅长数据分析、市场研究、报告撰写",
        icon="📈",
        system_prompt="""You are a senior industry trend researcher at a top advisory firm.
You approach problems with analytical rigor:

- Start with framing the research question and scope
- Structure analysis using established frameworks (Porter's 5, SWOT, PESTEL)
- Always cite data sources and distinguish facts from opinions
- Consider multiple perspectives: technology, market, regulatory, competitive
- Use quantitative evidence whenever possible
- Identify both current trends and emerging signals
- Synthesize findings into clear, actionable insights""",
        guidelines=[
            "Always cite data sources and publication dates",
            "Distinguish between facts, estimates, and opinions",
            "Consider multiple scenarios (base, bull, bear cases)",
            "Identify key uncertainties and their potential impact",
            "Structure reports with clear executive summary first",
        ],
        preferred_tools=["bash", "read_file", "write_file"],
        skills=["market_analysis", "data_research", "report_writing", "competitor_analysis"],
    ),
}


# ============================================================
# Expert Manager (simulates cache + session persistence)
# ============================================================

class ExpertManager:
    """Manages expert package loading, caching, and session activation.

    Production harness:
    - Caches expert metadata at ~/.workbuddy/app/cache/experts/metadata.json
    - Stores active expert_id in sessions table (SQLite)
    - expert-manager skill handles create/edit/review lifecycle
    """

    def __init__(self):
        self.cache: dict[str, dict] = {}       # Simulated metadata.json cache
        self.active_expert: ExpertPackage | None = None
        self.session_expert_id: str | None = None

        # Pre-populate cache (simulates marketplace fetch on first run)
        for eid, pkg in EXPERT_PACKAGES.items():
            self.cache[eid] = pkg.to_dict()
        print(f"\033[90m[Experts] Cached {len(self.cache)} expert packages\033[0m")

    def list_experts(self) -> str:
        """Display all available experts."""
        active = self.active_expert.expert_id if self.active_expert else None
        lines = ["\n┌─ Expert Center ──────────────────────────────────────────┐"]
        lines.append("│ ID                 Category    Name              Active │")
        lines.append("│ ─────────────────────────────────────────────────────── │")
        for eid, data in self.cache.items():
            mark = "  ►" if eid == active else "   "
            lines.append(
                f"│{mark} {eid:<16} {data['category']:<8}   {data['name']:<16}{'  ✓' if eid == active else '   '}│"
            )
        lines.append("└─────────────────────────────────────────────────────────┘")
        return "\n".join(lines)

    def get_expert(self, expert_id: str) -> ExpertPackage | None:
        """Load an expert package from cache."""
        data = self.cache.get(expert_id)
        if not data:
            return None
        return ExpertPackage(**data)

    def activate(self, expert_id: str) -> bool:
        """Activate an expert for the current session."""
        expert = self.get_expert(expert_id)
        if not expert:
            print(f"  \033[31mExpert not found: {expert_id}\033[0m")
            return False

        old_name = self.active_expert.name if self.active_expert else "(none)"
        self.active_expert = expert
        self.session_expert_id = expert_id

        print(f"  \033[32m✓ Expert switched: {old_name} → {expert.name} {expert.icon}\033[0m")
        print(f"  \033[90m  Category: {expert.category}\033[0m")
        print(f"  \033[90m  Guidelines: {len(expert.guidelines)} rules active\033[0m")
        print(f"  \033[90m  Skills bundled: {', '.join(expert.skills)}\033[0m")
        return True

    def deactivate(self):
        """Deactivate the current expert."""
        if self.active_expert:
            print(f"  \033[33mExpert deactivated: {self.active_expert.name}\033[0m")
        self.active_expert = None
        self.session_expert_id = None

    def build_system_prompt(self) -> str:
        """Build system prompt with expert specialization injected."""
        if not self.active_expert:
            return BASE_SYSTEM

        expert = self.active_expert
        guidelines_text = "\n".join(f"  - {g}" for g in expert.guidelines)

        return f"""{BASE_SYSTEM}

<expert_specialization>
You are now operating as: {expert.name} ({expert.expert_id})

{expert.system_prompt}

<behavior_guidelines>
{guidelines_text}
</behavior_guidelines>

<bundled_skills>
{', '.join(expert.skills)}
</bundled_skills>
</expert_specialization>"""


# ============================================================
# Built-in Tools (simple, for demonstration)
# ============================================================

def run_bash(command: str) -> str:
    import subprocess
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR, capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:5000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout"

def run_read(path: str) -> str:
    try:
        return safe_path(path).read_text()[:5000]
    except Exception as e:
        return f"Error: {e}"

def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path); fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"

def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path

TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
]

TOOL_HANDLERS = {"bash": run_bash, "read_file": run_read, "write_file": run_write}


# ============================================================
# Agent Loop with Expert System
# ============================================================

class ExpertAgent:
    """Agent loop that respects the active expert's specialization."""

    def __init__(self, manager: ExpertManager):
        self.manager = manager
        self.messages: list[dict] = []

    @property
    def system_prompt(self) -> str:
        """System prompt changes when expert switches."""
        return self.manager.build_system_prompt()

    def run(self, user_input: str):
        """Run one turn of the agent loop."""
        self.messages.append({"role": "user", "content": user_input})

        while True:
            response = client.messages.create(
                model=MODEL,
                system=self.system_prompt,
                messages=self.messages,
                tools=TOOLS,
                max_tokens=8000,
            )
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                for block in response.content:
                    if getattr(block, "type", None) == "text":
                        print(block.text)
                return

            results = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                print(f"\033[36m> {block.name}\033[0m {json.dumps(dict(block.input), ensure_ascii=False)[:100]}")
                handler = TOOL_HANDLERS.get(block.name)
                output = handler(**dict(block.input)) if handler else f"Unknown: {block.name}"
                print(f"\033[90m  {output[:150]}\033[0m")
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})

            self.messages.append({"role": "user", "content": results})


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("s18: Experts System — domain knowledge packages")
    print("=" * 60)
    print("Commands:")
    print("  /list          — List all available experts")
    print("  /use <ID>      — Activate an expert (e.g. /use SoftwareCompany)")
    print("  /off           — Deactivate current expert")
    print("  /status        — Show current expert status")
    print("  /prompt        — Show current system prompt")
    print("  q              — Quit")
    print()

    manager = ExpertManager()
    agent = ExpertAgent(manager)

    print(manager.list_experts())

    while True:
        # Show active expert in prompt
        expert_tag = f"[{manager.active_expert.icon}]" if manager.active_expert else ""
        try:
            query = input(f"\n\033[36ms18{expert_tag} >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break

        query = query.strip()
        if not query:
            continue
        if query.lower() in ("q", "exit", "quit"):
            break

        # Slash commands
        if query.startswith("/"):
            parts = query.split(maxsplit=1)
            cmd = parts[0]
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd == "/list":
                print(manager.list_experts())
            elif cmd == "/use":
                if not arg:
                    print("Usage: /use <expert_id>")
                else:
                    manager.activate(arg)
            elif cmd == "/off":
                manager.deactivate()
            elif cmd == "/status":
                if manager.active_expert:
                    e = manager.active_expert
                    print(f"  Active: {e.name} ({e.expert_id}) {e.icon}")
                    print(f"  Category: {e.category}")
                    print(f"  Guidelines: {len(e.guidelines)} rules")
                    print(f"  Skills: {', '.join(e.skills)}")
                else:
                    print("  No expert active (using base persona)")
            elif cmd == "/prompt":
                print("\n" + manager.build_system_prompt())
            else:
                print(f"Unknown command: {cmd}")
            continue

        # Regular query to agent
        agent.run(query)
