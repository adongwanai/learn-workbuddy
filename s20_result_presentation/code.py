#!/usr/bin/env python3
from __future__ import annotations
"""
s20_result_presentation.py - Result Presentation: present_files & Artifact Cards

Simulates WorkBuddy's present_files delivery mechanism:

    task complete ──► generate files ──► present_files ──► user sees results

Key mechanisms simulated:
  1. Single entry point for showing results (present_files)
  2. File queue with priority ordering (first = auto-opened)
  3. File type detection and appropriate display
  4. HTML preview panel simulation (live render)
  5. Artifact card generation for each file
  6. localhost URL handling
  7. Supplemental to text response (not replacement)

Production harnesses often use:
  - present_files as a registered tool in the agent's tool pool
  - Electron renderer opens HTML in a <webview> or iframe
  - Artifact cards rendered as React components
  - localhost URLs checked for reachability before preview
  - cwd parameter for routing previews to owning session
  - System prompt mandates: every task with viewable result MUST end with present_files

Teaching version uses:
  - Files saved to disk and opened via webbrowser
  - Terminal-printed artifact cards
  - Same priority ordering and file type detection logic

Usage:
    python s20_result_presentation/code.py
"""



# Machine-readable learning path metadata. Tests enforce that every
# chapter declares what it inherits and what it adds.
PROGRESSION = {'chapter': 's20_result_presentation',
 'builds_on': ['s19_visualizer'],
 'adds': ['present_files flow', 'artifact cards', 'deliverable prioritization'],
 'preserves': ['visual output artifacts']}
import os, sys, time, json, subprocess, webbrowser
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

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
OUTPUT_DIR = WORKDIR / ".deliverables"
OUTPUT_DIR.mkdir(exist_ok=True)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ.get("MODEL_ID")
if not MODEL:
    raise SystemExit(
        "MODEL_ID is not set. Copy .env.example to .env and fill in "
        "ANTHROPIC_API_KEY and MODEL_ID (see README quick start)."
    )

SYSTEM = f"""You are a coding agent at {WORKDIR}.
When you complete a task that produces viewable files (reports, HTML pages, charts, scripts, etc.),
you MUST call present_files as your final tool call to deliver the results to the user.

Rules for present_files:
1. Only present NEWLY GENERATED deliverable files — not files you merely read or modified.
2. Put the most important file FIRST (it gets auto-opened).
3. Pass all related files together in one call.
4. Write a brief text explanation, then call present_files.
5. Do NOT write extensive explanations of file contents — let users see for themselves.
6. present_files is mandatory for any task that produces a viewable result.
"""


# ============================================================
# File Type Detection & Artifact Card Generation
# ============================================================

FILE_TYPE_CONFIG = {
    # HTML — gets live preview panel
    ".html": {"icon": "🌐", "category": "html", "opens_preview": True},
    ".htm":  {"icon": "🌐", "category": "html", "opens_preview": True},

    # Images
    ".svg":  {"icon": "🖼️", "category": "image", "opens_preview": False},
    ".png":  {"icon": "🖼️", "category": "image", "opens_preview": False},
    ".jpg":  {"icon": "🖼️", "category": "image", "opens_preview": False},
    ".jpeg": {"icon": "🖼️", "category": "image", "opens_preview": False},
    ".gif":  {"icon": "🖼️", "category": "image", "opens_preview": False},
    ".webp": {"icon": "🖼️", "category": "image", "opens_preview": False},

    # Documents
    ".pdf":  {"icon": "📄", "category": "document", "opens_preview": False},
    ".docx": {"icon": "📄", "category": "document", "opens_preview": False},
    ".pptx": {"icon": "📊", "category": "document", "opens_preview": False},
    ".xlsx": {"icon": "📋", "category": "document", "opens_preview": False},

    # Video
    ".mp4":  {"icon": "🎬", "category": "video", "opens_preview": False},
    ".mov":  {"icon": "🎬", "category": "video", "opens_preview": False},
    ".webm": {"icon": "🎬", "category": "video", "opens_preview": False},

    # Code
    ".py":   {"icon": "🐍", "category": "code", "opens_preview": False},
    ".js":   {"icon": "📜", "category": "code", "opens_preview": False},
    ".ts":   {"icon": "📜", "category": "code", "opens_preview": False},
    ".go":   {"icon": "🐹", "category": "code", "opens_preview": False},
    ".rs":   {"icon": "🦀", "category": "code", "opens_preview": False},
    ".java": {"icon": "☕", "category": "code", "opens_preview": False},
    ".c":    {"icon": "🔧", "category": "code", "opens_preview": False},
    ".cpp":  {"icon": "🔧", "category": "code", "opens_preview": False},

    # Data
    ".json": {"icon": "📋", "category": "data", "opens_preview": False},
    ".csv":  {"icon": "📊", "category": "data", "opens_preview": False},
    ".yaml": {"icon": "⚙️", "category": "data", "opens_preview": False},
    ".yml":  {"icon": "⚙️", "category": "data", "opens_preview": False},
    ".xml":  {"icon": "⚙️", "category": "data", "opens_preview": False},

    # Text
    ".md":   {"icon": "📝", "category": "text", "opens_preview": False},
    ".txt":  {"icon": "📄", "category": "text", "opens_preview": False},
    ".log":  {"icon": "📃", "category": "text", "opens_preview": False},
}


@dataclass
class ArtifactCard:
    """Represents a single artifact card for a delivered file."""
    path: str
    name: str
    extension: str
    icon: str
    category: str
    size: str
    exists: bool
    is_primary: bool = False
    is_url: bool = False

    def render(self) -> str:
        """Render the artifact card as a terminal-friendly string."""
        marker = "★" if self.is_primary else " "
        if self.is_url:
            return f"  {marker} {self.icon} {self.name}  [URL → browser preview]"
        elif not self.exists:
            return f"  {marker} {self.icon} {self.name}  [NOT FOUND]"
        else:
            return f"  {marker} {self.icon} {self.name}  ({self.size})"


def format_size(bytes_size: int) -> str:
    """Format file size human-readably."""
    if bytes_size < 1024:
        return f"{bytes_size}B"
    elif bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.1f}KB"
    else:
        return f"{bytes_size / (1024 * 1024):.1f}MB"


def create_artifact_card(file_path: str, is_primary: bool = False) -> ArtifactCard:
    """Create an artifact card for a file path or URL."""
    # URL handling
    if file_path.startswith(("http://", "https://")):
        is_localhost = "localhost" in file_path or "127.0.0.1" in file_path
        return ArtifactCard(
            path=file_path,
            name=file_path,
            extension="[URL]",
            icon="🔗" if not is_localhost else "🖥️",
            category="url",
            size="—",
            exists=True,
            is_primary=is_primary,
            is_url=True,
        )

    # Local file handling
    path = Path(file_path)
    ext = path.suffix.lower()
    config = FILE_TYPE_CONFIG.get(ext, {"icon": "📎", "category": "other", "opens_preview": False})

    size = 0
    exists = path.exists()
    if exists:
        try:
            size = path.stat().st_size
        except OSError:
            pass

    return ArtifactCard(
        path=str(path),
        name=path.name,
        extension=ext,
        icon=config["icon"],
        category=config["category"],
        size=format_size(size),
        exists=exists,
        is_primary=is_primary,
    )


# ============================================================
# Result Presenter (simulates present_files tool)
# ============================================================

class ResultPresenter:
    """Simulates the present_files tool — the single entry point for delivery.

    Production harness:
    - present_files is a tool the agent calls as the final step
    - First file auto-opened in preview panel
    - HTML files get live preview + artifact card
    - localhost URLs opened in built-in browser
    - Artifact cards rendered as React components in conversation
    """

    def __init__(self):
        self.delivery_history: list[dict] = []
        self.total_delivered: int = 0

    def present_files(self, files: list[str], explanation: str = "", cwd: str = "") -> str:
        """Present files to the user as the final delivery step.

        Args:
            files: Ordered list of file paths or URLs.
                   First item is auto-opened. Order = viewing priority.
            explanation: Brief description of what was produced.
            cwd: Working directory for routing (used for localhost fallback).
        """
        if not files:
            return "Error: No files to present."

        self.total_delivered += len(files)

        print("\n" + "=" * 60)
        print("  📦 DELIVERY — present_files")
        print("=" * 60)

        if explanation:
            print(f"  {explanation}")
            print()

        # Generate artifact cards
        cards = []
        for i, f in enumerate(files):
            card = create_artifact_card(f, is_primary=(i == 0))
            cards.append(card)

        # Display artifact cards
        print("  Artifact Cards:")
        print("  ┌──────────────────────────────────────────────────┐")
        for card in cards:
            print(f"  │{card.render():<50s}│")
        print("  └──────────────────────────────────────────────────┘")

        # Handle primary file (auto-open)
        primary = cards[0]
        print(f"\n  → Auto-opening primary file: {primary.name}")

        if primary.is_url:
            self._open_url(primary.path)
        elif primary.category == "html" and primary.exists:
            self._open_html_preview(primary.path)
        elif primary.exists:
            self._open_file(primary.path, primary.category)
        else:
            print(f"  ⚠ Primary file not found: {primary.path}")

        # Handle HTML files that also get preview (not just primary)
        for card in cards[1:]:
            if card.category == "html" and card.exists:
                print(f"  → HTML preview available: {card.name}")

        # Record delivery
        delivery = {
            "timestamp": time.time(),
            "files": [{"path": c.path, "name": c.name, "category": c.category} for c in cards],
            "explanation": explanation,
            "count": len(files),
        }
        self.delivery_history.append(delivery)

        print(f"\n  ✅ Delivered {len(files)} file(s). Total this session: {self.total_delivered}")
        print("=" * 60)

        return json.dumps({
            "status": "presented",
            "files_delivered": len(files),
            "primary_file": primary.name,
            "total_session": self.total_delivered,
        })

    def _open_url(self, url: str):
        """Open a URL in the browser (simulates built-in browser preview)."""
        print(f"  🔗 Opening URL in browser: {url}")
        try:
            webbrowser.open(url)
        except Exception:
            print(f"  (Could not auto-open browser)")

    def _open_html_preview(self, path: str):
        """Open HTML file in preview panel (simulates live preview)."""
        print(f"  🌐 Opening HTML preview: {path}")
        try:
            webbrowser.open(f"file://{Path(path).resolve()}")
        except Exception:
            print(f"  (Could not auto-open preview)")

    def _open_file(self, path: str, category: str):
        """Open a non-HTML file."""
        if category == "image":
            print(f"  🖼️ Opening image: {path}")
            try:
                webbrowser.open(f"file://{Path(path).resolve()}")
            except Exception:
                pass
        elif category == "code":
            print(f"  📜 Code file ready for viewing: {path}")
        elif category == "data":
            print(f"  📋 Data file ready: {path}")
        else:
            print(f"  📎 File ready: {path}")

    def show_history(self) -> str:
        """Show delivery history."""
        if not self.delivery_history:
            return "\n  No deliveries yet."

        lines = [f"\n  Delivery History ({len(self.delivery_history)} deliveries, {self.total_delivered} files):"]
        for i, d in enumerate(self.delivery_history):
            t = time.strftime("%H:%M:%S", time.localtime(d["timestamp"]))
            lines.append(f"  [{i+1}] {t} — {d['count']} file(s)")
            for f in d["files"]:
                lines.append(f"       {f['name']} ({f['category']})")
        return "\n".join(lines)


# ============================================================
# Built-in Tools
# ============================================================

def run_bash(command: str) -> str:
    import subprocess
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR, capture_output=True, text=True, timeout=120)
        return (r.stdout + r.stderr).strip()[:5000] or "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout"

def run_write(path: str, content: str) -> str:
    try:
        fp = OUTPUT_DIR / path if not os.path.isabs(path) else Path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {fp}"
    except Exception as e:
        return f"Error: {e}"

def run_read(path: str) -> str:
    try:
        fp = OUTPUT_DIR / path if not os.path.isabs(path) else Path(path)
        return fp.read_text()[:5000]
    except Exception as e:
        return f"Error: {e}"

BUILTIN_TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "write_file", "description": "Write content to a file in the deliverables directory.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
]
BUILTIN_HANDLERS = {"bash": run_bash, "write_file": run_write, "read_file": run_read}


# ============================================================
# Agent Loop with Result Presentation
# ============================================================

class DeliveryAgent:
    """Agent loop that delivers results via present_files."""

    def __init__(self, presenter: ResultPresenter):
        self.presenter = presenter
        self.messages: list[dict] = []

    @property
    def tools(self) -> list[dict]:
        return BUILTIN_TOOLS + [
            {"name": "present_files", "description": (
                "Present files to the user as the final delivery step. "
                "This is the SINGLE entry point for showing results. "
                "Call this as the FINAL tool call when a task produces viewable results. "
                "First file in the array is auto-opened. Order = viewing priority."
             ),
             "input_schema": {"type": "object", "properties": {
                 "files": {"type": "array", "items": {"type": "string"},
                           "description": "Ordered list of file paths or URLs. First = auto-opened."},
                 "explanation": {"type": "string", "description": "Brief description of what was produced."},
                 "cwd": {"type": "string", "description": "Working directory for routing."},
             }, "required": ["files"]}},
        ]

    def handle_tool_call(self, block) -> str:
        name = block.name
        params = dict(block.input) if block.input else {}

        if name in BUILTIN_HANDLERS:
            return BUILTIN_HANDLERS[name](**params)

        elif name == "present_files":
            files = params.get("files", [])
            explanation = params.get("explanation", "")
            cwd = params.get("cwd", str(WORKDIR))

            # Resolve relative paths to deliverables dir
            resolved = []
            for f in files:
                if f.startswith(("http://", "https://")):
                    resolved.append(f)
                elif os.path.isabs(f):
                    resolved.append(f)
                else:
                    resolved.append(str((OUTPUT_DIR / f).resolve()))

            return self.presenter.present_files(resolved, explanation, cwd)

        return f"Unknown tool: {name}"

    def run(self, user_input: str):
        self.messages.append({"role": "user", "content": user_input})

        while True:
            response = client.messages.create(
                model=MODEL,
                system=SYSTEM,
                messages=self.messages,
                tools=self.tools,
                max_tokens=8000,
            )
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                for block in response.content:
                    if getattr(block, "type", None) == "text":
                        print(block.text)
                return

            results = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    if getattr(block, "type", None) == "text" and block.text.strip():
                        print(block.text)
                    continue

                # Truncate present_files input display (files list can be long)
                display = dict(block.input) if block.input else {}
                if block.name == "present_files":
                    display = {"files": display.get("files", []), "explanation": display.get("explanation", "")[:80]}
                print(f"\033[36m> {block.name}\033[0m {json.dumps(display, ensure_ascii=False)[:120]}")

                output = self.handle_tool_call(block)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})

            self.messages.append({"role": "user", "content": results})


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("s20: Result Presentation — present_files & artifact cards")
    print("=" * 60)
    print("Commands:")
    print("  /history   — Show delivery history")
    print("  /dir       — Open deliverables directory")
    print("  q          — Quit")
    print()
    print("The agent will call present_files when tasks produce files.")
    print(f"Deliverables are saved to: {OUTPUT_DIR}\n")

    presenter = ResultPresenter()
    agent = DeliveryAgent(presenter)

    while True:
        try:
            query = input("\n\033[36ms20 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break

        query = query.strip()
        if not query:
            continue
        if query.lower() in ("q", "exit", "quit"):
            break

        if query.startswith("/"):
            parts = query.split(maxsplit=1)
            cmd = parts[0]

            if cmd == "/history":
                print(presenter.show_history())
            elif cmd == "/dir":
                try:
                    webbrowser.open(f"file://{OUTPUT_DIR.resolve()}")
                    print(f"  Opened: {OUTPUT_DIR}")
                except Exception:
                    print(f"  Directory: {OUTPUT_DIR}")
            else:
                print(f"Unknown command: {cmd}")
            continue

        agent.run(query)
