"""Provider adapter — one harness loop, many model protocols.

Why this module exists
----------------------
`learn-workbuddy` teaches a *desktop agent harness*, not a single vendor's
API. The agent loop should not care whether the model speaks Anthropic's
`tool_use`/`tool_result` protocol or OpenAI's `function_call`/`tool`
protocol. This adapter normalizes both into one shape so the loop stays
identical across providers — which is itself a core harness lesson: the
loop is stable, the provider is swappable.

The normalized contract
-----------------------
Every provider takes a `ProviderRequest` (system prompt, running message
list, tool specs) and returns a `ModelTurn`:

    ModelTurn(
        text: str,                 # assistant prose, if any
        tool_calls: list[ToolCall],# normalized tool invocations
        raw_assistant: object,     # provider-native assistant turn, to append
    )

The loop then executes each ToolCall through the harness (permissions,
audit, externalization all unchanged) and hands results back via
`provider.format_tool_results(...)`, which re-encodes them in the
provider's native shape. The loop never touches provider-specific JSON.

Providers
---------
- AnthropicProvider          — messages API, tool_use / tool_result
- OpenAIResponsesProvider    — Responses API, function_call / function_call_output
- OfflineMockProvider        — deterministic, no network, for tests/offline demo

The offline provider is what lets CI and keyless readers exercise the
full loop. It is not a fake of any real model's answers; it is a scripted
tool-using agent that proves the harness plumbing works end to end.

Config (.env)
-------------
    PROVIDER=anthropic|openai|offline   (default: auto — see select_provider)
    ANTHROPIC_API_KEY, MODEL_ID         (anthropic)
    OPENAI_API_KEY, OPENAI_MODEL        (openai)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any


# --------------------------------------------------------------------------
# Normalized types — the only shapes the harness loop should touch.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    """Provider-neutral tool definition.

    Written once in this shape, then translated to each provider's schema.
    (Anthropic: {name, description, input_schema};
     OpenAI Responses: {type:function, name, description, parameters}.)
    """

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelTurn:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_assistant: Any = None  # provider-native, appended to the running messages

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class ProviderRequest:
    system: str
    messages: list[Any]
    tools: list[ToolSpec]
    max_tokens: int = 4000


class Provider:
    """Interface. Concrete providers implement these three methods."""

    name = "base"
    model = ""

    def create(self, request: ProviderRequest) -> ModelTurn:
        raise NotImplementedError

    def format_tool_results(self, results: list[tuple[ToolCall, str]]) -> Any:
        """Encode (call, output) pairs as one provider-native user turn."""
        raise NotImplementedError

    def initial_user_message(self, prompt: str) -> Any:
        """The provider-native shape for the first user message."""
        return {"role": "user", "content": prompt}


# --------------------------------------------------------------------------
# Anthropic — tool_use / tool_result
# --------------------------------------------------------------------------


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, model: str | None = None, base_url: str | None = None) -> None:
        from anthropic import Anthropic  # lazy: only real use needs the SDK

        self.model = model or os.environ["MODEL_ID"]
        self._client = Anthropic(base_url=base_url or os.getenv("ANTHROPIC_BASE_URL"))

    def _tools(self, tools: list[ToolSpec]) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in tools
        ]

    def create(self, request: ProviderRequest) -> ModelTurn:
        response = self._client.messages.create(
            model=self.model,
            system=request.system,
            messages=request.messages,
            tools=self._tools(request.tools),
            max_tokens=request.max_tokens,
        )
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
            elif getattr(block, "type", None) == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
        return ModelTurn(
            text="".join(text_parts),
            tool_calls=calls,
            raw_assistant={"role": "assistant", "content": response.content},
        )

    def format_tool_results(self, results: list[tuple[ToolCall, str]]) -> Any:
        return {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": call.id, "content": output}
                for call, output in results
            ],
        }


# --------------------------------------------------------------------------
# OpenAI — Responses API, function_call / function_call_output
# --------------------------------------------------------------------------


class OpenAIResponsesProvider(Provider):
    """Uses the OpenAI Responses API (direct model requests + tool use).

    Design note for readers: OpenAI positions the Responses API as the
    interface to use when *you* own the agent loop (as we do here). If you
    instead wanted the SDK to own turns/tools/handoffs/sessions, you'd
    reach for the Agents SDK — that's a separate, higher-level abstraction
    and deliberately not what this teaching harness models.
    """

    name = "openai"

    def __init__(self, model: str | None = None) -> None:
        from openai import OpenAI  # lazy

        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4.1")
        self._client = OpenAI()

    def _tools(self, tools: list[ToolSpec]) -> list[dict]:
        return [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in tools
        ]

    def initial_user_message(self, prompt: str) -> Any:
        # Responses API uses a flat input list of typed items.
        return {"role": "user", "content": prompt}

    def create(self, request: ProviderRequest) -> ModelTurn:
        response = self._client.responses.create(
            model=self.model,
            instructions=request.system,
            input=request.messages,
            tools=self._tools(request.tools),
            max_output_tokens=request.max_tokens,
        )
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        raw_items: list[Any] = []
        for item in response.output:
            raw_items.append(item)
            itype = getattr(item, "type", None)
            if itype == "function_call":
                calls.append(
                    ToolCall(
                        id=item.call_id,
                        name=item.name,
                        arguments=json.loads(item.arguments or "{}"),
                    )
                )
            elif itype == "message":
                for part in getattr(item, "content", []) or []:
                    if getattr(part, "type", None) in ("output_text", "text"):
                        text_parts.append(getattr(part, "text", ""))
        return ModelTurn(text="".join(text_parts), tool_calls=calls, raw_assistant=raw_items)

    def format_tool_results(self, results: list[tuple[ToolCall, str]]) -> Any:
        # Each result is its own typed input item referencing the call_id.
        return [
            {"type": "function_call_output", "call_id": call.id, "output": output}
            for call, output in results
        ]


# --------------------------------------------------------------------------
# Offline mock — deterministic, no network. Powers CI and keyless demo.
# --------------------------------------------------------------------------


class OfflineMockProvider(Provider):
    """A scripted tool-using agent. No model, no network, fully deterministic.

    It walks a fixed plan (list tools -> pwd -> read README -> summarize) so
    the *harness* (permissions, audit, transcript, externalization) is
    exercised end to end without any provider dependency. This is the
    provider the offline demo and the loop unit tests use.
    """

    name = "offline"
    model = "offline-mock"

    def __init__(self) -> None:
        self._step = 0

    def initial_user_message(self, prompt: str) -> Any:
        return {"role": "user", "content": prompt}

    def create(self, request: ProviderRequest) -> ModelTurn:
        plan = [
            ToolCall(id="call_1", name="tool_search", arguments={"query": ""}),
            ToolCall(id="call_2", name="bash", arguments={"command": "pwd"}),
            ToolCall(id="call_3", name="read_file", arguments={"path": "README.md"}),
        ]
        if self._step < len(plan):
            call = plan[self._step]
            self._step += 1
            # Only emit a tool the caller actually offered.
            offered = {t.name for t in request.tools}
            if call.name in offered:
                return ModelTurn(text="", tool_calls=[call], raw_assistant={"mock_step": self._step})
        return ModelTurn(
            text=(
                "Offline mock summary: the harness routed tool calls through "
                "permissions and audit, recorded a transcript, and externalized "
                "large output when needed. No real model was called."
            ),
            tool_calls=[],
            raw_assistant={"mock_step": self._step},
        )

    def format_tool_results(self, results: list[tuple[ToolCall, str]]) -> Any:
        # The mock doesn't consume results; return a benign record so the
        # running message list stays well-formed.
        return {"role": "user", "content": [{"mock_tool_results": len(results)}]}


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


def normalized_tools() -> list[ToolSpec]:
    """The three demo tools, defined once, provider-neutral."""
    return [
        ToolSpec("tool_search", "List or search available harness tools.",
                 {"type": "object", "properties": {"query": {"type": "string"}}}),
        ToolSpec("bash", "Run a shell command in the session cwd. Dangerous commands are denied.",
                 {"type": "object", "properties": {"command": {"type": "string"}},
                  "required": ["command"]}),
        ToolSpec("read_file", "Read a UTF-8 text file by cwd-relative path.",
                 {"type": "object", "properties": {"path": {"type": "string"}},
                  "required": ["path"]}),
    ]


def provider_env_ready(provider: str) -> bool:
    if provider == "anthropic":
        return bool(os.getenv("ANTHROPIC_API_KEY") and os.getenv("MODEL_ID"))
    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    if provider == "offline":
        return True
    return False


def select_provider(explicit: str | None = None) -> Provider:
    """Resolve which provider to use.

    Priority: explicit arg > PROVIDER env > auto-detect by available keys.
    Auto-detect falls back to offline so the demo always runs.
    """
    choice = (explicit or os.getenv("PROVIDER") or "auto").lower()

    if choice == "auto":
        if provider_env_ready("anthropic"):
            choice = "anthropic"
        elif provider_env_ready("openai"):
            choice = "openai"
        else:
            choice = "offline"

    if choice == "anthropic":
        if not provider_env_ready("anthropic"):
            raise SystemExit(
                "PROVIDER=anthropic requires ANTHROPIC_API_KEY and MODEL_ID. "
                "Copy .env.example to .env and fill them, or use PROVIDER=offline."
            )
        return AnthropicProvider()
    if choice == "openai":
        if not provider_env_ready("openai"):
            raise SystemExit(
                "PROVIDER=openai requires OPENAI_API_KEY (and optionally OPENAI_MODEL). "
                "Copy .env.example to .env and fill them, or use PROVIDER=offline."
            )
        return OpenAIResponsesProvider()
    if choice == "offline":
        return OfflineMockProvider()
    raise SystemExit(f"Unknown PROVIDER '{choice}'. Use anthropic | openai | offline.")
