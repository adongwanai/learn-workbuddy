#!/usr/bin/env python3
from __future__ import annotations
"""
s11_user_memory.py - User-Level Memory and Identity System

Simulates WorkBuddy's Layer 2 memory: cross-project preferences stored
at ~/.workbuddy/ with identity files (SOUL/IDENTITY/USER/BOOTSTRAP).

Production harnesses often use:
    - ~/.workbuddy/MEMORY.md    — cross-project memory (≤4,000 chars/session)
    - ~/.workbuddy/persona/core.md      — core personality, values, boundaries
    - ~/.workbuddy/persona/identity.md  — name, creature type, vibe, emoji
    - ~/.workbuddy/persona/user.md      — user's name, pronouns, city, notes
    - ~/.workbuddy/persona/bootstrap.md — one-time setup file, deleted after identity established
    - Bootstrap flow: conversation → write SOUL/IDENTITY/USER → delete persona/bootstrap.md
    - User-level memory is explicitly written (not implicitly learned)
    - Used for precise, mandatory rules that must be followed exactly

Teaching version uses:
    - Real file I/O to ~/.workbuddy/ directory
    - Real Anthropic API for bootstrap conversation
    - Simulated identity file management
    - 4,000 char limit enforcement on MEMORY.md writes

Usage:
    python s11_user_memory/code.py
"""



# Machine-readable learning path metadata. Tests enforce that every
# chapter declares what it inherits and what it adds.
PROGRESSION = {'chapter': 's11_user_memory',
 'builds_on': ['s10_workspace_memory'],
 'adds': ['user-level memory', 'preference dedupe', 'identity prompt blocks'],
 'preserves': ['workspace memory layer']}

# Shared learning entrypoints: --demo is offline; --provider deepseek configures real API env.
import sys as _wb_sys
from pathlib import Path as _wb_Path
_WB_ROOT = _wb_Path(__file__).resolve().parents[1]
if str(_WB_ROOT) not in _wb_sys.path:
    _wb_sys.path.insert(0, str(_WB_ROOT))
from mini_workbuddy.chapter_demo import maybe_run_chapter_demo as _wb_maybe_run_chapter_demo
_wb_maybe_run_chapter_demo(__file__, PROGRESSION)
from mini_workbuddy.chapter_demo import prepare_chapter_provider as _wb_prepare_chapter_provider
_wb_prepare_chapter_provider()
import os, sys, time, json, subprocess
from pathlib import Path
from datetime import datetime

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
from mini_workbuddy.paths import tutorial_workbuddy_home

load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"): os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path.cwd()
# Use an isolated tutorial directory to avoid touching real ~/.workbuddy.
WB_DIR = tutorial_workbuddy_home() / "user-memory"
MAX_USER_MEMORY_WRITE = 4000  # chars per session write

# Default files
BOOTSTRAP_TEMPLATE = """# Bootstrap

This is your first conversation. Get to know the user:
1. Ask their name and how they'd like to be called
2. Ask their city (for timezone awareness)
3. Ask their preferred communication style (direct vs. gentle)
4. Ask what name they'd like to call you (the AI assistant)
5. Ask for an emoji they associate with you

After the conversation, use the save_identity tool to write identity files,
then use the delete_bootstrap tool to remove this file.

Keep it natural — don't ask all questions at once. Have a real conversation.
"""

DEFAULT_SOUL = """# Soul

Be genuinely helpful, not performatively helpful.

## Values
- Honesty over comfort. Don't sugarcoat problems.
- Action over explanation. Do the thing, don't just describe it.
- Concise by default. Long answers need justification.

## Boundaries
- Never guess at URLs or API endpoints.
- Never modify files without understanding them first.
- Ask before destructive operations.

## Vibe
- Direct, warm, slightly dry humor.
- Treat the user as a competent adult.
"""


# ═══════════════════════════════════════════════════════════════
# UserMemory — Layer 2: cross-project identity and preferences
# ═══════════════════════════════════════════════════════════════

class UserMemory:
    """
    Manages user-level memory files at ~/.workbuddy/ (simulated at .workbuddy_user/).

    File structure:
        .workbuddy_user/
        ├── MEMORY.md       — cross-project preferences (≤4,000 chars/session)
        ├── persona/core.md         — core personality
        ├── persona/identity.md     — name, type, emoji, vibe
        ├── persona/user.md         — user's info
        └── persona/bootstrap.md    — one-time setup (deleted after identity established)
    """

    def __init__(self, base_dir: Path = None):
        self.base_dir = base_dir or WB_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)

    @property
    def soul_path(self) -> Path: return self.base_dir / "persona/core.md"
    @property
    def identity_path(self) -> Path: return self.base_dir / "persona/identity.md"
    @property
    def user_path(self) -> Path: return self.base_dir / "persona/user.md"
    @property
    def memory_path(self) -> Path: return self.base_dir / "MEMORY.md"
    @property
    def bootstrap_path(self) -> Path: return self.base_dir / "persona/bootstrap.md"

    # ── Bootstrap detection ───────────────────────────────────

    def needs_bootstrap(self) -> bool:
        """Check if persona/bootstrap.md exists (first-time setup needed)."""
        return self.bootstrap_path.exists()

    def create_bootstrap(self):
        """Create persona/bootstrap.md for first-time setup."""
        self.bootstrap_path.parent.mkdir(parents=True, exist_ok=True)
        self.bootstrap_path.write_text(BOOTSTRAP_TEMPLATE, encoding="utf-8")

    def read_bootstrap(self) -> str:
        if not self.bootstrap_path.exists():
            return ""
        return self.bootstrap_path.read_text(encoding="utf-8")

    def delete_bootstrap(self):
        """Delete persona/bootstrap.md — called after identity is established."""
        if self.bootstrap_path.exists():
            self.bootstrap_path.unlink()
            print(f"\033[90m[identity] persona/bootstrap.md deleted (identity established)\033[0m")

    # ── Identity files ────────────────────────────────────────

    def save_identity(self, soul: str, identity: str, user_info: str):
        """Write identity files after bootstrap conversation."""
        self.soul_path.parent.mkdir(parents=True, exist_ok=True)
        self.soul_path.write_text(soul, encoding="utf-8")
        self.identity_path.write_text(identity, encoding="utf-8")
        self.user_path.write_text(user_info, encoding="utf-8")
        print(f"\033[90m[identity] Written: persona/core.md, persona/identity.md, persona/user.md\033[0m")

    def load_identity(self) -> dict:
        """Load all identity files for system prompt injection."""
        return {
            "soul": self._read(self.soul_path),
            "identity": self._read(self.identity_path),
            "user": self._read(self.user_path),
            "memory": self._read(self.memory_path),
        }

    def is_identity_established(self) -> bool:
        """Check if identity files exist (bootstrap completed)."""
        return self.soul_path.exists() and self.identity_path.exists() and self.user_path.exists()

    # ── MEMORY.md (cross-project preferences) ─────────────────

    def read_memory(self) -> str:
        return self._read(self.memory_path)

    def append_memory(self, content: str):
        """
        Append to MEMORY.md with 4,000 char limit per session.

        Production harness: each session can append up to 4,000 chars.
        This is NOT the total file size — it's per-write.
        """
        if len(content) > MAX_USER_MEMORY_WRITE:
            content = content[:MAX_USER_MEMORY_WRITE] + "\n... (truncated at 4000 chars)"
            print(f"\033[33m[identity] MEMORY.md write truncated to {MAX_USER_MEMORY_WRITE} chars\033[0m")

        with open(self.memory_path, "a", encoding="utf-8") as f:
            f.write(content + "\n")
        print(f"\033[90m[identity] Appended {len(content)} chars to MEMORY.md\033[0m")

    # ── Context for agent ─────────────────────────────────────

    def get_context_for_agent(self) -> str:
        """Build identity context string for system prompt."""
        if not self.is_identity_established():
            return "(identity not yet established)"

        id = self.load_identity()
        parts = []

        if id["soul"]:
            parts.append(f"## Soul\n{id['soul']}")
        if id["identity"]:
            parts.append(f"## Identity\n{id['identity']}")
        if id["user"]:
            parts.append(f"## User\n{id['user']}")
        if id["memory"]:
            parts.append(f"## User-level Memory (MANDATORY — must follow these rules)\n{id['memory']}")

        return "\n\n".join(parts)

    def _read(self, path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════
# BootstrapAgent — first-time identity setup conversation
# ═══════════════════════════════════════════════════════════════

class BootstrapAgent:
    """
    Runs the bootstrap conversation to establish identity.

    Real WorkBuddy flow:
    1. persona/bootstrap.md exists → enter bootstrap mode
    2. Agent has a natural conversation to learn about user
    3. Agent calls save_identity tool → writes SOUL/IDENTITY/USER
    4. Agent calls delete_bootstrap tool → removes persona/bootstrap.md
    5. Next session starts normally (no persona/bootstrap.md)
    """

    def __init__(self, memory: UserMemory):
        self.memory = memory
        self.client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
        self.model = os.environ["MODEL_ID"]
        self.messages: list[dict] = []
        self._identity_data = None

    def _build_system(self) -> str:
        return f"""You are an AI assistant doing a first-time setup conversation.

{DEFAULT_SOUL}

# Bootstrap Instructions
{self.memory.read_bootstrap()}

# Current time
{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

You have tools to save the user's identity and complete bootstrap.
Use save_identity when you have enough information (name, city, style, your name, emoji).
Use delete_bootstrap after saving identity."""

    def _build_tools(self) -> list[dict]:
        return [
            {
                "name": "save_identity",
                "description": "Save identity files after learning about the user. "
                               "Call this when you have the user's name, city, style preference, "
                               "a name for yourself, and an emoji.",
                "input_schema": {"type": "object", "properties": {
                    "user_name": {"type": "string", "description": "User's name"},
                    "call_them": {"type": "string", "description": "How to address the user"},
                    "city": {"type": "string", "description": "User's city"},
                    "style": {"type": "string", "description": "Communication style: direct or gentle"},
                    "assistant_name": {"type": "string", "description": "Name the user chose for you"},
                    "emoji": {"type": "string", "description": "Emoji the user associates with you"},
                    "assistant_type": {"type": "string", "description": "What kind of assistant you are"},
                }, "required": ["user_name", "call_them", "city", "style",
                                 "assistant_name", "emoji"]},
            },
            {
                "name": "delete_bootstrap",
                "description": "Delete persona/bootstrap.md after identity is saved. "
                               "This completes the bootstrap process.",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]

    def chat(self, user_message: str) -> str:
        """Run one turn of the bootstrap conversation."""
        self.messages.append({"role": "user", "content": user_message})

        while True:
            resp = self.client.messages.create(
                model=self.model, system=self._build_system(),
                messages=self.messages, tools=self._build_tools(),
                max_tokens=4000)
            self.messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use":
                break

            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    if block.name == "save_identity":
                        self._identity_data = block.input
                        self._save_identity_files(block.input)
                        tool_results.append({"type": "tool_result",
                                             "tool_use_id": block.id,
                                             "content": "Identity files saved successfully."})
                    elif block.name == "delete_bootstrap":
                        self.memory.delete_bootstrap()
                        tool_results.append({"type": "tool_result",
                                             "tool_use_id": block.id,
                                             "content": "persona/bootstrap.md deleted. Bootstrap complete."})
            self.messages.append({"role": "user", "content": tool_results})

        # Extract text
        final = ""
        for block in self.messages[-1]["content"]:
            if getattr(block, "type", None) == "text":
                final += block.text
        return final

    def _save_identity_files(self, data: dict):
        """Format and write persona/core.md, persona/identity.md, persona/user.md."""
        soul = DEFAULT_SOUL
        default_vibe = "Reliable, sharp, doesn't waste your time."
        identity = f"""# Identity

Name: {data.get('assistant_name', 'WorkBuddy')}
Type: {data.get('assistant_type', 'Desktop AI companion')}
Emoji: {data.get('emoji', '🐝')}
Vibe: {data.get('style', default_vibe)}
"""
        user_info = f"""# User

Name: {data.get('user_name', '')}
Call them: {data.get('call_them', data.get('user_name', ''))}
City: {data.get('city', '')}
Timezone: (derived from city)

## Notes
- Prefers {data.get('style', 'direct')} communication style
"""
        self.memory.save_identity(soul, identity, user_info)

    @property
    def is_complete(self) -> bool:
        return not self.memory.needs_bootstrap() and self._identity_data is not None


# ═══════════════════════════════════════════════════════════════
# IdentityAwareAgent — uses established identity in conversation
# ═══════════════════════════════════════════════════════════════

class IdentityAwareAgent:
    """
    Agent that uses established identity (SOUL/IDENTITY/USER/MEMORY)
    in its system prompt.

    Production harness: identity files are injected into the system prompt
    at session start. MEMORY.md rules are marked as MANDATORY.
    """

    def __init__(self, memory: UserMemory, cwd: Path):
        self.memory = memory
        self.cwd = cwd
        self.client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
        self.model = os.environ["MODEL_ID"]
        self.messages: list[dict] = []

    def _build_system(self) -> str:
        identity_ctx = self.memory.get_context_for_agent()
        return f"""You are a coding agent at {self.cwd}.

{identity_ctx}

Follow the user-level memory rules exactly — they are MANDATORY.
Be concise. Act, don't over-explain."""

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
                "name": "save_user_memory",
                "description": "Save a cross-project preference or rule to MEMORY.md. "
                               "Use for rules that should apply to ALL projects.",
                "input_schema": {"type": "object",
                    "properties": {"rule": {"type": "string",
                        "description": "The rule or preference to remember permanently"}},
                    "required": ["rule"]},
            },
        ]

    def chat(self, user_message: str) -> str:
        self.messages.append({"role": "user", "content": user_message})

        while True:
            resp = self.client.messages.create(
                model=self.model, system=self._build_system(),
                messages=self.messages, tools=self._build_tools(),
                max_tokens=8000)
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
                    elif block.name == "save_user_memory":
                        rule = block.input.get("rule", "")
                        self.memory.append_memory(rule)
                        tool_results.append({"type": "tool_result",
                                             "tool_use_id": block.id,
                                             "content": "Rule saved to user-level MEMORY.md."})
            self.messages.append({"role": "user", "content": tool_results})

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
    print("║  s11: User Memory — 跨项目偏好 + 身份系统                  ║")
    print("║  跨项目的偏好, 放用户级                                    ║")
    print("╚═══════════════════════════════════════════════════════════╝")
    print()

    memory = UserMemory()

    print(f"用户级目录: {memory.base_dir}")
    print(f"身份状态:   {'已建立' if memory.is_identity_established() else '未建立'}")
    print(f"需引导:     {'是' if memory.needs_bootstrap() else '否'}")
    print()

    # ── Bootstrap or normal mode ──────────────────────────────

    if memory.needs_bootstrap() or not memory.is_identity_established():
        if not memory.needs_bootstrap():
            print("\033[90m[identity] No persona/bootstrap.md found, creating one...\033[0m")
            memory.create_bootstrap()

        print("\033[33m═══ 身份引导模式 ═══\033[0m")
        print("这是第一次对话，让我们先认识一下。\n")

        bootstrap = BootstrapAgent(memory)

        # Agent starts the conversation
        greeting = bootstrap.chat("Hi! I'm starting the app for the first time.")
        print(f"\033[33m{greeting}\033[0m\n")

        while not bootstrap.is_complete:
            try:
                user_input = input("\033[36m引导 >> \033[0m").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n\033[90m[identity] Bootstrap interrupted. Run again to continue.\033[0m")
                return
            if not user_input:
                continue

            reply = bootstrap.chat(user_input)
            print(f"\033[33m{reply}\033[0m\n")

        print("\033[32m═══ 引导完成，身份已建立 ═══\033[0m\n")

    # ── Normal conversation with identity ─────────────────────

    # Show identity summary
    identity = memory.load_identity()
    if identity["user"]:
        print(f"\033[90m用户: {identity['user'][:200]}\033[0m")
    if identity["identity"]:
        print(f"\033[90m助手: {identity['identity'][:200]}\033[0m")
    print()

    agent = IdentityAwareAgent(memory, WORKDIR)

    print("命令:")
    print("  /identity  — 查看身份文件")
    print("  /memory    — 查看 MEMORY.md (跨项目规则)")
    print("  /soul      — 查看 persona/core.md")
    print("  /reset-id  — 重置身份 (重新引导)")
    print("  直接输入   — 与 agent 对话")
    print("  q          — 退出\n")

    while True:
        try:
            query = input("\033[36ms11 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if query.lower() in ("q", "exit", "quit"):
            break
        if not query:
            continue

        if query == "/identity":
            id = memory.load_identity()
            for key, label in [("soul", "persona/core.md"), ("identity", "persona/identity.md"),
                               ("user", "persona/user.md"), ("memory", "MEMORY.md")]:
                content = id[key]
                if content:
                    print(f"\n\033[33m{'─'*50}")
                    print(f"{label}")
                    print(f"{'─'*50}\n{content}\033[0m")
            print()
            continue

        if query == "/memory":
            content = memory.read_memory()
            if content:
                print(f"\n\033[33m{'─'*50}\nMEMORY.md (跨项目规则)\n{'─'*50}\n{content}\033[0m\n")
            else:
                print("\n\033[90m(尚无 MEMORY.md — 让 agent 记住你的偏好)\033[0m\n")
            continue

        if query == "/soul":
            content = memory._read(memory.soul_path)
            if content:
                print(f"\n\033[33m{'─'*50}\npersona/core.md\n{'─'*50}\n{content}\033[0m\n")
            else:
                print("\n\033[90m(尚无 persona/core.md)\033[0m\n")
            continue

        if query == "/reset-id":
            for p in [memory.soul_path, memory.identity_path, memory.user_path,
                      memory.memory_path, memory.bootstrap_path]:
                if p.exists():
                    p.unlink()
            memory.create_bootstrap()
            agent.messages = []
            print("\033[33m[identity] 身份已重置。请重新运行程序进行引导。\033[0m\n")
            break

        # Normal chat
        result = agent.chat(query)
        print(f"\n\033[32m{result}\033[0m\n")

    print(f"\n\033[90mGoodbye. 身份文件在 {memory.base_dir}\033[0m")


if __name__ == "__main__":
    main()
