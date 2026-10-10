"""第三轮离线对抗测试：攻击空串比较、字符串子类及写入与撤回的身份一致性。"""

from __future__ import annotations

import importlib.util
import os
import sys
from collections import UserString
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def s12(tmp_path_factory: pytest.TempPathFactory):
    """隔离环境，优先使用离线桩，并以独立模块名加载章节。"""
    stub_dir = ROOT / "tests" / "stubs"
    state_root = tmp_path_factory.mktemp("s12-retraction-adversarial-r3-state")
    sys.path.insert(0, str(stub_dir))
    saved_anthropic = sys.modules.pop("anthropic", None)
    old_model = os.environ.get("MODEL_ID")
    old_home = os.environ.get("WORKBUDDY_HOME")
    os.environ["MODEL_ID"] = "offline-test-model"
    os.environ["WORKBUDDY_HOME"] = str(state_root)
    module_name = "s12_retraction_adversarial_r3_module"
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


class ComparisonRaises:
    """非法类型的相等比较会报错，用于检查类型校验是否先执行。"""

    def __eq__(self, other):
        raise TypeError("非法 ID 不应参与空串比较")


class TypedID(str):
    """文本相同但类型不同就不相等，模拟带自定义身份规则的字符串子类。"""

    def __eq__(self, other):
        return type(self) is type(other) and str.__eq__(self, other)

    __hash__ = str.__hash__


class FalseID(str):
    """非空文本的布尔值为假，用于攻击依赖对象真值的目标校验。"""

    def __bool__(self):
        return False


class UnhashableID(str):
    """文本和比较行为不变，但不支持哈希，用于检查锁内异常泄漏。"""

    __hash__ = None


def _source(s12):
    """创建有效来源，使测试只攻击 ID 边界。"""
    return s12.MemorySource(
        source_id="adversarial-r3-request",
        source_type="user_request",
        title="第三轮对抗测试来源",
        captured_at="2026-08-10T12:00:00Z",
    )


def _append(s12, store, memory_id):
    """通过公开写入入口提交指定 ID。"""
    return store.append(
        kind=s12.MemoryKind.CONVERSATION,
        memory_id=memory_id,
        content="layered memory",
        summary="layered memory",
        source=_source(s12),
        stored_at=AS_OF,
    )


def _retract(s12, store, memory_id, retraction_id=None):
    """通过公开撤回入口提交目标与事件 ID。"""
    return store.retract(
        memory_id,
        reason="只撤回指定记录。",
        source=_source(s12),
        retraction_id=retraction_id,
        stored_at=AS_OF,
    )


@pytest.mark.parametrize("field", ["memory_id", "retraction_id"])
@pytest.mark.parametrize(
    "bad_id",
    [
        pytest.param(UserString(""), id="wrapped-empty-string"),
        pytest.param(ComparisonRaises(), id="comparison-raises"),
    ],
)
def test_non_string_id_is_rejected_before_comparison(
    s12, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field, bad_id
) -> None:
    """攻击点：非法对象不能冒充空串，也不能在类型校验前执行相等比较或进入锁。"""
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    target = _append(s12, store, "target")
    before = store.path.read_bytes()

    def forbidden_lock(_path):
        """非法 ID 必须在任何加锁尝试之前被拒绝。"""
        raise AssertionError("非法 ID 已越过加锁前的校验边界")

    monkeypatch.setattr(s12, "_exclusive_store_lock", forbidden_lock)
    with pytest.raises(s12.RemoteMemoryValidationError):
        if field == "memory_id":
            _append(s12, store, bad_id)
        else:
            _retract(s12, store, target.memory_id, bad_id)

    assert store.path.read_bytes() == before
    assert store.read_all() == [target]
    assert store.active_records() == [target]


@pytest.mark.parametrize("field", ["memory_id", "retraction_id"])
def test_string_subclass_cannot_bypass_duplicate_check(
    s12, tmp_path: Path, field
) -> None:
    """攻击点：字符串子类的自定义相等规则不能绕过原始文本查重并破坏重读。"""
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    target = _append(s12, store, "collision")
    before = store.path.read_bytes()
    collision = TypedID("collision")

    with pytest.raises(s12.RemoteMemoryDuplicateError):
        if field == "memory_id":
            _append(s12, store, collision)
        else:
            _retract(s12, store, target.memory_id, collision)

    assert store.path.read_bytes() == before
    restarted = s12.RemoteMemoryStore(store.path, user_id="alice")
    assert restarted.read_all() == [target]
    assert restarted.active_records() == [target]


def test_false_string_subclass_round_trips(s12, tmp_path: Path) -> None:
    """攻击点：已写入的非空字符串子类不能因布尔值为假而无法用返回 ID 撤回。"""
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    target = _append(s12, store, FalseID("target"))
    assert str.__str__(target.memory_id) == "target"
    before = store.path.read_bytes()

    event = _retract(
        s12, store, target.memory_id, retraction_id=FalseID("retraction")
    )

    assert str.__str__(event.memory_id) == "retraction"
    assert str.__str__(event.retracts) == "target"
    assert store.path.read_bytes().startswith(before)
    restarted = s12.RemoteMemoryStore(store.path, user_id="alice")
    assert restarted.read_all() == [target, event]
    assert restarted.active_records() == []
    result = s12.RecallEngine(restarted).recall("layered memory", as_of=AS_OF)
    assert result.hits == ()
    assert result.retracted_records == 1


@pytest.mark.parametrize("field", ["memory_id", "retraction_id"])
def test_unhashable_string_subclass_round_trips(
    s12, tmp_path: Path, field
) -> None:
    """攻击点：不可哈希的字符串子类应按文本处理，两个写入入口不能出现锁内类型异常。"""
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    original = _append(s12, store, "target")
    before = store.path.read_bytes()

    if field == "memory_id":
        target = _append(s12, store, UnhashableID("replacement"))
        event = _retract(s12, store, target.memory_id, "retraction")
        expected = [original, target, event]
        active = [original]
    else:
        event = _retract(
            s12, store, original.memory_id, UnhashableID("retraction")
        )
        expected = [original, event]
        active = []

    assert str.__str__(event.memory_id) == "retraction"
    assert store.path.read_bytes().startswith(before)
    restarted = s12.RemoteMemoryStore(store.path, user_id="alice")
    assert restarted.read_all() == expected
    assert restarted.active_records() == active
    result = s12.RecallEngine(restarted).recall("layered memory", as_of=AS_OF)
    assert [hit.memory_id for hit in result.hits] == [
        record.memory_id for record in active
    ]
    assert result.retracted_records == 1
