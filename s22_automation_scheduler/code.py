#!/usr/bin/env python3
from __future__ import annotations
"""
s22_automation_scheduler.py - Automation Scheduler

Simulates WorkBuddy's automation scheduling system:
- Two schedule types: recurring (RRULE) and once (ISO 8601 datetime)
- RRULE parsing for next-run calculation (DAILY/HOURLY/WEEKLY/MONTHLY/YEARLY)
- Soft delete: status='deleted', NEVER DELETE FROM
- Runtime state tracking (last_run, next_run, last_status)
- Run history with output
- Scheduler loop that checks for due automations

Production harnesses often use: Sidecar process runs scheduler loop every 60s,
  automations table in SQLite, automation_update tool for CRUD.
Teaching version uses: Python scheduler with manual /run trigger,
  same SQLite schema, same soft-delete principle.

Usage:
    python s22_automation_scheduler/code.py
"""



# Machine-readable learning path metadata. Tests enforce that every
# chapter declares what it inherits and what it adds.
PROGRESSION = {'chapter': 's22_automation_scheduler',
 'builds_on': ['s21_sqlite_database'],
 'adds': ['RRULE scheduling', 'automation run history', 'runtime state table'],
 'preserves': ['SQLite persistence layer']}
import os, sys, time, json, sqlite3, uuid
from datetime import datetime, timedelta
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
# 1. Database (reuses s21 schema, focuses on automations)
# ============================================================

DB_DIR = Path(os.environ.get("WORKBUDDY_HOME", Path.home() / ".workbuddy"))
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "workbuddy.db"


def init_db():
    db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")

    db.executescript("""
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
        );
        CREATE TABLE IF NOT EXISTS automation_runtime_state (
            automation_id  TEXT PRIMARY KEY,
            last_run       TEXT,
            next_run       TEXT,
            last_status    TEXT
        );
        CREATE TABLE IF NOT EXISTS automation_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            automation_id   TEXT NOT NULL,
            started_at      TEXT NOT NULL,
            completed_at    TEXT,
            status          TEXT,
            output          TEXT
        );
    """)
    db.commit()
    return db


# ============================================================
# 2. RRULE Parser — RFC 5545 simplified
# ============================================================

def parse_rrule(rrule: str) -> dict:
    """Parse RRULE string into components."""
    parts = {}
    for p in rrule.upper().split(";"):
        if "=" in p:
            k, v = p.split("=", 1)
            parts[k] = v
    return parts


def calculate_next_run(rrule: str, after: datetime) -> str | None:
    """
    Calculate next run time from RRULE.

    Supports: FREQ=HOURLY|DAILY|WEEKLY|MONTHLY|YEARLY
    with INTERVAL and BYDAY (simplified).
    """
    if not rrule:
        return None

    parts = parse_rrule(rrule)
    freq = parts.get("FREQ", "DAILY")
    interval = int(parts.get("INTERVAL", "1"))
    byday = parts.get("BYDAY", "")

    if freq == "HOURLY":
        return (after + timedelta(hours=interval)).isoformat()
    elif freq == "DAILY":
        return (after + timedelta(days=interval)).isoformat()
    elif freq == "WEEKLY":
        if byday:
            # Simplified BYDAY: find next matching weekday
            day_map = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
            target_days = [day_map[d] for d in byday.split(",") if d in day_map]
            candidate = after + timedelta(days=1)
            while candidate.weekday() not in target_days:
                candidate += timedelta(days=1)
            return candidate.isoformat()
        return (after + timedelta(weeks=interval)).isoformat()
    elif freq == "MONTHLY":
        return (after + timedelta(days=30 * interval)).isoformat()
    elif freq == "YEARLY":
        return (after + timedelta(days=365 * interval)).isoformat()
    return (after + timedelta(days=1)).isoformat()


# ============================================================
# 3. Automation CRUD
# ============================================================

class AutomationManager:
    """
    Manages automation lifecycle.

    CRITICAL: Deletion is ALWAYS soft delete (status='deleted').
    NEVER use DELETE FROM or file operations to remove automations.
    """

    def __init__(self, db):
        self.db = db

    def create(self, name: str, prompt: str, schedule_type: str,
               rrule: str = None, scheduled_at: str = None,
               cwds: str = None, model_id: str = None) -> str:
        auto_id = f"auto_{uuid.uuid4().hex[:8]}"
        now = datetime.now().isoformat()

        self.db.execute(
            """INSERT INTO automations
               (id, name, prompt, schedule_type, rrule, scheduled_at,
                status, cwds, model_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?)""",
            (auto_id, name, prompt, schedule_type, rrule, scheduled_at,
             cwds, model_id, now, now)
        )

        # Initialize runtime state with next_run
        if schedule_type == "once":
            next_run = scheduled_at
        elif schedule_type == "recurring":
            next_run = calculate_next_run(rrule, datetime.now())
        else:
            next_run = None

        self.db.execute(
            "INSERT INTO automation_runtime_state (automation_id, next_run, last_status) "
            "VALUES (?, ?, NULL)",
            (auto_id, next_run)
        )
        self.db.commit()
        return auto_id

    def list_active(self):
        """List automations, excluding soft-deleted ones."""
        rows = self.db.execute(
            """SELECT a.id, a.name, a.schedule_type, a.rrule, a.scheduled_at,
                      a.status, r.next_run, r.last_run, r.last_status
               FROM automations a
               LEFT JOIN automation_runtime_state r ON a.id = r.automation_id
               WHERE a.status != 'deleted'
               ORDER BY a.updated_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def view(self, auto_id: str):
        row = self.db.execute(
            "SELECT * FROM automations WHERE id=? AND status != 'deleted'",
            (auto_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_status(self, auto_id: str, status: str):
        """Update status (ACTIVE/PAUSED). Not for deletion."""
        now = datetime.now().isoformat()
        self.db.execute(
            "UPDATE automations SET status=?, updated_at=? WHERE id=?",
            (status, now, auto_id)
        )
        if status == "PAUSED":
            self.db.execute(
                "UPDATE automation_runtime_state SET next_run=NULL WHERE automation_id=?",
                (auto_id,)
            )
        elif status == "ACTIVE":
            auto = self.view(auto_id)
            if auto and auto["schedule_type"] == "recurring":
                next_run = calculate_next_run(auto["rrule"], datetime.now())
                self.db.execute(
                    "UPDATE automation_runtime_state SET next_run=? WHERE automation_id=?",
                    (next_run, auto_id)
                )
        self.db.commit()

    def soft_delete(self, auto_id: str):
        """
        Soft delete: mark as 'deleted'.
        NEVER use: DELETE FROM automations WHERE id=?
        NEVER use: rm automation_file.json
        The row stays in the DB, hidden from list/view, can be restored.
        """
        now = datetime.now().isoformat()
        self.db.execute(
            "UPDATE automations SET status='deleted', updated_at=? WHERE id=?",
            (now, auto_id)
        )
        self.db.commit()
        print(f"  已软删除 {auto_id} (数据保留, 可恢复)")

    def get_run_history(self, auto_id: str):
        rows = self.db.execute(
            "SELECT id, started_at, completed_at, status, output "
            "FROM automation_runs WHERE automation_id=? ORDER BY started_at DESC LIMIT 10",
            (auto_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ============================================================
# 4. Scheduler — checks for due automations and executes them
# ============================================================

class Scheduler:
    """
    The scheduler loop. In real WorkBuddy, this runs in the Sidecar
    process every 60 seconds. Here we trigger it manually with /run.
    """

    def __init__(self, db, manager: AutomationManager):
        self.db = db
        self.manager = manager

    def check_and_run(self):
        """Check for due automations and execute them."""
        now = datetime.now()
        print(f"\n  调度器检查: {now.isoformat()}")

        # Find due automations
        rows = self.db.execute(
            """SELECT a.id, a.name, a.prompt, a.schedule_type, a.rrule,
                      a.scheduled_at, a.cwds, a.model_id, r.next_run
               FROM automations a
               LEFT JOIN automation_runtime_state r ON a.id = r.automation_id
               WHERE a.status = 'ACTIVE'
                 AND r.next_run IS NOT NULL
                 AND r.next_run <= ?""",
            (now.isoformat(),)
        ).fetchall()

        if not rows:
            print("  没有到期的自动化任务。")
            return 0

        count = 0
        for auto in rows:
            print(f"\n  执行: {auto['name']} ({auto['id']})")
            self._execute(auto)
            self._update_state_after_run(auto)
            count += 1

        print(f"\n  调度器完成: 执行了 {count} 个任务。")
        return count

    def _execute(self, auto):
        """Execute one automation in an isolated session."""
        started = datetime.now().isoformat()
        run_id = self.db.execute(
            "INSERT INTO automation_runs (automation_id, started_at, status) VALUES (?, ?, 'running')",
            (auto["id"], started)
        ).lastrowid
        self.db.commit()

        print(f"    开始: {started}")
        print(f"    Prompt: {auto['prompt'][:80]}...")

        # Build system prompt for automation (self-sufficient, no user interaction)
        system = (
            "You are an automated agent executing a scheduled task. "
            "The user may not be available. Complete the task autonomously. "
            "Do not ask questions — make reasonable assumptions and proceed."
        )

        messages = [{"role": "user", "content": auto["prompt"]}]

        try:
            response = client.messages.create(
                model=auto["model_id"] or MODEL,
                system=system,
                messages=messages,
                max_tokens=4000,
            )

            # Extract output text
            output_text = ""
            for block in response.content:
                if getattr(block, 'type', None) == 'text':
                    output_text += block.text

            completed = datetime.now().isoformat()
            self.db.execute(
                "UPDATE automation_runs SET completed_at=?, status='success', output=? WHERE id=?",
                (completed, output_text[:5000], run_id)
            )
            self.db.commit()
            print(f"    完成: {completed}")
            print(f"    输出: {output_text[:200]}...")

        except Exception as e:
            completed = datetime.now().isoformat()
            self.db.execute(
                "UPDATE automation_runs SET completed_at=?, status='failed', output=? WHERE id=?",
                (completed, str(e)[:5000], run_id)
            )
            self.db.commit()
            print(f"    失败: {e}")

    def _update_state_after_run(self, auto):
        """Update runtime state after execution."""
        now = datetime.now()

        if auto["schedule_type"] == "once":
            # One-time: no next run
            next_run = None
            # Also mark as completed (not ACTIVE anymore)
            self.db.execute(
                "UPDATE automations SET status='PAUSED', updated_at=? WHERE id=?",
                (now.isoformat(), auto["id"])
            )
        else:
            # Recurring: calculate next run
            next_run = calculate_next_run(auto["rrule"], now)

        self.db.execute(
            "UPDATE automation_runtime_state SET last_run=?, next_run=?, last_status='success' "
            "WHERE automation_id=?",
            (now.isoformat(), next_run, auto["id"])
        )
        self.db.commit()

        if next_run:
            print(f"    下次运行: {next_run}")
        else:
            print(f"    单次任务已完成, 已暂停。")


# ============================================================
# 5. Interactive REPL
# ============================================================

db = init_db()
manager = AutomationManager(db)
scheduler = Scheduler(db, manager)


def cmd_create():
    print("\n  创建自动化任务:")
    name = input("    名称: ").strip()
    if not name:
        print("  取消。")
        return
    prompt = input("    Prompt (任务描述): ").strip()
    if not prompt:
        print("  取消。")
        return

    print("    调度类型:")
    print("      1. recurring (重复)")
    print("      2. once (单次)")
    choice = input("    选择 [1/2]: ").strip()

    if choice == "2":
        scheduled = input("    运行时间 (YYYY-MM-DD HH:MM, 留空=立即): ").strip()
        if not scheduled:
            scheduled = datetime.now().isoformat()
        else:
            try:
                scheduled = datetime.strptime(scheduled, "%Y-%m-%d %H:%M").isoformat()
            except ValueError:
                print("  时间格式错误。")
                return
        auto_id = manager.create(name, prompt, "once", scheduled_at=scheduled, model_id=MODEL)
        print(f"\n  已创建: {auto_id} (单次, {scheduled})")
    else:
        print("    频率:")
        print("      1. 每小时")
        print("      2. 每天")
        print("      3. 每周")
        print("      4. 每月")
        fchoice = input("    选择 [1-4]: ").strip()
        rrules = {
            "1": "FREQ=HOURLY;INTERVAL=1",
            "2": "FREQ=DAILY;INTERVAL=1",
            "3": "FREQ=WEEKLY;INTERVAL=1",
            "4": "FREQ=MONTHLY;INTERVAL=1",
        }
        rrule = rrules.get(fchoice, "FREQ=DAILY;INTERVAL=1")
        auto_id = manager.create(name, prompt, "recurring", rrule=rrule, model_id=MODEL)
        next_run = calculate_next_run(rrule, datetime.now())
        print(f"\n  已创建: {auto_id} (重复, {rrule})")
        print(f"  下次运行: {next_run}")


def cmd_list():
    autos = manager.list_active()
    if not autos:
        print("  (暂无自动化任务)")
        return
    print(f"\n  {'ID':<16} {'名称':<20} {'类型':<10} {'状态':<8} {'下次运行':<26}")
    print(f"  {'─'*16} {'─'*20} {'─'*10} {'─'*8} {'─'*26}")
    for a in autos:
        print(f"  {a['id']:<16} {a['name'][:20]:<20} {a['schedule_type']:<10} "
              f"{a['status']:<8} {str(a['next_run'] or '-')[:19]:<26}")


def cmd_history(auto_id: str):
    history = manager.get_run_history(auto_id)
    if not history:
        print("  (暂无运行历史)")
        return
    for h in history:
        print(f"  Run #{h['id']} | {h['started_at'][:19]} | {h['status']}")
        if h['output']:
            print(f"    输出: {h['output'][:150]}...")


if __name__ == "__main__":
    print("s22: Automation Scheduler — 到点自动跑, 不需要人推")
    print(f"数据库: {DB_PATH}")
    print("命令: /create | /list | /run | /pause <id> | /resume <id> | /delete <id> | /history <id> | q 退出\n")

    try:
        while True:
            try:
                cmd = input("\033[36ms22 >> \033[0m").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not cmd:
                continue
            if cmd.lower() in ("q", "exit", "quit"):
                break
            if cmd == "/create":
                cmd_create()
            elif cmd == "/list":
                cmd_list()
            elif cmd == "/run":
                scheduler.check_and_run()
            elif cmd.startswith("/pause "):
                manager.update_status(cmd[7:].strip(), "PAUSED")
                print(f"  已暂停")
            elif cmd.startswith("/resume "):
                manager.update_status(cmd[8:].strip(), "ACTIVE")
                print(f"  已恢复")
            elif cmd.startswith("/delete "):
                manager.soft_delete(cmd[8:].strip())
            elif cmd.startswith("/history "):
                cmd_history(cmd[9:].strip())
            elif cmd.startswith("/view "):
                auto = manager.view(cmd[6:].strip())
                if auto:
                    print(json.dumps(auto, indent=2, ensure_ascii=False))
                else:
                    print("  未找到。")
            else:
                print("  未知命令。可用: /create /list /run /pause /resume /delete /history /view")
    finally:
        db.close()
        print("\n数据库已关闭。")
