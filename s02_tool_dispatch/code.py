#!/usr/bin/env python3
from __future__ import annotations
"""
s02_tool_dispatch.py - Multi-Tool Dispatch

One dispatch map replaces hardcoded tool execution:

    TOOL_HANDLERS = {
        "bash": run_bash,
        "read_file": run_read,
        "write_file": run_write,
        "edit_file": run_edit,
        "glob": run_glob,
    }

The agent loop doesn't change — just the dispatch line:
    handler = TOOL_HANDLERS[block.name]
    output = handler(**block.input)

Adds: path safety (safe_path), concurrent execution (ThreadPoolExecutor).

Usage:
    python s02_tool_dispatch/code.py
"""



# Machine-readable learning path metadata. Tests enforce that every
# chapter declares what it inherits and what it adds.
PROGRESSION = {'chapter': 's02_tool_dispatch',
 'builds_on': ['s01_agent_loop'],
 'adds': ['tool dispatch map', 'read/write/edit/glob tools', 'workspace path guard'],
 'preserves': ['same agent loop shape']}
import os, subprocess, glob as globmod
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

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

SYSTEM = f"You are a coding agent at {WORKDIR}. Use tools to solve tasks. Act, don't explain."


# == Tool Implementations ==

def safe_path(p: str) -> Path:
    """Ensure path stays within workspace."""
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


def run_read(path: str, limit: int | None = None) -> str:
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = safe_path(path)
        text = file_path.read_text()
        if old_text not in text:
            return f"Error: text not found in {path}"
        file_path.write_text(text.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


def run_glob(pattern: str) -> str:
    try:
        results = []
        for match in globmod.glob(str(WORKDIR / pattern)):
            p = Path(match).resolve()
            if p.is_relative_to(WORKDIR):
                results.append(str(p.relative_to(WORKDIR)))
        return "\n".join(results) if results else "(no matches)"
    except Exception as e:
        return f"Error: {e}"


# == Tool Definitions (schemas for the model) ==

TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object",
         "properties": {"command": {"type": "string"}},
         "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object",
         "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
         "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file.",
     "input_schema": {"type": "object",
         "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
         "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in a file once.",
     "input_schema": {"type": "object",
         "properties": {"path": {"type": "string"},
             "old_text": {"type": "string"}, "new_text": {"type": "string"}},
         "required": ["path", "old_text", "new_text"]}},
    {"name": "glob", "description": "Find files matching a glob pattern.",
     "input_schema": {"type": "object",
         "properties": {"pattern": {"type": "string"}},
         "required": ["pattern"]}},
]

# == Dispatch Map ==

TOOL_HANDLERS = {
    "bash": run_bash,
    "read_file": run_read,
    "write_file": run_write,
    "edit_file": run_edit,
    "glob": run_glob,
}


# == Agent Loop with dispatch map ==

def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return

        # Concurrent tool execution
        tool_blocks = [b for b in response.content if b.type == "tool_use"]
        results = []

        if len(tool_blocks) <= 1:
            # Single tool — no need for thread pool
            for block in tool_blocks:
                handler = TOOL_HANDLERS.get(block.name)
                output = handler(**block.input) if handler else f"Unknown: {block.name}"
                print(f"\033[36m> {block.name}\033[0m {str(output)[:100]}")
                results.append({"type": "tool_result",
                                "tool_use_id": block.id, "content": output})
        else:
            # Multiple tools — execute concurrently
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {}
                for block in tool_blocks:
                    handler = TOOL_HANDLERS.get(block.name)
                    if handler:
                        futures[block.id] = pool.submit(handler, **block.input)
                    print(f"\033[36m> {block.name} (concurrent)\033[0m")

                for block in tool_blocks:
                    if block.id in futures:
                        output = futures[block.id].result()
                    else:
                        output = f"Unknown: {block.name}"
                    print(f"  \033[90m{block.name}: {str(output)[:100]}\033[0m")
                    results.append({"type": "tool_result",
                                    "tool_use_id": block.id, "content": output})

        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    print("s02: Tool Dispatch — 5 tools, one dispatch map")
    print("输入问题，回车发送。输入 q 退出。\n")
    history = []
    while True:
        try:
            query = input("\033[36ms02 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        for block in history[-1]["content"]:
            if getattr(block, "type", None) == "text":
                print(block.text)
        print()
