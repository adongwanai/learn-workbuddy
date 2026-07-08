#!/usr/bin/env python3
from __future__ import annotations
"""
s19_visualizer.py - Visualizer: SVG/HTML Widget Streaming Injection

Simulates WorkBuddy's visualizer two-tool protocol:

    read_me(modules) → design guidance
    show_widget(title, widget_code, loading_messages) → inline render

Key mechanisms simulated:
  1. Design module system (diagram, chart, interactive, mockup, art)
  2. read_me: loads CSS vars, colors, typography, layout rules
  3. show_widget: renders raw SVG/HTML inline
  4. SVG generation with viewBox "0 0 680 ..." constraint
  5. HTML fragment generation (no DOCTYPE/html/head/body)
  6. Multi-widget narrative flow (text + widget alternation)
  7. Theme awareness (light/dark)

Production harnesses often use:
  - Two tools: read_me + show_widget (registered in tool pool)
  - SVG renders inline in Electron renderer (Chromium SVG engine)
  - HTML renders in sandboxed iframe within conversation
  - Theme from Electron nativeTheme / user preference
  - Loading messages shown in spinner during async render
  - Widget title used as download filename
  - Modules: diagram, mockup, interactive, chart, art

Teaching version uses:
  - SVG saved to file and opened in browser (terminal can't render SVG)
  - Same generation patterns and constraints
  - Same two-tool protocol

Usage:
    python s19_visualizer/code.py
"""



# Machine-readable learning path metadata. Tests enforce that every
# chapter declares what it inherits and what it adds.
PROGRESSION = {'chapter': 's19_visualizer',
 'builds_on': ['s18_experts_system'],
 'adds': ['visualizer protocol', 'SVG/HTML widget generation', 'theme-aware output'],
 'preserves': ['specialized output routing']}
import os, sys, time, json, subprocess, webbrowser
from pathlib import Path
from dataclasses import dataclass, field

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
WIDGET_DIR = WORKDIR / ".widgets"
WIDGET_DIR.mkdir(exist_ok=True)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ.get("MODEL_ID")
if not MODEL:
    raise SystemExit(
        "MODEL_ID is not set. Copy .env.example to .env and fill in "
        "ANTHROPIC_API_KEY and MODEL_ID (see README quick start)."
    )

SYSTEM = f"""You are a coding agent at {WORKDIR} with visualization capabilities.

You can generate SVG diagrams and HTML widgets that render inline in the conversation.

## Visualization Protocol

1. Before your FIRST show_widget call, you MUST call read_me to load design guidance.
   Example: read_me(modules=["diagram"]) for architecture diagrams.
   Available modules: diagram, chart, interactive, mockup, art.

2. Then call show_widget with:
   - title: A descriptive title (used as filename)
   - widget_code: Raw SVG (viewBox must start with "0 0 680") or HTML fragment
   - loading_messages: 1-4 short messages shown during rendering

## SVG Rules
- Must start with <svg> tag
- viewBox must start with "0 0 680" (e.g., "0 0 680 400")
- Use colors from the design guidance
- Keep diagrams clean — max 7-8 nodes

## HTML Rules
- Raw fragment only — NO <!DOCTYPE>, <html>, <head>, or <body> tags
- Can include <style> and <script> tags
- Self-contained

## Multi-Widget Narrative
For complex topics, break into multiple smaller widgets with prose between them.
Don't cram everything into one giant diagram.
"""


# ============================================================
# Design Guidance System (simulates read_me)
# ============================================================

THEMES = {
    "light": {
        "bg": "#ffffff", "fg": "#1a1a2e", "primary": "#3b82f6",
        "accent": "#f59e0b", "border": "#e2e8f0", "muted": "#64748b",
        "success": "#22c55e", "danger": "#ef4444",
        "card_bg": "#f8fafc", "shadow": "rgba(0,0,0,0.08)",
    },
    "dark": {
        "bg": "#1a1a2e", "fg": "#e2e8f0", "primary": "#60a5fa",
        "accent": "#fbbf24", "border": "#334155", "muted": "#94a3b8",
        "success": "#4ade80", "danger": "#f87171",
        "card_bg": "#16213e", "shadow": "rgba(0,0,0,0.3)",
    },
}

DESIGN_MODULES = {
    "diagram": {
        "description": "Architecture diagrams, flowcharts, system design",
        "rules": [
            "SVG viewBox must start with '0 0 680'",
            "Use rounded rectangles (rx=8) for nodes",
            "Arrows with <marker> for connections",
            "Maximum 7-8 nodes per diagram for clarity",
            "Node size: 120x60 for standard, 160x80 for emphasis",
            "Use muted color for connections, primary for nodes",
        ],
        "typography": {"title": "20px bold", "body": "14px", "label": "12px", "caption": "11px"},
        "layout": "Top-to-bottom or left-to-right flow",
    },
    "chart": {
        "description": "Data charts: bar, line, pie, comparison",
        "rules": [
            "SVG viewBox must start with '0 0 680'",
            "Always label axes with <text>",
            "Grid lines: stroke=muted, stroke-width=0.5, opacity=0.3",
            "Use consistent color palette across data series",
            "Bar width: 40-60px, gap: 20-30px",
            "Legend at top-right or bottom",
        ],
        "typography": {"title": "18px bold", "axis": "12px", "value": "11px", "legend": "12px"},
        "layout": "Standard chart axes with padding",
    },
    "interactive": {
        "description": "Interactive HTML widgets with CSS/JS",
        "rules": [
            "Raw HTML fragment — no DOCTYPE/html/head/body",
            "Can include <style> and <script> tags",
            "All CSS must be scoped (use unique class prefixes)",
            "All JS must be self-contained (no external imports)",
            "Interactive elements must have hover/focus states",
        ],
        "typography": {"title": "20px bold", "body": "14px", "button": "14px", "label": "12px"},
        "layout": "Responsive flexbox or grid",
    },
    "mockup": {
        "description": "UI mockups and prototypes",
        "rules": [
            "HTML fragment simulating UI layout",
            "Use realistic content, not lorem ipsum",
            "Show actual button labels, form fields, navigation",
            "Include hover states in <style>",
            "Mobile-first: start with 375px width",
        ],
        "typography": {"heading": "24px bold", "body": "16px", "caption": "14px", "button": "15px"},
        "layout": "Flexbox column for mobile, grid for desktop",
    },
    "art": {
        "description": "Decorative and conceptual graphics",
        "rules": [
            "SVG with creative use of shapes and colors",
            "Can use gradients, filters, patterns",
            "viewBox must start with '0 0 680'",
            "Artistic but still meaningful",
        ],
        "typography": {"title": "22px bold", "body": "14px"},
        "layout": "Free-form creative layout",
    },
}


class DesignGuide:
    """Simulates the read_me tool — provides design guidance for modules."""

    def __init__(self, theme: str = "light"):
        self.theme = theme
        self.loaded_modules: set[str] = set()

    def read_me(self, modules: list[str]) -> str:
        """Load design guidance for specified modules.

        Production harness: returns CSS vars, colors, typography, layout rules, examples.
        Must be called before first show_widget.
        """
        result = {"theme": self.theme, "colors": THEMES[self.theme], "modules": {}}

        for mod in modules:
            if mod in DESIGN_MODULES:
                result["modules"][mod] = DESIGN_MODULES[mod]
                self.loaded_modules.add(mod)

        return json.dumps(result, ensure_ascii=False, indent=2)

    @property
    def colors(self) -> dict:
        return THEMES[self.theme]

    def switch_theme(self, theme: str):
        self.theme = theme
        print(f"  \033[33mTheme switched to: {theme}\033[0m")


# ============================================================
# SVG Generation Helpers
# ============================================================

def svg_wrapper(content: str, height: int = 400) -> str:
    """Wrap SVG content with proper viewBox."""
    return f'<svg viewBox="0 0 680 {height}" xmlns="http://www.w3.org/2000/svg">{content}</svg>'


def svg_node(x: int, y: int, label: str, colors: dict, w: int = 120, h: int = 60) -> str:
    """Generate a rounded rectangle node."""
    return f"""<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{colors['primary']}" fill-opacity="0.12" rx="8"/>
<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="{colors['primary']}" stroke-width="2" rx="8"/>
<text x="{x+w//2}" y="{y+h//2+5}" text-anchor="middle" fill="{colors['fg']}" font-family="sans-serif" font-size="14" font-weight="600">{label}</text>"""


def svg_arrow(x1: int, y1: int, x2: int, y2: int, colors: dict, label: str = "") -> str:
    """Generate an arrow connection."""
    label_svg = ""
    if label:
        mx, my = (x1 + x2) // 2, (y1 + y2) // 2
        label_svg = f'<text x="{mx}" y="{my-5}" text-anchor="middle" fill="{colors["muted"]}" font-family="sans-serif" font-size="11">{label}</text>'
    return f"""<defs><marker id="arrowhead" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">
<path d="M0,0 L8,3 L0,6 Z" fill="{colors['muted']}"/></marker></defs>
<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{colors['muted']}" stroke-width="2" marker-end="url(#arrowhead)"/>{label_svg}"""


def svg_title(text: str, colors: dict, y: int = 30) -> str:
    """Generate a title text element."""
    return f'<text x="340" y="{y}" text-anchor="middle" fill="{colors["fg"]}" font-family="sans-serif" font-size="20" font-weight="700">{text}</text>'


def svg_bar_chart(data: list[tuple[str, int]], colors: dict, title: str = "") -> str:
    """Generate a bar chart SVG."""
    max_val = max(v for _, v in data) if data else 1
    bar_w = 50
    gap = 30
    start_x = 80
    chart_h = 280
    base_y = 340

    parts = []
    if title:
        parts.append(svg_title(title, colors))

    # Grid lines
    for i in range(5):
        gy = base_y - (chart_h * i // 4)
        val = int(max_val * i / 4)
        parts.append(f'<line x1="70" y1="{gy}" x2="650" y2="{gy}" stroke="{colors["border"]}" stroke-width="0.5"/>')
        parts.append(f'<text x="60" y="{gy+4}" text-anchor="end" fill="{colors["muted"]}" font-family="sans-serif" font-size="11">{val}</text>')

    # Bars
    for i, (label, value) in enumerate(data):
        bx = start_x + i * (bar_w + gap)
        bh = int(chart_h * value / max_val) if max_val > 0 else 0
        by = base_y - bh
        color = colors["primary"] if i == 0 else colors["accent"]
        parts.append(f'<rect x="{bx}" y="{by}" width="{bar_w}" height="{bh}" fill="{color}" rx="4"/>')
        parts.append(f'<text x="{bx+bar_w//2}" y="{by-8}" text-anchor="middle" fill="{colors["fg"]}" font-family="sans-serif" font-size="12" font-weight="600">{value}</text>')
        parts.append(f'<text x="{bx+bar_w//2}" y="{base_y+20}" text-anchor="middle" fill="{colors["muted"]}" font-family="sans-serif" font-size="12">{label}</text>')

    return svg_wrapper("\n".join(parts), 400)


# ============================================================
# Widget Renderer (simulates show_widget)
# ============================================================

class WidgetRenderer:
    """Simulates the show_widget tool — renders SVG/HTML widgets.

    Production harness: injects widget_code into the conversation DOM,
    Chromium renders it inline. Loading messages shown in spinner.
    Teaching version: saves to file and opens in browser.
    """

    def __init__(self, guide: DesignGuide):
        self.guide = guide
        self.widget_count = 0

    def show_widget(self, title: str, widget_code: str, loading_messages: list[str] = None) -> str:
        """Render a widget inline.

        Args:
            title: Widget identifier (also used as download filename)
            widget_code: Raw SVG or HTML fragment
            loading_messages: 1-4 short messages shown during rendering
        """
        if not loading_messages:
            loading_messages = ["Rendering..."]

        # Simulate loading messages
        for msg in loading_messages:
            print(f"  \033[90m  ⟳ {msg}\033[0m")
            time.sleep(0.3)

        # Validate SVG
        is_svg = widget_code.strip().startswith("<svg")
        if is_svg:
            if 'viewBox="0 0 680' not in widget_code:
                return f"Error: SVG viewBox must start with '0 0 680'. Got different viewBox."

        # Validate HTML (no full document)
        if not is_svg:
            for forbidden in ["<!DOCTYPE", "<html", "<head", "<body"]:
                if forbidden in widget_code:
                    return f"Error: HTML must be a raw fragment. Found '{forbidden}'."

        # Save to file
        self.widget_count += 1
        safe_title = title.replace(" ", "_").replace("/", "_")
        ext = ".svg" if is_svg else ".html"
        filepath = WIDGET_DIR / f"{safe_title}{ext}"
        filepath.write_text(widget_code)

        # For HTML, wrap in full document for browser preview
        if not is_svg:
            preview = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{background:{self.guide.colors['bg']};color:{self.guide.colors['fg']};font-family:sans-serif;padding:20px;}}</style>
</head><body>{widget_code}</body></html>"""
            filepath.write_text(preview)

        print(f"  \033[32m✓ Widget rendered: {title}\033[0m")
        print(f"  \033[90m  Saved: {filepath}\033[0m")
        print(f"  \033[90m  Type: {'SVG' if is_svg else 'HTML'} | Size: {len(widget_code)} chars\033[0m")

        # Open in browser (simulating inline render)
        try:
            webbrowser.open(f"file://{filepath.resolve()}")
        except Exception:
            pass

        return json.dumps({
            "status": "rendered",
            "title": title,
            "type": "svg" if is_svg else "html",
            "file": str(filepath),
            "size": len(widget_code),
        })


# ============================================================
# Built-in Tools
# ============================================================

def run_bash(command: str) -> str:
    import subprocess
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR, capture_output=True, text=True, timeout=120)
        return (r.stdout + r.stderr).strip()[:3000] or "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout"

BUILTIN_TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
]
BUILTIN_HANDLERS = {"bash": run_bash}


# ============================================================
# Agent Loop with Visualizer
# ============================================================

class VisualizerAgent:
    """Agent loop with visualization capabilities."""

    def __init__(self, guide: DesignGuide, renderer: WidgetRenderer):
        self.guide = guide
        self.renderer = renderer
        self.messages: list[dict] = []
        self._read_me_called = False

    @property
    def tools(self) -> list[dict]:
        return BUILTIN_TOOLS + [
            {"name": "read_me", "description": "Load design guidance for visualization modules. Call before first show_widget.",
             "input_schema": {"type": "object", "properties": {
                 "modules": {"type": "array", "items": {"type": "string"},
                             "description": "Modules to load: diagram, mockup, interactive, chart, art"}
             }, "required": ["modules"]}},
            {"name": "show_widget", "description": "Render SVG/HTML widget inline in conversation.",
             "input_schema": {"type": "object", "properties": {
                 "title": {"type": "string", "description": "Widget title (used as filename)"},
                 "widget_code": {"type": "string", "description": "Raw SVG (viewBox starts with '0 0 680') or HTML fragment (no DOCTYPE/html/head/body)"},
                 "loading_messages": {"type": "array", "items": {"type": "string"},
                                       "description": "1-4 short loading messages"}
             }, "required": ["title", "widget_code"]}},
        ]

    def handle_tool_call(self, block) -> str:
        name = block.name
        params = dict(block.input) if block.input else {}

        if name in BUILTIN_HANDLERS:
            return BUILTIN_HANDLERS[name](**params)

        elif name == "read_me":
            modules = params.get("modules", ["diagram"])
            self._read_me_called = True
            result = self.guide.read_me(modules)
            print(f"  \033[90m[read_me] Loaded modules: {modules}\033[0m")
            return result

        elif name == "show_widget":
            if not self._read_me_called:
                return "Error: Must call read_me before show_widget. Load design guidance first."
            title = params.get("title", "untitled")
            widget_code = params.get("widget_code", "")
            loading = params.get("loading_messages", ["Rendering..."])
            return self.renderer.show_widget(title, widget_code, loading)

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
                print(f"\033[36m> {block.name}\033[0m")
                output = self.handle_tool_call(block)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})

            self.messages.append({"role": "user", "content": results})


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("s19: Visualizer — SVG/HTML widget streaming injection")
    print("=" * 60)
    print("Commands:")
    print("  /theme <light|dark>  — Switch theme")
    print("  /modules             — List available design modules")
    print("  /widgets             — List rendered widgets")
    print("  q                    — Quit")
    print()
    print("The agent can generate SVG diagrams and HTML widgets.")
    print("It will call read_me first, then show_widget.\n")

    guide = DesignGuide(theme="light")
    renderer = WidgetRenderer(guide)
    agent = VisualizerAgent(guide, renderer)

    while True:
        theme_tag = f"[{guide.theme}]" if guide.theme != "light" else ""
        try:
            query = input(f"\n\033[36ms19{theme_tag} >> \033[0m")
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
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd == "/theme":
                if arg in ("light", "dark"):
                    guide.switch_theme(arg)
                else:
                    print("Usage: /theme <light|dark>")
            elif cmd == "/modules":
                print("\nAvailable design modules:")
                for name, mod in DESIGN_MODULES.items():
                    print(f"  {name:<14} — {mod['description']}")
            elif cmd == "/widgets":
                widgets = list(WIDGET_DIR.glob("*"))
                if widgets:
                    print(f"\nRendered widgets ({len(widgets)}):")
                    for w in widgets:
                        print(f"  {w.name}")
                else:
                    print("\nNo widgets rendered yet.")
            else:
                print(f"Unknown command: {cmd}")
            continue

        agent.run(query)
