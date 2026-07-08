#!/usr/bin/env python3
from __future__ import annotations
"""
s04_permission_hooks.py - Permission System & Hooks

Three gates inserted before tool execution:

    Gate 1: Hard deny list (rm -rf /, sudo, ...)
    Gate 2: Rule matching (write outside workspace? destructive cmd?)
    Gate 3: User approval (pause and wait for confirmation)

    +-------+    +--------+    +--------+    +--------+    +------+
    | Tool  | -> | Gate 1 | -> | Gate 2 | -> | Gate 3 | -> | Exec |
    | call  |    | deny?  |    | match? |    | allow? |    |      |
    +-------+    +--------+    +--------+    +--------+    +------+

Hooks system adds extension points without modifying the loop:
    PreToolUse  — permission check, audit log
    PostToolUse — output size warning, post-processing
    Stop        — stats, cleanup

Only two lines added to the agent loop:
    blocked = trigger_hooks("PreToolUse", block)
    trigger_hooks("PostToolUse", block, output)

Usage:
    python s04_permission_hooks/code.py
"""



# Machine-readable learning path metadata. Tests enforce that every
# chapter declares what it inherits and what it adds.
PROGRESSION = {'chapter': 's04_permission_hooks',
 'builds_on': ['s03_deferred_loading'],
 'adds': ['pre-tool permission gates', 'hook lifecycle', 'audit hook point'],
 'preserves': ['multi-tool execution boundary']}
import os, subprocess, json, re
from pathlib import Path

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

SYSTEM = f"You are a coding agent at {WORKDIR}. All destructive operations require user approval."


# == FROM s02: Tool Implementations (unchanged) ==

def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR): raise ValueError(f"Path escapes workspace: {p}")
    return path

def run_bash(command: str) -> str:
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR, capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired: return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e: return f"Error: {e}"

def run_read(path: str, limit: int | None = None) -> str:
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines): lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)
    except Exception as e: return f"Error: {e}"

def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path); fp.parent.mkdir(parents=True, exist_ok=True); fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e: return f"Error: {e}"

def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path); text = fp.read_text()
        if old_text not in text: return f"Error: text not found in {path}"
        fp.write_text(text.replace(old_text, new_text, 1)); return f"Edited {path}"
    except Exception as e: return f"Error: {e}"

def run_glob(pattern: str) -> str:
    import glob as g
    try:
        results = [str(Path(m).resolve().relative_to(WORKDIR))
                   for m in g.glob(str(WORKDIR / pattern))
                   if Path(m).resolve().is_relative_to(WORKDIR)]
        return "\n".join(results) if results else "(no matches)"
    except Exception as e: return f"Error: {e}"


# == NEW in s04: Permission System ==

HARD_DENY = ["rm -rf /", "sudo ", "shutdown", "reboot", "mkfs", "dd if=", "> /dev/sd"]

class PermissionRule:
    def __init__(self, name, tool, pattern, action):
        self.name = name; self.tool = tool; self.pattern = pattern; self.action = action

    def check(self, tool_name, tool_input):
        if tool_name != self.tool: return "allow"
        text = json.dumps(tool_input, default=str)
        if re.search(self.pattern, text): return self.action
        return "allow"

PERMISSION_RULES = [
    PermissionRule("no-rm-rf", "bash", r"rm\s+-rf", "deny"),
    PermissionRule("no-sudo", "bash", r"\bsudo\b", "deny"),
    PermissionRule("no-write-root", "write_file", r'"path"\s*:\s*"/', "deny"),
    PermissionRule("no-edit-root", "edit_file", r'"path"\s*:\s*"/', "deny"),
    PermissionRule("ask-destructive-bash", "bash", r"(rm\s|mv\s|chmod\s|chown\s)", "ask"),
    PermissionRule("ask-large-write", "write_file", r'"content"\s*:\s*".{5000,}', "ask"),
]

DESTRUCTIVE_TOOLS = {"write_file", "edit_file"}

def check_permission(tool_name: str, tool_input: dict) -> tuple[str, str]:
    """Returns (action, reason). action is 'allow', 'deny', or 'ask'."""
    # Gate 1: Hard deny
    if tool_name == "bash":
        cmd = tool_input.get("command", "")
        for pattern in HARD_DENY:
            if pattern in cmd:
                return ("deny", f"Hard deny: blocked pattern '{pattern}'")

    # Gate 2: Rule matching
    for rule in PERMISSION_RULES:
        result = rule.check(tool_name, tool_input)
        if result == "deny":
            return ("deny", f"Rule '{rule.name}' denied this operation")
        if result == "ask":
            return ("ask", f"Rule '{rule.name}' requires approval")

    return ("allow", "")


# == NEW in s04: Hooks System ==

HOOKS: dict[str, list] = {
    "PreToolUse": [],
    "PostToolUse": [],
    "UserPromptSubmit": [],
    "Stop": [],
}

def register_hook(event: str, hook):
    HOOKS.setdefault(event, []).append(hook)

def trigger_hooks(event: str, *args) -> str | None:
    for hook in HOOKS.get(event, []):
        result = hook(*args)
        if result and result.startswith("block:"):
            return result
    return None


# Built-in hooks
def permission_hook(block):
    """PreToolUse hook: check permissions."""
    action, reason = check_permission(block.name, dict(block.input))
    if action == "deny":
        return f"block: Permission denied — {reason}"
    if action == "ask":
        print(f"\n\033[33m⚠ Approval needed: {reason}\033[0m")
        print(f"   Tool: {block.name}")
        print(f"   Input: {json.dumps(dict(block.input), ensure_ascii=False)[:200]}")
        try:
            answer = input("   Allow? [y/N] ").strip().lower()
            if answer != "y":
                return f"block: User denied operation — {reason}"
        except (EOFError, KeyboardInterrupt):
            return f"block: User cancelled — {reason}"
    return None

def audit_hook(block):
    """PreToolUse hook: log tool calls."""
    print(f"\033[90m[audit] {block.name} called with {json.dumps(dict(block.input), ensure_ascii=False)[:100]}\033[0m")
    return None

def output_size_hook(block, output):
    """PostToolUse hook: warn on large outputs."""
    if len(str(output)) > 10000:
        print(f"\033[33m[warn] Large output: {len(str(output))} chars from {block.name}\033[0m")

def stats_hook():
    """Stop hook: print session stats."""
    print(f"\033[90m[stats] Session ended\033[0m")

# Register built-in hooks
register_hook("PreToolUse", permission_hook)
register_hook("PreToolUse", audit_hook)
register_hook("PostToolUse", output_size_hook)
register_hook("Stop", stats_hook)


# == Tool Definitions ==

TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in a file once.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "glob", "description": "Find files matching a glob pattern.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}},
]

TOOL_HANDLERS = {"bash": run_bash, "read_file": run_read, "write_file": run_write, "edit_file": run_edit, "glob": run_glob}


# == Agent Loop with hooks ==

def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages, tools=TOOLS, max_tokens=8000)
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            trigger_hooks("Stop")
            return

        results = []
        for block in response.content:
            if block.type != "tool_use": continue

            # PreToolUse hooks (includes permission check)
            blocked = trigger_hooks("PreToolUse", block)
            if blocked:
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": blocked})
                continue

            # Execute tool
            handler = TOOL_HANDLERS.get(block.name)
            output = handler(**block.input) if handler else f"Unknown: {block.name}"
            print(f"\033[36m> {block.name}\033[0m {str(output)[:100]}")

            # PostToolUse hooks
            trigger_hooks("PostToolUse", block, output)

            results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})

        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    print("s04: Permission & Hooks — three gates + extension points")
    print("输入问题，回车发送。输入 q 退出。\n")
    history = []
    while True:
        try: query = input("\033[36ms04 >> \033[0m")
        except (EOFError, KeyboardInterrupt): break
        if query.strip().lower() in ("q", "exit", ""): break
        trigger_hooks("UserPromptSubmit", query)
        history.append({"role": "user", "content": query})
        agent_loop(history)
        for block in history[-1]["content"]:
            if getattr(block, "type", None) == "text": print(block.text)
        print()
