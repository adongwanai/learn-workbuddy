"""Tests for the dual-provider adapter (mini_workbuddy/providers.py).

Coverage:
- select_provider resolves explicit > env > auto correctly, and fails
  loudly (SystemExit, not a raw KeyError) when a named provider's keys
  are missing.
- The offline mock provider drives a full tool-using loop through the
  real harness (permissions, audit, transcript) with no network.
- Normalized ToolSpec translates into each provider's native schema shape.

The real Anthropic/OpenAI providers are import-guarded and network-bound,
so they are exercised only by the opt-in real smoke script, never in CI.
"""

from __future__ import annotations

import os
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from mini_workbuddy import providers as P
from mini_workbuddy.audit import AuditLog
from mini_workbuddy.config import HarnessConfig
from mini_workbuddy.events import EventBus
from mini_workbuddy.storage import Storage
from mini_workbuddy.tools import ToolRegistry


# -- selection logic ---------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_provider_env(monkeypatch):
    for key in ["PROVIDER", "ANTHROPIC_API_KEY", "MODEL_ID", "OPENAI_API_KEY", "OPENAI_MODEL"]:
        monkeypatch.delenv(key, raising=False)
    yield


def test_auto_falls_back_to_offline_without_keys():
    provider = P.select_provider()
    assert provider.name == "offline"


def test_explicit_offline_always_works():
    assert P.select_provider("offline").name == "offline"


def test_auto_prefers_anthropic_when_keys_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("MODEL_ID", "claude-test")
    # Don't actually construct the SDK client; just check the routing decision.
    assert P.provider_env_ready("anthropic") is True
    monkeypatch.setattr(P.AnthropicProvider, "__init__", lambda self: setattr(self, "model", "claude-test"))
    assert P.select_provider().name == "anthropic"


def test_auto_uses_openai_when_only_openai_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setattr(P.OpenAIResponsesProvider, "__init__", lambda self: setattr(self, "model", "gpt-test"))
    assert P.select_provider().name == "openai"


def test_named_provider_without_keys_exits_cleanly(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        P.select_provider("anthropic")
    assert "ANTHROPIC_API_KEY" in str(exc.value)
    with pytest.raises(SystemExit) as exc2:
        P.select_provider("openai")
    assert "OPENAI_API_KEY" in str(exc2.value)


def test_unknown_provider_exits():
    with pytest.raises(SystemExit):
        P.select_provider("llama-at-home")


# -- normalized tool translation --------------------------------------------

def test_anthropic_tool_schema_shape(monkeypatch):
    monkeypatch.setattr(P.AnthropicProvider, "__init__", lambda self: None)
    prov = P.AnthropicProvider.__new__(P.AnthropicProvider)
    tools = prov._tools(P.normalized_tools())
    assert tools[0].keys() == {"name", "description", "input_schema"}
    assert tools[1]["name"] == "bash"


def test_openai_tool_schema_shape(monkeypatch):
    prov = P.OpenAIResponsesProvider.__new__(P.OpenAIResponsesProvider)
    tools = prov._tools(P.normalized_tools())
    assert tools[0]["type"] == "function"
    assert tools[0].keys() == {"type", "name", "description", "parameters"}


def test_result_formatting_differs_by_provider(monkeypatch):
    call = P.ToolCall(id="c1", name="bash", arguments={"command": "pwd"})
    anth = P.AnthropicProvider.__new__(P.AnthropicProvider)
    a_out = anth.format_tool_results([(call, "/tmp")])
    assert a_out["content"][0]["type"] == "tool_result"
    assert a_out["content"][0]["tool_use_id"] == "c1"

    oai = P.OpenAIResponsesProvider.__new__(P.OpenAIResponsesProvider)
    o_out = oai.format_tool_results([(call, "/tmp")])
    assert o_out[0]["type"] == "function_call_output"
    assert o_out[0]["call_id"] == "c1"


def test_anthropic_provider_parses_text_and_tool_use_blocks():
    class FakeMessages:
        def create(self, **kwargs):
            return SimpleNamespace(content=[
                SimpleNamespace(type="text", text="I will inspect."),
                SimpleNamespace(
                    type="tool_use",
                    id="toolu_1",
                    name="bash",
                    input={"command": "pwd"},
                ),
            ])

    prov = P.AnthropicProvider.__new__(P.AnthropicProvider)
    prov.model = "claude-test"
    prov._client = SimpleNamespace(messages=FakeMessages())

    turn = prov.create(P.ProviderRequest(system="sys", messages=[], tools=P.normalized_tools()))
    assert turn.text == "I will inspect."
    assert turn.tool_calls == [
        P.ToolCall(id="toolu_1", name="bash", arguments={"command": "pwd"})
    ]
    assert turn.raw_assistant["role"] == "assistant"


def test_openai_provider_parses_message_reasoning_and_function_call_items():
    class FakeResponses:
        def create(self, **kwargs):
            return SimpleNamespace(output=[
                SimpleNamespace(type="reasoning", summary=[]),
                SimpleNamespace(
                    type="message",
                    content=[
                        SimpleNamespace(type="output_text", text="Checking."),
                    ],
                ),
                SimpleNamespace(
                    type="function_call",
                    call_id="call_1",
                    name="read_file",
                    arguments=json.dumps({"path": "README.md"}),
                ),
            ])

    prov = P.OpenAIResponsesProvider.__new__(P.OpenAIResponsesProvider)
    prov.model = "gpt-test"
    prov._client = SimpleNamespace(responses=FakeResponses())

    turn = prov.create(P.ProviderRequest(system="sys", messages=[], tools=P.normalized_tools()))
    assert turn.text == "Checking."
    assert turn.tool_calls == [
        P.ToolCall(id="call_1", name="read_file", arguments={"path": "README.md"})
    ]
    assert len(turn.raw_assistant) == 3


def test_openai_provider_handles_empty_output_without_crashing():
    class FakeResponses:
        def create(self, **kwargs):
            return SimpleNamespace(output=[])

    prov = P.OpenAIResponsesProvider.__new__(P.OpenAIResponsesProvider)
    prov.model = "gpt-test"
    prov._client = SimpleNamespace(responses=FakeResponses())

    turn = prov.create(P.ProviderRequest(system="sys", messages=[], tools=P.normalized_tools()))
    assert turn.text == ""
    assert turn.tool_calls == []


# -- offline provider drives a full loop through the real harness -----------

def _run_offline_loop(tmp_path: Path, max_turns: int = 6):
    config = HarnessConfig(root_dir=tmp_path / "home")
    config.ensure_dirs()
    storage = Storage(config)
    tools = ToolRegistry(config, storage)
    audit = AuditLog(config)
    session = storage.create_session(cwd=str(tmp_path))
    (Path(tmp_path) / "README.md").write_text("# demo\nhello\n", encoding="utf-8")

    provider = P.OfflineMockProvider()
    spec = P.normalized_tools()
    messages = [provider.initial_user_message("inspect this project")]
    executed: list[str] = []

    for _ in range(max_turns):
        turn = provider.create(P.ProviderRequest(system="sys", messages=messages, tools=spec))
        messages.append(turn.raw_assistant)
        if not turn.wants_tools:
            return turn.text, executed, audit
        results = []
        for call in turn.tool_calls:
            audit.append("tool_call", {"tool": call.name})
            out = tools.run(call.name, _arg_for(call), session)
            executed.append(call.name)
            results.append((call, out.content))
        messages.append(provider.format_tool_results(results))
    raise AssertionError("offline loop did not terminate")


def _arg_for(call: P.ToolCall) -> str:
    # Map normalized args to the mini ToolRegistry's single-string argument.
    if call.name == "bash":
        return call.arguments.get("command", "")
    if call.name == "read_file":
        return call.arguments.get("path", "")
    return call.arguments.get("query", "")


def test_offline_provider_runs_full_loop_through_harness(tmp_path):
    final_text, executed, audit = _run_offline_loop(tmp_path)
    assert executed == ["tool_search", "bash", "read_file"]
    assert "no real model" in final_text.lower()
    assert audit.verify() is True


def test_offline_provider_only_calls_offered_tools(tmp_path):
    # If a tool isn't offered, the mock must not invent a call for it.
    provider = P.OfflineMockProvider()
    only_bash = [P.normalized_tools()[1]]  # bash only
    turn = provider.create(P.ProviderRequest(system="s", messages=[], tools=only_bash))
    # First planned step is tool_search, which isn't offered -> no tool call.
    assert turn.tool_calls == [] or turn.tool_calls[0].name in {"bash"}
