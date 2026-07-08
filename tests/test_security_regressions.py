"""Security-behavior regression tests for mini_workbuddy.

Each test here pins a failure mode that was actually observed (or is a
classic harness pitfall), so it can never silently come back:

1. Unparseable commands (unbalanced quotes) used to raise an uncaught
   ValueError out of shlex.split and crash the whole prompt. A harness
   must fail closed: what it cannot parse, it must deny.
2. subprocess.TimeoutExpired is NOT a subclass of builtin TimeoutError,
   so a 30s+ command used to escape MiniAgent's error boundary and crash.
3. A hash chain detects *modification* of past audit entries but not
   *truncation* — any prefix of a valid chain verifies. The head anchor
   file closes that gap; these tests pin both directions.
4. Deny-list boundaries: the mini policy blocks the obvious spellings
   and is *documented* to be bypassable by indirection (sh -c, xargs,
   env vars). The boundary tests make the current contract explicit so
   a future "improvement" that accidentally widens allow-behavior fails
   loudly, and so readers see exactly where the teaching harness stops.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mini_workbuddy.agent import MiniAgent
from mini_workbuddy.audit import AuditLog
from mini_workbuddy.config import HarnessConfig
from mini_workbuddy.events import EventBus
from mini_workbuddy.storage import Storage
from mini_workbuddy.tools import PermissionError as PolicyDenied
from mini_workbuddy.tools import ToolRegistry


@pytest.fixture()
def harness(tmp_path: Path):
    config = HarnessConfig(root_dir=tmp_path / "home")
    config.ensure_dirs()
    storage = Storage(config)
    tools = ToolRegistry(config, storage)
    audit = AuditLog(config)
    agent = MiniAgent(storage, tools, EventBus(), audit)
    session = storage.create_session(str(tmp_path))
    return agent, tools, audit, session


# -- 1. fail-closed on unparseable input ------------------------------------

def test_unbalanced_quotes_are_denied_not_crashed(harness) -> None:
    agent, _, _, session = harness
    result = agent.prompt(session, 'bash echo "unclosed')
    assert result["answer"].startswith("Tool failed")
    assert "parsed safely" in result["answer"]


def test_unparseable_command_raises_policy_denial(harness) -> None:
    _, tools, _, session = harness
    with pytest.raises(PolicyDenied):
        tools.run("bash", "echo 'nope", session)


# -- 2. timeout crosses the error boundary correctly ------------------------

def test_command_timeout_reports_tool_failure(harness, monkeypatch) -> None:
    agent, _, _, session = harness

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="sleep 999", timeout=30)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = agent.prompt(session, "bash sleep 999")
    assert result["answer"].startswith("Tool failed")
    assert "timed out" in result["answer"]


# -- 3. audit chain: modification AND truncation are both detected ----------

def test_audit_truncation_is_detected_by_head_anchor(harness) -> None:
    _, _, audit, _ = harness
    for i in range(5):
        audit.append("tool_call", {"i": i})
    assert audit.verify() is True

    lines = audit.path.read_text(encoding="utf-8").splitlines()
    audit.path.write_text("\n".join(lines[:-2]) + "\n", encoding="utf-8")
    assert audit.verify() is False, (
        "deleting tail entries must be detected — hash chains alone "
        "verify any prefix, the head anchor exists to close this gap"
    )


def test_audit_full_wipe_is_detected(harness) -> None:
    _, _, audit, _ = harness
    audit.append("tool_call", {"i": 0})
    audit.path.write_text("", encoding="utf-8")
    assert audit.verify() is False


def test_audit_anchor_matches_after_normal_appends(harness) -> None:
    _, _, audit, _ = harness
    for i in range(3):
        audit.append("tool_call", {"i": i})
    anchor = json.loads(audit.head_path.read_text(encoding="utf-8"))
    entries = audit.read_entries()
    assert anchor["count"] == len(entries) == 3
    assert anchor["head"] == entries[-1].hash
    assert audit.verify() is True


def test_audit_legacy_log_without_anchor_still_verifies(harness) -> None:
    _, _, audit, _ = harness
    audit.append("tool_call", {"i": 0})
    audit.head_path.unlink()  # simulate a log written before anchoring existed
    assert audit.verify() is True


# -- 4. deny-list boundary contract ------------------------------------------

@pytest.mark.parametrize(
    "command",
    ["rm -rf /tmp/x", "sudo ls", "shutdown now", "dd if=/dev/zero of=/dev/sda"],
)
def test_denied_first_tokens_stay_denied(harness, command: str) -> None:
    _, tools, _, session = harness
    with pytest.raises(PolicyDenied):
        tools.run("bash", command, session)


@pytest.mark.parametrize(
    "command",
    [
        # Documented bypasses of a first-token deny list. The teaching
        # harness intentionally stops here; a production harness needs
        # OS-level sandboxing (see s23 README / docs/security-boundaries.md).
        'sh -c "true"',
        "find /tmp -maxdepth 0 -name nothing-matches -delete",
        "echo safe | xargs true",
    ],
)
def test_documented_denylist_bypass_boundary(harness, command: str) -> None:
    _, tools, _, session = harness
    result = tools.run("bash", command, session)  # must NOT raise
    assert result.name == "bash"
