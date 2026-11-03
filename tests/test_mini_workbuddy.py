from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mini_workbuddy.audit import AuditCorruptionError, AuditLog, GENESIS_HASH
from mini_workbuddy.agent import MiniAgent
from mini_workbuddy.config import HarnessConfig, workspace_id
from mini_workbuddy.events import Event, EventBus
from mini_workbuddy.server import HarnessRuntime
from mini_workbuddy.storage import (
    Storage,
    TranscriptCorruptionError,
    TranscriptValidationError,
)
from mini_workbuddy.tools import PermissionError, ToolRegistry


def build_runtime(home: Path, cwd: Path, threshold_kb: int = 50):
    config = HarnessConfig(root_dir=home, tool_result_threshold=threshold_kb * 1024)
    storage = Storage(config)
    events = EventBus()
    tools = ToolRegistry(config, storage)
    agent = MiniAgent(storage, tools, events)
    session = storage.create_session(str(cwd), "pytest session")
    return config, storage, events, tools, agent, session


def test_workspace_id_is_stable_and_path_safe() -> None:
    assert workspace_id("/") == "root"
    assert workspace_id("/tmp/my project") == "tmp-my_project"
    assert "/" not in workspace_id("/tmp/my project")


def test_storage_appends_and_recovers_transcript(tmp_path: Path) -> None:
    config = HarnessConfig(root_dir=tmp_path / "home")
    storage = Storage(config)
    session = storage.create_session(str(tmp_path), "storage")

    first = storage.append_event(
        session,
        {"type": "message", "role": "user", "content": "hello"},
    )
    second = storage.append_event(
        session,
        {"type": "message", "role": "assistant", "content": "world"},
    )

    transcript = storage.read_transcript(session)
    assert [event["content"] for event in transcript] == ["hello", "world"]
    assert [event["sequence"] for event in transcript] == [1, 2]
    assert [event["event_id"] for event in transcript] == [
        f"transcript:{session.id}:1",
        f"transcript:{session.id}:2",
    ]
    assert all(event["schema_version"] == 1 for event in transcript)
    assert first == transcript[0]
    assert second == transcript[1]
    assert storage.transcript_path(session).exists()
    assert storage.load_session(session.id).title == "storage"
    assert storage.list_sessions()[0].id == session.id


def test_storage_rejects_spoofed_transcript_envelope(tmp_path: Path) -> None:
    storage = Storage(HarnessConfig(root_dir=tmp_path / "home"))
    session = storage.create_session(str(tmp_path), "storage")

    with pytest.raises(TranscriptValidationError, match="reserved envelope"):
        storage.append_event(
            session,
            {
                "type": "message",
                "content": "spoofed",
                "event_id": "transcript:other-session:99",
            },
        )

    assert storage.read_transcript(session) == []


def test_storage_recovers_unterminated_tail_before_next_append(
    tmp_path: Path,
) -> None:
    config = HarnessConfig(root_dir=tmp_path / "home")
    storage = Storage(config)
    session = storage.create_session(str(tmp_path), "storage")
    storage.append_event(session, {"type": "message", "content": "before"})

    with storage.transcript_path(session).open("ab") as handle:
        handle.write(b'{"type":')

    assert [event["content"] for event in storage.read_transcript(session)] == [
        "before"
    ]

    restarted = Storage(config)
    restarted.append_event(session, {"type": "message", "content": "after"})
    transcript = restarted.read_transcript(session)

    assert [event["content"] for event in transcript] == ["before", "after"]
    assert [event["sequence"] for event in transcript] == [1, 2]
    assert len(storage.transcript_path(session).read_text().splitlines()) == 2


def test_storage_rejects_complete_corrupt_transcript_record(
    tmp_path: Path,
) -> None:
    storage = Storage(HarnessConfig(root_dir=tmp_path / "home"))
    session = storage.create_session(str(tmp_path), "storage")
    storage.append_event(session, {"type": "message", "content": "safe"})

    with storage.transcript_path(session).open("a", encoding="utf-8") as handle:
        handle.write("{broken}\n")

    with pytest.raises(TranscriptCorruptionError, match="invalid complete JSON"):
        storage.read_transcript(session)
    with pytest.raises(TranscriptCorruptionError, match="invalid complete JSON"):
        storage.append_event(session, {"type": "message", "content": "unsafe"})


def test_storage_serializes_concurrent_session_appends(tmp_path: Path) -> None:
    config = HarnessConfig(root_dir=tmp_path / "home")
    storages = [Storage(config) for _ in range(4)]
    session = storages[0].create_session(str(tmp_path), "storage")

    def append(index: int) -> None:
        storages[index % len(storages)].append_event(
            session,
            {"type": "message", "content": str(index)},
        )

    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(append, range(100)))

    transcript = storages[0].read_transcript(session)
    assert len(transcript) == 100
    assert [event["sequence"] for event in transcript] == list(range(1, 101))
    assert {event["content"] for event in transcript} == {
        str(index) for index in range(100)
    }


def test_storage_appends_and_reads_memory(tmp_path: Path) -> None:
    config = HarnessConfig(root_dir=tmp_path / "home")
    storage = Storage(config)

    path = storage.append_memory("workspace", "- prefer small verified steps")

    assert path.exists()
    assert "small verified steps" in storage.read_memory("workspace")


def test_storage_memory_scopes_do_not_alias_or_escape_the_store(tmp_path: Path) -> None:
    storage = Storage(HarnessConfig(root_dir=tmp_path / "home"))
    scopes = [
        "team/a",
        "team_a",
        "Team_a",
        "team a",
        r"..\outside",
        "../outside",
        "CON",
        "con.txt",
    ]

    paths = {
        scope: storage.append_memory(scope, f"only-{index}")
        for index, scope in enumerate(scopes)
    }

    assert len(set(paths.values())) == len(scopes)
    assert all(path.parent == storage.config.memory_dir for path in paths.values())
    for index, scope in enumerate(scopes):
        assert storage.read_memory(scope) == f"only-{index}\n"


def test_audit_log_verifies_hash_chain_and_detects_tampering(tmp_path: Path) -> None:
    audit = AuditLog(HarnessConfig(root_dir=tmp_path / "home"))

    audit.append("tool_call", {"tool": "bash", "argument": "pwd"})
    audit.append("tool_result", {"tool": "bash", "exit_code": 0})

    assert audit.verify() is True

    text = audit.path.read_text(encoding="utf-8")
    audit.path.write_text(text.replace("pwd", "whoami"), encoding="utf-8")

    assert audit.verify() is False


def test_audit_log_detects_corrupt_trailing_line(tmp_path: Path) -> None:
    audit = AuditLog(HarnessConfig(root_dir=tmp_path / "home"))
    audit.append("tool_call", {"tool": "bash", "argument": "pwd"})

    with audit.path.open("a", encoding="utf-8") as fh:
        fh.write("{not-json\n")

    assert audit.verify() is False


def test_audit_log_serializes_concurrent_appends_across_instances(
    tmp_path: Path,
) -> None:
    config = HarnessConfig(root_dir=tmp_path / "home")
    audits = [AuditLog(config) for _ in range(4)]

    def append(index: int) -> None:
        audits[index % len(audits)].append("tool_call", {"request": index})

    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(append, range(100)))

    entries = audits[0].read_entries()
    assert len(entries) == 100
    assert [entry.index for entry in entries] == list(range(1, 101))
    assert len({entry.hash for entry in entries}) == 100
    assert {entry.data["request"] for entry in entries} == set(range(100))
    assert audits[0].verify() is True


def test_audit_log_serializes_appends_across_processes(tmp_path: Path) -> None:
    root_dir = tmp_path / "home"
    child_script = "\n".join(
        [
            "import sys",
            "from pathlib import Path",
            "from mini_workbuddy.audit import AuditLog",
            "from mini_workbuddy.config import HarnessConfig",
            "audit = AuditLog(HarnessConfig(root_dir=Path(sys.argv[1])))",
            "worker = int(sys.argv[2])",
            "for sequence in range(10):",
            "    audit.append('tool_call', {'worker': worker, 'sequence': sequence})",
        ]
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", child_script, str(root_dir), str(worker)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for worker in range(4)
    ]

    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stdout + stderr

    audit = AuditLog(HarnessConfig(root_dir=root_dir))
    entries = audit.read_entries()
    assert len(entries) == 40
    assert [entry.index for entry in entries] == list(range(1, 41))
    assert {
        (entry.data["worker"], entry.data["sequence"]) for entry in entries
    } == {(worker, sequence) for worker in range(4) for sequence in range(10)}
    assert audit.verify() is True


def test_audit_log_refuses_to_extend_a_corrupt_chain(tmp_path: Path) -> None:
    audit = AuditLog(HarnessConfig(root_dir=tmp_path / "home"))
    audit.append("tool_call", {"tool": "bash", "argument": "pwd"})
    original_head = audit.head_path.read_bytes()

    tampered = audit.path.read_text(encoding="utf-8").replace("pwd", "whoami")
    audit.path.write_text(tampered, encoding="utf-8")
    original_log = audit.path.read_bytes()

    with pytest.raises(AuditCorruptionError, match="hash is invalid"):
        audit.append("tool_result", {"exit_code": 0})
    with pytest.raises(AuditCorruptionError, match="hash is invalid"):
        audit.recover_interrupted_append()

    assert audit.path.read_bytes() == original_log
    assert audit.head_path.read_bytes() == original_head


def test_runtime_recovers_one_entry_after_log_fsync_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = HarnessConfig(root_dir=tmp_path / "home")
    audit = AuditLog(config)
    audit.append("before_crash", {"step": 1})

    def crash_before_head_replace(count: int, head: str) -> None:
        raise RuntimeError("simulated crash after audit.jsonl fsync")

    monkeypatch.setattr(audit, "_replace_head_unlocked", crash_before_head_replace)
    with pytest.raises(RuntimeError, match="after audit.jsonl fsync"):
        audit.append("crossed_fsync", {"step": 2})

    restarted = AuditLog(config)
    assert len(restarted.read_entries()) == 2
    assert restarted.verify() is False

    runtime = HarnessRuntime(config)
    assert runtime.audit_recovered is True
    assert runtime.audit.verify() is True
    assert [entry.action for entry in runtime.audit.read_entries()] == [
        "before_crash",
        "crossed_fsync",
    ]
    runtime.audit.append("after_restart", {"step": 3})
    assert runtime.audit.verify() is True

    second_restart = HarnessRuntime(config)
    assert second_restart.audit_recovered is False
    assert second_restart.audit.verify() is True
    assert len(second_restart.audit.read_entries()) == 3


def test_audit_recovery_rejects_more_than_one_unanchored_entry(
    tmp_path: Path,
) -> None:
    config = HarnessConfig(root_dir=tmp_path / "home")
    audit = AuditLog(config)
    for step in range(1, 4):
        audit.append("tool_call", {"step": step})
    entries = audit.read_entries()
    stale_anchor = json.dumps(
        {"count": 1, "head": entries[0].hash},
        sort_keys=True,
    ) + "\n"
    audit.head_path.write_text(stale_anchor, encoding="utf-8")

    with pytest.raises(AuditCorruptionError, match="exactly one unanchored entry"):
        audit.recover_interrupted_append()
    with pytest.raises(AuditCorruptionError, match="exactly one unanchored entry"):
        HarnessRuntime(config)

    assert audit.head_path.read_text(encoding="utf-8") == stale_anchor

    mismatched_anchor = json.dumps(
        {"count": 2, "head": GENESIS_HASH},
        sort_keys=True,
    ) + "\n"
    audit.head_path.write_text(mismatched_anchor, encoding="utf-8")
    with pytest.raises(AuditCorruptionError, match="penultimate entry"):
        audit.recover_interrupted_append()

    assert audit.head_path.read_text(encoding="utf-8") == mismatched_anchor


def test_tool_search_lists_and_filters_tools(tmp_path: Path) -> None:
    _, _, _, tools, _, session = build_runtime(tmp_path / "home", tmp_path)

    all_tools = tools.run("tool_search", "", session).content
    filtered = tools.run("tool_search", "read", session).content

    assert "bash" in all_tools
    assert "read_file" in all_tools
    assert "read_file" in filtered
    assert "tool_search" not in filtered


def test_read_file_supports_relative_session_paths(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello mini harness", encoding="utf-8")
    _, _, _, tools, _, session = build_runtime(tmp_path / "home", tmp_path)

    result = tools.run("read_file", "README.md", session)

    assert result.name == "read_file"
    assert result.content == "hello mini harness"


def test_read_file_denies_paths_outside_session_cwd(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("do not read", encoding="utf-8")
    _, _, _, tools, _, session = build_runtime(tmp_path / "home", workspace)

    with pytest.raises(PermissionError):
        tools.run("read_file", str(outside), session)

    with pytest.raises(PermissionError):
        tools.run("read_file", "../secret.txt", session)


def test_read_file_denies_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("do not read", encoding="utf-8")
    link = workspace / "linked-secret.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available on this filesystem")
    _, _, _, tools, _, session = build_runtime(tmp_path / "home", workspace)

    with pytest.raises(PermissionError):
        tools.run("read_file", "linked-secret.txt", session)


def test_bash_denies_dangerous_commands(tmp_path: Path) -> None:
    _, _, _, tools, _, session = build_runtime(tmp_path / "home", tmp_path)

    with pytest.raises(PermissionError):
        tools.run("bash", "rm -rf .", session)


def test_bash_uses_workspace_scoped_environment(monkeypatch, tmp_path: Path) -> None:
    """Tool commands must not inherit ambient credentials from the harness."""

    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/private-agent.sock")
    monkeypatch.setenv("WORKBUDDY_TEST_SECRET", "parent-only")
    _, _, _, tools, _, session = build_runtime(tmp_path / "home", tmp_path)
    command = (
        "printf '%s\\n%s\\n%s\\n%s\\n%s\\n' "
        '"${OPENAI_API_KEY-unset}" "${ANTHROPIC_API_KEY-unset}" '
        '"${SSH_AUTH_SOCK-unset}" "${WORKBUDDY_TEST_SECRET-unset}" '
        '"$HOME|$PWD|${PATH:+set}"'
    )

    result = tools.run("bash", command, session)

    assert result.content.splitlines() == [
        "unset",
        "unset",
        "unset",
        "unset",
        f"{tmp_path.resolve()}|{tmp_path.resolve()}|set",
    ]


def test_large_tool_output_externalizes_to_disk(tmp_path: Path) -> None:
    _, storage, _, tools, _, session = build_runtime(tmp_path / "home", tmp_path, threshold_kb=1)
    command = "python3 -c \"print('x' * 5000)\""

    result = tools.run("bash", command, session)

    assert result.externalized_path is not None
    externalized = Path(result.externalized_path)
    assert externalized.exists()
    assert externalized.read_text(encoding="utf-8").startswith("x")
    assert "Full output written to:" in result.content
    assert externalized.parent == storage.tool_result_path(session, result.tool_call_id).parent


def test_tool_call_ids_are_unique_under_fast_repeated_calls(tmp_path: Path) -> None:
    _, _, _, tools, _, session = build_runtime(tmp_path / "home", tmp_path)

    ids = {tools.run("tool_search", "", session).tool_call_id for _ in range(200)}

    assert len(ids) == 200


def test_tool_registry_rejects_untrusted_call_id(tmp_path: Path) -> None:
    _, _, _, tools, _, session = build_runtime(tmp_path / "home", tmp_path)

    with pytest.raises(ValueError, match="registry format"):
        tools.run(
            "tool_search",
            "",
            session,
            tool_call_id="../tool-result",
        )


def test_agent_records_correlated_tool_lifecycle_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, storage, events, _, agent, session = build_runtime(
        tmp_path / "home",
        tmp_path,
    )
    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        events,
        "publish",
        lambda name, data: published.append((name, data)),
    )

    result = agent.prompt(session, "pwd")
    transcript = storage.read_transcript(session)

    assert result["toolResults"][0]["name"] == "bash"
    assert [event["type"] for event in transcript] == [
        "message",
        "tool_call",
        "tool_result",
        "message",
    ]
    assert transcript[0]["role"] == "user"
    assert transcript[-1]["role"] == "assistant"
    assert transcript[1]["tool_call_id"] == transcript[2]["tool_call_id"]

    updates = [data for name, data in published if name == "session_update"]
    assert [event["eventId"] for event in updates] == [
        event["event_id"] for event in transcript
    ]
    assert [event["sequence"] for event in updates] == [1, 2, 3, 4]
    assert updates[1]["tool_call_id"] == updates[2]["tool_call_id"]


def test_agent_persists_tool_error_with_the_reserved_call_id(
    tmp_path: Path,
) -> None:
    _, storage, _, _, agent, session = build_runtime(
        tmp_path / "home",
        tmp_path,
    )

    result = agent.prompt(session, "bash rm -rf .")
    transcript = storage.read_transcript(session)

    assert result["toolResults"] == []
    assert result["answer"].startswith("Tool failed:")
    assert [event["type"] for event in transcript] == [
        "message",
        "tool_call",
        "tool_error",
        "message",
    ]
    assert transcript[1]["tool_call_id"] == transcript[2]["tool_call_id"]
    assert transcript[2]["tool"] == "bash"
    assert "denied" in transcript[2]["error"]


def test_agent_writes_audit_entries_when_enabled(tmp_path: Path) -> None:
    config = HarnessConfig(root_dir=tmp_path / "home")
    storage = Storage(config)
    events = EventBus()
    tools = ToolRegistry(config, storage)
    audit = AuditLog(config)
    agent = MiniAgent(storage, tools, events, audit)
    session = storage.create_session(str(tmp_path), "audit session")

    agent.prompt(session, "pwd")

    transcript = storage.read_transcript(session)
    entries = audit.read_entries()
    actions = [entry.action for entry in entries]
    assert actions == ["user_prompt", "tool_call", "tool_result", "assistant_message"]
    assert [entry.data["transcriptEventId"] for entry in entries] == [
        event["event_id"] for event in transcript
    ]
    assert entries[1].data["toolCallId"] == entries[2].data["toolCallId"]
    assert entries[1].data["toolCallId"] == transcript[1]["tool_call_id"]
    assert audit.verify() is True


@pytest.mark.parametrize("failure_stage", ["transcript", "after_append", "publish", "audit"])
@pytest.mark.parametrize("error_type", [OSError, KeyError])
def test_agent_does_not_report_evidence_failure_as_tool_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
    error_type: type[Exception],
) -> None:
    config, storage, events, tools, agent, session = build_runtime(
        tmp_path / "home", tmp_path,
    )
    audit = AuditLog(config)
    agent.audit = audit
    failure = error_type(f"injected {failure_stage} failure")
    original_run = tools.run
    original_append = storage.append_event
    original_audit = audit.append
    run_count = 0
    published: list[dict] = []

    def run(*args, **kwargs):
        nonlocal run_count
        run_count += 1
        return original_run(*args, **kwargs)

    def append(record, event):
        if event["type"] == "tool_result" and failure_stage == "transcript":
            raise failure
        persisted = original_append(record, event)
        # Model a write that became durable before its caller saw an error.
        # The harness cannot infer rollback from an append exception.
        if event["type"] == "tool_result" and failure_stage == "after_append":
            raise failure
        return persisted

    def publish(name, data):
        if data.get("type") == "tool_result" and failure_stage == "publish":
            raise failure
        published.append(data)

    def append_audit(action, data):
        if action == "tool_result" and failure_stage == "audit":
            raise failure
        return original_audit(action, data)

    monkeypatch.setattr(tools, "run", run)
    monkeypatch.setattr(storage, "append_event", append)
    monkeypatch.setattr(events, "publish", publish)
    monkeypatch.setattr(audit, "append", append_audit)

    with pytest.raises(error_type) as caught:
        agent.prompt(session, "tools")

    assert caught.value is failure
    assert run_count == 1
    transcript = storage.read_transcript(session)
    expected_types = ["message", "tool_call"]
    if failure_stage != "transcript":
        expected_types.append("tool_result")
        assert transcript[1]["tool_call_id"] == transcript[2]["tool_call_id"]
    assert [event["type"] for event in transcript] == expected_types
    assert not any(event.get("type") == "tool_error" for event in published)
    assert not any(event.get("role") == "assistant" for event in published)
    assert not any(entry.action == "tool_error" for entry in audit.read_entries())


@pytest.mark.parametrize("error_type", [PermissionError, OSError, KeyError, TimeoutError])
def test_agent_still_records_tool_execution_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    config, storage, events, tools, agent, session = build_runtime(
        tmp_path / "home", tmp_path,
    )
    audit = AuditLog(config)
    agent.audit = audit
    run_count = 0
    published: list[dict] = []

    def fail_tool(*args, **kwargs):
        nonlocal run_count
        run_count += 1
        raise error_type("injected tool execution failure")

    monkeypatch.setattr(tools, "run", fail_tool)
    monkeypatch.setattr(events, "publish", lambda name, data: published.append(data))

    result = agent.prompt(session, "tools")

    assert run_count == 1
    assert result["toolResults"] == []
    assert result["answer"].startswith("Tool failed:")
    transcript = storage.read_transcript(session)
    assert [event["type"] for event in transcript] == [
        "message", "tool_call", "tool_error", "message",
    ]
    call_id = transcript[1]["tool_call_id"]
    assert transcript[2]["tool_call_id"] == call_id
    assert published[2]["type"] == "tool_error"
    assert published[2]["tool_call_id"] == call_id
    entries = audit.read_entries()
    assert entries[2].action == "tool_error"
    assert entries[2].data["toolCallId"] == call_id
    assert audit.verify() is True


def test_agent_unknown_prompt_returns_help_without_tool_call(tmp_path: Path) -> None:
    _, storage, _, _, agent, session = build_runtime(tmp_path / "home", tmp_path)

    result = agent.prompt(session, "hello?")
    transcript = storage.read_transcript(session)

    assert result["toolResults"] == []
    assert "Try:" in result["answer"]
    assert [event["type"] for event in transcript] == ["message", "message"]


def test_event_to_sse_is_valid_event_stream() -> None:
    event = Event("session_update", {"content": "hello"})

    payload = event.to_sse().decode("utf-8")

    assert payload.startswith("event: session_update\n")
    assert 'data: {"content": "hello"}' in payload


def test_event_bus_delivers_to_active_subscriber() -> None:
    bus = EventBus()
    delivered: queue.Queue[Event] = queue.Queue()

    def consume_one() -> None:
        subscriber = bus.subscribe()
        try:
            delivered.put(next(subscriber))
        finally:
            subscriber.close()

    thread = threading.Thread(target=consume_one)
    thread.start()
    deadline = time.time() + 2
    while not bus._subscribers and time.time() < deadline:
        time.sleep(0.01)
    assert bus._subscribers

    bus.publish("session_update", {"n": 1})

    event = delivered.get(timeout=2)
    thread.join(timeout=2)

    assert event.name == "session_update"
    assert event.data == {"n": 1}
    assert not thread.is_alive()
