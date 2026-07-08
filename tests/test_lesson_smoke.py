from __future__ import annotations

import os
import py_compile
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


def test_all_python_files_compile(root: Path) -> None:
    failures: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in {".git", ".venv", "venv", "__pycache__"} for part in path.parts):
            continue
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            failures.append(f"{path.relative_to(root)}: {exc.msg}")
    assert failures == []


@pytest.mark.parametrize(
    "cmd",
    [
        ["s03_deferred_loading/code.py"],
        ["s08_model_routing/code.py"],
        ["s09_jsonl_transcript/code.py"],
        ["s13_output_externalization/code.py"],
        ["examples/mini_workbuddy_demo/code.py", "--mode", "offline"],
        ["examples/full_tour/code.py", "--provider", "offline"],
    ],
)
def test_offline_lesson_scripts_run(root: Path, tmp_path: Path, cmd: list[str]) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    env["MINI_WORKBUDDY_HOME"] = str(tmp_path / "mini")
    env["WORKBUDDY_DEMO_SLEEP_SCALE"] = "0"
    result = subprocess.run(
        [sys.executable, *cmd],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout[-4000:]


def test_real_api_demo_requires_api_key(root: Path, tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    env["MINI_WORKBUDDY_HOME"] = str(tmp_path / "mini")
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("MODEL_ID", None)
    result = subprocess.run(
        [sys.executable, "examples/mini_workbuddy_demo/code.py", "--mode", "real"],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=10,
    )
    assert result.returncode != 0
    assert "Real API demo requires a provider key" in result.stdout


def test_api_backed_lessons_are_documented_but_not_required_in_ci(root: Path) -> None:
    api_scripts = []
    for path in sorted(root.glob("s[0-9][0-9]_*/code.py")):
        text = path.read_text(encoding="utf-8")
        if "from anthropic import Anthropic" in text or "input(" in text:
            api_scripts.append(path.relative_to(root).as_posix())
    assert "s01_agent_loop/code.py" in api_scripts
    assert "s24_comprehensive/code.py" in api_scripts
    assert len(api_scripts) >= 15


def test_all_lesson_scripts_have_interactive_entrypoints(root: Path) -> None:
    missing: list[str] = []
    for path in sorted(root.glob("s[0-9][0-9]_*/code.py")):
        text = path.read_text(encoding="utf-8")
        if "input(" not in text:
            missing.append(path.relative_to(root).as_posix())
    assert missing == []


def test_all_lesson_scripts_have_keyless_demo_entrypoints(root: Path, tmp_path: Path) -> None:
    failures: list[str] = []
    for script in sorted(root.glob("s[0-9][0-9]_*/code.py")):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(root)
        env["WORKBUDDY_HOME"] = str(tmp_path / "wb")
        result = subprocess.run(
            [sys.executable, str(script.relative_to(root)), "--demo"],
            cwd=root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
        )
        if result.returncode != 0 or "本章 demo 完成" not in result.stdout:
            failures.append(f"{script.parent.name}:\n{result.stdout[-1200:]}")
    assert failures == []


def test_chapter_provider_deepseek_is_accepted_before_key_check(root: Path, tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    env["DEEPSEEK_API_KEY"] = "ds-test"
    env["WORKBUDDY_HOME"] = str(tmp_path / "wb")
    result = subprocess.run(
        [sys.executable, "s01_agent_loop/code.py", "--provider", "deepseek", "--demo"],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout[-2000:]
    assert "python3 s01_agent_loop/code.py --provider deepseek" in result.stdout


def test_s24_does_not_collide_with_existing_real_workbuddy_db(root: Path, tmp_path: Path) -> None:
    real_home = tmp_path / "home"
    real_wb = real_home / ".workbuddy"
    real_wb.mkdir(parents=True)
    con = sqlite3.connect(real_wb / "workbuddy.db")
    con.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            cwd TEXT,
            title TEXT,
            status TEXT,
            model TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    con.commit()
    con.close()

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(root / "tests" / "stubs"), str(root)])
    env["HOME"] = str(real_home)
    env["MODEL_ID"] = "offline-test-model"
    env.pop("WORKBUDDY_HOME", None)
    result = subprocess.run(
        [sys.executable, "s24_comprehensive/code.py"],
        input="q\n",
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout[-2000:]
    assert (real_home / ".learn_workbuddy" / "workbuddy.db").exists()


@pytest.mark.parametrize(
    "script",
    [
        "s03_deferred_loading/code.py",
        "s08_model_routing/code.py",
        "s09_jsonl_transcript/code.py",
        "s13_output_externalization/code.py",
    ],
)
def test_offline_interactive_modes_exit_cleanly(root: Path, tmp_path: Path, script: str) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    env["MINI_WORKBUDDY_HOME"] = str(tmp_path / "mini")
    result = subprocess.run(
        [sys.executable, script, "--interactive"],
        input="q\n",
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout[-4000:]


@pytest.mark.parametrize(
    "script,input_text,required",
    [
        ("s07_session_management/code.py", "q\n", "session started"),
    ],
)
def test_model_backed_lesson_startup_paths_do_not_crash(
    root: Path,
    tmp_path: Path,
    script: str,
    input_text: str,
    required: str,
) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(root / "tests" / "stubs"), str(root)])
    env["MODEL_ID"] = "offline-test-model"
    env["WORKBUDDY_HOME"] = str(tmp_path / "home")
    result = subprocess.run(
        [sys.executable, script],
        input=input_text,
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout[-4000:]
    assert required in result.stdout
    assert "Traceback" not in result.stdout
