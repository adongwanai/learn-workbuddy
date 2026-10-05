# s13: Tool Output Externalization - Move Large Results to Disk

[Chinese](README.md) · [English](README.en.md)
> *Context is memory; durable tool output belongs in an artifact with a pointer.*
>
> **Harness layer: context management and artifact lifecycle.**

Large tool results become immutable artifacts. The prompt keeps a bounded preview, digest, provenance, and a readable pointer.

![Chapter diagram 1](./images/output-externalization-en.svg)

## Code Architecture Diagram

```mermaid
flowchart LR
    A["Tool Output"] --> B["Threshold Check"]
    B --> C["Immutable Artifact"]
    C --> D["Context Pointer<br/>summary + preview + path"]
    D --> E["Read Page Fault"]
    C --> F["ArtifactReference<br/>digest + provenance"]
    F -. "later policy" .-> G["Memory Reference<br/>summary + pointer, no body"]
    G --> H["ArtifactRetentionClaim<br/>source ID + digest + lease"]
    H --> L["Lease Journal<br/>prepared → committed → released"]
    L --> R["Owner Reconciliation<br/>generation fence + tombstone"]
    R --> L
    C --> I["plan_cleanup → snapshot"]
    L --> I
    I --> J["retain referenced / recent<br/>delete expired orphan"]
```

## Prerequisites

The lesson assumes tool results can be represented as text and focuses on thresholds, artifact references, and safe retrieval.

## WorkBuddy-Style Mechanisms in This Chapter

The lesson moves large tool results to durable artifacts while keeping bounded pointers in the active context.

## Common Mistakes

Do not truncate evidence without retaining a path and digest, and do not let artifact cleanup invalidate active references.

## The Problem

The harness must control context growth without losing the complete output needed for inspection or later recovery.

## The Solution

```
Tool execution
    │
    ▼
┌──────────────────────┐
│ Output > threshold?          │
└──────────┬───────────┘
      No   │   Yes
      │    │    │
      ▼    │    ▼
  Place directly    │  ┌─────────────────────────┐
  in context  │  │ Write to disk:                │
            │  │   tool-results/         │
            │  │     tool_result_001.txt │
            │  │     (full output)           │
            │  └──────────┬──────────────┘
            │             │
            │             ▼
            │  ┌─────────────────────────┐
            │  │ Put a pointer in context:          │
            │  │   head 6KB              │
            │  │   ... (omitted) ...         │
            │  │   tail 24KB             │
            │  │   [full output at: path]│
            │  └──────────┬──────────────┘
            │             │
            ▼             ▼
       Context grows by ~the original size    Context grows only ~30KB
```

## How It Works

### Write to Disk

```python
BASH_MAX_OUTPUT_LENGTH = 30000        # chars — Bash Output exceeds this value → externalized
CODEBUDDY_TOOL_RESULT_THRESHOLD_KB = 50  # KB — non-Bash tool output exceeds this value → externalized
```

```python
def should_externalize(self, output: str, tool_name: str) -> bool:
    if tool_name == "bash":
        return len(output) > BASH_MAX_OUTPUT_LENGTH
    else:
        return len(output.encode("utf-8")) > CODEBUDDY_TOOL_RESULT_THRESHOLD_KB * 1024
```

### Replace Context with a Pointer and Preview

```
~/.workbuddy/projects/<workspace>/<session>/
└── tool-results/
    ├── tool_result_001.txt    # full output from the first externalization
    ├── tool_result_002.txt    # full output from the second externalization
    ├── tool_result_003.txt
    └── ...
```

```python
def _next_artifact_path(self) -> Path:
    while True:
        self._counter += 1
        path = self.tool_results_dir / f"tool_result_{self._counter:03d}.txt"
        try:
            path.touch(mode=0o600, exist_ok=False)  # old evidence must not be overwritten
        except FileExistsError:
            continue
        return path
```

### Artifact to Memory: Reference Only

```python
def make_pointer(self, output: str, artifact: ArtifactReference) -> str:
    head = output[:6 * 1024]       # First 6KB
    tail = output[-24 * 1024:]     # Last 24KB

    return (
        f"[Artifact: {artifact.source.source_id}]\n"
        f"Summary: {artifact.summary}\n"
        f"Source: {artifact.source_tool}; SHA-256: {artifact.content_sha256}\n"
        f"Full output: {artifact.path}\n\n"
        f"{head}\n"
        f"\n... [full output at: {artifact.path}] ...\n"
        f"\n{tail}"
    )
```

### Reference-Aware Lifecycle and GC

```python
memory_reference = result.artifact.for_memory()

# Only summary, artifact_path, content_sha256, source_tool, and source.
# No content and no head/tail preview in the context pointer.
payload = memory_reference.to_dict()
```

### Crash-Recoverable Lease Journal

```text
ArtifactMemoryReference
    └─ ArtifactRetentionClaim
         ├─ source_id              # path-free owner identity
         ├─ content_sha256         # full digest
         ├─ reference_count        # active reference count aggregated by the Memory adapter
         └─ retain_until?          # optional lease expiration time
```

#### Generation-Fenced Owner Reconciliation

```text
1. PREPARED  ── append + fsync ──▶ the lease intent is persisted
2. Memory adapter publishes the reference
3. COMMITTED ── append + fsync ──▶ the reference is confirmed visible
```

```python
journal = ArtifactRetentionJournal(session_dir)
claim = ArtifactRetentionClaim.from_memory_reference(memory_reference)

transaction, stored_record = journal.publish_reference(
    claim,
    lambda: memory_adapter.append(memory_reference),
    transaction_id="workspace-artifact-reference-1",
)

# Release only after Memory deletion succeeds; keep the lease committed on errors.
journal.remove_reference(
    transaction.transaction_id,
    lambda: memory_adapter.remove(memory_reference.source.source_id),
)
```

```python
def publish_validated_reference():
    if not memory_adapter.accepts(memory_reference):
        # Local validation only; no write has started.
        raise ArtifactPublicationRejected("reference rejected before write")
    # Do not convert an ordinary exception here into an explicit rejection: the write result may be uncertain.
    return memory_adapter.append(memory_reference)
```

#### Read: A Page-Fault-Like Operation

```python
# The adapter maps the database row version, ETag, or object generation to a fence.
reports = journal.reconcile_pending(memory_owner_adapter)
for report in reports:
    audit_sink.append(report.to_dict())
```

```python
claim = ArtifactRetentionClaim.from_memory_reference(
    memory_reference,
    reference_count=2,
    retain_until="2026-09-01T00:00:00+00:00",
)
policy = ArtifactCleanupPolicy(
    orphan_ttl_seconds=24 * 60 * 60,
    max_deletions=100,
    dry_run=False,
)
journal = ArtifactRetentionJournal(session_dir)
plan = externalizer.plan_cleanup_from_journal(journal, policy=policy)
report = externalizer.apply_cleanup_from_journal(plan, journal)
```

### The OS Analogy: Virtual Memory

```
Agent context                           Disk
┌─────────────────────┐               ┌──────────────────────┐
│ tool_result:         │               │ tool_result_001.txt  │
│   head 6KB ...       │               │ (50MB full output)       │
│   [full at: path]    │── Read ──▶    │                      │
│   ... tail 24KB      │               │                      │
│                      │◀──content──   │                      │
│ Read result:         │               │                      │
│   line 40000: ERROR  │               │                      │
└─────────────────────┘               └──────────────────────┘
       ~30KB                              does not enter context
```

```python
def read_artifact(self, artifact: ArtifactReference, offset: int = 0, limit: int = 2000) -> str:
    """Read owned evidence on demand and verify its digest first."""
    owned_path = self._owned_path(artifact.path)
    encoded = owned_path.read_bytes()
    if hashlib.sha256(encoded).hexdigest() != artifact.content_sha256:
        raise ArtifactIntegrityError("artifact digest mismatch")
    content = encoded.decode("utf-8")
    lines = content.split("\n")
    selected = lines[offset:offset + limit]
    return "\n".join(selected)
```

## WorkBuddy Architecture Comparison

This comparison maps large-output artifacts, context pointers, and retention to the corresponding WorkBuddy-style harness boundary.

## Output Thresholds

```
┌─────────────────────────────────────────────────────────┐
│ Layer 1: entry control (this chapter, s17)                              │
│   ├─ tool output externalization (large output → disk, only a pointer remains in context)        │
│   ├─ deferred tool loading (load schemas on demand, s04)                  │
│   ├─ background-task isolation (move long tasks to the background, do not consume context)              │
│   └─ SubAgent context isolation (child Agent gets an independent context)            │
│                                                         │
│   Strategy: keep large content out of context from the start (preventive)            │
├─────────────────────────────────────────────────────────┤
│ Layer 2: active compaction (s18)                                   │
│   ├─ pre-message compact (10% threshold, compact before a message)          │
│   └─ auto compact (70-92% threshold, automatic compaction)                │
│                                                         │
│   Strategy: compact when context becomes too large (remedial)                        │
├─────────────────────────────────────────────────────────┤
│ Layer 3: persistence extensions (s14-s16)                             │
│   ├─ cloud memory (user profile, server-side retrieval)                      │
│   ├─ user-level memory (MEMORY.md, manually maintained preferences)                     │
│   └─ workspace memory (daily log, append-only)                        │
│                                                         │
│   Strategy: move unneeded context to external storage and retrieve it on demand               │
└─────────────────────────────────────────────────────────┘
```

## Artifact Directory Layout

```
Without output externalization:
  Turn 1: grep → 50MB → context overflows → 💥 API error → task terminates

With output externalization:
  Turn 1: grep → 50MB → externalized → Context +30KB → continue running
  Turn 2: pytest → 20MB → externalized → Context +30KB → continue running
  Turn 3: cat → 100MB → externalized → Context +30KB → continue running
  ...
  Turn 50: Context is still not full → task completes ✅
```

## WorkBuddy Architecture Comparison

This comparison maps large-output artifacts, context pointers, and retention to the corresponding WorkBuddy-style harness boundary.

### Artifact Pointer Protocol

```bash
# Bash output exceeds this length → write to disk, keep head+tail in context
BASH_MAX_OUTPUT_LENGTH=30000

# non-Bash tool result exceeds this size → write to disk, keep a placeholder in context
CODEBUDDY_TOOL_RESULT_THRESHOLD_KB=50
```

### Desktop Delivery Contract

```
~/.workbuddy/projects/<workspace-hash>/<session-id>/
└── tool-results/
    ├── tool_result_001.txt
    ├── tool_result_002.txt
    └── ...
```

### Code Walkthrough

```javascript
// Bash output handling in the agent bridge (simplified)
if (output.length > BASH_MAX_OUTPUT_LENGTH) {
    const head = output.slice(0, 6 * 1024);
    const tail = output.slice(-24 * 1024);
    const filePath = writeToolResultToDisk(output, sessionDir);

    // Replace context with pointer
    toolResult.content = (
        head + "\n" +
        `[... omitted, full output at: ${filePath} ...]\n" +
        tail
    );
}
```

### Run

```javascript
// non-Bash tool result handling (simplified)
if (resultSize > CODEBUDDY_TOOL_RESULT_THRESHOLD_KB * 1024) {
    const filePath = writeToolResultToDisk(result, sessionDir);
    toolResult.content = (
        `[Output externalized to: ${filePath}]\n` +
        `Preview: ${result.slice(0, 2048)}...\n` +
        `Use Read tool to access full content.`
    );
}
```

### Exercises

Use these exercises to change one part of large-output artifacts, context pointers, and retention at a time and explain the resulting contract.

## Code Walkthrough

```
[externalize] tool_result_001.txt written, 1.3MB → 2KB in context (saved 99.8%)
[page-fault]  agent requested full output, reading tool_result_001.txt from disk
```

## Run

```bash
python s13_output_externalization/code.py
```

## Exercises

Use these exercises to change one part of large-output artifacts, context pointers, and retention at a time and explain the resulting contract.

## Next Lesson

- Large tool results become immutable artifacts. The prompt keeps a bounded preview, digest, provenance, and a readable pointer.

**Reference tables**

| Representation | Contains | Consumer | Owns the full body? |
|------|----------|----------|------------------|
| Artifact file | Complete tool output | Audit, on-demand reads, full recovery | Yes; the sole body owner |
| Context pointer | Source ID, summary, SHA-256, path, head/tail preview | Current Agent turn | No; bounded representation |
| Memory reference | Required summary, path, digest, and source fields | Later Memory policy | No; it intentionally has no `content` |

| Concept | Operating system | WorkBuddy |
|------|---------|-----------|
| Memory | RAM | Context window |
| External storage | Disk swap | `tool-results/*.txt` |
| Memory entry | Page-table entry | Pointer plus preview |
| Read-back | Page fault | Read tool |

| Owner observation | Harness behavior | Lease result |
|---|---|---|
| `PUBLISHED`, source ID and full digest match | Append `COMMITTED` inside the fence | Protect the reference by its lease |
| `PUBLISHED`, identity mismatch | Record `pending_conflict` | Keep `PREPARED` and protect indefinitely |
| `UNKNOWN` | Record `pending_unknown` | Keep `PREPARED` and retry later |
| Unsealed `ABSENT` | Owner writes a tombstone with CAS and advances generation | Append `ABORTED` after sealing |
| Generation changed before sealing | Record `pending_stale` | Keep `PREPARED` and observe again |
| Sealed `ABSENT` | Do not seal again; complete the journal transition | Append `ABORTED` |

| Status | Meaning | Delete? |
|---|---|---|
| `retained_referenced` | Active claim and full digest match | No |
| `retained_recent` | Orphan TTL has not elapsed | No |
| `retained_limit` | Maximum deletion count reached | No |
| `retained_unknown` / `denied` | Outside the S13 file, directory, or symlink boundary | No |
| `retained_corrupt` | Claim conflicts with file digest or snapshot | No |
| `missing_referenced` | Active claim points to a missing artifact | No file to delete; report the dangling reference |
| `planned_delete` | TTL elapsed and no active claim exists | Dry-run only reports |
| `deleted` | Revalidation succeeded during apply | Yes |
| `already_missing` / `race_detected` | Repeated run or file changed after planning | No |

| OS concept | WorkBuddy equivalent | Explanation |
|---------|---------------------|------|
| Physical memory (RAM) | Context window | Limited, fast, and expensive |
| Disk swap | `tool-results/*.txt` | Large, slower, and cheap |
| Page-table entry | Pointer plus preview | Small reference to the actual data |
| Page fault | Read tool reads a disk file | Bring data back on demand |
| Demand paging | Externalize output and Read on demand | Do not load data until needed |
| Process isolation | Independent SubAgent context | Prevent child context pollution |
| Asynchronous I/O | Background task mechanism | Long work does not block context |
| Memory reclamation / GC | Compact and auto-compact | Compact when context fills |
| Filesystem | Three-layer memory system | Remote -> user -> workspace |
| Replay limit | Replay at most 1000 events | Prevent history from exhausting memory |

The original Chinese README remains the primary-language reference. This English companion keeps the same code, diagrams, local paths, and chapter structure so readers can switch languages without losing runnable details.

Local references:

- [Reference](README.md)
- [Reference](README.en.md)
- [Reference](./images/output-externalization-en.svg)
