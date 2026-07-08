#!/usr/bin/env python3
from __future__ import annotations
"""
s14_context_compact.py - Context Compaction: Four-Layer Pipeline

Context window management for long conversations.

Four layers, triggered from lightest to heaviest:

  Layer 1: Tool result truncation
           Large tool outputs get truncated to a token budget.
           Cheapest — no messages are lost.

  Layer 2: File content deduplication
           Same file read multiple times → keep only the latest read.
           Cheap — redundant information is removed.

  Layer 3: Message history pruning
           Old messages get dropped, keeping recent N turns.
           Medium — may lose detail from early conversation.

  Layer 4: Full conversation summary
           When all else fails, generate a summary of the entire
           conversation using the model, replacing old messages.
           Expensive — costs one API call.

  ┌──────────────────────────────────────────────────────┐
  │                 Compact Pipeline                     │
  │                                                      │
  │  token_count > threshold?                            │
  │    ├─ L1: truncate large tool_results                │
  │    ├─ L2: dedup file reads (keep latest)             │
  │    ├─ L3: prune old messages (keep recent N)         │
  │    └─ L4: generate summary (model call)              │
  │                                                      │
  │  NEVER compact: system prompt, tool definitions      │
  └──────────────────────────────────────────────────────┘

Production harnesses often use: precise tiktoken counting, priority-based pruning,
incremental summarization.
Teaching version uses: 4-chars ≈ 1-token estimation, simple thresholds.

Usage:
    python s14_context_compact/code.py
"""



# Machine-readable learning path metadata. Tests enforce that every
# chapter declares what it inherits and what it adds.
PROGRESSION = {'chapter': 's14_context_compact',
 'builds_on': ['s13_output_externalization'],
 'adds': ['token pressure detection', 'structured compaction', 'summary preservation'],
 'preserves': ['externalized output pointers']}
import os, sys, time, json
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

SYSTEM = f"""你是一个桌面 AI 助手, 工作目录: {WORKDIR}
你有文件读写和命令执行工具。回答要简洁。"""


# ======================================================================
# Token estimation
# ======================================================================

# Real WorkBuddy uses tiktoken for precise counting.
# Teaching version: 4 characters ≈ 1 token (rough but fast).

TOKEN_THRESHOLD = 80_000        # Trigger compaction at 80K tokens
HARD_LIMIT = 120_000            # Hard limit — must compact before this
MAX_TOOL_RESULT_TOKENS = 5_000  # Layer 1: truncate tool results above this
KEEP_RECENT_TURNS = 6           # Layer 3: keep this many recent messages


def estimate_tokens(messages: list) -> int:
    """Estimate token count for messages.

    Production harness: tiktoken.encode() for precise count.
    Teaching version: len(text) // 4 as rough estimate.
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += len(json.dumps(block, default=str, ensure_ascii=False)) // 4
                elif hasattr(block, 'model_dump'):
                    total += len(json.dumps(block.model_dump(), default=str, ensure_ascii=False)) // 4
                else:
                    total += len(str(block)) // 4
        total += 4  # role overhead
    return total


# ======================================================================
# Layer 1: Tool result truncation
# ======================================================================

def truncate_tool_results(messages: list) -> tuple[list, int]:
    """Layer 1: Truncate tool results that exceed token budget.

    Scans all tool_result blocks. If a result exceeds
    MAX_TOOL_RESULT_TOKENS, truncate it and add a note.

    Returns: (modified messages, tokens saved)
    """
    saved = 0
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            result = str(block.get("content", ""))
            tokens = len(result) // 4
            if tokens > MAX_TOOL_RESULT_TOKENS:
                max_chars = MAX_TOOL_RESULT_TOKENS * 4
                original_len = len(result)
                truncated = result[:max_chars]
                block["content"] = (
                    truncated
                    + f"\n\n[... 已截断: 原始 {original_len} 字符, "
                    f"保留 {max_chars} 字符 ...]"
                )
                saved += tokens - MAX_TOOL_RESULT_TOKENS

    return messages, saved


# ======================================================================
# Layer 2: File content deduplication
# ======================================================================

def dedup_file_reads(messages: list) -> tuple[list, int]:
    """Layer 2: Remove duplicate file reads, keep only the latest.

    When the agent reads the same file multiple times, older reads
    are redundant. We track read_file tool calls and remove older
    results for the same path.

    Returns: (modified messages, tokens saved)
    """
    # Track metadata on tool results
    # In real implementation, we'd correlate tool_use and tool_result
    # by ID. Teaching version: we tag results during execution.

    # Find the latest read of each file path
    latest_reads: dict[str, int] = {}  # path -> msg index
    for mi, msg in enumerate(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                path = block.get("_read_path")
                if path:
                    latest_reads[path] = mi

    # Remove older reads of the same file
    saved = 0
    for mi, msg in enumerate(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        new_content = []
        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue
            if block.get("type") == "tool_result":
                path = block.get("_read_path")
                if path and latest_reads.get(path, mi) > mi:
                    # This is an older read — skip it
                    saved += len(str(block.get("content", ""))) // 4
                    continue
            new_content.append(block)
        msg["content"] = new_content

    return messages, saved


# ======================================================================
# Layer 3: Message history pruning
# ======================================================================

def prune_old_messages(messages: list) -> tuple[list, int]:
    """Layer 3: Drop old messages, keep recent N turns.

    Keeps the first user message (for context) and the most recent
    KEEP_RECENT_TURNS messages. Everything in between is dropped.

    CRITICAL: Cannot leave orphaned tool_result blocks — if we remove
    a tool_use, we must also remove its corresponding tool_result,
    and vice versa. Otherwise the API will error.

    Returns: (pruned messages, tokens saved)
    """
    if len(messages) <= KEEP_RECENT_TURNS + 1:
        return messages, 0

    old_count = estimate_tokens(messages)

    first = messages[0]
    recent = messages[-KEEP_RECENT_TURNS:]

    # Fix orphaned tool_results at the start of `recent`
    while recent:
        content = recent[0].get("content")
        if not isinstance(content, list):
            break
        first_block = content[0] if content else None
        if (isinstance(first_block, dict)
                and first_block.get("type") == "tool_result"):
            recent = recent[1:]
        else:
            break

    pruned = [first] + recent
    new_count = estimate_tokens(pruned)

    return pruned, old_count - new_count


# ======================================================================
# Layer 4: Full conversation summary
# ======================================================================

def generate_summary(messages: list) -> tuple[list, int]:
    """Layer 4: Generate a conversation summary replacing old messages.

    Calls the model to summarize the conversation, then replaces
    old messages with the summary. Keeps the most recent few messages
    for continuity.

    This is the most expensive layer — it costs one API call.
    But it can compress tens of thousands of tokens into a few hundred.

    Returns: (summarized messages, tokens saved)
    """
    if len(messages) <= 4:
        return messages, 0

    old_count = estimate_tokens(messages)

    # Split: old messages to summarize, recent to keep
    keep_recent = 4
    to_summarize = messages[:-keep_recent]
    recent = messages[-keep_recent:]

    # Build a text representation of old messages for summarization
    convo_text = []
    for msg in to_summarize:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        parts.append(f"[tool_use: {block.get('name', '?')}]")
                    elif block.get("type") == "tool_result":
                        parts.append(f"[tool_result: {str(block.get('content', ''))[:200]}]")
                elif hasattr(block, 'type'):
                    if block.type == "text":
                        parts.append(block.text)
                    elif block.type == "tool_use":
                        parts.append(f"[tool_use: {block.name}]")
                    elif block.type == "tool_result":
                        parts.append(f"[tool_result: {str(block.content)[:200]}]")
            content = " ".join(parts)
        convo_text.append(f"{role}: {content[:500]}")

    summary_prompt = (
        "请总结以下对话的关键信息, 用于后续对话的上下文恢复。\n"
        "包括: 讨论的问题, 执行的操作, 得到的结论, 当前任务进度。\n"
        "简洁, 只保留关键信息。"
    )

    try:
        response = client.messages.create(
            model=MODEL,
            system=summary_prompt,
            messages=[{"role": "user", "content": "\n".join(convo_text)}],
            max_tokens=2000,
        )
        summary = response.content[0].text
    except Exception as e:
        summary = f"[摘要生成失败: {e}]"

    summarized = [
        {"role": "user", "content": f"[之前的对话摘要]\n{summary}"},
        {"role": "assistant", "content": "好的, 我已了解之前的对话内容。请继续。"},
    ] + recent

    new_count = estimate_tokens(summarized)

    return summarized, old_count - new_count


# ======================================================================
# Compact dispatcher
# ======================================================================

def compact_if_needed(messages: list, verbose: bool = True) -> list:
    """Check token count and compact if needed.

    Tries layers in order: L1 → L2 → L3 → L4.
    Stops as soon as token count drops below threshold.
    """
    tokens = estimate_tokens(messages)

    if tokens < TOKEN_THRESHOLD:
        return messages

    if verbose:
        print(f"\n\033[33m[compact] 当前 token 估算: {tokens:,} (阈值: {TOKEN_THRESHOLD:,})\033[0m")

    # Layer 1: Truncate large tool results
    messages, saved = truncate_tool_results(messages)
    tokens = estimate_tokens(messages)
    if saved > 0 and verbose:
        print(f"\033[33m[compact] L1 截断工具结果: 节省 {saved:,} tokens, 当前 {tokens:,}\033[0m")
    if tokens < TOKEN_THRESHOLD:
        return messages

    # Layer 2: Dedup file reads
    messages, saved = dedup_file_reads(messages)
    tokens = estimate_tokens(messages)
    if saved > 0 and verbose:
        print(f"\033[33m[compact] L2 文件去重: 节省 {saved:,} tokens, 当前 {tokens:,}\033[0m")
    if tokens < TOKEN_THRESHOLD:
        return messages

    # Layer 3: Prune old messages
    messages, saved = prune_old_messages(messages)
    tokens = estimate_tokens(messages)
    if saved > 0 and verbose:
        print(f"\033[33m[compact] L3 修剪旧消息: 节省 {saved:,} tokens, 当前 {tokens:,}\033[0m")
    if tokens < TOKEN_THRESHOLD:
        return messages

    # Layer 4: Generate summary
    messages, saved = generate_summary(messages)
    tokens = estimate_tokens(messages)
    if saved > 0 and verbose:
        print(f"\033[33m[compact] L4 生成摘要: 节省 {saved:,} tokens, 当前 {tokens:,}\033[0m")

    return messages


# ======================================================================
# Tools (simplified)
# ======================================================================

def run_read(path: str) -> str:
    try:
        p = (WORKDIR / path).resolve()
        if not p.is_relative_to(WORKDIR):
            return f"Error: path escapes workspace"
        return p.read_text()[:20000]  # Allow large reads to test truncation
    except Exception as e:
        return f"Error: {e}"

def run_bash(command: str) -> str:
    import subprocess
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True, timeout=30)
        return (r.stdout + r.stderr).strip()[:5000] or "(no output)"
    except Exception as e:
        return f"Error: {e}"

def run_write(path: str, content: str) -> str:
    try:
        p = (WORKDIR / path).resolve()
        if not p.is_relative_to(WORKDIR):
            return "Error: path escapes workspace"
        p.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


TOOLS = [
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object",
         "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object",
         "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "write_file", "description": "Write content to a file.",
     "input_schema": {"type": "object",
         "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
         "required": ["path", "content"]}},
]

TOOL_HANDLERS = {"read_file": run_read, "bash": run_bash, "write_file": run_write}


# ======================================================================
# Agent Loop with compaction
# ======================================================================

def agent_loop(messages: list):
    """Agent loop with context compaction before each API call."""
    while True:
        # --- NEW: Compact if needed ---
        messages = compact_if_needed(messages, verbose=True)

        # Token check display
        tokens = estimate_tokens(messages)
        print(f"\033[90m[tokens: {tokens:,} / {TOKEN_THRESHOLD:,}]\033[0m")

        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return messages

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            handler = TOOL_HANDLERS.get(block.name)
            output = handler(**block.input) if handler else f"Unknown: {block.name}"

            display = str(output)[:100].replace('\n', ' ')
            print(f"  \033[36m> {block.name}\033[0m {display}")

            # Tag tool results for dedup (Layer 2)
            result_block = {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            }
            if block.name == "read_file":
                result_block["_read_path"] = block.input.get("path", "")

            results.append(result_block)

        messages.append({"role": "user", "content": results})
    return messages


# ======================================================================
# Main
# ======================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("s14: Context Compact — 四层压缩管线")
    print("=" * 60)
    print(f"\033[90m  阈值: {TOKEN_THRESHOLD:,} tokens\033[0m")
    print(f"\033[90m  L1: 工具结果截断 (> {MAX_TOOL_RESULT_TOKENS:,} tokens)\033[0m")
    print(f"\033[90m  L2: 文件内容去重 (同文件只留最新)\033[0m")
    print(f"\033[90m  L3: 消息修剪 (保留最近 {KEEP_RECENT_TURNS} 轮)\033[0m")
    print(f"\033[90m  L4: 全对话摘要 (模型生成)\033[0m")
    print(f"\033[90m  输入 stats 查看当前 token 使用\033[0m")
    print()

    history = []
    while True:
        try:
            query = input("\033[36ms14 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit"):
            break

        if query.strip().lower() == "stats":
            tokens = estimate_tokens(history)
            print(f"\033[90m消息数: {len(history)}\033[0m")
            print(f"\033[90mToken 估算: {tokens:,} / {TOKEN_THRESHOLD:,}\033[0m")
            print(f"\033[90m阈值比例: {tokens/TOKEN_THRESHOLD*100:.1f}%\033[0m")
            continue

        history.append({"role": "user", "content": query})
        history = agent_loop(history)

        for block in history[-1]["content"]:
            if getattr(block, "type", None) == "text":
                print(block.text)
        print()
