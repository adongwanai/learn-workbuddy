"""离线对抗测试：核对撤回目标身份、旧标识符兼容和混合类型的生效视图。"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def s12(tmp_path_factory: pytest.TempPathFactory):
    stub_dir = ROOT / "tests" / "stubs"
    state_root = tmp_path_factory.mktemp("s12-retraction-adversarial-state")
    sys.path.insert(0, str(stub_dir))
    saved_anthropic = sys.modules.pop("anthropic", None)
    old_model = os.environ.get("MODEL_ID")
    old_home = os.environ.get("WORKBUDDY_HOME")
    os.environ["MODEL_ID"] = "offline-test-model"
    os.environ["WORKBUDDY_HOME"] = str(state_root)
    module_name = "s12_retraction_adversarial_module"
    try:
        spec = importlib.util.spec_from_file_location(
            module_name, ROOT / "s12_cloud_memory" / "code.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(stub_dir))
        sys.modules.pop(module_name, None)
        sys.modules.pop("anthropic", None)
        if saved_anthropic is not None:
            sys.modules["anthropic"] = saved_anthropic
        if old_model is None:
            os.environ.pop("MODEL_ID", None)
        else:
            os.environ["MODEL_ID"] = old_model
        if old_home is None:
            os.environ.pop("WORKBUDDY_HOME", None)
        else:
            os.environ["WORKBUDDY_HOME"] = old_home


AS_OF = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)


def _source(s12):
    return s12.MemorySource(
        source_id="adversarial-request",
        source_type="user_request",
        title="对抗测试来源",
        captured_at="2026-08-10T12:00:00Z",
    )


def _append(s12, store, memory_id: str, *, kind=None):
    return store.append(
        kind=s12.MemoryKind.CONVERSATION if kind is None else kind,
        memory_id=memory_id,
        content="layered memory",
        summary="layered memory",
        source=_source(s12),
        stored_at=AS_OF,
    )


@pytest.mark.parametrize(
    ("target_id", "neighbor_id"),
    [
        (" memory-1 ", "memory-1"),
        ("memory  1", "memory 1"),
    ],
    ids=["surrounding-whitespace", "internal-whitespace"],
)
def test_retract_preserves_exact_target_identity(
    s12, tmp_path: Path, target_id: str, neighbor_id: str
) -> None:
    """攻击点：两个仅空白不同的有效 ID 共存时，撤回不能命中另一条记录。"""
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    target = _append(s12, store, target_id)
    neighbor = _append(s12, store, neighbor_id)
    assert [record.memory_id for record in store.read_all()] == [
        target_id,
        neighbor_id,
    ]
    before = store.path.read_bytes()

    retraction = store.retract(
        target.memory_id,
        reason="只撤回指定的原始 ID。",
        source=_source(s12),
        retraction_id="retraction-exact-target",
        stored_at=AS_OF,
    )

    assert retraction.retracts == target.memory_id
    assert store.path.read_bytes().startswith(before)
    restarted = s12.RemoteMemoryStore(store.path, user_id="alice")
    assert restarted.read_all() == [target, neighbor, retraction]
    assert restarted.active_records() == [neighbor]
    result = s12.RecallEngine(restarted).recall("layered memory", as_of=AS_OF)
    assert [hit.memory_id for hit in result.hits] == [neighbor_id]
    assert result.retracted_records == 1


def test_retract_accepts_an_already_stored_long_id(s12, tmp_path: Path) -> None:
    """攻击点：写入和旧记录读取支持的长 ID，不能在撤回入口新增限制而失去撤回能力。"""
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    target = _append(s12, store, "x" * 201)
    restarted = s12.RemoteMemoryStore(store.path, user_id="alice")
    assert restarted.read_all() == [target]

    retraction = restarted.retract(
        target.memory_id,
        reason="撤回已经成功保存的长 ID。",
        source=_source(s12),
        retraction_id="retraction-long-target",
        stored_at=AS_OF,
    )

    assert retraction.retracts == target.memory_id
    assert restarted.read_all() == [target, retraction]
    assert restarted.active_records() == []
    result = s12.RecallEngine(restarted).recall("layered memory", as_of=AS_OF)
    assert result.hits == ()
    assert result.searched_records == 0
    assert result.candidate_records == 0
    assert result.retracted_records == 1


def test_mixed_retractions_count_targets_and_allow_new_identity(
    s12, tmp_path: Path
) -> None:
    """攻击点：计数不能包含快照或重复撤回，全部快照撤回后为空，相同内容的新 ID 仍可召回。"""
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    conversation = _append(s12, store, "conversation-old")
    profile = _append(
        s12, store, "profile-only", kind=s12.MemoryKind.PROFILE
    )
    original_bytes = store.path.read_bytes()
    first = store.retract(
        conversation.memory_id,
        reason="撤回旧会话。",
        source=_source(s12),
        retraction_id="retraction-conversation",
        stored_at=AS_OF,
    )
    second = store.retract(
        profile.memory_id,
        reason="撤回唯一的快照。",
        source=_source(s12),
        retraction_id="retraction-profile",
        stored_at=AS_OF,
    )
    before_retry = store.path.read_bytes()
    retry = store.retract(
        conversation.memory_id,
        reason="重试不能增加撤回事件。",
        source=_source(s12),
        retraction_id="unused-retry-id",
        stored_at=AS_OF,
    )
    assert retry == first
    assert store.path.read_bytes() == before_retry

    replacement = _append(s12, store, "conversation-replacement")
    restarted = s12.RemoteMemoryStore(store.path, user_id="alice")
    assert restarted.path.read_bytes().startswith(original_bytes)
    assert restarted.read_all() == [
        conversation,
        profile,
        first,
        second,
        replacement,
    ]
    assert restarted.active_records() == [replacement]
    assert restarted.latest_profile() is None
    assert "<remote_profile " not in s12.build_system_prompt(restarted)
    result = s12.RecallEngine(restarted).recall("layered memory", as_of=AS_OF)
    assert [hit.memory_id for hit in result.hits] == [replacement.memory_id]
    assert result.searched_records == 1
    assert result.candidate_records == 1
    assert result.retracted_records == 1
    assert result.to_dict()["retracted_records"] == 1
