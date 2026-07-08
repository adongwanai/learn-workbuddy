from __future__ import annotations

import argparse
import os
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

# NB: `from anthropic import Anthropic` is intentionally NOT imported here.
# --mode offline must run with zero third-party deps beyond python-dotenv,
# so CI and readers without an API key can exercise the full harness. The
# SDK is imported lazily inside run_real_api_demo() only.

from mini_workbuddy.audit import AuditLog
from mini_workbuddy.agent import MiniAgent
from mini_workbuddy.config import HarnessConfig
from mini_workbuddy.events import EventBus
from mini_workbuddy.storage import Storage
from mini_workbuddy.tools import ToolRegistry


REAL_API_PROMPT = (
    "Use the available tools to inspect this project. First list available tools, "
    "then run pwd, then read README.md, then summarize what this WorkBuddy-style "
    "harness demo proves in three concise bullets."
)

TOOL_SCHEMAS = [
    {
        "name": "tool_search",
        "description": "List or search available harness tools.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    },
    {
        "name": "bash",
        "description": "Run a shell command in the session cwd. Dangerous commands are denied.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file by cwd-relative path.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
]


def build_runtime() -> tuple[HarnessConfig, Storage, EventBus, AuditLog, ToolRegistry]:
    config = HarnessConfig.from_env()
    storage = Storage(config)
    events = EventBus()
    audit = AuditLog(config)
    tools = ToolRegistry(config, storage)
    return config, storage, events, audit, tools


def run_offline_demo() -> None:
    _, storage, events, audit, tools = build_runtime()
    agent = MiniAgent(storage, tools, events, audit)

    session = storage.create_session(cwd=str(ROOT), title="mini workbuddy full demo")
    memory_path = storage.append_memory(
        "workspace",
        "- Mini demo memory: explain harness mechanics before product polish.",
    )

    print("Session:", session.id)
    print("Workspace:", session.cwd)
    print("Memory:", memory_path)

    prompts = [
        ("tool directory", "tools"),
        ("current directory", "pwd"),
        ("read project readme", "read README.md"),
        ("permission denial", "bash rm -rf ."),
        ("large output externalization", "bash python3 -c \"print('x' * 70000)\""),
    ]

    for label, prompt in prompts:
        print("\n" + "=" * 72)
        print(f"{label}: {prompt}")
        result = agent.prompt(session, prompt)
        print(result["answer"][:1200])
        for tool_result in result["toolResults"]:
            externalized = tool_result.get("externalized_path")
            if externalized:
                print("Externalized:", externalized)

    transcript = storage.read_transcript(session)
    sessions = storage.list_sessions()

    print("\n" + "=" * 72)
    print("Recovered state")
    print("Transcript:", storage.transcript_path(session))
    print("Transcript events:", len(transcript))
    print("Known sessions:", len(sessions))
    print("Workspace memory:")
    print(storage.read_memory("workspace").strip())

    print("\n" + "=" * 72)
    print("Audit")
    print("Audit file:", audit.path)
    print("Audit entries:", len(audit.read_entries()))
    print("Audit verified:", audit.verify())


def api_env_ready() -> bool:
    load_dotenv(override=True)
    return bool(os.getenv("ANTHROPIC_API_KEY") and os.getenv("MODEL_ID"))


def run_real_api_demo(prompt: str, max_turns: int = 8, provider_name: str | None = None) -> None:
    load_dotenv(override=True)
    if os.getenv("ANTHROPIC_BASE_URL"):
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

    from mini_workbuddy import providers as P

    # Resolve the provider. --provider (or PROVIDER env) picks anthropic /
    # openai / offline; the loop below is identical for all of them because
    # the adapter normalizes tool_use vs function_call into one shape.
    provider = P.select_provider(provider_name)
    if provider.name == "offline":
        print("No real provider key found; falling back to the offline mock provider.")

    _, storage, events, audit, tools = build_runtime()
    spec = P.normalized_tools()
    session = storage.create_session(cwd=str(ROOT), title=f"mini workbuddy real demo ({provider.name})")
    storage.append_memory(
        "workspace",
        "- Real demo: model must use tools through the mini harness.",
    )

    system = (
        f"You are Mini WorkBuddy running in {session.cwd}. "
        "For this teaching demo, use tools instead of answering from memory. "
        "Call tool_search, bash, and read_file when useful. "
        "The harness records transcript, memory, tool results, and audit entries."
    )
    messages: list = [provider.initial_user_message(prompt)]
    storage.append_event(session, {"type": "message", "role": "user", "content": prompt})
    audit.append("user_prompt", {"sessionId": session.id, "text": prompt})

    print(f"Mode: real ({provider.name})")
    print("Model:", provider.model)
    print("Session:", session.id)
    print("Workspace:", session.cwd)
    print("Prompt:", prompt)

    final_text = ""
    for turn in range(1, max_turns + 1):
        print("\n" + "=" * 72)
        print(f"model turn {turn}")
        model_turn = provider.create(
            P.ProviderRequest(system=system, messages=messages, tools=spec, max_tokens=4000)
        )
        messages.append(model_turn.raw_assistant)

        if model_turn.text:
            final_text = model_turn.text
            print(model_turn.text)

        if not model_turn.wants_tools:
            storage.append_event(session, {"type": "message", "role": "assistant", "content": final_text})
            audit.append("assistant_message", {"sessionId": session.id, "content": final_text[:500]})
            break

        results = []
        for call in model_turn.tool_calls:
            argument = tool_argument(call.name, call.arguments)
            print(f"[tool_call] {call.name}: {argument}")
            audit.append("tool_call", {"sessionId": session.id, "tool": call.name, "argument": argument})
            result = tools.run(call.name, argument, session)
            storage.append_event(session, {"type": "tool_result", **asdict(result)})
            events.publish("session_update", {"sessionId": session.id, "type": "tool_result", **asdict(result)})
            audit.append("tool_result", {
                "sessionId": session.id,
                "tool": result.name,
                "externalized": result.externalized_path is not None,
                "exit_code": result.exit_code,
            })
            print(result.content[:1000])
            if result.externalized_path:
                print("Externalized:", result.externalized_path)
            results.append((call, result.content))

        messages.append(provider.format_tool_results(results))
    else:
        print("\nReached max turns before the model stopped calling tools.")

    transcript = storage.read_transcript(session)
    print("\n" + "=" * 72)
    print("Recovered state")
    print("Transcript:", storage.transcript_path(session))
    print("Transcript events:", len(transcript))
    print("Audit file:", audit.path)
    print("Audit entries:", len(audit.read_entries()))
    print("Audit verified:", audit.verify())


def tool_argument(tool_name: str, tool_input: dict) -> str:
    if tool_name == "bash":
        return str(tool_input.get("command", ""))
    if tool_name == "read_file":
        return str(tool_input.get("path", ""))
    if tool_name == "tool_search":
        return str(tool_input.get("query", ""))
    raise KeyError(f"unknown tool: {tool_name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini WorkBuddy demo")
    parser.add_argument(
        "--mode",
        choices=["auto", "offline", "real"],
        default="auto",
        help="auto uses real API when ANTHROPIC_API_KEY and MODEL_ID exist, otherwise offline",
    )
    parser.add_argument("--prompt", default=REAL_API_PROMPT, help="prompt for --mode real")
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument(
        "--provider",
        choices=["anthropic", "openai", "offline"],
        default=None,
        help="model backend for --mode real (default: PROVIDER env or auto-detect)",
    )
    args = parser.parse_args()

    if args.mode == "offline":
        print("Mode: offline deterministic harness")
        run_offline_demo()
        return
    if args.mode == "real":
        # Backward-compatible guard: `--mode real` with no explicit provider
        # and no Anthropic keys keeps the original clear error (a documented
        # contract). Use --provider openai/offline to take other paths.
        if args.provider is None and not os.getenv("PROVIDER") and not api_env_ready():
            raise SystemExit(
                "Real API demo requires ANTHROPIC_API_KEY and MODEL_ID. "
                "Copy .env.example to .env, fill them, then rerun with --mode real. "
                "(Or pick a backend explicitly: --provider openai | offline.)"
            )
        run_real_api_demo(args.prompt, args.max_turns, args.provider)
        return
    if api_env_ready():
        run_real_api_demo(args.prompt, args.max_turns, args.provider)
        return

    print("Mode: auto -> no ANTHROPIC_API_KEY/MODEL_ID found, running offline deterministic harness.")
    print("For the real API path: cp .env.example .env, fill it, then run:")
    print("  python3 examples/mini_workbuddy_demo/code.py --mode real")
    print("  (dual provider: add --provider openai to use the OpenAI Responses API)\n")
    run_offline_demo()


if __name__ == "__main__":
    main()
