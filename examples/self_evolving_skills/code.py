#!/usr/bin/env python3
"""Offline example: distill successful trajectories into reviewable skills.

This example keeps model weights fixed.  The harness learns in external,
inspectable state: successful JSONL trajectories become a candidate SKILL.md,
which must pass held-out evaluation and receive explicit human approval before
it enters the versioned skill library.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import yaml


SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
SAFE_TOOL = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")
BLOCKED_TEXT = (
    "rm -rf",
    "sudo ",
    "curl ",
    "invoke-webrequest",
    "ignore previous",
    "authorization:",
    "api_key",
    "token=",
    "secret=",
)


class EvolutionError(RuntimeError):
    """Raised when evidence, evaluation, or promotion violates a gate."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _split_jsonl(raw: bytes) -> list[str]:
    """Split persisted records on physical newlines, not Unicode line breaks."""

    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return [line for line in text.split("\n") if line.strip()]


def _contains_blocked_text(value: str) -> str | None:
    lowered = value.casefold()
    return next((pattern for pattern in BLOCKED_TEXT if pattern in lowered), None)


def _validate_id(value: object, *, field_name: str) -> str:
    normalized = str(value).strip().casefold()
    if not SAFE_ID.fullmatch(normalized):
        raise EvolutionError(f"{field_name} must match {SAFE_ID.pattern}")
    return normalized


def _validate_text(value: object, *, field_name: str, max_chars: int = 500) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise EvolutionError(f"{field_name} must not be empty")
    if len(normalized) > max_chars:
        raise EvolutionError(f"{field_name} exceeds {max_chars} characters")
    blocked = _contains_blocked_text(normalized)
    if blocked:
        raise EvolutionError(f"{field_name} contains blocked text: {blocked}")
    return normalized


def _canonical_title(value: object) -> str:
    """标识符必须已是规范形式，不静默去空白或改大小写。"""
    if not isinstance(value, str) or _validate_id(value, field_name="title") != value:
        raise EvolutionError("title must be a canonical skill id")
    return value


def _atomic_write_text(path: Path, content: str) -> None:
    """Expose either the old file or the complete new file, never a partial one."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class StepEvidence:
    intent: str
    tool: str
    ok: bool

    @property
    def signature(self) -> tuple[str, str]:
        return (self.intent.casefold(), self.tool)


@dataclass(frozen=True)
class Trajectory:
    trace_id: str
    task_family: str
    task: str
    split: str
    outcome: str
    steps: tuple[StepEvidence, ...]
    source_digest: str

    @property
    def successful(self) -> bool:
        return (
            self.outcome == "success"
            and bool(self.steps)
            and all(step.ok for step in self.steps)
        )

    @property
    def signature(self) -> tuple[tuple[str, str], ...]:
        return tuple(step.signature for step in self.steps)


@dataclass(frozen=True)
class SkillCandidate:
    candidate_id: str
    title: str
    summary: str
    read_when: tuple[str, ...]
    steps: tuple[StepEvidence, ...]
    required_tools: tuple[str, ...]
    source_trace_ids: tuple[str, ...]
    source_digests: tuple[str, ...]
    created_at: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["steps"] = [asdict(step) for step in self.steps]
        return payload


@dataclass(frozen=True)
class EvaluationReport:
    candidate_id: str
    validation_trace_id: str
    checks: Mapping[str, bool]
    passed: bool
    evaluated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "validation_trace_id": self.validation_trace_id,
            "checks": dict(self.checks),
            "passed": self.passed,
            "evaluated_at": self.evaluated_at,
        }


class EvolutionStore:
    """Filesystem boundary for evidence, candidates, evaluations, and releases."""

    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.traces_dir = self.root / "traces"
        self.candidates_dir = self.root / "candidates"
        self.skills_dir = self.root / "skills"
        self.audit_path = self.root / "evolution-audit.jsonl"
        for directory in (self.traces_dir, self.candidates_dir, self.skills_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def append_audit(self, action: str, details: Mapping[str, object]) -> None:
        event = {"timestamp": _utc_now(), "action": action, "details": dict(details)}
        with self.audit_path.open("ab+") as handle:  # 末尾残留半行时先补换行，新事件独占一行
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(size - 1, 0))
            pad = b"\n" if size and handle.read(1) != b"\n" else b""
            handle.write(pad + (json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())

    def write_trajectory(
        self,
        *,
        trace_id: str,
        task_family: str,
        task: str,
        split: str,
        outcome: str,
        steps: Sequence[Mapping[str, object]],
    ) -> Path:
        safe_trace_id = _validate_id(trace_id, field_name="trace_id")
        path = self.traces_dir / f"{safe_trace_id}.jsonl"
        records = [
            {
                "type": "trajectory",
                "trace_id": safe_trace_id,
                "task_family": _validate_id(task_family, field_name="task_family"),
                "task": _validate_text(task, field_name="task"),
                "split": split,
                "outcome": outcome,
            }
        ]
        records.extend({"type": "step", **dict(step)} for step in steps)
        rendered = "".join(
            json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n" for record in records
        )
        if blocked := _contains_blocked_text(rendered):
            raise EvolutionError(f"trajectory contains blocked text: {blocked}")
        _atomic_write_text(path, rendered)
        self.append_audit("trajectory_written", {"trace_id": safe_trace_id, "path": str(path)})
        return path

    def load_trajectory(self, path: Path) -> Trajectory:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        if blocked := _contains_blocked_text(text):
            raise EvolutionError(f"trajectory contains blocked text: {blocked}")
        try:
            records = [json.loads(line) for line in _split_jsonl(raw)]
        except json.JSONDecodeError as exc:
            raise EvolutionError(f"invalid trajectory JSONL: {path.name}") from exc
        if not records or records[0].get("type") != "trajectory":
            raise EvolutionError("trajectory must start with a metadata record")

        header = records[0]
        split = str(header.get("split", ""))
        outcome = str(header.get("outcome", ""))
        if split not in {"train", "validation"}:
            raise EvolutionError("trajectory split must be train or validation")
        if outcome not in {"success", "failure"}:
            raise EvolutionError("trajectory outcome must be success or failure")

        steps: list[StepEvidence] = []
        for index, record in enumerate(records[1:], start=2):
            if record.get("type") != "step":
                raise EvolutionError(f"line {index} must be a step record")
            tool = str(record.get("tool", "")).strip()
            if not SAFE_TOOL.fullmatch(tool):
                raise EvolutionError(f"line {index} has invalid tool name")
            steps.append(
                StepEvidence(
                    intent=_validate_text(
                        record.get("intent", ""),
                        field_name=f"line {index} intent",
                    ),
                    tool=tool,
                    ok=record.get("ok") is True,
                )
            )

        return Trajectory(
            trace_id=_validate_id(header.get("trace_id"), field_name="trace_id"),
            task_family=_validate_id(header.get("task_family"), field_name="task_family"),
            task=_validate_text(header.get("task"), field_name="task"),
            split=split,
            outcome=outcome,
            steps=tuple(steps),
            source_digest=hashlib.sha256(raw).hexdigest(),
        )

    def candidate_dir(self, candidate_id: str) -> Path:
        return self.candidates_dir / _validate_id(candidate_id, field_name="candidate_id")

    def save_candidate(self, candidate: SkillCandidate) -> Path:
        directory = self.candidate_dir(candidate.candidate_id)
        _atomic_write_text(
            directory / "candidate.json",
            json.dumps(candidate.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        )
        draft = render_skill(candidate, status="candidate")
        _atomic_write_text(directory / "SKILL.md", draft)
        self.append_audit(
            "candidate_created",
            {
                "candidate_id": candidate.candidate_id,
                "source_trace_ids": candidate.source_trace_ids,
            },
        )
        return directory / "SKILL.md"

    def save_evaluation(self, report: EvaluationReport) -> Path:
        path = self.candidate_dir(report.candidate_id) / "evaluation.json"
        _atomic_write_text(
            path,
            json.dumps(report.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        )
        self.append_audit(
            "candidate_evaluated",
            {"candidate_id": report.candidate_id, "passed": report.passed},
        )
        return path

    def promote(
        self,
        candidate: SkillCandidate,
        report: EvaluationReport,
        *,
        approved_by: str,
    ) -> Path:
        if not approved_by.strip():
            raise EvolutionError("explicit approved_by is required")
        approver = _validate_text(approved_by, field_name="approved_by", max_chars=100)
        if (
            report.candidate_id != candidate.candidate_id
            or not report.passed
            or not report.checks
            or not all(report.checks.values())
        ):
            raise EvolutionError("candidate must have a matching passing evaluation")

        evaluation_path = self.candidate_dir(candidate.candidate_id) / "evaluation.json"
        if not evaluation_path.exists():
            raise EvolutionError("candidate has no stored evaluation evidence")
        stored_report = json.loads(evaluation_path.read_text(encoding="utf-8"))
        if stored_report != report.to_dict():
            raise EvolutionError("stored evaluation does not match the promotion request")

        skill_root = self.skills_dir / candidate.title
        manifest_path = skill_root / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            manifest = {"title": candidate.title, "active_version": None, "history": []}

        for item in manifest["history"]:
            if item["candidate_id"] == candidate.candidate_id:
                return skill_root / f"v{item['version']}" / "SKILL.md"

        version = len(manifest["history"]) + 1
        release_path = skill_root / f"v{version}" / "SKILL.md"
        _atomic_write_text(
            release_path,
            render_skill(
                candidate,
                status="approved",
                version=version,
                approved_by=approver,
            ),
        )
        manifest["active_version"] = version
        manifest["history"].append(
            {
                "version": version,
                "candidate_id": candidate.candidate_id,
                "approved_by": approver,
                "approved_at": _utc_now(),
                "path": str(release_path),
            }
        )
        _atomic_write_text(
            manifest_path,
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        )
        self.append_audit(
            "skill_promoted",
            {
                "candidate_id": candidate.candidate_id,
                "title": candidate.title,
                "version": version,
                "approved_by": approver,
            },
        )
        return release_path

    def _load_candidate(self, candidate_id: str) -> SkillCandidate:
        """读回 candidate.json 并还原 SkillCandidate；缺失、损坏或字段类型不对都拒绝。"""
        path = self.candidate_dir(candidate_id) / "candidate.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvolutionError(f"candidate evidence is unreadable: {candidate_id}") from exc
        texts = ("candidate_id", "title", "summary", "created_at")
        lists = ("read_when", "required_tools", "source_trace_ids", "source_digests")
        steps = data.get("steps") if isinstance(data, dict) else None
        if (
            not isinstance(steps, list)
            or set(data) != {*texts, *lists, "steps"}
            or not all(isinstance(data[key], str) for key in texts)
            or not all(isinstance(data[key], list) for key in lists)
            or not all(isinstance(item, str) for key in lists for item in data[key])
            or not all(
                isinstance(step, dict) and set(step) == {"intent", "tool", "ok"}
                and isinstance(step["intent"], str) and isinstance(step["tool"], str)
                and type(step["ok"]) is bool
                for step in steps
            )
            or data["candidate_id"] != candidate_id
        ):
            raise EvolutionError(f"candidate evidence is malformed: {candidate_id}")
        # 列表字段转回 tuple，steps 转回 StepEvidence，保证与原 candidate 相等
        return SkillCandidate(
            **{key: data[key] for key in texts},
            **{key: tuple(data[key]) for key in lists},
            steps=tuple(StepEvidence(**step) for step in steps),
        )

    def _read_manifest(self, title: str) -> dict | None:
        """读取 manifest；从未发布返回 None，其余读取或结构问题一律抛错。"""
        path = self.skills_dir / title / "manifest.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvolutionError(f"manifest is unreadable: {title}") from exc
        history = manifest.get("history") if isinstance(manifest, dict) else None
        if not isinstance(history, list) or not all(isinstance(item, dict) for item in history):
            raise EvolutionError(f"manifest is malformed: {title}")
        return manifest

    def _verify_release(self, title: str, version: int) -> Path:
        """按候选证据重新渲染并逐字节核对已发布版本；路径由 skills_dir 推导。"""
        manifest = self._read_manifest(title)
        if manifest is None:
            raise EvolutionError(f"skill has not been published: {title}")
        releases = {
            row["version"]: row for row in manifest["history"] if type(row.get("version")) is int
        }
        entry = releases.get(version)
        if entry is None:
            raise EvolutionError(f"version {version} is not in the release history of {title}")
        candidate_id, approver = entry.get("candidate_id"), entry.get("approved_by")
        if not isinstance(candidate_id, str) or not isinstance(approver, str):
            raise EvolutionError(f"release history entry v{version} is malformed")
        candidate = self._load_candidate(candidate_id)
        if candidate.title != title:
            raise EvolutionError(f"candidate {candidate_id} does not belong to {title}")
        # 不信 history[*].path：store 根目录整体搬迁后旧绝对路径会失效
        path = self.skills_dir / title / f"v{version}" / "SKILL.md"
        expect = render_skill(candidate, status="approved", version=version, approved_by=approver)
        try:
            actual = path.read_bytes()
        except OSError as exc:
            raise EvolutionError(f"release file v{version} is missing or unreadable") from exc
        if actual != expect.encode("utf-8"):
            raise EvolutionError(f"release file v{version} does not match its candidate evidence")
        return path

    def set_active_version(
        self, title: str, version: int, *, approved_by: str, reason: str
    ) -> Path:
        """只切换生效指针（回滚或前滚），不新建、不改写、不删除任何版本文件。"""
        safe_title = _canonical_title(title)
        if type(version) is not int or version < 1:
            raise EvolutionError("version must be a positive integer")
        if not isinstance(approved_by, str) or not isinstance(reason, str):
            raise EvolutionError("approved_by and reason must be strings")
        approver = _validate_text(approved_by, field_name="approved_by", max_chars=100)
        if approver != approved_by:
            raise EvolutionError("approved_by must not contain extra whitespace")
        why = _validate_text(reason, field_name="reason")
        path = self._verify_release(safe_title, version)
        manifest = self._read_manifest(safe_title) or {}
        previous = manifest.get("active_version")
        if type(previous) is int and previous == version:
            # 已经生效：幂等返回，不写 manifest，也不写审计
            return path
        manifest_path = self.skills_dir / safe_title / "manifest.json"
        old_manifest = manifest_path.read_text(encoding="utf-8")
        audit_size = self.audit_path.stat().st_size if self.audit_path.is_file() else 0
        manifest["active_version"] = version
        _atomic_write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        # 先原子写指针；审计（含补换行）失败时先恢复原 manifest，再尽力截掉半行（清理失败被忽略），抛原异常。
        details = {"title": safe_title, "from_version": previous, "to_version": version}
        try:
            self.append_audit("skill_activated", {**details, "approved_by": approver, "reason": why})
        except BaseException:
            _atomic_write_text(manifest_path, old_manifest)
            with contextlib.suppress(OSError):
                os.truncate(self.audit_path, audit_size)
            raise
        return path

    def active_skill_path(self, title: str) -> Path | None:
        """解析当前生效文件：从未发布返回 None，指针或文件有问题一律抛错。"""
        safe_title = _canonical_title(title)
        manifest = self._read_manifest(safe_title)
        if manifest is None or manifest.get("active_version") is None:
            return None
        active = manifest["active_version"]
        if type(active) is not int or active < 1:
            raise EvolutionError(f"manifest active_version is invalid: {title}")
        return self._verify_release(safe_title, active)


class SkillEvolutionPipeline:
    """Deterministic trajectory distillation with evaluation and approval gates."""

    def __init__(self, store: EvolutionStore, *, minimum_support: int = 2):
        if minimum_support < 2:
            raise ValueError("minimum_support must be at least 2")
        self.store = store
        self.minimum_support = minimum_support

    def triage(
        self, trajectories: Iterable[Trajectory], *, task_family: str
    ) -> tuple[list[Trajectory], dict[str, str]]:
        family = _validate_id(task_family, field_name="task_family")
        accepted: list[Trajectory] = []
        rejected: dict[str, str] = {}
        for trajectory in trajectories:
            reason: str | None = None
            if trajectory.task_family != family:
                reason = "different task family"
            elif trajectory.split != "train":
                reason = "held-out validation evidence"
            elif not trajectory.successful:
                reason = "trajectory did not complete successfully"
            if reason:
                rejected[trajectory.trace_id] = reason
            else:
                accepted.append(trajectory)
        return sorted(accepted, key=lambda item: item.trace_id), rejected

    def distill(self, trajectories: Sequence[Trajectory], *, task_family: str) -> SkillCandidate:
        if len(trajectories) < self.minimum_support:
            raise EvolutionError(
                f"need at least {self.minimum_support} successful training trajectories"
            )
        if len({item.trace_id for item in trajectories}) != len(trajectories):
            raise EvolutionError("minimum support requires distinct trajectory IDs")
        expected = trajectories[0].signature
        if not expected or any(item.signature != expected for item in trajectories[1:]):
            raise EvolutionError("successful trajectories do not share one stable procedure")

        family = _validate_id(task_family, field_name="task_family")
        if any(
            item.task_family != family or item.split != "train" or not item.successful
            for item in trajectories
        ):
            raise EvolutionError("distillation received ineligible evidence")

        source_ids = tuple(sorted(item.trace_id for item in trajectories))
        source_digests = tuple(
            item.source_digest for item in sorted(trajectories, key=lambda item: item.trace_id)
        )
        candidate_seed = json.dumps(
            {"family": family, "source_ids": source_ids, "signature": expected},
            sort_keys=True,
        )
        candidate_hash = hashlib.sha256(candidate_seed.encode("utf-8")).hexdigest()
        candidate_id = "candidate-" + candidate_hash[:16]
        tools = tuple(sorted({step.tool for step in trajectories[0].steps}))
        tokens = tuple(part for part in family.split("-") if len(part) >= 4)
        candidate = SkillCandidate(
            candidate_id=candidate_id,
            title=family,
            summary=(
                f"Reusable {family.replace('-', ' ')} procedure distilled from successful runs."
            ),
            read_when=(family.replace("-", " "),) + tokens,
            steps=trajectories[0].steps,
            required_tools=tools,
            source_trace_ids=source_ids,
            source_digests=source_digests,
            created_at=_utc_now(),
        )
        self.store.save_candidate(candidate)
        return candidate

    def evaluate(
        self, candidate: SkillCandidate, *, validation: Trajectory
    ) -> EvaluationReport:
        candidate_text = render_skill(candidate, status="candidate")
        observed_tools = tuple(sorted({step.tool for step in candidate.steps}))
        checks = {
            "minimum_support": len(candidate.source_trace_ids) >= self.minimum_support,
            "held_out_trace": validation.trace_id not in candidate.source_trace_ids,
            "held_out_digest": validation.source_digest not in candidate.source_digests,
            "same_task_family": validation.task_family == candidate.title,
            "validation_succeeded": validation.split == "validation" and validation.successful,
            "procedure_replayed": validation.signature
            == tuple(step.signature for step in candidate.steps),
            "declared_tools_match": candidate.required_tools == observed_tools
            == tuple(sorted({step.tool for step in validation.steps})),
            "content_safety_scan": _contains_blocked_text(candidate_text) is None,
        }
        report = EvaluationReport(
            candidate_id=candidate.candidate_id,
            validation_trace_id=validation.trace_id,
            checks=checks,
            passed=all(checks.values()),
            evaluated_at=_utc_now(),
        )
        self.store.save_evaluation(report)
        return report


def render_skill(
    candidate: SkillCandidate,
    *,
    status: str,
    version: int | None = None,
    approved_by: str | None = None,
) -> str:
    frontmatter: dict[str, object] = {
        "title": candidate.title,
        "summary": candidate.summary,
        "read_when": list(candidate.read_when),
        "agent_created": True,
        "status": status,
        "required_tools": list(candidate.required_tools),
        "source_trace_ids": list(candidate.source_trace_ids),
    }
    if version is not None:
        frontmatter["version"] = version
    if approved_by is not None:
        frontmatter["approved_by"] = approved_by

    heading = candidate.title.replace("-", " ").title()
    lines = [
        "---",
        yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip(),
        "---",
        "",
        f"# {heading}",
        "",
        (
            "> Generated from successful execution evidence. Review provenance and "
            "permissions before use."
        ),
        "",
        "## Procedure",
        "",
    ]
    for index, step in enumerate(candidate.steps, start=1):
        lines.append(
            f"{index}. {step.intent} Use `{step.tool}` through the harness permission gate."
        )
    lines.extend(
        [
            "",
            "## Evidence boundary",
            "",
            f"- Support: {len(candidate.source_trace_ids)} successful trajectories.",
            "- Raw commands and tool outputs stay in the source traces; they are not copied here.",
            "- A passing held-out replay and explicit human approval are required for promotion.",
            "",
        ]
    )
    return "\n".join(lines)


def seed_demo_trajectories(store: EvolutionStore, seed: str = "baseline") -> list[Trajectory]:
    shared_steps = [
        {"intent": "Inspect the project test configuration.", "tool": "read_file", "ok": True},
        {"intent": "Run the smallest relevant test target.", "tool": "bash", "ok": True},
        {"intent": "Record the verification result for review.", "tool": "bash", "ok": True},
    ]
    # "revised" keeps the first two steps and only rewords step 3 (same tool),
    # so the same task family gets a new candidate and, once approved, a v2.
    revised_steps = shared_steps[:2] + [
        {"intent": "Summarize the test outcome in the review notes.", "tool": "bash", "ok": True},
    ]
    steps = revised_steps if seed == "revised" else shared_steps
    prefix = "revised-" if seed == "revised" else ""
    specs = [
        (f"{prefix}train-run-1", "train", "success", steps),
        (f"{prefix}train-run-2", "train", "success", steps),
        (f"{prefix}validation-run-1", "validation", "success", steps),
        (
            f"{prefix}failed-run-1",
            "train",
            "failure",
            [
                {
                    "intent": "Run tests before inspecting configuration.",
                    "tool": "bash",
                    "ok": False,
                }
            ],
        ),
    ]
    paths = [
        store.write_trajectory(
            trace_id=trace_id,
            task_family="python-test-validation",
            task="Validate a Python change with focused tests and recorded evidence.",
            split=split,
            outcome=outcome,
            steps=steps,
        )
        for trace_id, split, outcome, steps in specs
    ]
    return [store.load_trajectory(path) for path in paths]


def run_demo(
    home: Path, *, approve: bool = False, approved_by: str = "demo-user", seed: str = "baseline"
) -> dict:
    store = EvolutionStore(home)
    pipeline = SkillEvolutionPipeline(store)
    trajectories = seed_demo_trajectories(store, seed)
    training, rejected = pipeline.triage(
        trajectories,
        task_family="python-test-validation",
    )
    candidate = pipeline.distill(training, task_family="python-test-validation")
    validation = next(item for item in trajectories if item.split == "validation")
    report = pipeline.evaluate(candidate, validation=validation)
    promoted_path = (
        store.promote(candidate, report, approved_by=approved_by) if approve else None
    )
    manifest = {
        "home": str(store.root),
        "candidate_id": candidate.candidate_id,
        "candidate_path": str(store.candidate_dir(candidate.candidate_id) / "SKILL.md"),
        "evaluation_path": str(store.candidate_dir(candidate.candidate_id) / "evaluation.json"),
        "evaluation_passed": report.passed,
        "source_trace_ids": list(candidate.source_trace_ids),
        "validation_trace_id": validation.trace_id,
        "rejected_traces": rejected,
        "promotion_requested": approve,
        "promoted_skill_path": str(promoted_path) if promoted_path else None,
        "audit_path": str(store.audit_path),
    }
    _atomic_write_text(
        store.root / "run_manifest.json",
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Distill successful trajectories into an evaluated, reviewable skill candidate."
        )
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=Path(".tmp/self-evolving-skills"),
        help="artifact directory (default: .tmp/self-evolving-skills)",
    )
    parser.add_argument(
        "--approve",
        action="store_true",
        help="simulate explicit human approval after the evaluation passes",
    )
    parser.add_argument(
        "--approved-by",
        default=None,
        help="identity recorded for --approve (default: demo-user) or --set-active (required)",
    )
    parser.add_argument(
        "--seed",
        choices=["baseline", "revised"],
        default=None,
        help="demo trajectories to seed (default: baseline); revised yields a new candidate",
    )
    parser.add_argument(
        "--set-active",
        type=int,
        metavar="N",
        help="switch the active version of an existing skill (rollback or roll forward)",
    )
    parser.add_argument("--reason", help="why the active version changes (required with --set-active)")
    args = parser.parse_args()

    if args.set_active is not None:
        if args.approve or args.seed is not None:
            parser.error("--set-active cannot be combined with --approve or --seed")
        if args.approved_by is None or args.reason is None:
            parser.error("--set-active requires --approved-by and --reason")
        store = EvolutionStore(args.home)
        try:
            path = store.set_active_version(
                "python-test-validation", args.set_active, approved_by=args.approved_by, reason=args.reason
            )
        except EvolutionError as exc:
            print(f"activation rejected: {exc}", file=sys.stderr)
            sys.exit(1)
        print("Active version:", args.set_active)
        print("Active skill:", path)
        print("Audit:", store.audit_path)
        return

    # Fall back only when the flag is absent; an empty value must still be rejected.
    approved_by = "demo-user" if args.approved_by is None else args.approved_by
    if args.approve and not approved_by.strip():
        parser.error("explicit approved_by is required")
    try:
        manifest = run_demo(
            args.home, approve=args.approve, approved_by=approved_by, seed=args.seed or "baseline"
        )
    except EvolutionError as exc:
        print(f"promotion rejected: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Self-evolving skills demo")
    print("Home:", manifest["home"])
    print("Candidate:", manifest["candidate_path"])
    print("Source traces:", ", ".join(manifest["source_trace_ids"]))
    print("Rejected traces:", json.dumps(manifest["rejected_traces"], ensure_ascii=False))
    print("Held-out evaluation passed:", manifest["evaluation_passed"])
    if manifest["promoted_skill_path"]:
        print("Approved skill:", manifest["promoted_skill_path"])
    else:
        print("Promotion: stopped at the human approval gate (use --approve to continue)")
    print("Audit:", manifest["audit_path"])


if __name__ == "__main__":
    main()
