# s24: Comprehensive - Many Mechanisms, One Loop

[中文](README.md) · [English](README.en.md)
> *The mechanisms are many; the contract is one.*
>
> **Harness layer: integration. The loop belongs to the agent; the mechanisms belong to the harness.**

The final lesson composes the earlier contracts into one offline RAG-memory harness and verifies that restart does not change their meaning.

![Chapter diagram 1](./images/comprehensive-overview-en.svg)

## Code Architecture Diagram

```mermaid
flowchart LR
    Q["Query"]

    subgraph M["Memory line · durable facts"]
        M1["Workspace Memory"] --> M2["S12 RemoteMemoryStore"]
        M2 --> M3["Fresh store after restart"]
    end

    subgraph R["RAG / Context line · query-scoped evidence"]
        R1["S12 RecallEngine"] --> R2["RecallResult + provenance"]
        R2 --> R3["S15 candidate + policy selection"]
        R3 --> R4["recalled_memory context"]
        R3 --> R5["S14 capture selected evidence"]
        R5 --> R6["DurableContextState"]
    end

    subgraph H["Harness line · one existing runtime"]
        H1["PromptAssembler"] --> H2["Tool registry + permission"]
        H2 --> H3["Transcript + Workspace Memory"]
        H3 --> H4["Fresh adapters replay"]
    end

    Q --> R1
    M2 --> R1
    Q --> H1
    R4 --> H1
    R6 --> H1
    H3 --> M1
    M3 --> H4
```

## Prerequisites

The final lesson assumes the earlier contracts are available and combines them in a keyless offline walkthrough.

## WorkBuddy-Style Mechanisms in This Chapter

The final lesson composes the earlier boundaries into one runtime and checks that restart preserves their contracts.

## Common Mistakes

Integration failures usually come from hidden ownership, duplicated writes, or losing evidence while moving between mechanisms.

## The Problem

```
s01  agent loop              → the loop itself
s02  tool dispatch           → tool dispatch inside the loop
s03  deferred loading        → tools expanded on demand inside the loop
s04  permission hooks        → security gate for the loop
s05  electron shell          → process shell for the loop
s06  sidecar server          → communication channel for the loop
s07  session management      → lifecycle of the loop
s08  model routing           → model selection for the loop
s09  jsonl transcript        → event log for the loop
s10  workspace memory        → workspace memory for the loop
s11  user memory             → user-level memory for the loop
s12  cloud memory            → remote-recall abstraction for the loop
s13  output externalization  → large-output externalization for the loop
s14  context compact         → context compaction for the loop
s15  prompt assembly         → prompt assembly for the loop
s16  skills system           → skill loading for the loop
s17  mcp connectors          → external-tool protocol for the loop
s18  experts system          → domain experts for the loop
s19  visualizer              → output visualization for the loop
s20  result presentation     → result delivery for the loop
s21  sqlite database         → persistence layer for the loop
s22  automation scheduler    → scheduled triggering for the loop
s23  audit sandbox           → security auditing for the loop
s24  comprehensive           → all mechanisms return to one loop
```

## The Solution

```
                    ┌─────────────────────────────────┐
                    │         System Prompt            │
                    │  ┌─────┐ ┌─────┐ ┌───────────┐  │
                    │  │SOUL │ │USER │ │SKILLS list │  │
                    │  │     │ │MEM  │ │EXPERTS    │  │
                    │  └─────┘ └─────┘ └───────────┘  │
                    └──────────────┬──────────────────┘
                                   │
                    ┌──────────────▼──────────────────┐
                    │       ┌─────────────┐           │
                    │       │  Agent Loop │           │
                    │       │  while True │           │
                    │       └──────┬──────┘           │
     ┌──────────────┼──────────────│──────────────────┼──────────────┐
     │              │              │                  │              │
     │   ┌──────────▼─────────┐   │   ┌──────────────▼───────────┐  │
     │   │  Tool Dispatch     │   │   │  Context Management       │  │
     │   │  ┌──────────────┐  │   │   │  ┌──────────────────────┐ │  │
     │   │  │ Permission   │  │   │   │  │ Compaction (s14)     │ │  │
     │   │  │ Check (s04)  │  │   │   │  │ Prompt Assembly (s15)│ │  │
     │   │  ├──────────────┤  │   │   │  └──────────────────────┘ │  │
     │   │  │ Sandbox      │  │   │   └──────────────────────────┘  │
     │   │  │ Check (s23)  │  │   │              │                  │
     │   │  ├──────────────┤  │   │   ┌──────────▼───────────┐     │
     │   │  │ Execute      │  │   │   │  Memory (s10-s12)     │     │
     │   │  ├──────────────┤  │   │   │  workspace / user /   │     │
     │   │  │ Audit Log    │  │   │   │  cloud                 │     │
     │   │  │ (s23)        │  │   │   └───────────────────────┘     │
     │   │  ├──────────────┤  │   │              │                  │
     │   │  │ Usage Track  │  │   │   ┌──────────▼───────────┐     │
     │   │  │ (s21)        │  │   │   │  SQLite DB (s21)      │     │
     │   │  └──────────────┘  │   │   │  sessions / usage /   │     │
     │   └────────────────────┘   │   │  automations           │     │
     │              │              │   └───────────────────────┘     │
     │   ┌──────────▼─────────┐   │              │                  │
     │   │  Visualizer (s19)  │   │   ┌──────────▼───────────┐     │
     │   │  present_files     │   │   │  Automation (s22)     │     │
     │   │  (s20)             │   │   │  Scheduler            │     │
     │   └────────────────────┘   │   └───────────────────────┘     │
     │              │              │              │                  │
     └──────────────┼──────────────│──────────────│──────────────────┘
                    │              │              │
                    └──────────────▼──────────────┘
                    ┌─────────────────────────────┐
                    │    Electron Shell (s05-s07) │
                    │    main + renderer + sidecar│
                    │    + CLI session            │
                    └─────────────────────────────┘
```

### How It Works

## How It Works

### 2. Mechanism Groups

```python
def comprehensive_agent_loop(messages, session):
    while True:
        # ── 1. Prompt Assembly (s15) ──
        system = assemble_prompt(
            soul=get_soul(),           # s11: identity
            user_mem=get_user_memory(), # s11: user preferences
            workspace_mem=get_workspace_log(),  # s10: workspace log
            cloud_profile=get_cloud_profile(),  # s12: cloud memory
            skills=list_skills(),       # s16: skill catalog
            expert=get_expert(),        # s18: domain expert
            tools_context=get_tools_info()  # s17: MCP connector
        )

        # ── 2. Context Compaction (s14) ──
        if token_count(messages) > THRESHOLD:
            messages = compact_context(messages)

        # ── 3. Model Routing (s08) ──
        model = route_model(session.agent_type)  # lite/default/craft

        # ── 4. API Call ──
        response = client.messages.create(
            model=model, system=system,
            messages=messages, tools=session.tools,
        )

        # ── 5. JSONL Transcript (s09) ──
        transcript.append({
            "type": "message",
            "role": "assistant",
            "content": response.content
        })

        # ── 6. Usage Tracking (s21) ──
        db.track_usage(session.id, model, response.usage)

        # ── 7. Inspect normalized content blocks (s01) ──
        messages.append({"role": "assistant", "content": response.content})
        tool_blocks = [b for b in response.content if b.type == "tool_use"]
        if not tool_blocks:
            # ── 8. Result Presentation (s19, s20) ──
            present_result(response.content)
            # ── 9. Memory Update (s10) ──
            update_workspace_memory(session, messages)
            return

        # ── 10. Tool Dispatch (s02) + Deferred Loading (s03) ──
        results = []
        for block in tool_blocks:
                # ── 10a. Deferred Tool? (s03) ──
                if block.name in DEFERRED_TOOLS:
                    tool_schema = tool_search(block.name)  # ToolSearch
                    result = defer_execute(tool_schema, block.input)  # DeferExecuteTool
                    results.append({"type": "tool_result",
                                    "tool_use_id": block.id, "content": result})
                    continue

                # ── 10b. Permission Check (s04) ──
                decision = check_permission(block.name)
                if decision is DENY:
                    results.append(denied_result(block.id))
                    continue

                # ── 10c. Sandbox Check (s23) ──
                if not check_sandbox(block.input):
                    results.append(blocked_result(block.id))
                    continue

                # ── 10d. Execute + Audit (s23) ──
                audit_entry("tool_execute", block.name, block.input)
                output = TOOL_HANDLERS[block.name](**block.input)
                audit_entry("tool_result", block.name, output)

                # ── 10e. Output Externalization (s13) ──
                if should_externalize(output):
                    pointer = write_to_disk(output)
                    output = make_pointer(pointer)  # Context keeps only a pointer

                # ── 10f. Tool Usage (s21) + JSONL (s09) ──
                db.record_tool_call(session.id, block.name)
                transcript.append({
                    "type": "function_call_result",
                    "tool": block.name, "result": output
                })

                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })

        messages.append({"role": "user", "content": results})
```

### 3. Core Insight

```
┌──────────────────────────────────────────────────────────────────┐
│                       Agent Loop (s01)                            │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────┐  │
│  │ Tool layer    │  │ Process layer    │  │ Persistence layer    │  │  Memory layer         │  │
│  │ s02 s03  │  │ s05 s06  │  │ s09 s21  │  │  s10 s11 s12  │  │
│  │ s16 s17  │  │ s07 s08  │  │ s22      │  │  s15          │  │
│  │ s18      │  │          │  │          │  │                │  │
│  └──────────┘  └──────────┘  └──────────┘  └────────────────┘  │
│                                                                  │
│  ┌──────────────┐  ┌──────────┐  ┌──────────┐                  │
│  │ Context management layer  │  │ Security layer    │  │ Interaction layer    │                  │
│  │ s13 s14      │  │ s04 s23  │  │ s19 s20  │                  │
│  └──────────────┘  └──────────┘  └──────────┘                  │
└──────────────────────────────────────────────────────────────────┘
```

### WorkBuddy Architecture Comparison

This comparison maps the integrated harness and RAG-memory restart invariants to the corresponding WorkBuddy-style harness boundary.

```
Agency comes from the model.
Harness makes agency operational.

Model = Claude / GPT / GLM (reasoning + decision-making)
Harness = 24 mechanisms (execution environment + safety + memory + persistence)

Agent = Model × Harness
```

## WorkBuddy Architecture Comparison

This comparison maps the integrated harness and RAG-memory restart invariants to the corresponding WorkBuddy-style harness boundary.

### Multi-Process Architecture

### One-Sentence Summary

```
Electron Main Process
  ├── SidecarServer
  │     ├── JSON-RPC over Unix Socket (s06)
  │     ├── Model Router lite/default/craft (s08)
  │     ├── SQLite Database (s21)
  │     ├── Automation Scheduler (s22)
  │     └── Audit Log Writer (s23)
  ├── Renderer Process (renderer/)
  │     └── React UI (s05)
  ├── Preload Script (preload/)
  │     └── IPC Bridge (s05)
  └── CLI Session Process (cli/)
        └── Agent Loop (agent bridge module)
              ├── Prompt Assembly (s15)
              ├── Tool Dispatch (s02)
              ├── Deferred Loading (s03)
              ├── JSONL Transcript (s09)
              ├── Output Externalization (s13)
              ├── Context Compaction (s14)
              └── Memory Management (s10-s12)
```

### Code Walkthrough

```
WorkBuddy-style harness = an agent loop (s01)
                        + 22 cumulative mechanisms (s02-s23)
                        + one integrated conclusion (s24)
```

## Code Walkthrough

### Compaction and Restart Invariants

### Why Retry Must Not Write Another Memory

### Run

## Run

### The 24-Lesson Takeaway

```bash
python3 s24_comprehensive/code.py --walkthrough
```

```bash
python3 -m pytest -q tests/test_comprehensive_contracts.py
```

```bash
python3 -m pytest -q
python3 scripts/verify.py
```

```bash
python s24_comprehensive/code.py
```

## Exercises

Use these exercises to change one part of the integrated harness and RAG-memory restart invariants at a time and explain the resulting contract.

## The 24-Lesson Takeaway

```
s01  Agent Loop            ──▶  starting point: one loop + one tool
s02  Tool Dispatch         ──▶  multiple tools, one dispatch map
s03  Deferred Loading      ──▶  ToolSearch + DeferExecuteTool two-step call
s04  Permission Hooks      ──▶  set boundaries first, then grant freedom
s05  Electron Shell        ──▶  three processes, one application
s06  Sidecar Server        ──▶  JSON-RPC, RingBuffer
s07  Session Management    ──▶  one child process per session
s08  Model Routing         ──▶  lite/default/craft three-tier routing
s09  JSONL Transcript      ──▶  conversation persistence, append-only writes, crash recovery
s10  Workspace Memory      ──▶  record each day's work
s11  User Memory           ──▶  cross-project preferences
s12  Cloud Memory          ──▶  server-side retrieval
s13  Output Externalization──▶  write large output to disk, keep a pointer in context
s14  Context Compact       ──▶  four-layer compaction pipeline
s15  Prompt Assembly       ──▶  assemble segments at runtime
s16  Skills System         ──▶  load skills on demand
s17  MCP Connectors        ──▶  connector ecosystem
s18  Experts System        ──▶  domain expert packages
s19  Visualizer            ──▶  SVG/HTML visualization
s20  Result Presentation   ──▶  file delivery
s21  SQLite Database       ──▶  WAL mode, 7 tables
s22  Automation Scheduler  ──▶  run automatically at the scheduled time
s23  Audit & Sandbox       ──▶  leave an immutable trace for every step
s24  Comprehensive         ──▶  endpoint: bring everything back to one loop
```

- The final lesson composes the earlier contracts into one offline RAG-memory harness and verifies that restart does not change their meaning.

**Reference tables**

| Lesson | Mechanism | Motto | Position in the loop |
|---|---|---|---|
| s01 | Agent Loop | One loop is enough | The loop itself |
| s02 | Tool Dispatch | Add tools without changing the loop | Inside the loop, tool dispatch |
| s03 | Deferred Loading | Do not load every tool | Discover tools on demand |
| s04 | Permission Hooks | Set the boundary before granting freedom | Before tool execution |
| s05 | Electron Shell | One process is not enough; use three | The loop's outer shell |
| s06 | Sidecar Server | Main does not run the agent | Inter-process communication |
| s07 | Session Management | One child process per session | Loop lifecycle |
| s08 | Model Routing | Use AI to manage AI | Model choice before an API call |
| s09 | JSONL Transcript | Append the conversation to disk | Persistence after each turn |
| s10 | Workspace Memory | Scope project facts | After substantive work |
| s11 | User Memory | Keep cross-project preferences at user scope | Prompt assembly |
| s12 | Cloud Memory | Some memory lives remotely | Prompt assembly |
| s13 | Output Externalization | Put large output on disk and keep a pointer | After tools, before context |
| s14 | Context Compact | Context always fills up | Before the API call |
| s15 | Prompt Assembly | A prompt is assembled | Each API call |
| s16 | Skills System | List skills first | Prompt assembly and tool pool |
| s17 | MCP Connectors | External tools use a standard protocol | Tool-pool extension |
| s18 | Experts System | Load a domain expert bundle | Prompt, tools, and memory |
| s19 | Visualizer | The agent can draw | Output processing |
| s20 | Result Presentation | Finish by delivering the result | Output processing |
| s21 | SQLite Database | Sessions must persist | After each operation |
| s22 | Automation Scheduler | Run at the right time | Trigger outside the loop |
| s23 | Audit and Sandbox | Leave evidence at every step | Before and after tools |
| **s24** | **Comprehensive** | **Many mechanisms, one loop** | **All mechanisms integrated** |

| Layer | Lessons | Responsibility |
|---|---|---|
| Tool layer | s02, s03, s16, s17, s18 | Dispatch, deferred loading, skills, connectors, and experts |
| Process layer | s05, s06, s07, s08 | Electron, Sidecar, sessions, and model routing |
| Persistence layer | s09, s21, s22 | JSONL transcripts, SQLite, and automation |
| Memory layer | s10, s11, s12, s15 | Three memory scopes and prompt assembly |
| Context layer | s13, s14 | Output externalization and compaction |
| Safety layer | s04, s23 | Permission checks, sandboxing, and audit logs |
| Interaction layer | s19, s20 | Visualization and result delivery |

The original Chinese README remains the primary-language reference. This English companion keeps the same code, diagrams, local paths, and chapter structure so readers can switch languages without losing runnable details.

Local references:

- [Reference](README.md)
- [Reference](README.en.md)
- [Reference](./images/comprehensive-overview-en.svg)
