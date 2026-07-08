#!/usr/bin/env python3
from __future__ import annotations
"""
s06_sidecar_server.py - Sidecar Server Architecture

Simulates WorkBuddy's Sidecar process: a JSON-RPC server over Unix Socket
that manages agent sessions, captures logs in a RingBuffer, and routes
RPC calls across many domains (teaching-scale sample).

Production harnesses often use:
    - sidecar runtime: SidecarServer class, net.createServer() on Unix Socket
    - bounded RingBuffer for stdout/stderr capture (circular buffer)
    - Newline-delimited JSON-RPC protocol
    - Domain-based RPC routing: session/*, sidecar/*, tool/*, memory/*, mcp/*, skill/*
    - PTY backend for interactive commands, pipe for non-interactive
    - Main Process spawns Sidecar as child process, connects via socket

Teaching version uses:
    - Python socketpair to simulate Unix Domain Socket
    - threading instead of separate processes (simpler, same concept)
    - In-memory RingBuffer with the same circular logic
    - Real Anthropic API calls to demonstrate agent execution via RPC

Usage:
    python s06_sidecar_server/code.py
"""



# Machine-readable learning path metadata. Tests enforce that every
# chapter declares what it inherits and what it adds.
PROGRESSION = {'chapter': 's06_sidecar_server',
 'builds_on': ['s05_electron_shell'],
 'adds': ['sidecar control plane', 'JSON-RPC routing', 'ring buffer logs'],
 'preserves': ['desktop process boundary']}

# Shared learning entrypoints: --demo is offline; --provider deepseek configures real API env.
import sys as _wb_sys
from pathlib import Path as _wb_Path
_WB_ROOT = _wb_Path(__file__).resolve().parents[1]
if str(_WB_ROOT) not in _wb_sys.path:
    _wb_sys.path.insert(0, str(_WB_ROOT))
from mini_workbuddy.chapter_demo import maybe_run_chapter_demo as _wb_maybe_run_chapter_demo
_wb_maybe_run_chapter_demo(__file__, PROGRESSION)
from mini_workbuddy.chapter_demo import prepare_chapter_provider as _wb_prepare_chapter_provider
_wb_prepare_chapter_provider()
import os, sys, time, json, socket, threading, struct
from pathlib import Path
from collections import deque

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
DEMO_RING_BUFFER_SIZE = 1024 * 1024


# ═══════════════════════════════════════════════════════════════
# RingBuffer — 固定大小 circular buffer for log capture
# ═══════════════════════════════════════════════════════════════

class RingBuffer:
    """
    Circular buffer that mimics WorkBuddy's bounded RingBuffer in sidecar runtime.

    In WorkBuddy: captures all child process stdout/stderr.
    When full, oldest data is overwritten by new data.
    This ensures bounded memory usage for long-running sessions.

    Production-style implementation:
        class RingBuffer {
            constructor(size = fixedLimit) {
                this.buffer = Buffer.alloc(size);
                this.writePos = 0;
                this.totalWritten = 0;
            }
        }
    """

    def __init__(self, size: int = DEMO_RING_BUFFER_SIZE):
        self.size = size
        self.buffer = bytearray(size)
        self.write_pos = 0
        self.total_written = 0
        self._lock = threading.Lock()

    def write(self, data: bytes | str):
        """Write data into the ring buffer, overwriting old data if full."""
        if isinstance(data, str):
            data = data.encode('utf-8')
        with self._lock:
            for byte in data:
                self.buffer[self.write_pos] = byte
                self.write_pos = (self.write_pos + 1) % self.size
                self.total_written += 1

    def read_all(self) -> str:
        """Read all valid data from the buffer (in order: oldest to newest)."""
        with self._lock:
            if self.total_written < self.size:
                # Buffer not yet full — read from start
                return self.buffer[:self.write_pos].decode('utf-8', errors='replace')
            else:
                # Buffer full — read from write_pos (oldest) around to write_pos (newest)
                return (
                    self.buffer[self.write_pos:].decode('utf-8', errors='replace') +
                    self.buffer[:self.write_pos].decode('utf-8', errors='replace')
                )

    @property
    def used(self) -> int:
        """How many bytes are currently valid."""
        return min(self.total_written, self.size)

    @property
    def is_full(self) -> bool:
        return self.total_written >= self.size


# ═══════════════════════════════════════════════════════════════
# JSON-RPC Protocol — newline-delimited JSON over socket
# ═══════════════════════════════════════════════════════════════

class RPCConnection:
    """
    Handles newline-delimited JSON-RPC framing over a socket.

    Real WorkBuddy uses the same pattern:
        - Each message is a JSON object followed by \n
        - Requests have {jsonrpc, method, params, id}
        - Responses have {jsonrpc, result, id} or {jsonrpc, error, id}

    This is simpler than length-prefixed framing and works well with
    text-based debugging.
    """

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self._recv_buffer = b""

    def send_message(self, msg: dict):
        """Send a JSON-RPC message (newline-delimited)."""
        data = (json.dumps(msg) + "\n").encode('utf-8')
        self.sock.sendall(data)

    def recv_message(self) -> dict | None:
        """Receive a JSON-RPC message. Returns None on disconnect."""
        while b"\n" not in self._recv_buffer:
            chunk = self.sock.recv(65536)
            if not chunk:
                return None
            self._recv_buffer += chunk
        line, self._recv_buffer = self._recv_buffer.split(b"\n", 1)
        return json.loads(line.decode('utf-8'))

    def close(self):
        self.sock.close()


# ═══════════════════════════════════════════════════════════════
# SidecarServer — RPC server with domain-based routing
# ═══════════════════════════════════════════════════════════════

class SidecarServer:
    """
    Simulates WorkBuddy's SidecarServer from sidecar runtime.

    Responsibilities:
    1. Listen on Unix Socket for RPC connections
    2. Route RPC calls to registered handlers, grouped by domain
    3. Capture all subprocess output in RingBuffer
    4. Manage session processes (spawn, monitor, kill)

    Real WorkBuddy has these RPC domains:
        session/*, sidecar/*, tool/*, memory/*, mcp/*, skill/*,
        automation/*, expert/*, connector/*, permission/*, ...
    """

    def __init__(self):
        self.ring_buffer = RingBuffer(size=DEMO_RING_BUFFER_SIZE)  # 固定大小
        self.sessions: dict[str, dict] = {}
        self.rpc_handlers: dict[str, callable] = {}
        self._rpc_id_counter = 0
        self._lock = threading.Lock()
        self._register_channels()

    def _register_channels(self):
        """Register RPC handlers — a teaching-scale sample of the domain-routing pattern."""
        # sidecar/* — self-management
        self.rpc_handlers["sidecar/ping"] = self._handle_ping
        self.rpc_handlers["sidecar/status"] = self._handle_status
        self.rpc_handlers["sidecar/shutdown"] = self._handle_shutdown

        # session/* — session lifecycle (s07 covers this in detail)
        self.rpc_handlers["session/create"] = self._handle_session_create
        self.rpc_handlers["session/list"] = self._handle_session_list
        self.rpc_handlers["session/destroy"] = self._handle_session_destroy

        # agent/* — agent execution
        self.rpc_handlers["agent/send"] = self._handle_agent_send

        # tool/* — tool management
        self.rpc_handlers["tool/list"] = self._handle_tool_list

        # memory/* — memory system (s10, s11)
        self.rpc_handlers["memory/getProfile"] = self._handle_memory_get

        self._log(f"Registered {len(self.rpc_handlers)} RPC handlers")

    def _log(self, msg: str):
        """Write to RingBuffer (simulating stdout/stderr capture)."""
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] [sidecar] {msg}"
        self.ring_buffer.write(line + "\n")

    # ── RPC Handlers ──────────────────────────────────────────

    def _handle_ping(self, params: dict) -> dict:
        self._log("ping received")
        return {"status": "ok", "uptime": time.time() - self._start_time}

    def _handle_status(self, params: dict) -> dict:
        return {
            "sessions": len(self.sessions),
            "ringBufferUsed": self.ring_buffer.used,
            "ringBufferTotal": self.ring_buffer.size,
            "ringBufferFull": self.ring_buffer.is_full,
            "handlers": len(self.rpc_handlers),
        }

    def _handle_session_create(self, params: dict) -> dict:
        sid = f"sess_{int(time.time()*1000)}"
        cwd = params.get("cwd", str(WORKDIR))
        mode = params.get("mode", "craft")
        model = params.get("model", os.environ.get("MODEL_ID", ""))
        self.sessions[sid] = {
            "id": sid, "cwd": cwd, "mode": mode, "model": model,
            "status": "running", "created_at": time.time(),
            "messages": [],
        }
        self._log(f"session created: {sid} (cwd={cwd}, mode={mode})")
        return {"sessionId": sid}

    def _handle_session_list(self, params: dict) -> dict:
        return {"sessions": [
            {"id": s["id"], "cwd": s["cwd"], "status": s["status"]}
            for s in self.sessions.values()
        ]}

    def _handle_session_destroy(self, params: dict) -> dict:
        sid = params.get("sessionId", "")
        if sid in self.sessions:
            del self.sessions[sid]
            self._log(f"session destroyed: {sid}")
            return {"status": "ok"}
        return {"error": "session not found"}

    def _handle_tool_list(self, params: dict) -> dict:
        return {"tools": [
            {"name": "bash", "description": "Run a shell command"},
            {"name": "read_file", "description": "Read file contents"},
            {"name": "write_file", "description": "Write file contents"},
        ]}

    def _handle_memory_get(self, params: dict) -> dict:
        return {"profile": "user-level memory (s11)", "workspace": "workspace memory (s10)"}

    def _handle_agent_send(self, params: dict) -> dict:
        """Execute agent loop — the core RPC that runs the LLM."""
        sid = params.get("sessionId", "")
        text = params.get("message", "")
        if sid not in self.sessions:
            return {"error": "session not found"}

        session = self.sessions[sid]
        self._log(f"agent/send to {sid}: {text[:80]}...")

        # Run agent loop (simplified; production apps place this behind an agent bridge)
        result = self._run_agent_loop(session, text)
        return {"response": result}

    # ── Agent Loop (delegated to Anthropic API) ───────────────

    def _run_agent_loop(self, session: dict, user_text: str) -> str:
        """
        The actual agent loop. In WorkBuddy, this runs in a separate
        CLI session process (s07). Here we run it inline for simplicity.

        Production harness: Sidecar spawns a CLI process per session,
        communicates via ACP HTTP endpoints.
        """
        client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
        MODEL = os.environ.get("MODEL_ID")
        if not MODEL:
            raise SystemExit(
                "MODEL_ID is not set. Copy .env.example to .env and fill in "
                "ANTHROPIC_API_KEY and MODEL_ID (see README quick start)."
            )

        system = f"You are a coding agent at {session['cwd']}. Be concise."
        tools = [{
            "name": "bash",
            "description": "Run a shell command.",
            "input_schema": {"type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"]},
        }]

        messages = session["messages"] + [{"role": "user", "content": user_text}]

        import subprocess
        while True:
            resp = client.messages.create(
                model=MODEL, system=system, messages=messages,
                tools=tools, max_tokens=8000)
            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use":
                break

            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    cmd = block.input.get("command", "")
                    self._log(f"tool_use: bash '{cmd[:60]}'")
                    try:
                        r = subprocess.run(cmd, shell=True, cwd=session["cwd"],
                                           capture_output=True, text=True, timeout=120)
                        out = (r.stdout + r.stderr).strip()[:50000]
                    except Exception as e:
                        out = f"Error: {e}"
                    tool_results.append({"type": "tool_result",
                                         "tool_use_id": block.id, "content": out})
            messages.append({"role": "user", "content": tool_results})

        # Save conversation history
        session["messages"] = messages
        session["status"] = "idle"

        # Extract final text
        final = ""
        for block in messages[-1]["content"]:
            if getattr(block, "type", None) == "text":
                final += block.text
        return final

    # ── RPC Server Loop ───────────────────────────────────────

    def handle_connection(self, conn: RPCConnection):
        """Handle a single RPC connection — process messages until disconnect."""
        self._log("new RPC connection established")
        while True:
            try:
                req = conn.recv_message()
            except Exception:
                break
            if req is None:
                break

            method = req.get("method", "")
            params = req.get("params", {})
            req_id = req.get("id")

            self._log(f"RPC: {method} (id={req_id})")

            handler = self.rpc_handlers.get(method)
            if handler:
                try:
                    result = handler(params)
                    conn.send_message({"jsonrpc": "2.0", "result": result, "id": req_id})
                except Exception as e:
                    self._log(f"RPC error: {method}: {e}")
                    conn.send_message({"jsonrpc": "2.0",
                                       "error": {"code": -32603, "message": str(e)}, "id": req_id})
            else:
                conn.send_message({"jsonrpc": "2.0",
                                   "error": {"code": -32601, "message": f"Method not found: {method}"},
                                   "id": req_id})
        conn.close()
        self._log("RPC connection closed")

    _start_time = time.time()

    def _handle_shutdown(self, params: dict) -> dict:
        self._shutdown = True
        self._log("shutdown requested")
        return {"status": "shutting down"}


# ═══════════════════════════════════════════════════════════════
# Main Process — connects to Sidecar via Unix Socket
# ═══════════════════════════════════════════════════════════════

class MainProcessClient:
    """
    Simulates Electron Main Process connecting to Sidecar.

    Real WorkBuddy (index.js):
        const client = net.createConnection(socketPath);
        client.write(JSON.stringify({method: 'session/create', ...}) + '\n');
    """

    def __init__(self):
        self.conn = None
        self._rpc_id = 0

    def connect(self, sidecar: SidecarServer):
        """Connect to Sidecar via socketpair (simulating Unix Socket)."""
        # Create a socket pair — simulates Unix Domain Socket
        server_sock, client_sock = socket.socketpair()

        # Start Sidecar connection handler in background thread
        server_conn = RPCConnection(server_sock)
        threading.Thread(
            target=sidecar.handle_connection,
            args=(server_conn,),
            daemon=True
        ).start()

        # Small delay to let handler register
        time.sleep(0.05)

        self.conn = RPCConnection(client_sock)

    def call(self, method: str, params: dict = None) -> dict:
        """Make an RPC call and wait for response."""
        self._rpc_id += 1
        req = {"jsonrpc": "2.0", "method": method,
               "params": params or {}, "id": self._rpc_id}
        self.conn.send_message(req)
        resp = self.conn.recv_message()
        return resp

    def close(self):
        if self.conn:
            self.conn.close()


# ═══════════════════════════════════════════════════════════════
# Entry Point — Main ↔ Sidecar ↔ Agent
# ═══════════════════════════════════════════════════════════════

def main():
    print("╔═══════════════════════════════════════════════════════════╗")
    print("║  s06: Sidecar Server — JSON-RPC over Unix Socket        ║")
    print("║  主进程不跑 agent, Sidecar 来跑                            ║")
    print("╚═══════════════════════════════════════════════════════════╝")
    print()

    # 1. Start Sidecar (simulating child process spawn)
    sidecar = SidecarServer()
    print(f"\033[90m[main] Sidecar started, {len(sidecar.rpc_handlers)} RPC handlers registered\033[0m")

    # 2. Main Process connects to Sidecar
    client = MainProcessClient()
    client.connect(sidecar)
    print(f"\033[90m[main] Connected to Sidecar via Unix Socket (socketpair)\033[0m\n")

    # 3. Health check
    pong = client.call("sidecar/ping")
    print(f"\033[90m[main] sidecar/ping → {pong['result']}\033[0m")

    # 4. Create a session
    session = client.call("session/create", {"cwd": str(WORKDIR), "mode": "craft"})
    sid = session["result"]["sessionId"]
    print(f"\033[90m[main] session/create → {sid}\033[0m\n")

    # 5. Interactive loop
    print("输入问题，回车发送。输入 q 退出。")
    print("特殊命令: /status, /sessions, /logs\n")

    while True:
        try:
            query = input("\033[36ms06 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if query.lower() in ("q", "exit", "quit"):
            break
        if not query:
            continue

        if query == "/status":
            r = client.call("sidecar/status")["result"]
            print(f"\033[33m  sessions: {r['sessions']}")
            print(f"  ringBuffer: {r['ringBufferUsed']}/{r['ringBufferTotal']} bytes")
            print(f"  handlers: {r['handlers']}\033[0m\n")
            continue

        if query == "/sessions":
            r = client.call("session/list")["result"]
            for s in r["sessions"]:
                print(f"\033[33m  {s['id']}  {s['status']}  {s['cwd']}\033[0m")
            print()
            continue

        if query == "/logs":
            logs = sidecar.ring_buffer.read_all()
            print(f"\033[90m{logs[-2000:] if len(logs) > 2000 else logs}\033[0m\n")
            continue

        # Send message to agent via Sidecar RPC
        print("\033[90m[main] → agent/send via RPC...\033[0m")
        r = client.call("agent/send", {"sessionId": sid, "message": query})

        if "error" in r:
            print(f"\033[31mError: {r['error']}\033[0m\n")
        else:
            print(f"\033[32m{r['result']['response']}\033[0m\n")

    # Cleanup
    client.call("sidecar/shutdown")
    client.close()
    print("\n\033[90m[main] Sidecar shut down. Goodbye.\033[0m")


if __name__ == "__main__":
    main()
