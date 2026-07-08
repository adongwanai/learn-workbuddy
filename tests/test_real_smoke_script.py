from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_real_smoke(root: Path):
    spec = importlib.util.spec_from_file_location(
        "run_real_smoke", root / "scripts" / "run_real_smoke.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_real_smoke_has_full_tour_target_for_gateway_providers(root: Path) -> None:
    mod = _load_real_smoke(root)

    assert {"mini", "s01", "s24", "full", "all-lessons"} <= set(mod.TARGETS)


def test_openai_chat_still_skips_anthropic_lesson_entrypoints(root: Path, capsys) -> None:
    mod = _load_real_smoke(root)

    code = mod.smoke_lesson("s01_agent_loop/code.py", "openai-chat")

    assert code == 0
    assert "lesson entrypoints use the Anthropic env" in capsys.readouterr().out


def test_openai_chat_still_skips_all_lesson_entrypoints(root: Path, capsys) -> None:
    mod = _load_real_smoke(root)

    code = mod.smoke_all_lessons("openai-chat")

    assert code == 0
    assert "lesson entrypoints use the Anthropic-compatible env" in capsys.readouterr().out
