#!/usr/bin/env python3
from __future__ import annotations
"""
s17_mcp_connectors.py - MCP Connectors: Protocol, Trust, Deferred Tools

Simulates WorkBuddy's MCP connector lifecycle:

    configured ──► disconnected ──► trusted ──► connected
         │              │               │            │
    mcp.json 写入   进程未启动      用户点击 Trust   tools/list 发现

Key mechanisms simulated:
  1. Connector config parsing (mcp.json format)
  2. Trust model (manual approval before activation)
  3. tools/list discovery (MCP protocol)
  4. Deferred tool loading (ToolSearch + DeferExecuteTool pattern)
  5. Namespace: mcp__connectorname__toolname

Production harnesses often use:
  - Real MCP server processes (stdio transport) spawned by Sidecar
  - Trust state persisted in ~/.workbuddy/connector_trust.json
  - Actual JSON-RPC over stdio to communicate with connector processes
  - Connector status displayed in Electron settings UI
  - A catalog of built-in connectors (teaching sample): docs, github, feishu, notion, etc.

Teaching version uses:
  - Simulated connector processes (Python dicts, no real subprocesses)
  - In-memory trust state (no file persistence)
  - Simulated tools/list responses (hardcoded tool definitions)
  - Same namespace pattern, same deferred loading concept

Usage:
    python s17_mcp_connectors/code.py
"""



# Machine-readable learning path metadata. Tests enforce that every
# chapter declares what it inherits and what it adds.
PROGRESSION = {'chapter': 's17_mcp_connectors',
 'builds_on': ['s16_skills_system'],
 'adds': ['connector config', 'trust workflow', 'MCP tool namespace'],
 'preserves': ['lazy capability loading']}
import os, sys, time, json
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


# ============================================================
# MCP Connector Configuration (simulates mcp.json)
# ============================================================

MCP_CONFIG = {
    "mcpServers": {
        "github": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": "ghp_***"}
        },
        "feishu": {
            "command": "npx",
            "args": ["-y", "@workbuddy/mcp-feishu"],
            "env": {"FEISHU_APP_ID": "cli_***", "FEISHU_APP_SECRET": "***"}
        },
        "notion": {
            "command": "npx",
            "args": ["-y", "@workbuddy/mcp-notion"],
            "env": {"NOTION_TOKEN": "ntn_***"}
        }
    }
}


# ============================================================
# Simulated MCP Server Tool Definitions
# In real WorkBuddy, these are discovered via tools/list RPC
# ============================================================

SIMULATED_TOOLS = {
    "github": [
        {
            "name": "create_pull_request",
            "description": "Create a pull request on a GitHub repository.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository (owner/repo)"},
                    "title": {"type": "string", "description": "PR title"},
                    "body": {"type": "string", "description": "PR description"},
                    "head": {"type": "string", "description": "Source branch"},
                    "base": {"type": "string", "description": "Target branch"}
                },
                "required": ["repo", "title", "head", "base"]
            }
        },
        {
            "name": "list_issues",
            "description": "List issues on a GitHub repository.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "state": {"type": "string", "enum": ["open", "closed", "all"]}
                },
                "required": ["repo"]
            }
        }
    ],
    "feishu": [
        {
            "name": "send_message",
            "description": "Send a message to a Feishu (Lark) chat.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string", "description": "Target chat ID"},
                    "text": {"type": "string", "description": "Message content"},
                    "msg_type": {"type": "string", "enum": ["text", "markdown"]}
                },
                "required": ["chat_id", "text"]
            }
        }
    ],
    "notion": [
        {
            "name": "search_pages",
            "description": "Search Notion pages by query.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Max results"}
                },
                "required": ["query"]
            }
        }
    ]
}


# ============================================================
# Connector Lifecycle Management
# ============================================================

@dataclass
class MCPConnector:
    """Represents a single MCP connector and its lifecycle state."""
    name: str
    config: dict
    status: str = "disconnected"  # disconnected → trusted → connected
    tools: list[dict] = field(default_factory=list)
    trusted: bool = False

    def trust(self):
        """User manually trusts this connector."""
        self.trusted = True
        self.status = "trusted"
        print(f"  \033[32m✓ Connector '{self.name}' trusted by user\033[0m")

    def connect(self):
        """Start the connector process and discover tools."""
        if not self.trusted:
            print(f"  \033[31m✗ Connector '{self.name}' is not trusted\033[0m")
            return False

        self.status = "connecting"
        # Simulate: spawn process, send tools/list request
        self.tools = self._tools_list()
        self.status = "connected"
        print(f"  \033[32m✓ Connector '{self.name}' connected — {len(self.tools)} tools discovered\033[0m")
        return True

    def disconnect(self):
        """Stop the connector process."""
        self.status = "disconnected"
        self.tools = []

    def _tools_list(self) -> list[dict]:
        """Simulate MCP tools/list protocol method.

        Real WorkBuddy sends: {"method": "tools/list", "params": {}}
        over stdio JSON-RPC to the connector subprocess.
        """
        raw_tools = SIMULATED_TOOLS.get(self.name, [])
        # Namespace: mcp__connectorname__toolname
        namespaced = []
        for tool in raw_tools:
            namespaced.append({
                "name": f"mcp__{self.name}__{tool['name']}",
                "description": tool["description"],
                "input_schema": tool["inputSchema"],
                "_connector": self.name,
                "_original_name": tool["name"],
            })
        return namespaced

    def call_tool(self, tool_name: str, params: dict) -> str:
        """Simulate MCP tools/call protocol method.

        Real WorkBuddy sends: {"method": "tools/call", "params": {"name": "...", "arguments": {...}}}
        """
        # Simulate execution
        return json.dumps({
            "status": "ok",
            "connector": self.name,
            "tool": tool_name,
            "params": params,
            "result": f"Simulated response from {self.name}.{tool_name}"
        }, ensure_ascii=False, indent=2)


class ConnectorManager:
    """Manages all MCP connectors — parse config, trust, connect, discover."""

    def __init__(self, config: dict):
        self.connectors: dict[str, MCPConnector] = {}
        self._deferred_schemas: dict[str, dict] = {}  # tool_name -> full schema

        # Parse config
        for name, cfg in config.get("mcpServers", {}).items():
            self.connectors[name] = MCPConnector(name=name, config=cfg)
        print(f"\033[90m[MCP] Loaded {len(self.connectors)} connector configs from mcp.json\033[0m")

    def list_connectors(self) -> str:
        """Display all connectors and their status."""
        lines = ["\n┌─ MCP Connectors ─────────────────────────────────────┐"]
        for name, conn in self.connectors.items():
            trust_badge = "✓" if conn.trusted else "✗"
            tool_count = len(conn.tools) if conn.status == "connected" else 0
            lines.append(
                f"│ {name:<12} status={conn.status:<12} "
                f"trusted={trust_badge} tools={tool_count:<3} │"
            )
        lines.append("└──────────────────────────────────────────────────────┘")
        return "\n".join(lines)

    def trust_connector(self, name: str) -> bool:
        """Trust a connector by name."""
        conn = self.connectors.get(name)
        if not conn:
            print(f"  \033[31mUnknown connector: {name}\033[0m")
            return False
        conn.trust()
        conn.connect()
        return True

    def trust_all(self):
        """Trust all connectors (for demo convenience)."""
        for conn in self.connectors.values():
            if not conn.trusted:
                conn.trust()
                conn.connect()

    def get_deferred_tools_list(self) -> list[dict]:
        """Get list of available deferred tools (name + description only, no schema).

        This is what gets injected into the system prompt.
        Real WorkBuddy injects this as <available_deferred_tools> block.
        """
        tools = []
        for conn in self.connectors.values():
            if conn.status == "connected":
                for tool in conn.tools:
                    tools.append({
                        "name": tool["name"],
                        "description": tool["description"],
                    })
        return tools

    def tool_search(self, tool_name: str) -> dict | None:
        """Simulate ToolSearch: load full schema for a deferred tool.

        Production harness: agent calls ToolSearch with tool_names list,
        system returns full input_schema for each.
        """
        for conn in self.connectors.values():
            if conn.status != "connected":
                continue
            for tool in conn.tools:
                if tool["name"] == tool_name:
                    schema = {
                        "name": tool["name"],
                        "description": tool["description"],
                        "input_schema": tool["input_schema"],
                    }
                    self._deferred_schemas[tool_name] = schema
                    return schema
        return None

    def defer_execute_tool(self, tool_name: str, params: dict) -> str:
        """Simulate DeferExecuteTool: execute a deferred tool.

        Production harness: agent calls DeferExecuteTool with validated params.
        """
        # Verify schema was loaded
        if tool_name not in self._deferred_schemas:
            return f"Error: Tool '{tool_name}' schema not loaded. Call ToolSearch first."

        # Find connector
        for conn in self.connectors.values():
            for tool in conn.tools:
                if tool["name"] == tool_name:
                    return conn.call_tool(tool["_original_name"], params)

        return f"Error: Tool '{tool_name}' not found in any connected connector."

    def build_context(self) -> str:
        """Build connector context string for system prompt injection."""
        lines = ["<available_deferred_tools>"]
        for tool in self.get_deferred_tools_list():
            lines.append(f"- {tool['name']}: {tool['description']}")
        if len(lines) == 1:
            lines.append("(no connectors connected — use trust_connector to activate)")
        lines.append("</available_deferred_tools>")
        return "\n".join(lines)


# ============================================================
# Built-in Tools (always available, not deferred)
# ============================================================

def run_bash(command: str) -> str:
    import subprocess
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR, capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:5000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout"

BUILTIN_TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
]

BUILTIN_HANDLERS = {"bash": run_bash}


# ============================================================
# Agent Loop with MCP Connector Integration
# ============================================================

class MCPAgent:
    """Agent loop with MCP connector support."""

    def __init__(self, manager: ConnectorManager):
        self.manager = manager
        self.messages: list[dict] = []

    @property
    def system_prompt(self) -> str:
        """System prompt with connector context injected."""
        base = f"You are a coding agent at {WORKDIR}. You have built-in tools and MCP connector tools."
        connector_ctx = self.manager.build_context()
        deferred_instructions = """

<deferred_tool_instructions>
The tools listed in <available_deferred_tools> are NOT directly callable.
To use them:
1. First call ToolSearch with tool_names to load the tool's schema.
2. Then call DeferExecuteTool with the tool name and parameters.
Example: To use mcp__github__create_pull_request:
  1. ToolSearch(tool_names=["mcp__github__create_pull_request"])
  2. DeferExecuteTool(toolName="mcp__github__create_pull_request", params={{...}})

If no connectors are connected, tell the user to trust connectors first.
</deferred_tool_instructions>"""
        return base + "\n" + connector_ctx + deferred_instructions

    @property
    def tools(self) -> list[dict]:
        """Tool definitions available to the model.

        Note: MCP tools are NOT included here — they are deferred.
        Only ToolSearch and DeferExecuteTool are exposed.
        """
        return BUILTIN_TOOLS + [
            {"name": "ToolSearch", "description": "Load schema for a deferred MCP tool. Pass tool_names array.",
             "input_schema": {"type": "object", "properties": {
                 "tool_names": {"type": "array", "items": {"type": "string"}}
             }, "required": ["tool_names"]}},
            {"name": "DeferExecuteTool", "description": "Execute a deferred tool by name with params.",
             "input_schema": {"type": "object", "properties": {
                 "toolName": {"type": "string"},
                 "params": {"type": "object"}
             }, "required": ["toolName", "params"]}},
        ]

    def handle_tool_call(self, block) -> str:
        """Dispatch a tool call to the appropriate handler."""
        name = block.name
        params = dict(block.input) if block.input else {}

        if name in BUILTIN_HANDLERS:
            return BUILTIN_HANDLERS[name](**params)

        elif name == "ToolSearch":
            tool_names = params.get("tool_names", [])
            results = []
            for tn in tool_names:
                schema = self.manager.tool_search(tn)
                if schema:
                    results.append(schema)
                else:
                    results.append({"error": f"Tool '{tn}' not found. Check available_deferred_tools."})
            return json.dumps(results, ensure_ascii=False, indent=2)

        elif name == "DeferExecuteTool":
            tool_name = params.get("toolName", "")
            tool_params = params.get("params", {})
            return self.manager.defer_execute_tool(tool_name, tool_params)

        return f"Unknown tool: {name}"

    def run(self, user_input: str):
        """Run one turn of the agent loop."""
        self.messages.append({"role": "user", "content": user_input})

        while True:
            response = client.messages.create(
                model=MODEL,
                system=self.system_prompt,
                messages=self.messages,
                tools=self.tools,
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

                print(f"\033[36m> {block.name}\033[0m {json.dumps(dict(block.input), ensure_ascii=False)[:120]}")
                output = self.handle_tool_call(block)
                print(f"\033[90m  {output[:200]}\033[0m")
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})

            self.messages.append({"role": "user", "content": results})


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("s17: MCP Connectors — protocol, trust, deferred tools")
    print("=" * 60)
    print("Commands:")
    print("  /list     — List all connectors and their status")
    print("  /trust N  — Trust connector N (e.g. /trust github)")
    print("  /trustall — Trust all connectors")
    print("  /ctx      — Show system prompt connector context")
    print("  q         — Quit")
    print()

    manager = ConnectorManager(MCP_CONFIG)
    agent = MCPAgent(manager)

    print(manager.list_connectors())

    while True:
        try:
            query = input("\n\033[36ms17 >> \033[0m")
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
            arg = parts[1] if len(parts) > 1 else ""

            if cmd == "/list":
                print(manager.list_connectors())
            elif cmd == "/trust":
                if not arg:
                    print("Usage: /trust <connector_name>")
                else:
                    manager.trust_connector(arg.strip())
            elif cmd == "/trustall":
                manager.trust_all()
                print(manager.list_connectors())
            elif cmd == "/ctx":
                print(manager.build_context())
            else:
                print(f"Unknown command: {cmd}")
            continue

        # Regular query to agent
        agent.run(query)
