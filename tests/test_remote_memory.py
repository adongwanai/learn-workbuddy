"""Offline contracts for s12 stored memory and query-scoped recall."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def s12(tmp_path_factory: pytest.TempPathFactory):
    stub_dir = ROOT / "tests" / "stubs"
    state_root = tmp_path_factory.mktemp("s12-import-state")
    sys.path.insert(0, str(stub_dir))
    saved_anthropic = sys.modules.pop("anthropic", None)
    old_model = os.environ.get("MODEL_ID")
    old_home = os.environ.get("WORKBUDDY_HOME")
    os.environ["MODEL_ID"] = "offline-test-model"
    os.environ["WORKBUDDY_HOME"] = str(state_root)
    module_name = "s12_remote_memory_test_module"
    try:
        spec = importlib.util.spec_from_file_location(
            module_name, ROOT / "s12_cloud_memory" / "code.py"
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
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


def _time(day: int) -> datetime:
    return datetime(2026, 8, day, 12, tzinfo=timezone.utc)


def _source(s12, source_id: str, day: int, *, source_type: str = "transcript"):
    return s12.MemorySource(
        source_id=source_id,
        source_type=source_type,
        title=f"source {source_id}",
        captured_at=s12._iso(_time(day)),
    )


def test_import_keeps_provider_and_default_store_lazy(s12) -> None:
    assert s12.client is None
    assert s12.DEFAULT_STORE is None
    assert s12.DEFAULT_RECALL is None
    assert s12.SYSTEM is None


def test_provider_config_is_checked_only_at_online_boundary(
    s12, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MODEL_ID", raising=False)
    monkeypatch.setattr(s12, "MODEL", None)
    monkeypatch.setattr(s12, "client", None)

    with pytest.raises(RuntimeError, match="MODEL_ID is not set"):
        s12.runtime_client()


def test_stored_memory_keeps_source_and_survives_restart(s12, tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    writer = s12.RemoteMemoryStore(path, user_id="alice")
    created = writer.append(
        kind=s12.MemoryKind.CONVERSATION,
        memory_id="memory-1",
        content="The project selected SQLite WAL for local persistence.",
        summary="Selected SQLite WAL.",
        source=_source(s12, "transcript-42", 1),
        stored_at=_time(2),
    )

    reader = s12.RemoteMemoryStore(path, user_id="alice")
    recovered = reader.read_all()[0]

    assert recovered == created
    assert recovered.source.source_id == "transcript-42"
    assert recovered.source.captured_at == "2026-08-01T12:00:00Z"


def test_duplicate_check_and_append_are_atomic_for_concurrent_writers(
    s12,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second writer must not pass a stale duplicate check."""

    path = tmp_path / "concurrent-records.jsonl"
    first = s12.RemoteMemoryStore(path, user_id="alice")
    second = s12.RemoteMemoryStore(path, user_id="alice")
    source = _source(s12, "stable-source", 1)

    real_write = s12.os.write
    first_write_started = threading.Event()
    release_first_write = threading.Event()
    second_started = threading.Event()
    second_finished = threading.Event()
    write_gate = threading.Lock()
    should_block_first_write = True

    def controlled_write(descriptor: int, payload: bytes) -> int:
        nonlocal should_block_first_write
        with write_gate:
            block_this_write = should_block_first_write
            should_block_first_write = False
        if block_this_write:
            first_write_started.set()
            if not release_first_write.wait(timeout=2):
                raise TimeoutError("test did not release the first memory writer")
        return real_write(descriptor, payload)

    monkeypatch.setattr(s12.os, "write", controlled_write)
    outcomes: dict[str, str] = {}

    def append(store, label: str) -> None:
        if label == "second":
            second_started.set()
        try:
            store.append(
                kind=s12.MemoryKind.CONVERSATION,
                memory_id="stable-id",
                content="One immutable concurrent fact.",
                summary="One immutable concurrent fact.",
                source=source,
                stored_at=_time(2),
            )
            outcomes[label] = "created"
        except Exception as exc:  # captured so thread failures stay assertable
            outcomes[label] = type(exc).__name__
        finally:
            if label == "second":
                second_finished.set()

    first_thread = threading.Thread(target=append, args=(first, "first"))
    second_thread = threading.Thread(target=append, args=(second, "second"))
    first_thread.start()
    assert first_write_started.wait(timeout=1)
    second_thread.start()
    assert second_started.wait(timeout=1)
    try:
        # The first writer is paused after acquiring the file lock. The second
        # writer must still be waiting, not reading an empty store and appending.
        assert not second_finished.wait(timeout=0.05)
    finally:
        release_first_write.set()
        first_thread.join(timeout=2)
        second_thread.join(timeout=2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert outcomes == {
        "first": "created",
        "second": "RemoteMemoryDuplicateError",
    }
    assert [record.memory_id for record in first.read_all()] == ["stable-id"]
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_store_rejects_anonymous_or_invalid_source(s12, tmp_path: Path) -> None:
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")

    with pytest.raises(s12.RemoteMemoryValidationError, match="source_id"):
        store.append(
            kind=s12.MemoryKind.CONVERSATION,
            content="Source is required.",
            summary="Missing source.",
            source=s12.MemorySource("", "transcript", "Missing", s12._iso(_time(1))),
        )
    with pytest.raises(s12.RemoteMemoryCorruptionError, match="invalid timestamp"):
        store.append(
            kind=s12.MemoryKind.CONVERSATION,
            content="Source time must parse.",
            summary="Bad source time.",
            source=s12.MemorySource("source-1", "transcript", "Bad time", "yesterday"),
        )

    assert store.read_all() == []


def test_recall_returns_query_hit_source_score_contract(s12, tmp_path: Path) -> None:
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    store.append(
        kind=s12.MemoryKind.CONVERSATION,
        memory_id="memory-new",
        content="We designed layered memory boundaries for the agent harness.",
        summary="Layered memory boundary design.",
        source=_source(s12, "transcript-new", 9),
        stored_at=_time(9),
    )
    store.append(
        kind=s12.MemoryKind.CONVERSATION,
        memory_id="memory-old",
        content="We compared unrelated frontend typography options.",
        summary="Frontend typography comparison.",
        source=_source(s12, "transcript-old", 1),
        stored_at=_time(1),
    )

    result = s12.RecallEngine(store).recall(
        "continue the layered memory design",
        query_id="query-1",
        as_of=_time(10),
    )

    assert result.query.query_id == "query-1"
    assert result.query.user_scope == store.user_scope
    assert result.query.normalized_text == "continue the layered memory design"
    assert result.query.terms == ("continue", "design", "layered", "memory", "the")
    assert result.searched_records == 2
    assert result.candidate_records == 1
    assert len(result.hits) == 1
    hit = result.hits[0]
    assert hit.query_id == "query-1"
    assert hit.memory_id == "memory-new"
    assert hit.scope.user_scope == store.user_scope
    assert hit.scope.memory_kind is s12.MemoryKind.CONVERSATION
    assert hit.provenance.source_id == "transcript-new"
    # ``source`` remains a compatibility view for the layered walkthrough.
    assert hit.source.source_id == "transcript-new"
    assert hit.rank == 1
    assert 0 < hit.score <= 1
    assert "memory" in hit.matched_terms
    assert hit.score == hit.score_breakdown.total
    assert hit.score_breakdown.lexical_contribution > 0
    assert hit.score_breakdown.recency_contribution > 0


def test_recall_normalizes_english_and_chinese_queries(s12, tmp_path: Path) -> None:
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    store.append(
        kind=s12.MemoryKind.CONVERSATION,
        memory_id="memory-bilingual",
        content="We documented layered memory boundaries，并讨论分层记忆的作用域隔离。",
        summary="Layered memory / 分层记忆 boundary.",
        source=_source(s12, "transcript-bilingual", 9),
        stored_at=_time(9),
    )
    engine = s12.RecallEngine(store)

    english = engine.recall("  LAYERED   Memory  ", query_id="query-en", as_of=_time(10))
    chinese = engine.recall("分层记忆", query_id="query-zh", as_of=_time(10))

    assert english.query.text == "LAYERED Memory"
    assert english.query.normalized_text == "layered memory"
    assert english.query.terms == ("layered", "memory")
    assert english.hits[0].memory_id == "memory-bilingual"
    assert chinese.query.normalized_text == "分层记忆"
    assert chinese.query.terms == ("分层", "层记", "记忆")
    assert chinese.hits[0].score_breakdown.matched_terms == ("分层", "层记", "记忆")


def test_stable_rank_uses_explicit_tie_breakers(s12, tmp_path: Path) -> None:
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    # Append in reverse ID order. Equal content and timestamps must not leak
    # storage iteration order into the ranked result.
    for memory_id in ("memory-b", "memory-a"):
        store.append(
            kind=s12.MemoryKind.CONVERSATION,
            memory_id=memory_id,
            content="Memory retrieval contract.",
            summary="Memory retrieval contract.",
            source=_source(s12, f"transcript-{memory_id}", 9),
            stored_at=_time(9),
        )

    result = s12.RecallEngine(store).recall(
        "memory retrieval", query_id="query-tie", as_of=_time(10)
    )

    assert [hit.memory_id for hit in result.hits] == ["memory-a", "memory-b"]
    assert [hit.rank for hit in result.hits] == [1, 2]
    assert result.hits[0].score_breakdown == result.hits[1].score_breakdown


@pytest.mark.parametrize(
    ("query", "error"),
    [
        ("   ", "must not be empty"),
        ("!!!", "no searchable terms"),
    ],
)
def test_recall_rejects_empty_or_unsearchable_queries(
    s12, tmp_path: Path, query: str, error: str
) -> None:
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")

    with pytest.raises(s12.RemoteMemoryValidationError, match=error):
        s12.RecallEngine(store).recall(query, as_of=_time(10))


def test_no_match_is_explicit_data_and_renders_no_context(s12, tmp_path: Path) -> None:
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    store.append(
        kind=s12.MemoryKind.CONVERSATION,
        memory_id="memory-1",
        content="Agent loop dispatch contract.",
        summary="Agent loop dispatch.",
        source=_source(s12, "transcript-1", 9),
        stored_at=_time(9),
    )

    result = s12.RecallEngine(store).recall(
        "database migration", query_id="query-miss", as_of=_time(10)
    )

    assert result.searched_records == 1
    assert result.candidate_records == 0
    assert result.hits == ()
    assert result.empty_reason == "no_matching_terms"
    assert s12.render_recall_context(result) == ""


def test_recall_is_a_derived_view_and_never_appends_to_store(s12, tmp_path: Path) -> None:
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    store.append(
        kind=s12.MemoryKind.CONVERSATION,
        memory_id="memory-1",
        content="Agent loop tools use explicit dispatch contracts.",
        summary="Explicit agent loop dispatch.",
        source=_source(s12, "transcript-1", 8),
        stored_at=_time(8),
    )
    before = store.path.read_bytes()

    first = s12.RecallEngine(store).recall(
        "agent loop dispatch", query_id="query-a", as_of=_time(10)
    )
    second = s12.RecallEngine(store).recall(
        "missing database topic", query_id="query-b", as_of=_time(10)
    )

    assert first.hits[0].query_id == "query-a"
    assert second.query.query_id == "query-b"
    assert second.hits == ()
    assert store.path.read_bytes() == before
    assert len(store.read_all()) == 1


def test_profile_snapshot_is_injected_but_not_returned_as_history_hit(
    s12, tmp_path: Path
) -> None:
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    store.append(
        kind=s12.MemoryKind.PROFILE,
        memory_id="profile-old",
        content="Response preference: verbose.",
        summary="Old remote profile",
        source=_source(s12, "profile-1", 1, source_type="profile_snapshot"),
        stored_at=_time(1),
    )
    store.append(
        kind=s12.MemoryKind.PROFILE,
        memory_id="profile-new",
        content="Response preference: concise.",
        summary="New remote profile",
        source=_source(s12, "profile-2", 9, source_type="profile_snapshot"),
        stored_at=_time(9),
    )
    store.append(
        kind=s12.MemoryKind.CONVERSATION,
        memory_id="conversation-1",
        content="Discussed concise API error contracts.",
        summary="Concise API errors.",
        source=_source(s12, "transcript-1", 8),
        stored_at=_time(8),
    )

    prompt = s12.build_system_prompt(store)
    result = s12.RecallEngine(store).recall(
        "concise response", query_id="query-profile", as_of=_time(10)
    )

    assert store.latest_profile().memory_id == "profile-new"
    assert "profile-new" in prompt
    assert "profile-2" in prompt
    assert "concise" in prompt
    assert all(hit.memory_id != "profile-new" for hit in result.hits)
    assert result.searched_records == 1


def test_remote_store_rejects_records_from_another_user_scope(
    s12, tmp_path: Path
) -> None:
    alice = s12.RemoteMemoryStore(tmp_path / "alice.jsonl", user_id="alice")
    bob = s12.RemoteMemoryStore(tmp_path / "bob.jsonl", user_id="bob")
    alice.append(
        kind=s12.MemoryKind.CONVERSATION,
        memory_id="private-memory",
        content="Alice private project history.",
        summary="Alice history.",
        source=_source(s12, "transcript-alice", 8),
        stored_at=_time(8),
    )
    bob.path.write_bytes(alice.path.read_bytes())

    with pytest.raises(s12.RemoteMemoryScopeError, match="another user scope"):
        bob.read_all()


def test_rendered_context_preserves_query_source_and_score(s12, tmp_path: Path) -> None:
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    store.append(
        kind=s12.MemoryKind.CONVERSATION,
        memory_id="memory-1",
        content="The harness uses append-only transcript evidence.",
        summary="Append-only transcript evidence.",
        source=_source(s12, "transcript-1", 9),
        stored_at=_time(9),
    )
    result = s12.RecallEngine(store).recall(
        "transcript evidence", query_id="query-xml", as_of=_time(10)
    )

    context = s12.render_recall_context(result)

    assert 'query_id="query-xml"' in context
    assert f'user_scope="{store.user_scope}"' in context
    assert 'memory_id="memory-1"' in context
    assert 'source_id="transcript-1"' in context
    assert '<score total="' in context
    assert 'lexical_coverage="' in context
    assert '<provenance source_id="transcript-1"' in context


def test_tool_payload_is_structured_json_not_preformatted_history(
    s12, monkeypatch, tmp_path: Path
) -> None:
    store = s12.RemoteMemoryStore(tmp_path / "tool-records.jsonl", user_id="alice")
    store.append(
        kind=s12.MemoryKind.CONVERSATION,
        memory_id="memory-tool-1",
        content="Layered memory keeps query and source contracts explicit.",
        summary="Layered memory source contracts.",
        source=_source(s12, "transcript-tool-1", 9),
        stored_at=_time(9),
    )
    monkeypatch.setattr(s12, "DEFAULT_STORE", store)
    monkeypatch.setattr(s12, "DEFAULT_RECALL", s12.RecallEngine(store))
    monkeypatch.setattr(s12, "SYSTEM", None)

    payload = json.loads(s12.recall_history("layered memory design", limit=2))

    assert set(payload) == {
        "query",
        "hits",
        "searched_records",
        "candidate_records",
        "retracted_records",
        "empty_reason",
    }
    assert payload["query"]["text"] == "layered memory design"
    assert all(
        {
            "scope",
            "provenance",
            "source",
            "score",
            "score_breakdown",
        }.issubset(hit)
        for hit in payload["hits"]
    )


# ---------------------------------------------------------------------------
# 撤回：只追加的逻辑删除。read_all 保留完整审计链，召回与 profile 只看生效记录。
# ---------------------------------------------------------------------------


def _conversation(s12, store, memory_id: str, content: str, day: int):
    return store.append(
        kind=s12.MemoryKind.CONVERSATION,
        memory_id=memory_id,
        content=content,
        summary=content,
        source=_source(s12, f"transcript-{memory_id}", day),
        stored_at=_time(day),
    )


def _user_request(s12, day: int = 10):
    return _source(s12, "user-request-1", day, source_type="user_request")


def test_retraction_hides_record_from_recall_but_keeps_audit_trail(
    s12, tmp_path: Path
) -> None:
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    _conversation(s12, store, "memory-wrong", "Layered memory uses Redis as the store.", 8)
    _conversation(s12, store, "memory-right", "Layered memory uses SQLite WAL as the store.", 9)
    engine = s12.RecallEngine(store)

    before = engine.recall("layered memory store", query_id="q-before", as_of=_time(10))
    retraction = store.retract(
        "memory-wrong",
        reason="用户指出这条记录写错了。",
        source=_user_request(s12),
        retraction_id="retraction-1",
        stored_at=_time(10),
    )
    after = engine.recall("layered memory store", query_id="q-after", as_of=_time(10))

    assert {hit.memory_id for hit in before.hits} == {"memory-wrong", "memory-right"}
    assert [hit.memory_id for hit in after.hits] == ["memory-right"]
    assert after.searched_records == 1
    assert after.retracted_records == 1
    assert after.to_dict()["retracted_records"] == 1
    assert retraction.kind is s12.MemoryKind.RETRACTION
    assert retraction.retracts == "memory-wrong"
    # 审计视图完整：原记录和撤回记录都还在，且能跨重启读回。
    restarted = s12.RemoteMemoryStore(store.path, user_id="alice")
    assert [record.memory_id for record in restarted.read_all()] == [
        "memory-wrong",
        "memory-right",
        "retraction-1",
    ]
    assert [record.memory_id for record in restarted.active_records()] == ["memory-right"]


def test_retracting_latest_profile_falls_back_to_previous_snapshot(
    s12, tmp_path: Path
) -> None:
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    for memory_id, day, content in (
        ("profile-old", 1, "Response preference: concise."),
        ("profile-bad", 9, "Response preference: written by mistake."),
    ):
        store.append(
            kind=s12.MemoryKind.PROFILE,
            memory_id=memory_id,
            content=content,
            summary="Remote profile",
            source=_source(s12, f"snapshot-{memory_id}", day, source_type="profile_snapshot"),
            stored_at=_time(day),
        )
    assert store.latest_profile().memory_id == "profile-bad"

    store.retract("profile-bad", reason="错误的 profile 快照。", source=_user_request(s12))

    assert store.latest_profile().memory_id == "profile-old"
    prompt = s12.build_system_prompt(store)
    assert "profile-old" in prompt
    assert "written by mistake" not in prompt


def test_retract_rejects_unknown_foreign_or_retraction_targets(
    s12, tmp_path: Path
) -> None:
    alice = s12.RemoteMemoryStore(tmp_path / "alice.jsonl", user_id="alice")
    bob = s12.RemoteMemoryStore(tmp_path / "bob.jsonl", user_id="bob")
    _conversation(s12, alice, "alice-memory", "Alice project history.", 8)
    _conversation(s12, bob, "bob-memory", "Bob project history.", 8)
    retraction = alice.retract(
        "alice-memory", reason="不再需要。", source=_user_request(s12)
    )
    bob_before = bob.path.read_bytes()

    with pytest.raises(s12.RemoteMemoryValidationError, match="unknown memory_id"):
        alice.retract("missing-memory", reason="不存在。", source=_user_request(s12))
    # 每个作用域使用独立的 store 文件，别人的 memory_id 不在本文件里，按未知目标拒绝，也不会动到对方的文件。
    with pytest.raises(s12.RemoteMemoryValidationError, match="unknown memory_id"):
        alice.retract("bob-memory", reason="越权撤回。", source=_user_request(s12))
    with pytest.raises(s12.RemoteMemoryValidationError, match="retraction record"):
        alice.retract(retraction.memory_id, reason="撤回撤回。", source=_user_request(s12))
    # 撤回记录只能经由 retract() 写入，绕开目标校验的 append 会被拒绝。
    with pytest.raises(s12.RemoteMemoryValidationError, match="use retract"):
        alice.append(
            kind=s12.MemoryKind.RETRACTION,
            content="伪造的撤回。",
            summary="Forged retraction",
            source=_user_request(s12),
        )

    assert bob.path.read_bytes() == bob_before
    assert len(alice.read_all()) == 2


def test_retract_is_idempotent_for_the_same_target(s12, tmp_path: Path) -> None:
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    _conversation(s12, store, "memory-1", "Agent loop dispatch contract.", 8)

    first = store.retract("memory-1", reason="第一次撤回。", source=_user_request(s12))
    second = store.retract("memory-1", reason="重试撤回。", source=_user_request(s12))

    assert second == first
    assert second.content == "第一次撤回。"
    kinds = [record.kind for record in store.read_all()]
    assert kinds.count(s12.MemoryKind.RETRACTION) == 1


def test_concurrent_retractions_leave_exactly_one_record(s12, tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    _conversation(s12, s12.RemoteMemoryStore(path, user_id="alice"), "memory-1", "Shared fact.", 8)
    workers = 8
    # Barrier 让所有线程在同一时刻起跑，竞争完全交给文件锁裁决，不依赖 sleep。
    start = threading.Barrier(workers)
    results: list[object] = [None] * workers

    def retract(index: int) -> None:
        store = s12.RemoteMemoryStore(path, user_id="alice")
        start.wait(timeout=5)
        try:
            results[index] = store.retract(
                "memory-1",
                reason=f"并发撤回 {index}",
                source=_user_request(s12),
            )
        except Exception as exc:  # 记录下来，让线程内的失败可以被断言
            results[index] = exc

    threads = [threading.Thread(target=retract, args=(index,)) for index in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert all(isinstance(result, s12.StoredMemory) for result in results)
    assert len({result.memory_id for result in results}) == 1
    records = s12.RemoteMemoryStore(path, user_id="alice").read_all()
    assert [record.kind for record in records].count(s12.MemoryKind.RETRACTION) == 1


def test_legacy_records_without_retracts_field_still_load(s12, tmp_path: Path) -> None:
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="alice")
    legacy = {
        "memory_id": "legacy-1",
        "user_scope": store.user_scope,
        "kind": "conversation",
        "content": "Legacy layered memory record.",
        "summary": "Legacy layered memory record.",
        "source": {
            "source_id": "transcript-legacy",
            "source_type": "transcript",
            "title": "legacy",
            "captured_at": "2026-08-09T12:00:00Z",
        },
        "stored_at": "2026-08-09T12:00:00Z",
        "schema_version": 1,
    }
    store.path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

    (record,) = store.read_all()
    result = s12.RecallEngine(store).recall("layered memory", as_of=_time(10))

    assert record.retracts is None
    # 普通记录序列化时不带 retracts，旧格式保持不变。
    assert record.to_dict() == legacy
    assert [hit.memory_id for hit in result.hits] == ["legacy-1"]
    assert result.retracted_records == 0

    # 撤回记录缺少 retracts，或普通记录多出 retracts，都视为损坏而不是静默接受。
    for bad in (
        {**legacy, "memory_id": "bad-retraction", "kind": "retraction"},
        {**legacy, "memory_id": "bad-conversation", "retracts": "legacy-1"},
    ):
        with pytest.raises(s12.RemoteMemoryCorruptionError, match="inconsistent"):
            s12.StoredMemory.from_dict(bad)
