#!/usr/bin/env python3
from __future__ import annotations
"""
s21_sqlite_database.py - SQLite Database Layer

Simulates WorkBuddy's SQLite persistence layer:
- WAL mode for concurrent read/write
- 7 tables: sessions, messages, automations, automation_runtime_state,
            automation_runs, tool_usage, usage_tracking
- Session CRUD with soft delete
- Usage tracking (tokens + cost) per API call
- Tool usage statistics

Production harnesses often use: better-sqlite3 (sync) in the Sidecar process,
  ~/.workbuddy/workbuddy.db with WAL mode.
Teaching version uses: Python sqlite3 stdlib, same WAL mode, same schema.

Usage:
    python s21_sqlite_database/code.py
"""



# Machine-readable learning path metadata. Tests enforce that every
# chapter declares what it inherits and what it adds.
PROGRESSION = {'chapter': 's21_sqlite_database',
 'builds_on': ['s20_result_presentation'],
 'adds': ['SQLite WAL database', 'session metadata', 'usage tracking'],
 'preserves': ['deliverable and session persistence']}
import os, sys, time, json, sqlite3
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
# 1. Database Layer — WAL mode, 7 tables
# ============================================================

DB_DIR = Path(os.environ.get("WORKBUDDY_HOME", Path.home() / ".workbuddy"))
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "workbuddy.db"


class WorkBuddyDB:
    """
    SQLite database with WAL mode.

    Production harness: better-sqlite3 in sidecar-entry.js, synchronous calls.
    Teaching version: Python sqlite3, same PRAGMA settings.
    """

    def __init__(self, path: Path):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_pragmas()
        self._init_tables()

    def _init_pragmas(self):
        """Enable WAL mode for concurrent read/write."""
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        # WAL creates -wal and -shm files alongside the main .db file

    def _init_tables(self):
        """Create all 7 tables if they don't exist."""

        # Table 1: sessions — one row per conversation
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                cwd         TEXT NOT NULL,
                title       TEXT DEFAULT 'New Session',
                status      TEXT DEFAULT 'active',
                mode        TEXT DEFAULT 'code',
                model       TEXT,
                expert_id   TEXT,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """)

        # Table 2: messages — all messages in all sessions
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                role        TEXT NOT NULL,
                content     TEXT,
                tool_calls  TEXT,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            )
        """)

        # Table 3: automations — scheduled task definitions (s22)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS automations (
                id              TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                prompt          TEXT NOT NULL,
                schedule_type   TEXT NOT NULL,
                rrule           TEXT,
                scheduled_at    TEXT,
                status          TEXT DEFAULT 'ACTIVE',
                valid_from      TEXT,
                valid_until     TEXT,
                cwds            TEXT,
                expert_id       TEXT,
                model_id        TEXT,
                connector_ids   TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
        """)

        # Table 4: automation_runtime_state — scheduler tracking
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS automation_runtime_state (
                automation_id  TEXT PRIMARY KEY,
                last_run       TEXT,
                next_run       TEXT,
                last_status    TEXT,
                FOREIGN KEY (automation_id) REFERENCES automations(id)
            )
        """)

        # Table 5: automation_runs — execution history
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS automation_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                automation_id   TEXT NOT NULL,
                started_at      TEXT NOT NULL,
                completed_at    TEXT,
                status          TEXT,
                output          TEXT,
                FOREIGN KEY (automation_id) REFERENCES automations(id)
            )
        """)

        # Table 6: tool_usage — per-tool statistics
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS tool_usage (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                tool_name   TEXT NOT NULL,
                call_count  INTEGER DEFAULT 0,
                token_usage INTEGER DEFAULT 0,
                updated_at  TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            )
        """)

        # Table 7: usage_tracking — token + cost per API call
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS usage_tracking (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id              TEXT NOT NULL,
                model                   TEXT NOT NULL,
                input_tokens            INTEGER DEFAULT 0,
                output_tokens           INTEGER DEFAULT 0,
                cache_creation_tokens   INTEGER DEFAULT 0,
                cache_read_tokens       INTEGER DEFAULT 0,
                cost                    REAL DEFAULT 0.0,
                created_at              TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            )
        """)

        self.db.commit()

    # -- Session CRUD --

    def create_session(self, cwd: str, model: str) -> str:
        sid = f"sess_{int(time.time() * 1000)}"
        now = datetime.now().isoformat()
        self.db.execute(
            "INSERT INTO sessions (id, cwd, title, status, mode, model, created_at, updated_at) "
            "VALUES (?, ?, 'New Session', 'active', 'code', ?, ?, ?)",
            (sid, cwd, model, now, now)
        )
        self.db.commit()
        return sid

    def update_session_title(self, sid: str, title: str):
        now = datetime.now().isoformat()
        self.db.execute(
            "UPDATE sessions SET title=?, updated_at=? WHERE id=?",
            (title, now, sid)
        )
        self.db.commit()

    def list_sessions(self, status: str = "active"):
        rows = self.db.execute(
            "SELECT id, title, cwd, model, updated_at FROM sessions "
            "WHERE status=? ORDER BY updated_at DESC",
            (status,)
        ).fetchall()
        return [dict(r) for r in rows]

    def archive_session(self, sid: str):
        """Soft delete: mark as archived, never DELETE FROM."""
        now = datetime.now().isoformat()
        self.db.execute(
            "UPDATE sessions SET status='archived', updated_at=? WHERE id=?",
            (now, sid)
        )
        self.db.commit()

    # -- Message persistence --

    def save_message(self, sid: str, role: str, content: str, tool_calls: str = None):
        now = datetime.now().isoformat()
        self.db.execute(
            "INSERT INTO messages (session_id, role, content, tool_calls, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, role, content, tool_calls, now)
        )
        self.db.commit()

    def get_messages(self, sid: str):
        rows = self.db.execute(
            "SELECT role, content FROM messages WHERE session_id=? ORDER BY id",
            (sid,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Usage tracking --

    def track_usage(self, sid: str, model: str, usage_obj):
        """Record token usage and cost after each API call."""
        cost = self._calc_cost(model, usage_obj)
        now = datetime.now().isoformat()
        self.db.execute(
            "INSERT INTO usage_tracking "
            "(session_id, model, input_tokens, output_tokens, "
            " cache_creation_tokens, cache_read_tokens, cost, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (sid, model,
             getattr(usage_obj, 'input_tokens', 0),
             getattr(usage_obj, 'output_tokens', 0),
             getattr(usage_obj, 'cache_creation_input_tokens', 0),
             getattr(usage_obj, 'cache_read_input_tokens', 0),
             cost, now)
        )
        self.db.commit()

    def _calc_cost(self, model: str, usage) -> float:
        """Simplified cost calculation (per 1M tokens)."""
        rates = {
            "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
            "claude-opus-4-20250514": {"input": 15.0, "output": 75.0},
        }
        rate = rates.get(model, {"input": 3.0, "output": 15.0})
        inp = getattr(usage, 'input_tokens', 0)
        out = getattr(usage, 'output_tokens', 0)
        return (inp / 1_000_000 * rate["input"]) + (out / 1_000_000 * rate["output"])

    def get_usage_stats(self, sid: str = None):
        if sid:
            rows = self.db.execute(
                "SELECT model, SUM(input_tokens) as inp, SUM(output_tokens) as out, "
                "SUM(cost) as cost, COUNT(*) as calls "
                "FROM usage_tracking WHERE session_id=? GROUP BY model",
                (sid,)
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT model, SUM(input_tokens) as inp, SUM(output_tokens) as out, "
                "SUM(cost) as cost, COUNT(*) as calls "
                "FROM usage_tracking GROUP BY model"
            ).fetchall()
        return [dict(r) for r in rows]

    # -- Tool usage tracking --

    def record_tool_call(self, sid: str, tool_name: str):
        now = datetime.now().isoformat()
        existing = self.db.execute(
            "SELECT id, call_count FROM tool_usage WHERE session_id=? AND tool_name=?",
            (sid, tool_name)
        ).fetchone()
        if existing:
            self.db.execute(
                "UPDATE tool_usage SET call_count=?, updated_at=? WHERE id=?",
                (existing["call_count"] + 1, now, existing["id"])
            )
        else:
            self.db.execute(
                "INSERT INTO tool_usage (session_id, tool_name, call_count, token_usage, updated_at) "
                "VALUES (?, ?, 1, 0, ?)",
                (sid, tool_name, now)
            )
        self.db.commit()

    def get_tool_stats(self, sid: str):
        rows = self.db.execute(
            "SELECT tool_name, call_count FROM tool_usage WHERE session_id=? ORDER BY call_count DESC",
            (sid,)
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self.db.close()


# ============================================================
# 2. Agent with DB integration
# ============================================================

db = WorkBuddyDB(DB_PATH)

SYSTEM = f"""You are a coding agent at {os.getcwd()}.
Use the bash tool to solve tasks. Act, don't over-explain.
Keep responses concise."""

TOOLS = [{
    "name": "bash",
    "description": "Run a shell command.",
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
}]

DANGEROUS = ["rm -rf /", "sudo ", "shutdown", "reboot", "mkfs"]


def run_bash(command: str) -> str:
    if any(d in command for d in DANGEROUS):
        return "Error: Dangerous command blocked."
    try:
        import subprocess
        r = subprocess.run(command, shell=True, cwd=os.getcwd(),
                           capture_output=True, text=True, timeout=60)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (60s)"
    except Exception as e:
        return f"Error: {e}"


def agent_loop(messages: list, session_id: str):
    """Agent loop with full DB integration."""
    total_cost = 0.0

    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )

        # Track usage after each API call
        db.track_usage(session_id, MODEL, response.usage)
        cost = db._calc_cost(MODEL, response.usage)
        total_cost += cost

        # Save assistant message to DB
        text_parts = []
        for block in response.content:
            if getattr(block, 'type', None) == 'text':
                text_parts.append(block.text)
        db.save_message(session_id, "assistant", "\n".join(text_parts))

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return total_cost

        results = []
        for block in response.content:
            if block.type == "tool_use":
                # Record tool call in DB
                db.record_tool_call(session_id, block.name)

                print(f"\033[33m  $ {block.input.get('command', block.name)}\033[0m")
                if block.name == "bash":
                    output = run_bash(block.input["command"])
                else:
                    output = f"Unknown tool: {block.name}"
                print(f"  {output[:200]}")
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })

        messages.append({"role": "user", "content": results})


# ============================================================
# 3. Entry point — REPL with DB commands
# ============================================================

def print_stats():
    stats = db.get_usage_stats()
    if not stats:
        print("  (暂无用量数据)")
        return
    print(f"  {'Model':<35} {'Input':>8} {'Output':>8} {'Cost':>8} {'Calls':>6}")
    print(f"  {'─'*35} {'─'*8} {'─'*8} {'─'*8} {'─'*6}")
    for s in stats:
        print(f"  {s['model']:<35} {s['inp']:>8} {s['out']:>8} ${s['cost']:>7.4f} {s['calls']:>6}")


def print_sessions():
    sessions = db.list_sessions()
    if not sessions:
        print("  (暂无会话)")
        return
    for s in sessions:
        print(f"  {s['id'][:20]}  {s['title'][:30]:<30}  {s['updated_at'][:19]}")


def print_tool_stats(session_id: str):
    tools = db.get_tool_stats(session_id)
    if not tools:
        print("  (暂无工具调用)")
        return
    for t in tools:
        print(f"  {t['tool_name']:<20} {t['call_count']:>5} calls")


if __name__ == "__main__":
    print("s21: SQLite Database — 会话要持久, 用量要追踪")
    print(f"数据库: {DB_PATH}")
    print(f"WAL 文件: {DB_PATH}-wal")
    print("命令: /stats | /sessions | /tools | /archive | q 退出\n")

    session_id = db.create_session(os.getcwd(), MODEL)
    print(f"会话已创建: {session_id}\n")

    history = []
    try:
        while True:
            try:
                query = input("\033[36ms21 >> \033[0m").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not query:
                continue
            if query.lower() in ("q", "exit", "quit"):
                break
            if query == "/stats":
                print_stats()
                continue
            if query == "/sessions":
                print_sessions()
                continue
            if query == "/tools":
                print_tool_stats(session_id)
                continue
            if query.startswith("/title "):
                db.update_session_title(session_id, query[7:])
                print(f"  标题已更新")
                continue

            # Save user message to DB
            db.save_message(session_id, "user", query)
            history.append({"role": "user", "content": query})

            cost = agent_loop(history, session_id)

            # Print final response
            last = history[-1]["content"]
            if isinstance(last, list):
                for block in last:
                    if getattr(block, 'type', None) == 'text':
                        print(block.text)
            print(f"\n\033[90m  [本轮成本: ${cost:.4f}]\033[0m\n")

            # Auto-set title from first query
            if session_id and len(history) == 2:
                title = query[:50] + ("..." if len(query) > 50 else "")
                db.update_session_title(session_id, title)

    finally:
        db.close()
        print(f"\n数据库已关闭。数据保存在 {DB_PATH}")
        print(f"用 sqlite3 {DB_PATH} 查看数据。")
