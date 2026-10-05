# s17: MCP Connectors - External Tools Need Trust and Grants

[中文](README.md) · [English](README.en.md)
> *A connector can expose tools, but trust and the active skill grant still decide visibility.*
>
> **Harness layer: external tool protocols and connector lifecycle.**

The lesson models configured, trusted, connected, and deferred MCP tools with namespacing and fail-closed permission checks.

![Chapter diagram 1](./images/mcp-connectors-en.svg)

## Code Architecture Diagram

```mermaid
flowchart LR
    A["MCP Config"] --> B["Client Handshake"]
    B --> C["Tool Schema Import"]
    T["Connector Trust"] --> G{"Trust ∩ Skill Grant"}
    P["Skill Permission Grant"] --> G
    C --> G
    G -->|Allow| D["Tool Dispatch"]
    G -->|Deny| X["Permission Error"]
    D --> E["Remote Tool Result"]
    C -. "state" .-> S["runtime state"]
    S -. "recover" .-> C
```

## Prerequisites

The lesson uses offline connector definitions so discovery and trust decisions can be tested without a live MCP server.

## WorkBuddy-Style Mechanisms in This Chapter

The lesson treats connectors as governed tool providers with explicit discovery, trust, configuration, and deferred execution.

## Common Mistakes

A connector should not become trusted merely because it is discoverable, and unavailable tools must remain explicit to the caller.

## The Problem

The harness must extend its tool surface without giving an external connector an implicit path around local policy.

## The Solution

```
                    MCP Connector Lifecycle

  configured ──► disconnected ──► trusted ──► connected
       │              │               │            │
       │              │               │            │
  mcp.json written   process not started      user clicks Trust   tools/list discovers tools
  configuration saved      tool pool remains hidden     trust is persisted       then intersect with the Skill Grant

           ┌──────────────────────────────────────┐
           │         Agent Tool Pool              │
           │                                      │
           │  Built-in Tools    MCP Tools         │
           │  ┌──────────┐    ┌────────────────┐ │
           │  │ bash     │    │ mcp__github__  │ │
           │  │ read     │    │   create_pr    │ │
           │  │ write    │    │ mcp__feishu__  │ │
           │  │ glob     │    │   send_msg     │ │
           │  │ grep     │    │ mcp__notion__  │ │
           │  │ ...      │    │   search       │ │
           │  └──────────┘    └────────────────┘ │
           └──────────────────────────────────────┘
```

## How It Works

### Tool Discovery

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "ghp_xxx" }
    },
    "feishu": {
      "command": "npx",
      "args": ["-y", "@workbuddy/mcp-feishu"],
      "env": { "FEISHU_APP_ID": "cli_xxx", "FEISHU_APP_SECRET": "xxx" }
    }
  }
}
```

### Deferred Tool Loading

```python
TRUST_FILE = Path.home() / ".workbuddy" / "connector_trust.json"

def is_trusted(connector_name: str) -> bool:
    """Check whether the connector has been trusted by the user."""
    trust_data = json.loads(TRUST_FILE.read_text()) if TRUST_FILE.exists() else {}
    return trust_data.get(connector_name, {}).get("trusted", False)

def trust_connector(connector_name: str):
    """The user manually trusts a connector."""
    trust_data = json.loads(TRUST_FILE.read_text()) if TRUST_FILE.exists() else {}
    trust_data[connector_name] = {"trusted": True, "timestamp": time.time()}
    TRUST_FILE.write_text(json.dumps(trust_data, indent=2))
```

```python
grant = MCPPermissionGrant(
    tools={"mcp__github__list_issues"},
    network=True,
)
manager = ConnectorManager(MCP_CONFIG, grant)
```

### Connector State in Context

```python
def discover_tools(connector_name: str) -> list[dict]:
    """Discover tools provided by the connector through MCP."""
    connector = connectors[connector_name]
    response = connector.request("tools/list", {})

    discovered = []
    for tool in response.get("tools", []):
        # namespace it: mcp__connectorname__toolname
        namespaced_name = f"mcp__{connector_name}__{tool['name']}"
        if not active_skill_grant.allows(namespaced_name):
            continue
        discovered.append({
            "name": namespaced_name,
            "description": tool["description"],
            "input_schema": tool["inputSchema"],
        })
    return discovered
```

### WorkBuddy Architecture Comparison

This comparison maps connector configuration, trust, tool discovery, and deferred execution to the corresponding WorkBuddy-style harness boundary.

```python
# tell the agent in the system prompt only which deferred tools are available
# do not include the full schema

DEFERRED_TOOLS = [
    {"name": "mcp__github__create_pr", "description": "Create a GitHub PR"},
    {"name": "mcp__feishu__send_message", "description": "Send a Feishu message"},
    # ... only name + description, without input_schema
]

# When the agent decides to use a deferred tool:
# 1. ToolSearch loads the schema
schema = tool_search("mcp__github__create_pr")  # check the Skill grant again
# 2. DeferExecuteTool executes
result = defer_execute_tool("mcp__github__create_pr", {"title": "...", "body": "..."})  # check again
```

### Configuration Files

```python
def build_connector_context() -> str:
    """Build connector state context and inject it into the system prompt."""
    lines = ["<available_deferred_tools>"]
    for name, conn in connectors.items():
        if conn.status == "connected":
            for tool in conn.tools:
                if not active_skill_grant.allows(tool["name"]):
                    continue
                lines.append(f"- {tool['name']}: {tool['description']}")
    lines.append("</available_deferred_tools>")
    return "\n".join(lines)
```

## WorkBuddy Architecture Comparison

This comparison maps connector configuration, trust, tool discovery, and deferred execution to the corresponding WorkBuddy-style harness boundary.

### Trust Flow

```json
{
  "mcpServers": {
    "tencent-docs": {
      "command": "npx",
      "args": ["-y", "@workbuddy/mcp-tencent-docs"],
      "env": { "TENCENT_DOCS_TOKEN": "..." }
    }
  }
}
```

### Deferred Tool Loading

### Connector Ecosystem

### Code Walkthrough

### Run

## Code Walkthrough

Trace connector configuration, trust admission, tool discovery, schema loading, and the final governed call.

## Run

```bash
python s17_mcp_connectors/code.py
python -m pytest -q tests/test_skill_permissions.py
```

## Exercises

Use these exercises to change one part of connector configuration, trust, tool discovery, and deferred execution at a time and explain the resulting contract.

## Next Lesson

- The lesson models configured, trusted, connected, and deferred MCP tools with namespacing and fail-closed permission checks.

**Reference tables**

| Stage | Meaning | Tool visibility |
|---|---|-----------|
| configured | Configuration written to `mcp.json` | Not visible |
| disconnected | Process is not running | Not visible |
| trusted | User has trusted it; process is waiting to start | Not visible |
| connected | Process started and `tools/list` completed | Only declared tools are visible, with deferred loading |

| Method | Direction | Purpose |
|------|------|------|
| `tools/list` | client -> server | Discover available tools |
| `tools/call` | client -> server | Call a tool |
| `resources/list` | client -> server | List available resources |
| `resources/read` | client -> server | Read a resource |

| Connector | Capability |
|--------|------|
| `tencent-docs` | Read and write Tencent Docs |
| `github` | GitHub PR and issue operations |
| `feishu` | Feishu messages and documents |
| `notion` | Notion page management |
| `dingtalk` | DingTalk messages and approvals |
| `wecom` | WeCom messages |
| `ardot` | AR design-tool integration |

The original Chinese README remains the primary-language reference. This English companion keeps the same code, diagrams, local paths, and chapter structure so readers can switch languages without losing runnable details.

Local references:

- [Reference](README.md)
- [Reference](README.en.md)
- [Reference](./images/mcp-connectors-en.svg)
