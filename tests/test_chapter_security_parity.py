"""Parity checks between chapter security code and mini_workbuddy fixes.

The mini runtime is the hardened reference implementation, but readers learn
from chapter code first. These tests stop security fixes from living only in
mini_workbuddy while the teaching chapters keep demonstrating the old blind
spot.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_chapter(name: str):
    stub_dir = ROOT / "tests" / "stubs"
    sys.path.insert(0, str(stub_dir))
    saved = sys.modules.pop("anthropic", None)
    import os

    old_model = os.environ.get("MODEL_ID")
    old_home = os.environ.get("WORKBUDDY_HOME")
    os.environ["MODEL_ID"] = "offline-test-model"
    try:
        spec = importlib.util.spec_from_file_location(
            f"{name}_code", ROOT / name / "code.py"
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[f"{name}_code"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(stub_dir))
        sys.modules.pop("anthropic", None)
        if saved is not None:
            sys.modules["anthropic"] = saved
        if old_model is None:
            os.environ.pop("MODEL_ID", None)
        else:
            os.environ["MODEL_ID"] = old_model
        if old_home is None:
            os.environ.pop("WORKBUDDY_HOME", None)
        else:
            os.environ["WORKBUDDY_HOME"] = old_home


def test_s23_chapter_detects_audit_tail_truncation(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "home"))
    s23 = _load_chapter("s23_audit_sandbox")

    for i in range(5):
        s23.append_audit_entry("tool_call", {"i": i}, "ok")
    ok, count = s23.verify_chain()
    assert ok is True
    assert count == 5

    path = s23.audit_log_path()
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-2]) + "\n", encoding="utf-8")

    ok, _ = s23.verify_chain()
    assert ok is False


def test_s23_chapter_detects_full_audit_wipe(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "home"))
    s23 = _load_chapter("s23_audit_sandbox")

    s23.append_audit_entry("tool_call", {"i": 1}, "ok")
    s23.audit_log_path().write_text("", encoding="utf-8")

    ok, _ = s23.verify_chain()
    assert ok is False


def test_s24_list_files_tool_accepts_directory_iterators(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "home"))
    s24 = _load_chapter("s24_comprehensive")
    (tmp_path / "alpha.txt").write_text("a", encoding="utf-8")
    (tmp_path / "beta.txt").write_text("b", encoding="utf-8")

    output = s24.TOOL_HANDLERS["list_files"]({"path": str(tmp_path)})

    assert "alpha.txt" in output
    assert "beta.txt" in output


def test_s11_user_memory_creates_persona_directory_for_bootstrap(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "home"))
    s11 = _load_chapter("s11_user_memory")

    memory = s11.UserMemory()
    memory.create_bootstrap()

    assert memory.bootstrap_path.exists()
    assert memory.bootstrap_path.parent == tmp_path / "home" / "user-memory" / "persona"
