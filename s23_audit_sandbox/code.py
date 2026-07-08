#!/usr/bin/env python3
from __future__ import annotations
"""
s23_audit_sandbox.py - Audit Log & Sandbox

Simulates WorkBuddy's security layer:
- SHA256 hash chain audit log (~/.workbuddy/audit-log/YYYY-MM-DD.jsonl)
- Each entry's hash includes the previous entry's hash (immutable chain)
- Sandbox configuration for allowed/blocked paths and commands
- Command safety classification (BLOCKED/DESTRUCTIVE/HIGH_RISK/CAUTION/SAFE)
- Personal file safety rules (Desktop/Downloads/Documents = HIGH-RISK)

Production harnesses often use: sandbox-config.json, hash chain in JSONL files,
  dangerouslyDisableSandbox parameter for user consent.
Teaching version uses: Python hashlib, same JSONL format, same chain logic.

Usage:
    python s23_audit_sandbox/code.py
"""



# Machine-readable learning path metadata. Tests enforce that every
# chapter declares what it inherits and what it adds.
PROGRESSION = {'chapter': 's23_audit_sandbox',
 'builds_on': ['s22_automation_scheduler'],
 'adds': ['hash-chain audit log', 'command safety classifier', 'sandbox policy'],
 'preserves': ['scheduled autonomous execution boundary']}
import os, sys, time, json, hashlib, subprocess
from datetime import datetime
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
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ.get("MODEL_ID")
if not MODEL:
    raise SystemExit(
        "MODEL_ID is not set. Copy .env.example to .env and fill in "
        "ANTHROPIC_API_KEY and MODEL_ID (see README quick start)."
    )

# ============================================================
# 1. Hash Chain Audit Log
# ============================================================

GENESIS_HASH = "0" * 64  # First entry has prev_hash = 64 zeros
AUDIT_DIR = Path(os.environ.get("WORKBUDDY_HOME", Path.home() / ".workbuddy")) / "audit-log"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)


def audit_log_path() -> Path:
    """~/.workbuddy/audit-log/2026-07-08.jsonl — one file per day."""
    return AUDIT_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"


def audit_head_path() -> Path:
    """Out-of-band chain tip anchor for today's log.

    A hash chain detects mutation of existing entries, but any valid prefix
    still verifies. The head anchor records the expected entry count and tip
    hash so deleting tail entries is detectable in the teaching chapter too.
    """
    return AUDIT_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.head.json"


def compute_hash(entry_data: dict, prev_hash: str) -> str:
    """
    entry.hash = SHA256(entry_data + prev_entry.hash)

    This creates an immutable chain: tampering with any entry
    breaks the hash for all subsequent entries.
    """
    # Exclude the hash field itself from the data being hashed
    data = {k: v for k, v in entry_data.items() if k != "hash"}
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False) + prev_hash
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def append_audit_entry(action: str, params: dict, result: str):
    """Append a new entry to today's audit log."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "params": params,
        "result": result,
    }

    # Read the last entry's hash
    path = audit_log_path()
    prev_hash = GENESIS_HASH
    if path.exists():
        content = path.read_text().strip()
        if content:
            lines = content.split("\n")
            try:
                prev_hash = json.loads(lines[-1])["hash"]
            except (json.JSONDecodeError, KeyError):
                prev_hash = GENESIS_HASH

    # Compute this entry's hash
    entry["hash"] = compute_hash(entry, prev_hash)

    # Append (never modify existing entries)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    entries_count = len(path.read_text(encoding="utf-8").strip().splitlines())
    audit_head_path().write_text(
        json.dumps({"count": entries_count, "head": entry["hash"]}, sort_keys=True),
        encoding="utf-8",
    )
    return entry["hash"]


def verify_chain() -> tuple[bool, int]:
    """Verify the integrity of today's audit chain."""
    path = audit_log_path()
    if not path.exists():
        return True, 0

    entries = []
    for line in path.read_text().strip().split("\n"):
        if line.strip():
            entries.append(json.loads(line))

    if not entries:
        head = audit_head_path()
        if head.exists():
            try:
                anchor = json.loads(head.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return False, 0
            return anchor.get("count", 0) == 0, 0
        return True, 0

    prev_hash = GENESIS_HASH
    for i, entry in enumerate(entries):
        expected = compute_hash(entry, prev_hash)
        if entry.get("hash") != expected:
            return False, i  # Chain broken at entry i
        prev_hash = entry["hash"]

    head = audit_head_path()
    if head.exists():
        anchor = json.loads(head.read_text(encoding="utf-8"))
        if anchor.get("count") != len(entries) or anchor.get("head") != prev_hash:
            return False, len(entries)

    return True, len(entries)


def read_today_entries() -> list:
    """Read all entries from today's audit log."""
    path = audit_log_path()
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().strip().split("\n"):
        if line.strip():
            entries.append(json.loads(line))
    return entries


# ============================================================
# 2. Sandbox Configuration & Command Safety
# ============================================================

SANDBOX_CONFIG = {
    "allowed_paths": [os.getcwd(), "/tmp"],
    "blocked_paths": ["/etc", "/var", "/System", "/usr"],
    "blocked_commands": ["rm -rf /", "sudo ", "shutdown", "reboot", "mkfs", "dd if="],
    "high_risk_zones": ["Desktop", "Downloads", "Documents"],
    "destructive_commands": ["rm ", "rmdir", "mv ", "rename", "truncate", "> "],
    "safe_commands": ["ls", "cat", "head", "tail", "grep", "find", "echo", "pwd",
                      "wc", "sort", "uniq", "diff", "which", "file", "stat"],
    "max_batch_files": 10,
}


def classify_safety(command: str) -> str:
    """
    Classify command safety level.

    BLOCKED       — never execute (system-destructive)
    DESTRUCTIVE   — modifies or deletes files
    HIGH_RISK     — touches personal file zones
    CAUTION       — potentially modifying
    SAFE          — read-only
    UNKNOWN       — not classified
    """
    cmd = command.strip()

    # Check blocked commands first
    for blocked in SANDBOX_CONFIG["blocked_commands"]:
        if blocked in cmd:
            return "BLOCKED"

    # Check high-risk zones
    for zone in SANDBOX_CONFIG["high_risk_zones"]:
        if zone in cmd and ("rm" in cmd or "mv" in cmd or "delete" in cmd):
            return "HIGH_RISK"

    # Check destructive commands
    for destructive in SANDBOX_CONFIG["destructive_commands"]:
        if destructive in cmd:
            return "DESTRUCTIVE"

    # Check safe commands
    first_word = cmd.split()[0] if cmd.split() else ""
    if first_word in SANDBOX_CONFIG["safe_commands"]:
        return "SAFE"

    return "UNKNOWN"


def check_sandbox(command: str) -> tuple[bool, str]:
    """
    Check if a command passes sandbox rules.
    Returns (allowed, reason).
    """
    level = classify_safety(command)

    if level == "BLOCKED":
        return False, f"BLOCKED: Command matches blocked pattern"

    if level == "HIGH_RISK":
        return False, f"HIGH_RISK: Touches personal file zone — requires explicit consent"

    if level == "DESTRUCTIVE":
        # Destructive but not in high-risk zone
        # In real WorkBuddy, this would prompt the user
        return True, f"CAUTION: Destructive command — proceed with care"

    if level == "UNKNOWN":
        # Unknown commands are allowed but audited
        return True, f"UNKNOWN: Command not classified — executing with audit"

    return True, f"SAFE: Read-only command"


def run_with_sandbox(command: str, disable_sandbox: bool = False) -> str:
    """
    Execute a command with sandbox checks and audit logging.

    Production harness: dangerouslyDisableSandbox parameter requires
    explicit user consent in the UI.
    """
    # 1. Sandbox check (unless explicitly disabled)
    if not disable_sandbox:
        allowed, reason = check_sandbox(command)
        if not allowed:
            # Audit the block
            append_audit_entry("sandbox_block", {
                "command": command,
                "reason": reason,
            }, "blocked")
            return f"Error: {reason}"

    # 2. Audit the execution attempt
    append_audit_entry("command_execute", {
        "command": command,
        "safety": classify_safety(command),
        "sandbox_disabled": disable_sandbox,
    }, "started")

    # 3. Execute
    try:
        r = subprocess.run(command, shell=True, cwd=os.getcwd(),
                           capture_output=True, text=True, timeout=60)
        output = (r.stdout + r.stderr).strip()
        result = output[:50000] if output else "(no output)"

        # 4. Audit the result
        append_audit_entry("command_result", {
            "command": command,
            "exit_code": r.returncode,
        }, result[:500])

        return result
    except subprocess.TimeoutExpired:
        append_audit_entry("command_timeout", {"command": command}, "60s timeout")
        return "Error: Timeout (60s)"
    except Exception as e:
        append_audit_entry("command_error", {"command": command}, str(e))
        return f"Error: {e}"


# ============================================================
# 3. Agent with Audit + Sandbox
# ============================================================

SYSTEM = f"""You are a coding agent at {os.getcwd()}.
Use the bash tool to solve tasks. Act, don't over-explain.

Safety rules:
- Desktop, Downloads, Documents are HIGH-RISK zones
- Scan = read-only (report only, don't modify)
- Warn + confirm before destructive actions
- Use trash, not rm, when possible
- Max {SANDBOX_CONFIG['max_batch_files']} files per batch operation

Keep responses concise."""

TOOLS = [{
    "name": "bash",
    "description": "Run a shell command. Commands are sandboxed and audited.",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run."},
            "dangerouslyDisableSandbox": {
                "type": "boolean",
                "description": "Set to true ONLY when sandbox must be bypassed. Requires user consent.",
                "default": False,
            },
        },
        "required": ["command"],
    },
}]


def agent_loop(messages: list):
    """Agent loop with sandbox checks and audit logging."""
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return

        results = []
        for block in response.content:
            if block.type == "tool_use":
                command = block.input.get("command", "")
                disable = block.input.get("dangerouslyDisableSandbox", False)

                safety = classify_safety(command)
                color = {"BLOCKED": 31, "DESTRUCTIVE": 31, "HIGH_RISK": 31,
                         "CAUTION": 33, "SAFE": 32, "UNKNOWN": 33}.get(safety, 33)
                print(f"\033[{color}m  [{safety}] ${command}\033[0m")

                output = run_with_sandbox(command, disable_sandbox=disable)
                print(f"  {output[:200]}")

                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })

        messages.append({"role": "user", "content": results})


# ============================================================
# 4. Interactive REPL
# ============================================================

def print_audit():
    entries = read_today_entries()
    if not entries:
        print("  (今天暂无审计记录)")
        return
    print(f"\n  审计日志: {audit_log_path()}")
    print(f"  共 {len(entries)} 条记录\n")
    print(f"  {'#':>3} {'时间':<26} {'动作':<20} {'结果':<30}")
    print(f"  {'─'*3} {'─'*26} {'─'*20} {'─'*30}")
    for i, e in enumerate(entries):
        ts = e.get("timestamp", "")[:19]
        action = e.get("action", "")[:20]
        result = str(e.get("result", ""))[:30]
        print(f"  {i+1:>3} {ts:<26} {action:<20} {result:<30}")


def print_verify():
    valid, count = verify_chain()
    if valid:
        print(f"  \033[32m✓ 审计链完整\033[0m — {count} 条记录, 无篡改")
    else:
        print(f"  \033[31m✗ 审计链断裂\033[0m — 第 {count+1} 条记录被篡改!")


def print_chain_detail():
    """Show the hash chain in detail."""
    entries = read_today_entries()
    if not entries:
        print("  (今天暂无审计记录)")
        return
    prev = GENESIS_HASH
    for i, e in enumerate(entries):
        print(f"\n  Entry {i+1}:")
        print(f"    action: {e.get('action')}")
        print(f"    prev:   {prev[:32]}...")
        print(f"    hash:   {e.get('hash', '')[:32]}...")
        prev = e.get("hash", GENESIS_HASH)


if __name__ == "__main__":
    print("s23: Audit & Sandbox — 每步留痕, 不可篡改")
    print(f"审计目录: {AUDIT_DIR}")
    print(f"今日日志: {audit_log_path()}")
    print("命令: /audit | /verify | /chain | q 退出\n")

    history = []
    try:
        while True:
            try:
                query = input("\033[36ms23 >> \033[0m").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not query:
                continue
            if query.lower() in ("q", "exit", "quit"):
                break
            if query == "/audit":
                print_audit()
                continue
            if query == "/verify":
                print_verify()
                continue
            if query == "/chain":
                print_chain_detail()
                continue

            # Log user input
            append_audit_entry("user_input", {"query": query[:200]}, "received")

            history.append({"role": "user", "content": query})
            agent_loop(history)

            # Print final response
            last = history[-1]["content"]
            if isinstance(last, list):
                for block in last:
                    if getattr(block, 'type', None) == 'text':
                        print(block.text)

            # Log agent completion
            append_audit_entry("agent_response", {}, "completed")
            print()
    finally:
        # Final verification
        valid, count = verify_chain()
        print(f"\n审计链: {'✓ 完整' if valid else '✗ 断裂'} ({count} 条记录)")
        print(f"日志位置: {audit_log_path()}")
