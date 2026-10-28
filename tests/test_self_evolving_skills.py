"""Offline contracts for the trajectory-to-skill evolution example."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def evolution():
    module_name = "self_evolving_skills_test_module"
    spec = importlib.util.spec_from_file_location(
        module_name,
        ROOT / "examples" / "self_evolving_skills" / "code.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop(module_name, None)


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    return yaml.safe_load(text.split("---", 2)[1])


def _prepared(evolution, tmp_path: Path):
    store = evolution.EvolutionStore(tmp_path / "evolution")
    pipeline = evolution.SkillEvolutionPipeline(store)
    trajectories = evolution.seed_demo_trajectories(store)
    training, rejected = pipeline.triage(
        trajectories,
        task_family="python-test-validation",
    )
    candidate = pipeline.distill(training, task_family="python-test-validation")
    validation = next(item for item in trajectories if item.split == "validation")
    report = pipeline.evaluate(candidate, validation=validation)
    return store, candidate, report, rejected


def test_demo_stops_at_human_gate_and_excludes_failed_evidence(
    evolution, tmp_path: Path
) -> None:
    manifest = evolution.run_demo(tmp_path / "candidate-only", approve=False)

    assert manifest["evaluation_passed"] is True
    assert manifest["promoted_skill_path"] is None
    assert manifest["source_trace_ids"] == ["train-run-1", "train-run-2"]
    assert manifest["rejected_traces"]["failed-run-1"] == (
        "trajectory did not complete successfully"
    )
    assert manifest["rejected_traces"]["validation-run-1"] == (
        "held-out validation evidence"
    )
    assert Path(manifest["candidate_path"]).exists()
    assert Path(manifest["evaluation_path"]).exists()
    assert list((tmp_path / "candidate-only" / "skills").rglob("SKILL.md")) == []


def test_explicit_approval_promotes_a_versioned_provenance_bearing_skill(
    evolution, tmp_path: Path
) -> None:
    manifest = evolution.run_demo(
        tmp_path / "approved",
        approve=True,
        approved_by="alice",
    )
    skill_path = Path(manifest["promoted_skill_path"])
    metadata = _frontmatter(skill_path)

    assert skill_path.as_posix().endswith("skills/python-test-validation/v1/SKILL.md")
    assert metadata["status"] == "approved"
    assert metadata["version"] == 1
    assert metadata["approved_by"] == "alice"
    assert metadata["required_tools"] == ["bash", "read_file"]
    assert metadata["source_trace_ids"] == ["train-run-1", "train-run-2"]

    library_manifest = json.loads(
        skill_path.parents[1].joinpath("manifest.json").read_text(encoding="utf-8")
    )
    assert library_manifest["active_version"] == 1
    assert len(library_manifest["history"]) == 1


def test_promotion_requires_matching_evaluation_and_explicit_approver(
    evolution, tmp_path: Path
) -> None:
    store, candidate, report, _ = _prepared(evolution, tmp_path)

    with pytest.raises(evolution.EvolutionError, match="approved_by"):
        store.promote(candidate, report, approved_by="")

    failed_report = replace(report, passed=False)
    with pytest.raises(evolution.EvolutionError, match="passing evaluation"):
        store.promote(candidate, failed_report, approved_by="alice")

    forged_report = replace(report, validation_trace_id="forged-validation")
    with pytest.raises(evolution.EvolutionError, match="stored evaluation"):
        store.promote(candidate, forged_report, approved_by="alice")


def test_repeated_promotion_is_idempotent(evolution, tmp_path: Path) -> None:
    store, candidate, report, _ = _prepared(evolution, tmp_path)

    first = store.promote(candidate, report, approved_by="alice")
    second = store.promote(candidate, report, approved_by="alice")
    manifest = json.loads(first.parents[1].joinpath("manifest.json").read_text(encoding="utf-8"))

    assert second == first
    assert len(manifest["history"]) == 1
    assert list(first.parents[1].glob("v*/SKILL.md")) == [first]


def test_distillation_requires_distinct_supporting_trajectories(
    evolution, tmp_path: Path
) -> None:
    store = evolution.EvolutionStore(tmp_path / "duplicates")
    pipeline = evolution.SkillEvolutionPipeline(store)
    trajectories = evolution.seed_demo_trajectories(store)
    training, _ = pipeline.triage(trajectories, task_family="python-test-validation")

    with pytest.raises(evolution.EvolutionError, match="distinct trajectory IDs"):
        pipeline.distill(
            [training[0], training[0]],
            task_family="python-test-validation",
        )

    validation = next(item for item in trajectories if item.split == "validation")
    with pytest.raises(evolution.EvolutionError, match="ineligible evidence"):
        pipeline.distill(
            [training[0], validation],
            task_family="python-test-validation",
        )


def test_secret_bearing_external_trajectory_is_rejected(
    evolution, tmp_path: Path
) -> None:
    store = evolution.EvolutionStore(tmp_path / "unsafe")
    unsafe = store.traces_dir / "unsafe-run.jsonl"
    unsafe.write_text(
        """{"type":"trajectory","trace_id":"unsafe-run",\
"task_family":"python-test-validation","task":"validate",\
"split":"train","outcome":"success"}
{"type":"step","intent":"Use token=private-value","tool":"bash","ok":true}
""",
        encoding="utf-8",
    )

    with pytest.raises(evolution.EvolutionError, match="blocked text"):
        store.load_trajectory(unsafe)


def test_cli_offline_demo_writes_manifest(evolution, tmp_path: Path) -> None:
    home = tmp_path / "cli-home"
    result = subprocess.run(
        [
            sys.executable,
            "examples/self_evolving_skills/code.py",
            "--home",
            str(home),
            "--approve",
            "--approved-by",
            "test-user",
        ],
        cwd=ROOT,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout
    manifest = json.loads((home / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["evaluation_passed"] is True
    assert Path(manifest["promoted_skill_path"]).exists()
    assert "Held-out evaluation passed: True" in result.stdout


def test_readme_validation_glob_covers_exactly_all_self_evolving_tests():
    import re
    import shlex

    tests_dir = ROOT / "tests"
    adversarial = {
        f"test_self_evolving_skills_adversarial_r{i}.py"
        for i in range(1, 5)
    }
    expected = adversarial | {
        "test_self_evolving_skills.py",
        "test_self_evolving_skills_cli.py",
        "test_self_evolving_skills_rollback.py",
    }

    assert not list(
        tests_dir.glob("test_examples_skill_rollback_adversarial*")
    )
    assert all((tests_dir / name).is_file() for name in adversarial)

    readme = (
        ROOT / "examples/self_evolving_skills/README.md"
    ).read_text(encoding="utf-8")
    section = re.search(
        r"^## 验证[ \t]*\n(.*?)(?=^##[ \t]|\Z)",
        readme,
        re.MULTILINE | re.DOTALL,
    )
    assert section is not None
    block = re.search(
        r"^```bash[ \t]*\n(.*?)^```[ \t]*$",
        section.group(1),
        re.MULTILINE | re.DOTALL,
    )
    assert block is not None

    commands = [
        line.strip()
        for line in block.group(1).splitlines()
        if line.strip()
    ]
    assert len(commands) == 1
    argv = shlex.split(commands[0])
    assert argv == [
        "python3", "-m", "pytest", "-q",
        "tests/test_self_evolving_skills*.py",
    ]

    matched = list(ROOT.glob(argv[-1]))
    assert all(path.is_file() for path in matched)
    assert {path.name for path in matched} == expected
    assert len(matched) == 7


@pytest.mark.parametrize("line_break", ["\u2028", "\u2029", "\x85"])
def test_trajectory_reader_keeps_unicode_line_breaks_in_strings(
    evolution, tmp_path: Path, line_break: str
) -> None:
    store = evolution.EvolutionStore(tmp_path / "evolution")
    path = store.traces_dir / "unicode-evidence.jsonl"
    header = {
        "type": "trajectory",
        "trace_id": "unicode-run",
        "task_family": "python-test-validation",
        "task": "Validate a Python change.",
        "split": "train",
        "outcome": "success",
    }
    steps = [
        {
            "type": "step",
            "intent": f"Inspect{line_break}the test configuration.",
            "tool": "read_file",
            "ok": True,
        },
        {
            "type": "step",
            "intent": "Run the focused tests.",
            "tool": "bash",
            "ok": True,
        },
    ]
    rendered = "".join(
        json.dumps(record, ensure_ascii=False) + "\n" for record in [header, *steps]
    )
    assert line_break in rendered
    path.write_text(rendered, encoding="utf-8")

    trajectory = store.load_trajectory(path)

    assert trajectory.trace_id == "unicode-run"
    assert len(trajectory.steps) == 2
    assert trajectory.steps[0].intent == "Inspect the test configuration."
    assert trajectory.steps[1].tool == "bash"
