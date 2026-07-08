#!/usr/bin/env python3
from __future__ import annotations
"""
s10_workspace_memory.py - Workspace Memory System

Simulates WorkBuddy's Layer 3 memory: project-local daily logs and
curated long-term notes.

Production harnesses often use:
    - {project}/.workbuddy/memory/YYYY-MM-DD.md — daily logs (append-only)
    - {project}/.workbuddy/memory/MEMORY.md — curated notes (≤3,000 chars/session)
    - Written after substantive work (building, fixing, writing reports)
    - NOT written for trivial exchanges (greetings, simple lookups)
    - Daily logs >30 days distilled into MEMORY.md by topic, then deleted
    - Memory update happens in tool-call phase, before final text reply
    - Memory is supplemental — does NOT replace the normal reply

Teaching version uses:
    - Real file I/O to .workbuddy/memory/ directory
    - Real Anthropic API calls with memory-aware system prompt
    - Simulated distillation with topic-based summarization
    - 3,000 char limit enforcement on MEMORY.md writes

Usage:
    python s10_workspace_memory/code.py
"""



# Machine-readable learning path metadata. Tests enforce that every
# chapter declares what it inherits and what it adds.
PROGRESSION = {'chapter': 's10_workspace_memory',
 'builds_on': ['s09_jsonl_transcript'],
 'adds': ['workspace daily log', 'topic distillation', 'memory injection'],
 'preserves': ['append-only persistence']}
import os, sys, time, json, subprocess, re
from pathlib import Path
from datetime import datetime, timedelta

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
MEMORY_DIR = WORKDIR / ".workbuddy" / "memory"
MEMORY_FILE = MEMORY_DIR / "MEMORY.md"
RETENTION_DAYS = 30
MAX_MEMORY_WRITE = 3000  # chars per session write


# ═══════════════════════════════════════════════════════════════
# WorkspaceMemory — Layer 3 memory: project-local
# ═══════════════════════════════════════════════════════════════

class WorkspaceMemory:
    """
    Manages project-local memory files.

    Real WorkBuddy structure:
        {project}/.workbuddy/memory/
        ├── 2026-07-01.md   (daily log, append-only)
        ├── 2026-07-02.md
        └── MEMORY.md       (curated long-term notes)

    Key rules:
    1. Daily logs are APPEND-ONLY — never overwrite
    2. Write after substantive work only (not trivial exchanges)
    3. MEMORY.md writes capped at 3,000 chars per session
    4. Logs older than 30 days → distilled into MEMORY.md → deleted
    """

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.memory_dir = project_dir / ".workbuddy" / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.memory_file = self.memory_dir / "MEMORY.md"

    def today_log_path(self) -> Path:
        """Get today's daily log file path."""
        return self.memory_dir / f"{datetime.now().strftime('%Y-%m-%d')}.md"

    def append_daily_log(self, entry: str):
        """
        Append to today's daily log. NEVER overwrite.

        Production harness: fs.appendFile(logFile, entry + '\\n')
        The 'a' flag ensures append mode.
        """
        log_path = self.today_log_path()
        timestamp = datetime.now().strftime("%H:%M")

        # Format entry as a log section
        formatted = f"\n## {timestamp} — {entry}\n"

        # APPEND mode — the critical part
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(formatted)

        print(f"\033[90m[memory] Appended to {log_path.name}\033[0m")

    def read_memory_md(self) -> str:
        """Read MEMORY.md (curated long-term notes)."""
        if not self.memory_file.exists():
            return ""
        return self.memory_file.read_text(encoding="utf-8")

    def append_memory_md(self, content: str):
        """
        Append to MEMORY.md with 3,000 char limit per session.

        Production harness: each session can append up to 3,000 chars.
        This is NOT the file size limit — it's per-write limit.
        Prevents agent from over-writing and staying concise.
        """
        # Enforce 3,000 char limit
        if len(content) > MAX_MEMORY_WRITE:
            content = content[:MAX_MEMORY_WRITE] + "\n... (truncated at 3000 chars)"
            print(f"\033[33m[memory] MEMORY.md write truncated to {MAX_MEMORY_WRITE} chars\033[0m")

        with open(self.memory_file, "a", encoding="utf-8") as f:
            f.write(content + "\n")

        print(f"\033[90m[memory] Appended {len(content)} chars to MEMORY.md\033[0m")

    def read_today_log(self) -> str:
        """Read today's daily log."""
        path = self.today_log_path()
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def list_logs(self) -> list[Path]:
        """List all daily log files, sorted by date."""
        logs = sorted(self.memory_dir.glob("*.md"))
        # Exclude MEMORY.md from daily logs list
        return [l for l in logs if l.name != "MEMORY.md" and re.match(r'\d{4}-\d{2}-\d{2}', l.name)]

    def distill_old_logs(self) -> int:
        """
        Distill logs older than 30 days into MEMORY.md, then delete them.

        Production harness:
        1. Read old daily log
        2. Call LLM to summarize by topic
        3. Append summary to MEMORY.md (≤3,000 chars)
        4. Delete original log file

        Returns number of logs distilled.
        """
        cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
        old_logs = []

        for log_path in self.list_logs():
            try:
                log_date = datetime.strptime(log_path.stem, "%Y-%m-%d")
                if log_date < cutoff:
                    old_logs.append(log_path)
            except ValueError:
                continue

        if not old_logs:
            print(f"\033[90m[memory] No logs older than {RETENTION_DAYS} days to distill.\033[0m")
            return 0

        client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
        MODEL = os.environ.get("MODEL_ID")
        if not MODEL:
            raise SystemExit(
                "MODEL_ID is not set. Copy .env.example to .env and fill in "
                "ANTHROPIC_API_KEY and MODEL_ID (see README quick start)."
            )

        distilled_count = 0
        for log_path in old_logs:
            content = log_path.read_text(encoding="utf-8")
            if not content.strip():
                log_path.unlink()
                continue

            # Use LLM to distill by topic
            prompt = f"""Summarize this work log by topic. Keep only essential facts,
decisions, and file changes. Be extremely concise. Max 500 chars.

Log date: {log_path.stem}
Content:
{content[:8000]}

Format: - topic: summary (date)"""

            resp = client.messages.create(
                model=MODEL, max_tokens=1000,
                messages=[{"role": "user", "content": prompt}])

            summary = ""
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    summary += block.text

            # Append to MEMORY.md
            header = f"\n### Distilled from {log_path.stem}\n"
            self.append_memory_md(header + summary)

            # Delete original log
            log_path.unlink()
            distilled_count += 1
            print(f"\033[90m[memory] Distilled {log_path.name} → MEMORY.md (deleted original)\033[0m")

        return distilled_count

    def get_context_for_agent(self) -> str:
        """
        Build memory context string for the agent's system prompt.

        Includes:
        - MEMORY.md (curated long-term notes)
        - Today's daily log (recent context)
        """
        parts = []

        long_term = self.read_memory_md()
        if long_term.strip():
            parts.append(f"## Long-term Memory (MEMORY.md)\n{long_term.strip()}")

        today_log = self.read_today_log()
        if today_log.strip():
            parts.append(f"## Today's Work Log\n{today_log.strip()}")

        return "\n\n".join(parts) if parts else "(no workspace memory yet)"


# ═══════════════════════════════════════════════════════════════
# Agent with Workspace Memory
# ═══════════════════════════════════════════════════════════════

class MemoryAwareAgent:
    """
    Agent that writes workspace memory after substantive work.

    Real WorkBuddy flow:
    1. User sends message
    2. Agent runs tool calls (bash, read_file, etc.)
    3. After tool phase ends → update memory
    4. Then generate final text reply
    5. Memory is supplemental — doesn't replace reply
    """

    # Tools that count as "substantive work"
    SUBSTANTIVE_TOOLS = {"bash", "write_file", "edit_file"}

    def __init__(self, cwd: Path, memory: WorkspaceMemory):
        self.cwd = cwd
        self.memory = memory
        self.client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
        self.model = os.environ["MODEL_ID"]
        self.messages: list[dict] = []
        self._did_substantive_work = False
        self._work_summary: list[str] = []

    def _build_system(self) -> str:
        """Build system prompt with memory context."""
        mem_ctx = self.memory.get_context_for_agent()
        return f"""You are a coding agent at {self.cwd}. Be concise.

## Workspace Memory
{mem_ctx}

## Memory Rules
After doing substantive work (writing code, fixing bugs, making decisions),
you MUST write a memory entry summarizing what you did. Use the write_memory tool.
Do NOT write memory for trivial exchanges (greetings, simple lookups).
Memory entries should record: what changed, why, and which files."""

    def _build_tools(self) -> list[dict]:
        return [
            {
                "name": "bash",
                "description": "Run a shell command.",
                "input_schema": {"type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"]},
            },
            {
                "name": "write_memory",
                "description": "Write a memory entry to today's workspace log. "
                               "Use after completing substantive work.",
                "input_schema": {"type": "object",
                    "properties": {"entry": {"type": "string",
                        "description": "What was done, why, and which files. Past tense."}},
                    "required": ["entry"]},
            },
        ]

    def chat(self, user_message: str) -> str:
        """Run one agent turn with memory integration."""
        self._did_substantive_work = False
        self._work_summary = []

        system = self._build_system()
        tools = self._build_tools()
        self.messages.append({"role": "user", "content": user_message})

        while True:
            resp = self.client.messages.create(
                model=self.model, system=system, messages=self.messages,
                tools=tools, max_tokens=8000)
            self.messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use":
                break

            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    if block.name == "bash":
                        cmd = block.input.get("command", "")
                        print(f"\033[90m[tool] bash: {cmd[:80]}\033[0m")
                        out = self._run_bash(cmd)
                        tool_results.append({"type": "tool_result",
                                             "tool_use_id": block.id, "content": out})
                        self._did_substantive_work = True
                        self._work_summary.append(f"Ran: {cmd[:100]}")

                    elif block.name == "write_memory":
                        entry = block.input.get("entry", "")
                        self.memory.append_daily_log(entry)
                        tool_results.append({"type": "tool_result",
                                             "tool_use_id": block.id,
                                             "content": "Memory entry written."})

            self.messages.append({"role": "user", "content": tool_results})

        # Extract final text
        final = ""
        for block in self.messages[-1]["content"]:
            if getattr(block, "type", None) == "text":
                final += block.text
        return final

    def _run_bash(self, command: str) -> str:
        dangerous = ["rm -rf /", "sudo", "shutdown", "reboot"]
        if any(d in command for d in dangerous):
            return "Error: Dangerous command blocked"
        try:
            r = subprocess.run(command, shell=True, cwd=str(self.cwd),
                               capture_output=True, text=True, timeout=120)
            out = (r.stdout + r.stderr).strip()
            return out[:50000] if out else "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: Timeout (120s)"
        except Exception as e:
            return f"Error: {e}"


# ═══════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════

def main():
    print("╔═══════════════════════════════════════════════════════════╗")
    print("║  s10: Workspace Memory — 日志追加, 主题蒸馏                ║")
    print("║  每天的工作要记下来, 只追加不覆盖                            ║")
    print("╚═══════════════════════════════════════════════════════════╝")
    print()

    memory = WorkspaceMemory(WORKDIR)
    agent = MemoryAwareAgent(WORKDIR, memory)

    print(f"记忆目录: {memory.memory_dir}")
    print(f"长期记忆: {'存在' if memory.memory_file.exists() else '尚无'}")
    print(f"今日日志: {'存在' if memory.today_log_path().exists() else '尚无'}")
    print()
    print("命令:")
    print("  /memory    — 查看 MEMORY.md (长期策展笔记)")
    print("  /today     — 查看今日日志")
    print("  /logs      — 列出所有日志文件")
    print("  /distill   — 蒸馏 30 天前的日志到 MEMORY.md")
    print("  /reset     — 清空对话历史 (保留记忆文件)")
    print("  直接输入   — 与 agent 对话 (agent 会自动写记忆)")
    print("  q          — 退出\n")

    while True:
        try:
            query = input("\033[36ms10 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if query.lower() in ("q", "exit", "quit"):
            break
        if not query:
            continue

        if query == "/memory":
            content = memory.read_memory_md()
            if content:
                print(f"\n\033[33m{'─'*60}")
                print("MEMORY.md (长期策展笔记)")
                print(f"{'─'*60}\n{content}\033[0m\n")
            else:
                print("\n\033[90m(尚无 MEMORY.md — 运行 /distill 蒸馏旧日志)\033[0m\n")
            continue

        if query == "/today":
            content = memory.read_today_log()
            if content:
                print(f"\n\033[33m{'─'*60}")
                print(f"今日日志 ({memory.today_log_path().name})")
                print(f"{'─'*60}\n{content}\033[0m\n")
            else:
                print("\n\033[90m(今日尚无日志 — 做点实质工作后会自动记录)\033[0m\n")
            continue

        if query == "/logs":
            logs = memory.list_logs()
            if logs:
                print(f"\n\033[33m日志文件 ({len(logs)} 个):\033[0m")
                for log in logs:
                    size = log.stat().st_size
                    print(f"  {log.name}  ({size} bytes)")
                print()
            else:
                print("\n\033[90m(尚无日志文件)\033[0m\n")
            continue

        if query == "/distill":
            print("\n\033[90m[memory] 开始蒸馏 30 天前的日志...\033[0m")
            count = memory.distill_old_logs()
            print(f"\033[90m[memory] 蒸馏完成: {count} 个日志已处理\033[0m\n")
            continue

        if query == "/reset":
            agent.messages = []
            print("\033[90m[agent] 对话历史已清空 (记忆文件保留)\033[0m\n")
            continue

        # Normal chat
        result = agent.chat(query)
        print(f"\n\033[32m{result}\033[0m\n")

    print("\n\033[90mGoodbye. 记忆文件保留在 .workbuddy/memory/\033[0m")


if __name__ == "__main__":
    main()
