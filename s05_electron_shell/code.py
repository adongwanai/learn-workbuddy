#!/usr/bin/env python3
from __future__ import annotations
"""
s05_electron_shell.py - Multi-Process Architecture

Simulates Electron's three-process model in Python:

    ┌─────────────┐     IPC      ┌──────────────┐
    │ Main Process │◄───────────►│ Renderer     │
    │ (agent loop) │             │ (user UI)    │
    └─────────────┘             └──────────────┘
           │  Preload bridge (contextBridge equivalent)

Real Electron uses:
    - Main: Node.js, full system access
    - Renderer: Browser, sandboxed, no Node.js
    - Preload: Bridge, exposes safe API via contextBridge

Teaching version uses multiprocessing to demonstrate isolation:
    - Each process has independent memory
    - Communication via queues (IPC equivalent)
    - Crash in one process doesn't kill the other

Usage:
    python s05_electron_shell/code.py
"""



# Machine-readable learning path metadata. Tests enforce that every
# chapter declares what it inherits and what it adds.
PROGRESSION = {'chapter': 's05_electron_shell',
 'builds_on': ['s04_permission_hooks'],
 'adds': ['main/renderer/preload split', 'IPC bridge', 'process isolation'],
 'preserves': ['agent request boundary']}
import os, sys, time, json, multiprocessing as mp
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


# == Preload Bridge API (equivalent to contextBridge.exposeInMainWorld) ==

class PreloadBridge:
    """
    Simulates Electron's preload script.
    In real Electron, this runs in an isolated world in the renderer
    and exposes a safe API via contextBridge.
    
    Here, it's a proxy that sends IPC messages to the main process.
    """
    def __init__(self, send_queue: mp.Queue, recv_queue: mp.Queue):
        self._send = send_queue
        self._recv = recv_queue

    def send_message(self, text: str) -> str:
        """IPC: renderer → main → agent"""
        self._send.put({"type": "agent/message", "data": text})
        result = self._recv.get()  # Block until main responds
        return result.get("data", "")

    def list_sessions(self) -> list:
        self._send.put({"type": "session/list"})
        result = self._recv.get()
        return result.get("data", [])

    def ping(self) -> str:
        """Health check to main process"""
        self._send.put({"type": "ping"})
        result = self._recv.get(timeout=5)
        return result.get("data", "")


# == Main Process (equivalent to Electron Main) ==

def main_process(recv_queue: mp.Queue, send_queue: mp.Queue, renderer_queue: mp.Queue):
    """
    Electron Main Process equivalent.
    
    Responsibilities:
    - Receive IPC from renderer (via recv_queue)
    - Route to agent (simulated Sidecar)
    - Send results back to renderer (via send_queue)
    
    In a production desktop harness, this main-process router grows into many RPC domains (teaching abstraction; not a claim about any private bundle).
    """
    from anthropic import Anthropic
    import os
    
    client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
    MODEL = os.environ.get("MODEL_ID")
    if not MODEL:
        raise SystemExit(
            "MODEL_ID is not set. Copy .env.example to .env and fill in "
            "ANTHROPIC_API_KEY and MODEL_ID (see README quick start)."
        )
    
    SYSTEM = f"You are a coding agent at {WORKDIR}. Use bash to solve tasks. Act, don't explain."
    TOOLS = [{
        "name": "bash",
        "description": "Run a shell command.",
        "input_schema": {"type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"]},
    }]

    import subprocess
    def run_bash(command):
        dangerous = ["rm -rf /", "sudo", "shutdown", "reboot"]
        if any(d in command for d in dangerous):
            return "Error: Dangerous command blocked"
        try:
            r = subprocess.run(command, shell=True, cwd=WORKDIR,
                               capture_output=True, text=True, timeout=120)
            out = (r.stdout + r.stderr).strip()
            return out[:50000] if out else "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: Timeout (120s)"

    sessions = {}  # Simulate session management

    while True:
        msg = recv_queue.get()
        if msg is None:
            break

        msg_type = msg.get("type")

        if msg_type == "ping":
            send_queue.put({"type": "pong", "data": "main alive"})

        elif msg_type == "session/list":
            send_queue.put({"type": "result", "data": list(sessions.keys())})

        elif msg_type == "agent/message":
            # This is where the Sidecar would be called in real WorkBuddy
            # For teaching, we run the agent loop directly here
            text = msg["data"]
            
            # Simple single-turn agent loop (no conversation history across messages)
            messages = [{"role": "user", "content": text}]
            
            while True:
                response = client.messages.create(
                    model=MODEL, system=SYSTEM, messages=messages,
                    tools=TOOLS, max_tokens=8000)
                messages.append({"role": "assistant", "content": response.content})

                if response.stop_reason != "tool_use":
                    break

                results = []
                for block in response.content:
                    if block.type == "tool_use":
                        output = run_bash(block.input["command"])
                        results.append({"type": "tool_result",
                                        "tool_use_id": block.id, "content": output})
                messages.append({"role": "user", "content": results})

            # Extract final text
            final_text = ""
            for block in messages[-1]["content"]:
                if getattr(block, "type", None) == "text":
                    final_text += block.text

            send_queue.put({"type": "result", "data": final_text})


# == Renderer Process (equivalent to Electron Renderer) ==

def renderer_process(send_queue: mp.Queue, recv_queue: mp.Queue):
    """
    Electron Renderer Process equivalent.
    
    In real Electron, this is a browser window with HTML/CSS/JS.
    Here, it's a simple terminal UI that uses the PreloadBridge.
    
    The renderer CANNOT directly:
    - Access the file system
    - Execute shell commands
    - Import Node.js modules
    
    It can ONLY use the API exposed by PreloadBridge.
    """
    bridge = PreloadBridge(send_queue, recv_queue)

    # Verify main process is alive
    pong = bridge.ping()
    print(f"\033[90m[renderer] Main process: {pong}\033[0m\n")

    print("s05: Electron Shell — three-process architecture")
    print("输入问题，回车发送。输入 q 退出。\n")

    while True:
        try:
            query = input("\033[36ms05 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break

        # Renderer calls bridge API — IPC to main process
        # The renderer itself has NO access to LLM, bash, or file system
        result = bridge.send_message(query)
        print(f"\033[32m{result}\033[0m\n")

    # Signal main to stop
    send_queue.put(None)


# == Entry Point ==

if __name__ == "__main__":
    # Create IPC queues (simulating Electron's IPC)
    renderer_to_main = mp.Queue()
    main_to_renderer = mp.Queue()

    # Start main process
    main_proc = mp.Process(
        target=main_process,
        args=(renderer_to_main, main_to_renderer, None),
        name="MainProcess"
    )
    main_proc.start()

    # Run renderer in main thread (user interaction)
    try:
        renderer_process(renderer_to_main, main_to_renderer)
    finally:
        # Clean up
        renderer_to_main.put(None)
        main_proc.join(timeout=5)
        if main_proc.is_alive():
            main_proc.terminate()
            main_proc.join()
