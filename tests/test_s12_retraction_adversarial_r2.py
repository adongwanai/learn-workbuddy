"""第二轮离线对抗测试：检查 ID 类型边界、落盘身份和撤回后的审计视图。"""

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
    """按章节现有方式隔离环境，并通过离线桩加载被测模块。"""
    stub_dir = ROOT / "tests" / "stubs"
    state_root = tmp_path_factory.mktemp("s12-retraction-adversarial-r2-state")
    sys.path.insert(0, str(stub_dir))
    saved_anthropic = sys.modules.pop("anthropic", None)
    old_model = os.environ.get("MODEL_ID")
    old_home = os.environ.get("WORKBUDDY_HOME")
    os.environ["MODEL_ID"] = "offline-test-model"
    os.environ["WORKBUDDY_HOME"] = str(state_root)
    module_name = "s12_retraction_adversarial_r2_module"
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
INVALID_IDS = [
    pytest.param(123, id="integer"),
    pytest.param(0, id="zero"),
    pytest.param(True, id="true"),
    pytest.param(False, id="false"),
    pytest.param(b"id", id="bytes"),
    pytest.param(b"", id="empty-bytes"),
]


class StringID(str):
    """保持标准字符串行为的子类，用于防止误用严格类型相等检查。"""


def _source(s12):
    return s12.MemorySource(
        source_id="adversarial-r2-request",
        source_type="user_request",
        title="第二轮对抗测试来源",
        captured_at="2026-08-10T12:00:00Z",
    )


def _append(s12, store, memory_id):
    return store.append(
        kind=s12.MemoryKind.CONVERSATION,
        memory_id=memory_id,
        content="layered memory",
        summary="layered memory",
        source=_source(s12),
        stored_at=AS_OF,
    )


def _retract(s12, store, memory_id, retraction_id=None):
    return store.retract(
        memory_id,
        reason="只撤回指定记录。",
        source=_source(s12),
        retraction_id=retraction_id,
        stored_at=AS_OF,
    )


@pytest.mark.parametrize("bad_id", INVALID_IDS)
def test_append_rejects_non_string_ids_before_writing(
    s12, tmp_path: Path, bad_id
) -> None:
    """攻击点：非字符串不能写入后变成另一个身份，假值也不能冒充未提供 ID。"""
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    original = _append(s12, store, "123")
    before = store.path.read_bytes()

    with pytest.raises(s12.RemoteMemoryValidationError):
        _append(s12, store, bad_id)

    assert store.path.read_bytes() == before
    restarted = s12.RemoteMemoryStore(store.path, user_id="alice")
    assert restarted.read_all() == [original]
    assert restarted.active_records() == [original]


@pytest.mark.parametrize("bad_id", INVALID_IDS)
def test_retraction_id_rejects_non_string_before_writing(
    s12, tmp_path: Path, bad_id
) -> None:
    """攻击点：整数撤回 ID 绕过字符串查重，不能污染整个只追加存储。"""
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    target = _append(s12, store, "123")
    before = store.path.read_bytes()

    with pytest.raises(s12.RemoteMemoryValidationError):
        _retract(s12, store, target.memory_id, retraction_id=bad_id)

    assert store.path.read_bytes() == before
    restarted = s12.RemoteMemoryStore(store.path, user_id="alice")
    assert restarted.read_all() == [target]
    assert restarted.active_records() == [target]
    result = s12.RecallEngine(restarted).recall("layered memory", as_of=AS_OF)
    assert [hit.memory_id for hit in result.hits] == ["123"]
    assert result.retracted_records == 0


@pytest.mark.parametrize(
    "bad_id",
    INVALID_IDS + [
        pytest.param(None, id="none"),
        pytest.param("", id="empty-string"),
    ],
)
def test_retract_rejects_invalid_targets_without_writing(
    s12, tmp_path: Path, bad_id
) -> None:
    """攻击点：修复写入校验时，不能重新通过隐式字符串转换撤回别的记录。"""
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    targets = [_append(s12, store, value) for value in ("123", "True", "b'id'")]
    before = store.path.read_bytes()

    with pytest.raises(s12.RemoteMemoryValidationError):
        _retract(s12, store, bad_id)

    assert store.path.read_bytes() == before
    assert store.read_all() == targets
    assert store.active_records() == targets


@pytest.mark.parametrize(
    ("target_id", "neighbor_id"),
    [
        pytest.param(" \t ", " ", id="whitespace-only"),
        pytest.param("a\n", "a", id="trailing-newline"),
        pytest.param(StringID(" memory "), "memory", id="string-subclass"),
    ],
)
def test_string_ids_round_trip_without_normalization(
    s12, tmp_path: Path, target_id, neighbor_id
) -> None:
    """攻击点：纯空白、换行、字符串子类及长撤回 ID 必须保持精确身份和幂等性。"""
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    target = _append(s12, store, target_id)
    neighbor = _append(s12, store, neighbor_id)
    before = store.path.read_bytes()

    # 撤回事件与普通记录共享 ID 命名空间，冲突时不能写入。
    with pytest.raises(s12.RemoteMemoryDuplicateError):
        _retract(s12, store, target_id, retraction_id=neighbor_id)
    assert store.path.read_bytes() == before

    retraction_id = StringID(" retraction\t" + "x" * 201 + " ")
    event = _retract(s12, store, target_id, retraction_id=retraction_id)
    assert event.retracts == target_id
    assert event.memory_id == retraction_id
    assert store.path.read_bytes().startswith(before)

    restarted = s12.RemoteMemoryStore(store.path, user_id="alice")
    assert restarted.read_all() == [target, neighbor, event]
    assert restarted.active_records() == [neighbor]
    after = restarted.path.read_bytes()
    assert _retract(s12, restarted, target_id, "unused-retry-id") == event
    assert restarted.path.read_bytes() == after

    result = s12.RecallEngine(restarted).recall("layered memory", as_of=AS_OF)
    assert [hit.memory_id for hit in result.hits] == [neighbor_id]
    assert result.retracted_records == 1


@pytest.mark.parametrize("automatic_id", [None, ""])
def test_generated_ids_can_be_retracted(s12, tmp_path: Path, automatic_id) -> None:
    """攻击点：现有自动生成 ID 的入口必须返回可撤回、可跨重启读取的真实 ID。"""
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    target = _append(s12, store, automatic_id)
    assert isinstance(target.memory_id, str) and target.memory_id

    event = _retract(s12, store, target.memory_id, retraction_id=automatic_id)
    assert isinstance(event.memory_id, str) and event.memory_id
    assert event.memory_id != target.memory_id
    assert event.retracts == target.memory_id

    restarted = s12.RemoteMemoryStore(store.path, user_id="alice")
    assert restarted.read_all() == [target, event]
    assert restarted.active_records() == []
    result = s12.RecallEngine(restarted).recall("layered memory", as_of=AS_OF)
    assert result.hits == ()
    assert result.retracted_records == 1
