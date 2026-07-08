from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_model_benchmark_dry_run_writes_matrix_without_secrets(root: Path, tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    env["DEEPSEEK_API_KEY"] = "sk-test-deepseek"
    env["OPENAI_CHAT_API_KEY"] = "sk-test-gateway"
    env["OPENAI_CHAT_BASE_URL"] = "http://127.0.0.1:8080/v1"
    env["OPENAI_CHAT_MODEL"] = "gpt-test"

    out_dir = tmp_path / "bench"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/model_benchmark.py",
            "--providers",
            "deepseek",
            "openai-chat",
            "--max-lessons",
            "2",
            "--output",
            str(out_dir),
            "--dry-run",
        ],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
    )

    assert result.returncode == 0, result.stdout
    stats = json.loads((out_dir / "latest-stats.json").read_text(encoding="utf-8"))
    case_ids = {case["case_id"] for case in stats["cases"]}

    assert "deepseek::mini" in case_ids
    assert "deepseek::full" in case_ids
    assert "openai-chat::mini" in case_ids
    assert "openai-chat::full" in case_ids
    assert any(case_id.startswith("deepseek::lesson::s01") for case_id in case_ids)
    assert not any(case_id.startswith("openai-chat::lesson::") for case_id in case_ids)
    assert stats["summary"]["total"] == 6
    assert "sk-test" not in (out_dir / "latest-stats.json").read_text(encoding="utf-8")
    assert "sk-test" not in (out_dir / "latest.md").read_text(encoding="utf-8")


def test_model_benchmark_discovers_model_backed_lessons(root: Path) -> None:
    sys.path.insert(0, str(root))
    try:
        from scripts import model_benchmark as bm
    finally:
        sys.path.remove(str(root))

    lessons = bm.discover_model_lessons()
    names = [lesson.chapter for lesson in lessons]

    assert "s01_agent_loop" in names
    assert "s24_comprehensive" in names
    assert "s03_deferred_loading" not in names
    assert len(lessons) >= 20


def test_model_benchmark_uses_command_script_for_s22(root: Path) -> None:
    sys.path.insert(0, str(root))
    try:
        from scripts import model_benchmark as bm
    finally:
        sys.path.remove(str(root))

    cases = bm.build_cases(["deepseek"])
    s22 = next(case for case in cases if case.case_id == "deepseek::lesson::s22_automation_scheduler")

    assert s22.stdin == "/list\nq\n"
