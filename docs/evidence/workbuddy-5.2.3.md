# WorkBuddy-Style Harness Research Summary

This note is intentionally public-safe. It does not reproduce proprietary
source code, packaged assets, private prompts, local paths, hashes, raw logs,
or exact internal file names. It records only the architecture-level lessons
that shaped this clean-room teaching project.

## What The Observations Suggest

Modern desktop AI assistant harnesses tend to split responsibilities across
several layers:

| Layer | Transferable Pattern |
|---|---|
| Desktop shell | Renderer UI talks to a narrow bridge instead of executing tools directly. |
| Local control plane | A local server or sidecar coordinates sessions, connectors, and runtime state. |
| Session runtime | Each conversation owns a workspace, transcript, tool stream, and lifecycle state. |
| Tool system | Tools are registered, described, permissioned, and often discovered lazily. |
| Memory system | Workspace notes, user preferences, transcripts, and remote recall are kept separate. |
| Safety layer | Permission gates, audit records, sandbox boundaries, and artifact indexes are first-class. |

The tutorial reimplements these patterns with original Python code. The goal is
not to match a private implementation, but to teach why these boundaries exist
and how to build a small version from scratch.

## Public-Safe Architecture Takeaways

### Sidecar Runtime

A desktop app should avoid running long-lived agent work directly inside the UI
process. A sidecar or session runtime gives the product a place to manage:

- session creation, resume, reconnect, cancel, and teardown
- terminal or command streams
- local HTTP or JSON-RPC style control APIs
- crash recovery and output replay
- environment isolation between desktop shell and tool process

In this repository, the same idea is taught by:

- `s06_sidecar_server`
- `s07_session_management`
- `mini_workbuddy.server`
- `mini_workbuddy.sidecar`

### Context And Output Management

The core context problem is that tool output, memory, history, and schemas grow
faster than the model window. A harness needs several pressure valves:

- store the full transcript outside the prompt
- write large tool output to files and keep only a pointer in context
- summarize older conversation spans
- defer loading rare tool schemas until needed
- select memories instead of injecting every memory

In this repository, this becomes:

- `s03_deferred_loading`
- `s09_jsonl_transcript`
- `s13_output_externalization`
- `s14_context_compact`
- `s15_prompt_assembly`

### Memory System

The useful teaching abstraction is not "one memory file". It is a layered
system:

| Memory Layer | Teaching Role |
|---|---|
| Workspace memory | Project-specific facts and decisions. |
| User memory | Cross-project preferences and stable working style. |
| Remote/profile recall | A provider or server-side abstraction for long-horizon retrieval. |
| Transcript | Event log for replay and evidence, not raw prompt stuffing. |
| Tool-result artifacts | Durable storage for large outputs and deliverables. |

This maps to `s10_workspace_memory`, `s11_user_memory`, `s12_cloud_memory`,
and `mini_workbuddy.storage`.

### Extensions

Desktop agent products often expose multiple extension surfaces. The useful
clean-room lesson is to keep the concepts distinct:

- skills: model-facing instructions, references, and scripts
- connectors: protocol-backed external tools or services
- plugins: packaging and distribution boundary for skills, connectors, hooks,
  and assets
- experts: domain-specific prompt/context packs

This repository teaches those ideas in `s16_skills_system`,
`s17_mcp_connectors`, and `s18_experts_system`.

### Safety And Audit

The stronger the tool system, the more important the safety layer becomes.
Teaching examples here focus on:

- command policy checks
- path escape prevention
- permission denial as a normal result path
- append-only audit logs
- hash-chain verification with a head anchor
- explicit documentation that string rules are not production sandboxes

See `s04_permission_hooks`, `s23_audit_sandbox`, and
`docs/security-boundaries.md`.

## How This Evidence Is Used

This file is not a reproduction guide for a proprietary product. It is a
public-safe map from observed product-category behavior to original teaching
chapters. Contributors should keep new evidence in this form:

1. Describe the generic engineering problem.
2. State the transferable architecture pattern.
3. Point to this repository's original implementation.
4. Omit local paths, package internals, file names, hashes, raw logs, tokens,
   and machine-specific details.

When in doubt, remove the specific detail and keep the lesson.
