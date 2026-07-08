#!/usr/bin/env python3
from __future__ import annotations
"""
s03_deferred_loading.py - Deferred Tool Loading

Two-step pattern: ToolSearch → DeferExecuteTool

    ┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
    │ Model needs │────►│  ToolSearch  │────►│  Schema loaded   │
    │  a tool     │     │  (by name)   │     │  into context    │
    └─────────────┘     └──────────────┘     └────────┬─────────┘
                                                    │
    ┌─────────────┐     ┌──────────────┐            │
    │ Tool result │◄────│ DeferExecute │◄───────────┘
    │             │     │    Tool      │
    └─────────────┘     └──────────────┘

Problem:  dozens of tools x ~1000 tokens/schema = tens of thousands of tokens just for
          tool descriptions. That's 20% of a 200K context window.

Solution: Tools marked deferLoading=true only have their name + brief
          description loaded at startup. When the model needs the tool,
          it calls ToolSearch to get the full schema, then
          DeferExecuteTool to execute.

This is like dynamic linking vs static linking:
  - Static linking: load all code at startup (slow, memory-heavy)
  - Dynamic linking: load code on demand (fast startup, efficient)

Usage:
    python s03_deferred_loading/code.py

No API key needed — uses a mock LLM with predefined responses.
"""



# Machine-readable learning path metadata. Tests enforce that every
# chapter declares what it inherits and what it adds.
PROGRESSION = {'chapter': 's03_deferred_loading',
 'builds_on': ['s02_tool_dispatch'],
 'adds': ['ToolSearch', 'DeferExecuteTool', 'lazy schema loading'],
 'preserves': ['tool registry and dispatch']}
import argparse
import json
import textwrap
from dataclasses import dataclass, field
from typing import Any, Callable


# ═══════════════════════════════════════════════════════════════════
# Token Estimation
# ═══════════════════════════════════════════════════════════════════

def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for JSON/mixed text."""
    return max(1, len(text) // 4)


def schema_tokens(schema: dict) -> int:
    """Estimate tokens for a tool schema (name + description + input_schema)."""
    return estimate_tokens(json.dumps(schema, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════════════
# Tool Registry
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ToolEntry:
    """A registered tool with its schema, handler, and loading mode."""
    name: str
    schema: dict
    handler: Callable
    defer: bool                       # True = deferred (lazy) loading
    description: str = ""             # Brief description for the directory
    _schema_loaded: bool = field(default=False, repr=False)
                                     # Tracks if schema has been loaded this session

    def __post_init__(self):
        if not self.description:
            self.description = self.schema.get("description", "")[:120]

    @property
    def full_schema_tokens(self) -> int:
        return schema_tokens(self.schema)

    @property
    def directory_tokens(self) -> int:
        """Tokens for just name + brief description (the 'symbol table' entry)."""
        return estimate_tokens(f"{self.name}: {self.description}")


class ToolRegistry:
    """
    Manages both immediate and deferred tools.

    - Immediate tools: full schema always in context
    - Deferred tools: only name + brief description in context;
      full schema loaded on demand via ToolSearch
    """

    def __init__(self):
        self._tools: dict[str, ToolEntry] = {}
        # Cache: schemas loaded via ToolSearch in this session
        self._loaded_schemas: dict[str, dict] = {}

    # -- Registration --

    def register(self, name: str, schema: dict, handler: Callable,
                 defer: bool = False, description: str = "") -> None:
        """Register a tool as immediate or deferred."""
        entry = ToolEntry(
            name=name, schema=schema, handler=handler,
            defer=defer, description=description,
        )
        self._tools[name] = entry

    # -- Accessors --

    def get_immediate_schemas(self) -> list[dict]:
        """Full schemas for all immediate tools (always in context)."""
        return [t.schema for t in self._tools.values() if not t.defer]

    def get_deferred_directory(self) -> str:
        """
        Compact directory of deferred tools: name + one-line description.
        This is what the model sees instead of full schemas.
        """
        lines = []
        for t in self._tools.values():
            if t.defer:
                lines.append(f"  - {t.name}: {t.description}")
        return "\n".join(lines)

    def get_deferred_names(self) -> list[str]:
        """Names of all deferred tools."""
        return [t.name for t in self._tools.values() if t.defer]

    def get_handler(self, name: str) -> Callable | None:
        """Get the handler for a tool (works for both immediate and deferred)."""
        entry = self._tools.get(name)
        return entry.handler if entry else None

    def is_deferred(self, name: str) -> bool:
        entry = self._tools.get(name)
        return entry.defer if entry else False

    # -- ToolSearch: load deferred schema on demand --

    def load_deferred_schema(self, tool_names: list[str]) -> list[dict]:
        """
        Load full schemas for deferred tools by name.
        This is what ToolSearch calls internally.
        """
        results = []
        for name in tool_names:
            entry = self._tools.get(name)
            if entry is None:
                results.append({"error": f"Tool not found: {name}"})
                continue
            if not entry.defer:
                # Already loaded as immediate — return it anyway
                results.append(entry.schema)
                continue
            # Mark as loaded (cache)
            if name not in self._loaded_schemas:
                self._loaded_schemas[name] = entry.schema
                entry._schema_loaded = True
            results.append(entry.schema)
        return results

    def search_by_query(self, queries: list[str], top_k: int = 3) -> list[dict]:
        """
        Fuzzy search deferred tools by keyword.
        Simple substring matching (WorkBuddy uses MiniSearch).
        """
        scored: list[tuple[int, ToolEntry]] = []
        for entry in self._tools.values():
            if not entry.defer:
                continue
            score = 0
            haystack = (entry.name + " " + entry.description).lower()
            for q in queries:
                if q.lower() in haystack:
                    score += len(q)  # longer match = higher score
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda x: -x[0])
        return [entry.schema for _, entry in scored[:top_k]]

    # -- Token Accounting --

    def token_report(self) -> dict:
        """Calculate token usage for immediate vs deferred vs full loading."""
        immediate_tokens = sum(
            t.full_schema_tokens for t in self._tools.values() if not t.defer
        )
        deferred_dir_tokens = sum(
            t.directory_tokens for t in self._tools.values() if t.defer
        )
        full_tokens = sum(t.full_schema_tokens for t in self._tools.values())
        return {
            "immediate_tools": sum(1 for t in self._tools.values() if not t.defer),
            "deferred_tools": sum(1 for t in self._tools.values() if t.defer),
            "immediate_tokens": immediate_tokens,
            "deferred_dir_tokens": deferred_dir_tokens,
            "full_load_tokens": full_tokens,
            "current_tokens": immediate_tokens + deferred_dir_tokens,
            "saved_tokens": full_tokens - (immediate_tokens + deferred_dir_tokens),
            "saving_pct": round(
                (full_tokens - immediate_tokens - deferred_dir_tokens)
                / max(full_tokens, 1) * 100
            ),
        }


# ═══════════════════════════════════════════════════════════════════
# Mock Tool Handlers (no real side effects)
# ═══════════════════════════════════════════════════════════════════

def mock_read_file(path: str) -> str:
    return f"[MOCK] Read {path}: # Example\nprint('hello')\n"

def mock_write_file(path: str, content: str) -> str:
    return f"[MOCK] Wrote {len(content)} chars to {path}"

def mock_bash(command: str) -> str:
    return f"[MOCK] $ {command}\n(total 8\ndrwxr-xr-x  3 user  staff   96B Jul 8 10:00 src)"

def mock_glob(pattern: str) -> str:
    return f"[MOCK] Matches for '{pattern}':\nsrc/main.py\nsrc/utils.py"

def mock_image_gen(prompt: str, size: str = "1024x1024") -> str:
    slug = prompt.lower().replace(" ", "_")[:30]
    return f"[MOCK] Generated image: {slug}.png ({size})"

def mock_image_edit(image_path: str, instruction: str) -> str:
    return f"[MOCK] Edited {image_path}: {instruction} → saved."

def mock_notebook_edit(notebook_path: str, cell_index: int, cell_type: str, source: str) -> str:
    return f"[MOCK] Edited cell {cell_index} ({cell_type}) in {notebook_path}"

def mock_lsp(operation: str, file_path: str) -> str:
    return f"[MOCK] LSP {operation} on {file_path}: 3 definitions found."

def mock_computer_use(action: str, coordinate: list = None, text: str = "") -> str:
    return f"[MOCK] ComputerUse: {action} at {coordinate or text}"

def mock_cron_create(name: str, rrule: str, prompt: str) -> str:
    return f"[MOCK] Created cron '{name}': {rrule}"

def mock_cron_list() -> str:
    return "[MOCK] Crons:\n  - daily_report: FREQ=DAILY\n  - weekly_sync: FREQ=WEEKLY"

def mock_enter_plan_mode() -> str:
    return "[MOCK] Entered plan mode."

def mock_exit_plan_mode() -> str:
    return "[MOCK] Exited plan mode."

# ═══════════════════════════════════════════════════════════════════
# Tool Schemas
# ═══════════════════════════════════════════════════════════════════

IMMEDIATE_TOOL_SCHEMAS = {
    "read_file": {
        "name": "read_file",
        "description": "Read the contents of a file.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    "write_file": {
        "name": "write_file",
        "description": "Write content to a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    "bash": {
        "name": "bash",
        "description": "Run a shell command.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    "glob": {
        "name": "glob",
        "description": "Find files matching a glob pattern.",
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
    },
}

DEFERRED_TOOL_SCHEMAS = {
    "image_gen": {
        "name": "image_gen",
        "description": "Generate images from text descriptions using AI models. Supports various sizes and styles.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Text description of the image to generate."},
                "size": {"type": "string", "enum": ["1024x1024", "1792x1024", "1024x1792"], "default": "1024x1024"},
                "style": {"type": "string", "enum": ["natural", "vivid"], "default": "natural"},
                "seed": {"type": "integer", "description": "Random seed for reproducibility."},
            },
            "required": ["prompt"],
        },
    },
    "image_edit": {
        "name": "image_edit",
        "description": "Edit or modify an existing image using AI models based on text instructions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "image_path": {"type": "string", "description": "Path to the source image."},
                "instruction": {"type": "string", "description": "What to change (e.g., 'add a sunset')."},
                "size": {"type": "string", "enum": ["1024x1024", "1792x1024"]},
            },
            "required": ["image_path", "instruction"],
        },
    },
    "notebook_edit": {
        "name": "notebook_edit",
        "description": "Replace the contents of a specific cell in a Jupyter notebook (.ipynb).",
        "input_schema": {
            "type": "object",
            "properties": {
                "notebook_path": {"type": "string"},
                "cell_index": {"type": "integer", "description": "Index of the cell to edit (0-based)."},
                "cell_type": {"type": "string", "enum": ["code", "markdown"]},
                "source": {"type": "string", "description": "New cell content."},
            },
            "required": ["notebook_path", "cell_index", "cell_type", "source"],
        },
    },
    "lsp": {
        "name": "lsp",
        "description": "Interact with Language Server Protocol servers for code intelligence (definitions, references, hover, diagnostics).",
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["definition", "references", "hover", "diagnostics"]},
                "file_path": {"type": "string"},
                "line": {"type": "integer"},
                "character": {"type": "integer"},
            },
            "required": ["operation", "file_path"],
        },
    },
    "computer_use": {
        "name": "computer_use",
        "description": "Control the desktop: mouse movement, clicks, keyboard input, and screenshots.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["screenshot", "mouse_move", "click", "type", "key"]},
                "coordinate": {"type": "array", "items": {"type": "integer"}, "description": "[x, y] pixel coordinates."},
                "text": {"type": "string", "description": "Text to type."},
            },
            "required": ["action"],
        },
    },
    "cron_create": {
        "name": "cron_create",
        "description": "Create a scheduled automation task with RFC 5545 RRULE recurrence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "rrule": {"type": "string", "description": "RFC 5545 recurrence rule, e.g., FREQ=DAILY"},
                "prompt": {"type": "string", "description": "Instruction to execute at each run."},
            },
            "required": ["name", "rrule", "prompt"],
        },
    },
    "cron_list": {
        "name": "cron_list",
        "description": "List all scheduled automation tasks.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    "enter_plan_mode": {
        "name": "enter_plan_mode",
        "description": "Enter plan mode to analyze and plan before executing.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
}

DEFERRED_DESCRIPTIONS = {
    "image_gen": "Generate images from text descriptions using AI models.",
    "image_edit": "Edit or modify an existing image using AI models.",
    "notebook_edit": "Edit a specific cell in a Jupyter notebook.",
    "lsp": "Language Server Protocol code intelligence (definitions, references, etc.).",
    "computer_use": "Control the desktop: mouse, keyboard, screenshots.",
    "cron_create": "Create a scheduled automation task with RRULE recurrence.",
    "cron_list": "List all scheduled automation tasks.",
    "enter_plan_mode": "Enter plan mode to analyze before executing.",
}


# ═══════════════════════════════════════════════════════════════════
# Build the Registry
# ═══════════════════════════════════════════════════════════════════

def build_registry() -> ToolRegistry:
    """Create and populate the tool registry."""
    reg = ToolRegistry()

    # --- Immediate tools (schema always in context) ---
    reg.register("read_file", IMMEDIATE_TOOL_SCHEMAS["read_file"],
                 mock_read_file, defer=False)
    reg.register("write_file", IMMEDIATE_TOOL_SCHEMAS["write_file"],
                 mock_write_file, defer=False)
    reg.register("bash", IMMEDIATE_TOOL_SCHEMAS["bash"],
                 mock_bash, defer=False)
    reg.register("glob", IMMEDIATE_TOOL_SCHEMAS["glob"],
                 mock_glob, defer=False)

    # ToolSearch and DeferExecuteTool are themselves immediate tools
    # — they are the bridge to the deferred tools.
    toolsearch_schema = {
        "name": "ToolSearch",
        "description": (
            "Search for deferred tools by exact name or keyword. "
            "Returns the full JSON schema for matching tools. "
            "Use this before DeferExecuteTool to load the tool's schema."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tool_names": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Exact tool names to look up.",
                },
                "queries": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Keywords for fuzzy search.",
                },
                "top_k": {"type": "integer", "default": 3},
            },
        },
    }
    defer_exec_schema = {
        "name": "DeferExecuteTool",
        "description": (
            "Execute a deferred tool by name. The tool's schema must have "
            "been loaded via ToolSearch first. Pass the tool name and its "
            "parameters."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "toolName": {"type": "string", "description": "Name of the deferred tool."},
                "params": {"type": "object", "description": "Parameters for the tool."},
            },
            "required": ["toolName"],
        },
    }
    reg.register("ToolSearch", toolsearch_schema, None, defer=False)
    reg.register("DeferExecuteTool", defer_exec_schema, None, defer=False)

    # --- Deferred tools (schema loaded on demand) ---
    deferred_handlers = {
        "image_gen": mock_image_gen,
        "image_edit": mock_image_edit,
        "notebook_edit": mock_notebook_edit,
        "lsp": mock_lsp,
        "computer_use": mock_computer_use,
        "cron_create": mock_cron_create,
        "cron_list": mock_cron_list,
        "enter_plan_mode": mock_enter_plan_mode,
    }
    for name, schema in DEFERRED_TOOL_SCHEMAS.items():
        reg.register(
            name, schema, deferred_handlers[name],
            defer=True,
            description=DEFERRED_DESCRIPTIONS.get(name, schema["description"][:80]),
        )

    return reg


# ═══════════════════════════════════════════════════════════════════
# Tool Handlers for ToolSearch and DeferExecuteTool
# ═══════════════════════════════════════════════════════════════════

def handle_tool_search(registry: ToolRegistry,
                       tool_names: list[str] | None = None,
                       queries: list[str] | None = None,
                       top_k: int = 3) -> str:
    """Execute ToolSearch: load deferred schemas on demand."""
    if tool_names:
        schemas = registry.load_deferred_schema(tool_names)
        parts = []
        for name, schema in zip(tool_names, schemas):
            if "error" in schema:
                parts.append(f"✗ {name}: not found")
            else:
                tokens = schema_tokens(schema)
                parts.append(f"✓ {name}: schema loaded ({tokens} tokens)")
                parts.append(json.dumps(schema, indent=2, ensure_ascii=False))
        return "\n".join(parts)

    if queries:
        schemas = registry.search_by_query(queries, top_k)
        if not schemas:
            return f"No tools matched: {queries}"
        parts = []
        for schema in schemas:
            name = schema.get("name", "?")
            tokens = schema_tokens(schema)
            parts.append(f"✓ {name}: schema loaded ({tokens} tokens)")
            parts.append(json.dumps(schema, indent=2, ensure_ascii=False))
        return "\n".join(parts)

    return "Error: provide tool_names or queries."


def handle_defer_execute(registry: ToolRegistry,
                         toolName: str,
                         params: dict | None = None) -> str:
    """Execute DeferExecuteTool: run the deferred tool's handler."""
    params = params or {}
    handler = registry.get_handler(toolName)
    if handler is None:
        return f"Error: Unknown tool '{toolName}'"
    if registry.is_deferred(toolName) and toolName not in registry._loaded_schemas:
        return (f"Error: Schema for '{toolName}' not loaded. "
                f"Call ToolSearch first.")
    try:
        return handler(**params)
    except Exception as e:
        return f"Error executing {toolName}: {e}"


# ═══════════════════════════════════════════════════════════════════
# Mock LLM
# ═══════════════════════════════════════════════════════════════════

@dataclass
class MockResponse:
    """Simulates an LLM response with tool calls or text."""
    tool_calls: list[dict] = field(default_factory=list)
    text: str = ""
    stop_reason: str = "tool_use"  # or "end_turn"

    @property
    def is_tool_use(self) -> bool:
        return len(self.tool_calls) > 0


# Predefined mock responses: simulate a conversation where the model
# generates an image. The model knows from the deferred directory that
# "image_gen" exists, but doesn't have its schema yet.
MOCK_CONVERSATION = [
    MockResponse(
        tool_calls=[{
            "name": "ToolSearch",
            "input": {"tool_names": ["image_gen"]},
        }],
        stop_reason="tool_use",
    ),
    MockResponse(
        tool_calls=[{
            "name": "DeferExecuteTool",
            "input": {
                "toolName": "image_gen",
                "params": {
                    "prompt": "a cat sitting on a desk",
                    "size": "1024x1024",
                },
            },
        }],
        stop_reason="tool_use",
    ),
    MockResponse(
        text="图像已生成！文件名: a_cat_sitting_on_a_desk.png (1024x1024)\n"
             "如果你需要编辑这张图片，可以让我用 image_edit 工具修改。",
        stop_reason="end_turn",
    ),
]


class MockLLM:
    """Returns predefined responses in sequence."""

    def __init__(self, responses: list[MockResponse]):
        self._responses = list(responses)
        self._index = 0

    def chat(self, messages: list[dict]) -> MockResponse:
        if self._index >= len(self._responses):
            return MockResponse(text="(no more responses)", stop_reason="end_turn")
        resp = self._responses[self._index]
        self._index += 1
        return resp


# ═══════════════════════════════════════════════════════════════════
# Agent Loop (with deferred tool loading)
# ═══════════════════════════════════════════════════════════════════

# ANSI colors for logging
C_CYAN   = "\033[36m"
C_YELLOW = "\033[33m"
C_GREEN  = "\033[32m"
C_RED    = "\033[31m"
C_DIM    = "\033[90m"
C_RESET  = "\033[0m"
C_BOLD   = "\033[1m"


def agent_loop(registry: ToolRegistry, llm: MockLLM, user_query: str):
    """
    Agent loop with deferred tool loading.

    The loop itself is the same as s01/s02 — while stop_reason == "tool_use".
    The difference is in HOW tools are dispatched:
      - Immediate tools: direct call (same as s02)
      - Deferred tools: ToolSearch → DeferExecuteTool (two-step)
    """
    messages = [{"role": "user", "content": user_query}]
    turn = 0
    total_tool_calls = 0
    toolsearch_calls = 0
    deferexec_calls = 0

    while True:
        turn += 1
        response = llm.chat(messages)

        if not response.is_tool_use:
            # Model finished — print final text
            print(f"\n{C_GREEN}[Turn {turn}]{C_RESET} Model responds with text:")
            print(textwrap.indent(response.text, "  "))
            break

        # Process tool calls
        results = []
        for call in response.tool_calls:
            name = call["name"]
            params = call.get("input", {})
            total_tool_calls += 1

            if name == "ToolSearch":
                toolsearch_calls += 1
                tool_names = params.get("tool_names")
                queries = params.get("queries")
                print(f"\n{C_CYAN}[Turn {turn}] ToolSearch{C_RESET}")
                if tool_names:
                    print(f"  {C_DIM}tool_names={tool_names}{C_RESET}")
                if queries:
                    print(f"  {C_DIM}queries={queries}{C_RESET}")
                output = handle_tool_search(
                    registry, tool_names=tool_names, queries=queries,
                    top_k=params.get("top_k", 3),
                )
                # Log which schemas were loaded
                for line in output.split("\n"):
                    if line.startswith("✓"):
                        print(f"  {C_GREEN}{line}{C_RESET}")
                    elif line.startswith("✗"):
                        print(f"  {C_RED}{line}{C_RESET}")

            elif name == "DeferExecuteTool":
                deferexec_calls += 1
                tool_name = params.get("toolName")
                tool_params = params.get("params", {})
                print(f"\n{C_YELLOW}[Turn {turn}] DeferExecuteTool{C_RESET}")
                print(f"  {C_DIM}toolName={tool_name}{C_RESET}")
                print(f"  {C_DIM}params={json.dumps(tool_params, ensure_ascii=False)}{C_RESET}")
                output = handle_defer_execute(registry, toolName=tool_name, params=tool_params)
                print(f"  {C_GREEN}→ {output}{C_RESET}")

            else:
                # Immediate tool — direct dispatch (like s02)
                handler = registry.get_handler(name)
                print(f"\n{C_CYAN}[Turn {turn}] {name} (immediate){C_RESET}")
                if handler:
                    output = handler(**params)
                else:
                    output = f"Unknown tool: {name}"
                print(f"  {C_GREEN}→ {output}{C_RESET}")

            results.append({
                "type": "tool_result",
                "tool_name": name,
                "content": output,
            })

        messages.append({"role": "assistant", "content": results})

    return {
        "turns": turn,
        "total_tool_calls": total_tool_calls,
        "toolsearch_calls": toolsearch_calls,
        "deferexec_calls": deferexec_calls,
    }


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def print_separator(title: str):
    print(f"\n{C_BOLD}{'=' * 60}{C_RESET}")
    print(f"{C_BOLD}  {title}{C_RESET}")
    print(f"{C_BOLD}{'=' * 60}{C_RESET}")


def interactive():
    """Interactive shell for manually trying ToolSearch and DeferExecuteTool."""
    print_separator("s03: Deferred Tool Loading Interactive")
    registry = build_registry()
    print("Commands:")
    print("  tools")
    print("  search <query>")
    print("  schema <tool_name>")
    print("  run <tool_name> <json_params>")
    print("  q")
    print("\nTry: search image")
    print("Try: schema image_gen")
    print('Try: run image_gen {"prompt":"a cat at a desk","size":"1024x1024"}')

    while True:
        try:
            line = input("s03 >> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line or line.lower() in {"q", "quit", "exit"}:
            return
        if line == "tools":
            print("Immediate:")
            for schema in registry.get_immediate_schemas():
                print(f"  - {schema['name']}")
            print("Deferred:")
            print(registry.get_deferred_directory())
            continue
        if line.startswith("search "):
            query = line[7:].strip()
            print(handle_tool_search(registry, queries=[query]))
            continue
        if line.startswith("schema "):
            name = line[7:].strip()
            print(handle_tool_search(registry, tool_names=[name]))
            continue
        if line.startswith("run "):
            _, rest = line.split("run ", 1)
            parts = rest.strip().split(" ", 1)
            tool_name = parts[0]
            raw_params = parts[1] if len(parts) > 1 else "{}"
            try:
                params = json.loads(raw_params)
            except json.JSONDecodeError as exc:
                print(f"Invalid JSON params: {exc}")
                continue
            print(handle_defer_execute(registry, toolName=tool_name, params=params))
            continue
        print("Unknown command. Use: tools | search <q> | schema <tool> | run <tool> <json> | q")


def main():
    print_separator("s03: Deferred Tool Loading")
    print("格言: 工具先列目录, schema 用到再展开")
    print("模式: ToolSearch → DeferExecuteTool (两步调用)\n")

    # -- Build registry --
    registry = build_registry()

    # -- Print token report --
    print_separator("Tool Registry Summary")

    report = registry.token_report()
    immediate_names = [t.name for t in registry._tools.values() if not t.defer]
    deferred_names = registry.get_deferred_names()

    print(f"\n{C_BOLD}Immediate tools ({report['immediate_tools']}):{C_RESET}")
    print(f"  {', '.join(immediate_names)}")
    print(f"\n{C_BOLD}Deferred tools ({report['deferred_tools']}):{C_RESET}")
    print(f"  {', '.join(deferred_names)}")

    print(f"\n{C_BOLD}Token estimation:{C_RESET}")
    print(f"  Full loading (all schemas):     ~{report['full_load_tokens']:,} tokens")
    print(f"  Deferred loading (startup):      ~{report['current_tokens']:,} tokens")
    print(f"    ├─ immediate schemas:          ~{report['immediate_tokens']:,} tokens")
    print(f"    └─ deferred directory:         ~{report['deferred_dir_tokens']:,} tokens")
    saved = report['saved_tokens']
    pct = report['saving_pct']
    print(f"  {C_GREEN}Saved:                           ~{saved:,} tokens ({pct}% reduction){C_RESET}")

    # -- Print what the model sees at startup --
    print_separator("What the Model Sees at Startup")

    print(f"\n{C_DIM}# Immediate tool schemas (full):{C_RESET}")
    for schema in registry.get_immediate_schemas():
        print(f"{C_DIM}  - {schema['name']}: {schema['description'][:60]}{C_RESET}")

    print(f"\n{C_DIM}# Deferred tool directory (names only):{C_RESET}")
    directory = registry.get_deferred_directory()
    for line in directory.split("\n"):
        print(f"{C_DIM}{line}{C_RESET}")

    print(f"\n{C_DIM}# System prompt hint to model:{C_RESET}")
    print(f"{C_DIM}  The following tools are available but their schemas are NOT loaded.{C_RESET}")
    print(f"{C_DIM}  Use ToolSearch(tool_names=[...]) to load a tool's schema, then{C_RESET}")
    print(f"{C_DIM}  DeferExecuteTool(toolName=..., params=...) to execute it.{C_RESET}")

    # -- Run agent loop with mock LLM --
    print_separator("Agent Loop (mock LLM)")

    user_query = "帮我生成一张猫坐在桌子上的图片"
    print(f"\n{C_BOLD}User:{C_RESET} {user_query}")

    llm = MockLLM(MOCK_CONVERSATION)
    stats = agent_loop(registry, llm, user_query)

    # -- Print session summary --
    print_separator("Session Summary")

    # Recalculate tokens with loaded schemas
    loaded_schema_tokens = sum(
        schema_tokens(s) for s in registry._loaded_schemas.values()
    )
    final_tokens = report["current_tokens"] + loaded_schema_tokens
    net_saved = report["full_load_tokens"] - final_tokens

    print(f"\n  Turns:                      {stats['turns']}")
    print(f"  Total tool calls:           {stats['total_tool_calls']}")
    print(f"    ├─ ToolSearch calls:      {stats['toolsearch_calls']}")
    print(f"    └─ DeferExecuteTool calls:{stats['deferexec_calls']}")
    print(f"\n  Token accounting:")
    print(f"    Startup cost (immediate + directory): ~{report['current_tokens']:,}")
    print(f"    Loaded via ToolSearch:                ~{loaded_schema_tokens:,}")
    print(f"    Total context from tools:             ~{final_tokens:,}")
    print(f"    Full loading would have cost:         ~{report['full_load_tokens']:,}")
    if net_saved > 0:
        net_pct = round(net_saved / report["full_load_tokens"] * 100)
        print(f"  {C_GREEN}Net saved:                              ~{net_saved:,} tokens ({net_pct}%){C_RESET}")
    else:
        print(f"  {C_RED}Net cost (more than full loading):      ~{-net_saved:,} tokens{C_RESET}")

    print(f"\n{C_BOLD}Key insight:{C_RESET} The agent only loaded schemas for tools it")
    print(f"actually used. If the conversation had stayed about file editing,")
    print(f"the {len(deferred_names)} deferred tool schemas would never have")
    print(f"entered the context at all.")

    # -- OS analogy --
    print_separator("OS Analogy: Dynamic Linking")
    print(f"""
  {C_BOLD}Static Linking (s02){C_RESET}          {C_BOLD}Dynamic Linking (s04){C_RESET}
  ─────────────────────          ──────────────────────
  All schemas at startup         Only names at startup
  High memory, no lookup         Low memory, dlopen() on demand
  ToolSearch: not needed         ToolSearch: required
  DeferExecuteTool: not needed   DeferExecuteTool: required

  {C_DIM}ToolSearch   = dlopen() — load the library on demand{C_RESET}
  {C_DIM}DeferExecute = call     — use the loaded library{C_RESET}
  {C_DIM}Schema       = .so file — the actual code{C_RESET}
  {C_DIM}Tool name    = symbol   — just a name in the symbol table{C_RESET}
""")

    print("下一课: s04 Permission & Hooks — 先划边界, 再给自由\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deferred tool loading demo")
    parser.add_argument("--interactive", action="store_true", help="open an interactive ToolSearch shell")
    args = parser.parse_args()
    if args.interactive:
        interactive()
    else:
        main()
