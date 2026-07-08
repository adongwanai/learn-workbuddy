"""Unit tests for the s04 permission gates (the security-critical lesson).

The s04 chapter teaches the three-gate model (hard deny -> rules -> user
approval). Its logic was previously only exercised manually because the
chapter script imports the SDK at module level. With the stub SDK on
sys.path we can import the chapter and pin the gate behavior offline.

These tests double as the chapter's spec:

- what is hard-denied (gate 1),
- what rules deny or escalate to ask (gate 2),
- and — just as important for readers — what the simple pattern
  matching does NOT catch, so nobody mistakes a teaching deny-list
  for a sandbox.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def s04():
    root = ROOT
    stub_dir = root / "tests" / "stubs"
    sys.path.insert(0, str(stub_dir))
    saved = sys.modules.pop("anthropic", None)
    import os

    os.environ.setdefault("MODEL_ID", "offline-test-model")
    try:
        spec = importlib.util.spec_from_file_location(
            "s04_permission_hooks_code", root / "s04_permission_hooks" / "code.py"
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(stub_dir))
        sys.modules.pop("anthropic", None)
        if saved is not None:
            sys.modules["anthropic"] = saved


# -- Gate 1: hard deny --------------------------------------------------------

@pytest.mark.parametrize(
    "command",
    ["sudo apt install x", "rm -rf / --no-preserve-root", "mkfs.ext4 /dev/sda1", "dd if=/dev/zero"],
)
def test_gate1_hard_deny(s04, command: str) -> None:
    action, reason = s04.check_permission("bash", {"command": command})
    assert action == "deny"
    assert "Hard deny" in reason


# -- Gate 2: rules ------------------------------------------------------------

def test_gate2_rm_rf_denied_even_in_workspace(s04) -> None:
    action, _ = s04.check_permission("bash", {"command": "rm -rf ./build"})
    assert action == "deny"


@pytest.mark.parametrize(
    "command", ["rm old.log", "mv a b", "chmod +x run.sh", "chown user file"]
)
def test_gate2_destructive_bash_escalates_to_ask(s04, command: str) -> None:
    action, _ = s04.check_permission("bash", {"command": command})
    assert action == "ask"


def test_gate2_write_to_absolute_root_denied(s04) -> None:
    action, _ = s04.check_permission("write_file", {"path": "/etc/hosts", "content": "x"})
    assert action == "deny"


def test_gate2_large_write_escalates_to_ask(s04) -> None:
    action, _ = s04.check_permission("write_file", {"path": "big.txt", "content": "a" * 6000})
    assert action == "ask"


def test_benign_commands_are_allowed(s04) -> None:
    for command in ["ls -la", "cat notes.md", "python3 script.py"]:
        action, reason = s04.check_permission("bash", {"command": command})
        assert action == "allow", (command, reason)


# -- Boundary: what pattern matching does NOT catch ---------------------------

@pytest.mark.parametrize(
    "command,expected",
    [
        # Indirection bypasses a command-string deny list. Kept as an
        # explicit, documented boundary of the lesson (see the s04 README
        # and docs/security-boundaries.md): pattern matching is a seatbelt,
        # not a sandbox.
        ("find . -name '*.tmp' -delete", "allow"),
        ('sh -c "rm -rf ./x"', "deny"),  # still caught: 'rm -rf' substring survives quoting
        ("SUDO=1 env true", "allow"),  # \bsudo\b is case-sensitive; SUDO= passes
    ],
)
def test_documented_pattern_matching_boundary(s04, command: str, expected: str) -> None:
    action, _ = s04.check_permission("bash", {"command": command})
    assert action == expected


# -- Hooks wiring --------------------------------------------------------------

def test_pretooluse_permission_hook_blocks_denied_calls(s04) -> None:
    class Block:
        name = "bash"
        input = {"command": "sudo rm -rf /"}

    outcome = s04.permission_hook(Block())
    assert outcome is not None and outcome.startswith("block:")


def test_hooks_registry_contains_builtin_hooks(s04) -> None:
    assert s04.permission_hook in s04.HOOKS["PreToolUse"]
    assert s04.audit_hook in s04.HOOKS["PreToolUse"]
    assert s04.output_size_hook in s04.HOOKS["PostToolUse"]


def test_s04_run_bash_reports_os_errors(s04, monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr(s04.subprocess, "run", fake_run)
    assert s04.run_bash("echo hi") == "Error: spawn failed"
