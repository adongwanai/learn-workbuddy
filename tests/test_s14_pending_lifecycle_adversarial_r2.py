"""复核完整迁移链的确认时间，以及 done 增长与消息硬上限的隔离。"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
T0 = "2026-10-01T09:00:00Z"


@pytest.fixture(scope="module")
def s14():
    """用独立模块名和离线替身导入章节，最终恢复环境与模块。"""
    stub_dir = str(ROOT / "tests" / "stubs")
    saved_environ = os.environ.copy()
    saved_path = sys.path[:]
    saved_anthropic = sys.modules.pop("anthropic", None)
    name = "s14_pending_lifecycle_adversarial_r2_module"
    sys.path.insert(0, stub_dir)
    os.environ["MODEL_ID"] = "offline-test-model"
    try:
        spec = importlib.util.spec_from_file_location(
            name, ROOT / "s14_context_compact" / "code.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path[:] = saved_path
        sys.modules.pop(name, None)
        sys.modules.pop("anthropic", None)
        if saved_anthropic is not None:
            sys.modules["anthropic"] = saved_anthropic
        os.environ.clear()
        os.environ.update(saved_environ)


def test_lifecycle_chain_uses_latest_confirmation_and_preserves_other_items(s14):
    """攻击只比较初始时间、遗漏原因清空或误改其他条目的迁移实现。"""
    target = s14.PendingItem("task", "发布", "transcript:demo:1", T0)
    other = s14.PendingItem("other", "保留", "transcript:demo:2", T0)
    state = s14.DurableContextState(pending_items=(target, other))
    blocked = s14.transition_pending_item(
        state,
        "task",
        "blocked",
        last_confirmed_at="2026-10-01T18:00:00+08:00",
        source_pointer="transcript:demo:3",
        reason="等待审批",
    )

    # 此时刻晚于初始值，却早于最近一次确认，仍然必须拒绝。
    with pytest.raises(ValueError):
        s14.transition_pending_item(
            blocked,
            "task",
            "open",
            last_confirmed_at="2026-10-01T09:30:00Z",
        )

    reopened = s14.transition_pending_item(
        blocked,
        "task",
        "open",
        last_confirmed_at="2026-10-01T11:00:00Z",
    )
    done = s14.transition_pending_item(
        reopened,
        "task",
        "done",
        last_confirmed_at="2026-10-01T12:00:00Z",
        source_pointer="transcript:demo:4",
    )

    assert tuple(
        snapshot.pending_items[0].status
        for snapshot in (state, blocked, reopened, done)
    ) == ("open", "blocked", "open", "done")
    assert blocked.pending_items[0].reason == "等待审批"
    assert reopened.pending_items[0].reason is None
    assert reopened.pending_items[0].source_pointer == "transcript:demo:3"
    assert (
        done.pending_items[0].reason,
        done.pending_items[0].source_pointer,
        done.pending_items[0].last_confirmed_at,
    ) == (None, "transcript:demo:4", "2026-10-01T12:00:00Z")
    assert state.pending_items == (target, other)
    assert all(
        snapshot.pending_items[1] is other
        for snapshot in (blocked, reopened, done)
    )


def test_done_growth_does_not_change_hard_limit_rejection(s14, monkeypatch):
    """攻击把 durable 项计入消息上限，或因 done 折叠而漏掉上限拒绝的实现。"""
    monkeypatch.setattr(s14, "TOKEN_THRESHOLD", 1)
    monkeypatch.setattr(s14, "HARD_LIMIT", 10)
    messages = [{"role": "user", "content": "x" * 200}]

    for count in (0, 200):
        items = tuple(
            s14.PendingItem(
                f"done-{index}",
                "完成",
                f"transcript:demo:{index + 1}",
                T0,
                status="done",
            )
            for index in range(count)
        )
        state = s14.DurableContextState(pending_items=items)
        with pytest.raises(s14.MessageViewLimitExceeded) as caught:
            s14.compact_context(
                messages,
                state,
                summarizer=lambda _: "摘要",
                verbose=False,
            )

        # 单条文本为 200 / 4，加消息角色开销 4；单条消息不能被摘要缩减。
        assert (
            caught.value.tokens_before,
            caught.value.tokens_after,
            caught.value.hard_limit,
        ) == (54, 54, 10)
        assert state.pending_items == items
        assert len(state.pending_items) == count

    assert messages == [{"role": "user", "content": "x" * 200}]
