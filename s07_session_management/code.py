#!/usr/bin/env python3
from __future__ import annotations
"""
s07_session_management.py - Session Management with PTY/Pipe backends

Simulates WorkBuddy's session management: each conversation is a separate
CLI child process communicating via ACP HTTP endpoints.

Production harnesses often use:
    - CLI runtime resources/ — CLI package with the agent bridge module
    - PTY backend (pty.open) for interactive commands — preserves colors/signals
    - Pipe backend (subprocess.PIPE) for non-interactive — lightweight
    - ACP HTTP endpoints: /agent/send, /agent/status, /agent/abort, /agent/messages
    - SQLite sessions table: id, cwd, title, status, mode, model, expert_id
    - Session modes: "craft" (act), "plan" (think first), "ask" (talk only)
    - Sidecar spawns/monitors/kills session processes

Teaching version uses:
    - threading to simulate separate processes (one thread per session)
    - http.server for ACP HTTP endpoints
    - Real Anthropic API calls in each session
    - In-memory session table (no SQLite)

Usage:
    python s07_session_management/code.py
"""



# Machine-readable learning path metadata. Tests enforce that every
# chapter declares what it inherits and what it adds.
PROGRESSION = {'chapter': 's07_session_management',
 'builds_on': ['s06_sidecar_server'],
 'adds': ['session lifecycle', 'ACP-like HTTP endpoints', 'PTY/pipe model'],
 'preserves': ['sidecar-managed runtime']}
import os, sys, time, json, threading, subprocess, signal
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

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


# ═══════════════════════════════════════════════════════════════
# Session States — teaching model of a desktop session lifecycle (analysis-notes abstraction, not source-derived)
# ═══════════════════════════════════════════════════════════════

STATE_CREATING  = "creating"
STATE_RUNNING   = "running"
STATE_IDLE      = "idle"
STATE_TERMINATED = "terminated"
STATE_ERROR     = "error"

# Session modes
MODE_CRAFT = "craft"   # Act immediately
MODE_PLAN  = "plan"    # Think first, then act
MODE_ASK   = "ask"     # Talk only, no tools


# ═══════════════════════════════════════════════════════════════
# ACP HTTP Handler — Agent Communication Protocol
# ═══════════════════════════════════════════════════════════════

class ACPRequestHandler(BaseHTTPRequestHandler):
    """
    Simulates the ACP HTTP server inside each CLI session process.

    Real WorkBuddy (agent bridge module) provides these endpoints:
        POST /agent/send     — send user message, get agent response
        GET  /agent/status   — query session status
        POST /agent/abort    — interrupt current execution
        GET  /agent/messages — get conversation history

    The HTTP server runs inside the CLI child process.
    Sidecar makes HTTP requests to communicate with the session.
    """

    def log_message(self, format, *args):
        pass  # Suppress default logging

    def do_GET(self):
        if self.path == "/agent/status":
            session = self.server.session
            self._json(200, {
                "sessionId": session.id,
                "status": session.status,
                "messages": len(session.messages),
                "mode": session.mode,
            })
        elif self.path == "/agent/messages":
            session = self.server.session
            self._json(200, {"messages": [
                {"role": m["role"], "content": str(m["content"])}
                for m in session.messages
            ]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/agent/send":
            session = self.server.session
            content_len = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(content_len))
            message = body.get("message", "")

            # Run agent loop (blocking — in real WorkBuddy this is async)
            session.status = STATE_RUNNING
            result = session.run_agent_loop(message)
            session.status = STATE_IDLE

            self._json(200, {"response": result})

        elif self.path == "/agent/abort":
            self.server.session.abort()
            self._json(200, {"status": "aborted"})
        else:
            self._json(404, {"error": "not found"})

    def _json(self, code, data):
        body = json.dumps(data).encode('utf-8')
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ═══════════════════════════════════════════════════════════════
# SessionProcess — one CLI child process per conversation
# ═══════════════════════════════════════════════════════════════

class SessionProcess:
    """
    Simulates a WorkBuddy CLI session process.

    Production harness:
        - Sidecar spawns: node cli-entry.js --mode craft --port <assigned>
        - CLI process starts ACP HTTP server on assigned port
        - Sidecar communicates via HTTP requests
        - Process has its own memory space, cwd, message history

    Teaching version:
        - Runs in a thread instead of separate process
        - Uses http.server for ACP endpoints
        - Runs real Anthropic API calls
    """

    _next_port = 13001

    def __init__(self, session_id: str, cwd: str, mode: str = MODE_CRAFT,
                 backend: str = "pipe"):
        self.id = session_id
        self.cwd = cwd
        self.mode = mode
        self.backend = backend  # "pty" or "pipe"
        self.status = STATE_CREATING
        self.model = os.environ.get("MODEL_ID", "")
        self.created_at = time.time()
        self.updated_at = time.time()
        self.messages: list[dict] = []
        self._aborted = False

        # Assign ACP port
        self.port = SessionProcess._next_port
        SessionProcess._next_port += 1

        # ACP HTTP server (runs inside the "CLI process")
        self._http_server = None
        self._http_thread = None

    def start(self):
        """
        Start the session process — simulates Sidecar spawning CLI.

        Production harness:
            PTY backend:  pty.open() → spawn with slave_fd
            Pipe backend: spawn with PIPE for stdin/stdout/stderr
        """
        # Start ACP HTTP server
        self._http_server = HTTPServer(("127.0.0.1", self.port), ACPRequestHandler)
        self._http_server.session = self  # type: ignore
        self._http_thread = threading.Thread(
            target=self._http_server.serve_forever, daemon=True)
        self._http_thread.start()

        self.status = STATE_IDLE
        self._log(f"session started on ACP port {self.port} (backend={self.backend})")

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"\033[90m  [{ts}] [session:{self.id}] {msg}\033[0m")

    def run_agent_loop(self, user_message: str) -> str:
        """
        Run the agent loop. In real WorkBuddy, this is the 生产级 agent bridge
        running inside the CLI process.

        Here we call the Anthropic API directly.
        """
        client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))

        # Mode affects system prompt
        if self.mode == MODE_ASK:
            system = f"You are a helpful assistant at {self.cwd}. Answer questions. Do NOT use tools."
            tools = []
        elif self.mode == MODE_PLAN:
            system = f"You are a coding agent at {self.cwd}. Plan first, then act. Be concise."
            tools = [self._bash_tool()]
        else:
            system = f"You are a coding agent at {self.cwd}. Act immediately. Be concise."
            tools = [self._bash_tool()]

        messages = self.messages + [{"role": "user", "content": user_message}]

        while True:
            if self._aborted:
                return "(aborted by user)"

            resp = client.messages.create(
                model=self.model, system=system, messages=messages,
                tools=tools if tools else None, max_tokens=8000)
            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use":
                break

            # Execute tool calls
            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    cmd = block.input.get("command", "")
                    self._log(f"tool_use: bash '{cmd[:60]}'")
                    out = self._execute_tool(cmd)
                    tool_results.append({"type": "tool_result",
                                         "tool_use_id": block.id, "content": out})
            messages.append({"role": "user", "content": tool_results})

        # Save conversation history
        self.messages = messages
        self.updated_at = time.time()

        # Extract final text
        final = ""
        for block in messages[-1]["content"]:
            if getattr(block, "type", None) == "text":
                final += block.text
        return final

    def _bash_tool(self):
        return {
            "name": "bash",
            "description": "Run a shell command.",
            "input_schema": {"type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"]},
        }

    def _execute_tool(self, command: str) -> str:
        """
        Execute a shell command. Backend choice simulates PTY vs Pipe.

        Production harness:
            PTY:  pty.openpty() → preserves ANSI colors, signals
            Pipe: subprocess.PIPE → plain text, no terminal features
        """
        dangerous = ["rm -rf /", "sudo", "shutdown", "reboot"]
        if any(d in command for d in dangerous):
            return "Error: Dangerous command blocked"

        try:
            if self.backend == "pty":
                # Simulate PTY — would use pty.openpty() in real code
                # PTY preserves colors, so we add a note
                r = subprocess.run(command, shell=True, cwd=self.cwd,
                                   capture_output=True, text=True, timeout=120)
                out = r.stdout + r.stderr
                # Simulate PTY color preservation
                if out.strip():
                    out = out.strip()  # Would have ANSI codes in real PTY
                return out[:50000] if out else "(no output)"
            else:
                # Pipe backend — plain text
                r = subprocess.run(command, shell=True, cwd=self.cwd,
                                   capture_output=True, text=True, timeout=120)
                out = (r.stdout + r.stderr).strip()
                return out[:50000] if out else "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: Timeout (120s)"
        except Exception as e:
            return f"Error: {e}"

    def abort(self):
        """Simulate SIGINT — abort current execution."""
        self._aborted = True
        self._log("abort requested")

    def terminate(self):
        """Terminate the session process."""
        self.status = STATE_TERMINATED
        if self._http_server:
            self._http_server.shutdown()
        self._log("session terminated")

    def info(self) -> dict:
        return {
            "id": self.id, "cwd": self.cwd, "status": self.status,
            "mode": self.mode, "backend": self.backend,
            "port": self.port, "messages": len(self.messages),
        }


# ═══════════════════════════════════════════════════════════════
# SessionManager — manages all session processes (Sidecar role)
# ═══════════════════════════════════════════════════════════════

class SessionManager:
    """
    Manages session lifecycle — equivalent to Sidecar's session management.

    Real WorkBuddy (sidecar-entry.js):
        - sessions Map: sessionId → { proc, port, status, ... }
        - create: spawn CLI process, wait for ACP port
        - destroy: HTTP abort → SIGTERM → SIGKILL
        - SQLite persistence: sessions table
    """

    def __init__(self):
        self.sessions: dict[str, SessionProcess] = {}
        self._counter = 0

    def create_session(self, cwd: str, mode: str = MODE_CRAFT,
                       backend: str = "pipe") -> str:
        """Create a new session process."""
        self._counter += 1
        sid = f"sess_{self._counter:04d}"

        session = SessionProcess(sid, cwd, mode, backend)
        session.start()
        self.sessions[sid] = session
        return sid

    def get_session(self, sid: str) -> SessionProcess | None:
        return self.sessions.get(sid)

    def list_sessions(self) -> list[dict]:
        return [s.info() for s in self.sessions.values()]

    def destroy_session(self, sid: str) -> bool:
        session = self.sessions.get(sid)
        if session:
            session.terminate()
            del self.sessions[sid]
            return True
        return False

    def shutdown_all(self):
        """Graceful shutdown — like Sidecar's shutdown sequence."""
        for sid, session in list(self.sessions.items()):
            session.terminate()
        self.sessions.clear()


# ═══════════════════════════════════════════════════════════════
# Entry Point — Interactive session manager
# ═══════════════════════════════════════════════════════════════

def main():
    print("╔═══════════════════════════════════════════════════════════╗")
    print("║  s07: Session Management — PTY/Pipe + ACP HTTP           ║")
    print("║  每个会话一个子进程, PTY 或管道                            ║")
    print("╚═══════════════════════════════════════════════════════════╝")
    print()

    mgr = SessionManager()

    # Create default session
    default_sid = mgr.create_session(str(WORKDIR), mode=MODE_CRAFT, backend="pipe")
    print(f"\033[90m[sidecar] Default session: {default_sid} (pipe backend)\033[0m\n")

    print("命令:")
    print("  /sessions          — 列出所有会话")
    print("  /new [craft|plan|ask] [pty|pipe] — 创建新会话")
    print("  /switch <id>       — 切换到指定会话")
    print("  /destroy <id>      — 销毁会话")
    print("  /mode <mode>       — 切换当前会话模式")
    print("  直接输入文字       — 给当前会话发消息")
    print("  q                  — 退出\n")

    current_sid = default_sid

    while True:
        session = mgr.get_session(current_sid)
        prompt_mode = session.mode if session else "??"
        try:
            query = input(f"\033[36m{s07}[{current_sid}:{prompt_mode}] >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if query.lower() in ("q", "exit", "quit"):
            break
        if not query:
            continue

        # Commands
        if query == "/sessions":
            print(f"\033[33m  {'ID':<12} {'Status':<12} {'Mode':<8} {'Backend':<8} {'Port':<8} {'Msgs':<6}{'CWD'}")
            print(f"  {'─'*12} {'─'*12} {'─'*8} {'─'*8} {'─'*8} {'─'*6}{'─'*20}")
            for s in mgr.list_sessions():
                marker = " ►" if s["id"] == current_sid else "  "
                print(f"{marker}{s['id']:<12} {s['status']:<12} {s['mode']:<8} "
                      f"{s['backend']:<8} {s['port']:<8} {s['messages']:<6}{s['cwd']}")
            print()
            continue

        if query.startswith("/new"):
            parts = query.split()
            mode = parts[1] if len(parts) > 1 and parts[1] in ("craft", "plan", "ask") else "craft"
            backend = parts[2] if len(parts) > 2 and parts[2] in ("pty", "pipe") else "pipe"
            sid = mgr.create_session(str(WORKDIR), mode=mode, backend=backend)
            current_sid = sid
            print(f"\033[90m[sidecar] New session: {sid} (mode={mode}, backend={backend})\033[0m\n")
            continue

        if query.startswith("/switch"):
            parts = query.split()
            if len(parts) > 1 and parts[1] in mgr.sessions:
                current_sid = parts[1]
                print(f"\033[90m[sidecar] Switched to {current_sid}\033[0m\n")
            else:
                print(f"\033[31mSession not found. Use /sessions to list.\033[0m\n")
            continue

        if query.startswith("/destroy"):
            parts = query.split()
            target = parts[1] if len(parts) > 1 else current_sid
            if mgr.destroy_session(target):
                print(f"\033[90m[sidecar] Session {target} destroyed\033[0m")
                if current_sid == target and mgr.sessions:
                    current_sid = list(mgr.sessions.keys())[0]
                    print(f"\033[90m[sidecar] Switched to {current_sid}\033[0m")
                elif not mgr.sessions:
                    current_sid = mgr.create_session(str(WORKDIR))
                    print(f"\033[90m[sidecar] Auto-created new session: {current_sid}\033[0m")
            else:
                print(f"\033[31mSession not found.\033[0m")
            print()
            continue

        if query.startswith("/mode"):
            parts = query.split()
            if len(parts) > 1 and parts[1] in ("craft", "plan", "ask") and session:
                session.mode = parts[1]
                print(f"\033[90m[sidecar] Mode → {parts[1]}\033[0m\n")
            else:
                print(f"\033[31mUsage: /mode [craft|plan|ask]\033[0m\n")
            continue

        # Send message to current session
        if not session:
            print("\033[31mNo active session. Use /new to create one.\033[0m\n")
            continue

        session._aborted = False
        session.status = STATE_RUNNING
        print(f"\033[90m[sidecar] → ACP POST http://localhost:{session.port}/agent/send\033[0m")

        result = session.run_agent_loop(query)

        print(f"\033[32m{result}\033[0m\n")

    # Cleanup
    mgr.shutdown_all()
    print("\n\033[90m[sidecar] All sessions terminated. Goodbye.\033[0m")


if __name__ == "__main__":
    main()
