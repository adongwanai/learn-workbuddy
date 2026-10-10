"""独立 Verifier：确认时间重放、构造器类型冒充与跨时区折叠。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "s14_context_compact" / "code.py"
T0 = "2026-10-01T09:00:00+00:00"


@pytest.fixture(scope="module")
def s14():
    stub_path = str(ROOT / "tests" / "stubs")
    sys.path.insert(0, stub_path)
    saved = sys.modules.pop("anthropic", None)
    spec = importlib.util.spec_from_file_location(
        "s14_pending_lifecycle_adversarial_mod", CODE
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(stub_path)
        sys.modules.pop("anthropic", None)
        sys.modules.pop(spec.name, None)
        if saved is not None:
            sys.modules["anthropic"] = saved


@pytest.mark.parametrize(
    "confirmed",
    [
        T0,
        "2026-10-01T08:59:59+00:00",
        "2026-10-01T17:00:00+08:00",  # 与 T0 是同一时刻
    ],
    ids=["replayed", "older", "same-instant-other-zone"],
)
def test_transition_requires_fresh_confirmation(s14, confirmed):
    item = s14.PendingItem("task", "发布", "transcript:demo:3", T0)
    state = s14.DurableContextState(pending_items=(item,))
    with pytest.raises(ValueError):
        s14.transition_pending_item(
            state,
            "task",
            "done",
            last_confirmed_at=confirmed,
            source_pointer="transcript:demo:7",
        )
    assert state.pending_items == (item,)
    assert (item.status, item.last_confirmed_at) == ("open", T0)


class _SpoofedStatus(str):
    __hash__ = None

    def __eq__(self, other):
        return True


def test_constructor_rejects_unknown_status_with_spoofed_equality(s14):
    # 攻击构造器，而非已有测试覆盖的 transition_pending_item 入口。
    status = _SpoofedStatus("closed")
    assert str(status) == "closed"
    with pytest.raises(ValueError):
        s14.PendingItem("task", "发布", "transcript:demo:3", T0, status=status)


def test_done_selection_uses_instants_and_keeps_active_items(s14):
    # old-local 的日期字符串最大，但真实时刻最早，必须被折叠。
    done_times = (
        ("old-local", "2026-10-02T00:00:00+14:00"),  # UTC 10:00
        ("new-a", "2026-10-01T11:00:00Z"),
        ("new-b", "2026-10-01T07:00:00-05:00"),     # UTC 12:00
        ("new-c", "2026-10-01T22:00:00+09:00"),     # UTC 13:00
    )
    items = (
        s14.PendingItem("active", "继续", "transcript:demo:1", T0),
        s14.PendingItem(
            "blocked", "等待", "transcript:demo:2", T0,
            reason="等待审批", status="blocked",
        ),
    ) + tuple(
        s14.PendingItem(
            item_id, "已完成", f"transcript:demo:{index}", confirmed,
            status="done",
        )
        for index, (item_id, confirmed) in enumerate(done_times, start=3)
    )
    state = s14.DurableContextState(pending_items=items)
    rendered = s14.render_durable_context(state)
    pending_lines = [
        line for line in rendered.splitlines() if line.startswith("- ")
    ]
    shown_ids = {
        line[2:].split(":", 1)[0]
        for line in pending_lines
        if ": " in line
    }
    assert shown_ids == {"active", "blocked", "new-a", "new-b", "new-c"}
    assert pending_lines.count("- done_omitted=1") == 1
    assert sum("(status=done;" in line for line in pending_lines) == 3
    assert "status=blocked; reason=等待审批" in rendered
    assert state.pending_items == items
    assert len(state.pending_items) == 6
