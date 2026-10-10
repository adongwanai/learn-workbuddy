#!/usr/bin/env python3
"""s12_cloud_memory - stored remote memory and query-scoped recall.

s10 and s11 define durable state owned by a workspace and a user.  This
chapter adds a remote/service boundary and, more importantly, separates two
objects that are often both called "memory":

* ``StoredMemory`` is a durable record with owner and source provenance.
* ``RecallHit`` is a scored candidate produced for one ``RecallQuery``.

A hit is not a second stored memory and it is not automatically trusted prompt
context.  The recall path stays explicit and inspectable:

    normalize -> candidate -> score -> stable rank -> render

The teaching scorer is intentionally small (lexical coverage plus recency),
but every contribution is carried into the hit contract.  A production
provider can replace candidate generation or scoring without changing the
scope, provenance, ranking, and rendering boundaries taught here.

Usage:
    python s12_cloud_memory/code.py --demo
    python s12_cloud_memory/code.py
"""

from __future__ import annotations

import fcntl
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Iterator, Mapping, Sequence


# Machine-readable learning path metadata.  The chapter preserves s11's user
# ownership boundary while distinguishing persistent evidence from a temporary
# retrieval view.
PROGRESSION = {
    "chapter": "s12_cloud_memory",
    "builds_on": ["s11_user_memory"],
    "adds": [
        "source-bearing remote memory records",
        "query-scoped recall hits",
        "normalize-candidate-score-rank-render recall pipeline",
        "per-hit score breakdown, scope, and provenance",
        "append-only retraction records that forget without rewriting history",
    ],
    "preserves": ["workspace and user memory ownership boundaries"],
}


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mini_workbuddy.chapter_demo import maybe_run_chapter_demo
from mini_workbuddy.chapter_demo import prepare_chapter_provider

maybe_run_chapter_demo(__file__, PROGRESSION)
# The chapter is also a library boundary for later integration examples.  Only
# the direct teaching entrypoint owns CLI provider parsing; an importing
# runtime may have a different provider vocabulary (for example ``offline``).
if __name__ == "__main__":
    prepare_chapter_provider()

from anthropic import Anthropic
from dotenv import load_dotenv
from mini_workbuddy.paths import tutorial_workbuddy_home


try:
    import readline

    readline.parse_and_bind("set bind-tty-special-chars off")
    readline.parse_and_bind("set input-meta on")
    readline.parse_and_bind("set output-meta on")
    readline.parse_and_bind("set convert-meta off")
except ImportError:
    pass


load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)


WORKDIR = Path.cwd()
MODEL = os.environ.get("MODEL_ID")
client: Anthropic | None = None


def _split_jsonl(raw: bytes) -> list[str]:
    # 按 read_text() 规范化换行后只按 "\n" 切分；不用 splitlines()，以免 U+2028 等被当成行界
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    parts = text.split("\n")
    return [p + "\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def runtime_client() -> tuple[Anthropic, str]:
    """Create the provider client only when the online loop actually needs it.

    Storage, recall, rendering, and cross-chapter walkthroughs are pure local
    contracts.  Requiring a provider key while importing those types would
    make an otherwise keyless lesson impossible to compose or test.
    """

    global client
    model = os.environ.get("MODEL_ID") or MODEL
    if not model:
        raise RuntimeError(
            "MODEL_ID is not set. Copy .env.example to .env and fill in "
            "the provider key and MODEL_ID (see README quick start)."
        )
    if client is None:
        client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
    return client, model


SCHEMA_VERSION = 1
MAX_RECALL_LIMIT = 10
MAX_RECORD_CHARS = 20_000
LEXICAL_WEIGHT = 0.85
RECENCY_WEIGHT = 0.15
RECENCY_WINDOW_DAYS = 30.0
DEFAULT_REMOTE_ROOT = tutorial_workbuddy_home() / "remote-memory"


class RemoteMemoryError(RuntimeError):
    """Base class for remote-memory contract failures."""


class RemoteMemoryValidationError(RemoteMemoryError):
    """Raised before malformed memory or query data crosses the boundary."""


class RemoteMemoryDuplicateError(RemoteMemoryValidationError):
    """Raised when an atomic append observes an existing memory identifier."""


class RemoteMemoryScopeError(RemoteMemoryError):
    """Raised when a record belongs to another remote user scope."""


class RemoteMemoryCorruptionError(RemoteMemoryError):
    """Raised when a durable record cannot be decoded safely."""


class MemoryKind(str, Enum):
    """Remote records have different selection policies."""

    PROFILE = "profile"
    CONVERSATION = "conversation"
    # 撤回记录：本身不参与召回，只负责指向被撤回的 memory_id。
    RETRACTION = "retraction"


@dataclass(frozen=True)
class MemorySource:
    """Where a durable record came from, independent of its current storage."""

    source_id: str
    source_type: str
    title: str
    captured_at: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "MemorySource":
        try:
            return cls(
                source_id=str(payload["source_id"]),
                source_type=str(payload["source_type"]),
                title=str(payload.get("title", "")),
                captured_at=str(payload["captured_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RemoteMemoryCorruptionError(f"invalid memory source: {exc}") from exc


@dataclass(frozen=True)
class StoredMemory:
    """One immutable, source-bearing record in remote durable storage."""

    memory_id: str
    user_scope: str
    kind: MemoryKind
    content: str
    summary: str
    source: MemorySource
    stored_at: str
    schema_version: int = SCHEMA_VERSION
    # 只有 RETRACTION 记录才带这个字段，指向被撤回的 memory_id。
    # 旧 JSONL 没有这个字段，读取时默认为 None，保持向后兼容。
    retracts: str | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "StoredMemory":
        try:
            raw_retracts = payload.get("retracts")
            record = cls(
                memory_id=str(payload["memory_id"]),
                user_scope=str(payload["user_scope"]),
                kind=MemoryKind(str(payload["kind"])),
                content=str(payload["content"]),
                summary=str(payload["summary"]),
                source=MemorySource.from_dict(dict(payload["source"])),
                stored_at=str(payload["stored_at"]),
                schema_version=int(payload.get("schema_version", SCHEMA_VERSION)),
                retracts=None if raw_retracts is None else str(raw_retracts),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RemoteMemoryCorruptionError(f"invalid stored memory: {exc}") from exc
        # 撤回记录必须指向目标；普通记录不能带 retracts，否则语义不明。
        if (record.kind is MemoryKind.RETRACTION) != (record.retracts is not None):
            raise RemoteMemoryCorruptionError(
                f"memory {record.memory_id} has inconsistent retraction fields"
            )
        return record

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        # 普通记录不写 retracts 字段，旧记录的落盘格式保持逐字节不变。
        if self.retracts is None:
            payload.pop("retracts")
        return payload


@dataclass(frozen=True)
class RecallQuery:
    """A normalized, self-contained request scoped to one user and one turn.

    text preserves a readable form of the caller's input. Retrieval uses
    normalized_text and deterministic terms instead, so whitespace, case, and
    Unicode width do not silently change matching behavior.
    """

    query_id: str
    text: str
    normalized_text: str
    terms: tuple[str, ...]
    user_scope: str
    limit: int
    issued_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "query_id": self.query_id,
            "text": self.text,
            "normalized_text": self.normalized_text,
            "terms": list(self.terms),
            "user_scope": self.user_scope,
            "limit": self.limit,
            "issued_at": self.issued_at,
        }


@dataclass(frozen=True)
class RecallScope:
    """The ownership and record-kind boundary attached to every hit."""

    user_scope: str
    memory_kind: MemoryKind

    def to_dict(self) -> dict[str, str]:
        return {
            "user_scope": self.user_scope,
            "memory_kind": self.memory_kind.value,
        }


@dataclass(frozen=True)
class RecallCandidate:
    """One stored record that shares searchable terms with the query.

    Candidate generation is deliberately separate from scoring. This object
    remains an internal derived view: it neither copies nor mutates the durable
    StoredMemory record.
    """

    query_id: str
    record: StoredMemory
    searchable_terms: tuple[str, ...]
    matched_terms: tuple[str, ...]
    captured_at: datetime


@dataclass(frozen=True)
class RecallScoreBreakdown:
    """Auditable components of the small offline teaching score."""

    matched_terms: tuple[str, ...]
    query_term_count: int
    lexical_coverage: float
    recency: float
    lexical_contribution: float
    recency_contribution: float
    total: float

    def to_dict(self) -> dict[str, object]:
        return {
            "matched_terms": list(self.matched_terms),
            "query_term_count": self.query_term_count,
            "lexical_coverage": self.lexical_coverage,
            "recency": self.recency,
            "weights": {
                "lexical": LEXICAL_WEIGHT,
                "recency": RECENCY_WEIGHT,
            },
            "contributions": {
                "lexical": self.lexical_contribution,
                "recency": self.recency_contribution,
            },
            "total": self.total,
        }


@dataclass(frozen=True)
class ScoredRecallCandidate:
    """A candidate paired with its score before stable ranking assigns rank."""

    candidate: RecallCandidate
    score_breakdown: RecallScoreBreakdown


@dataclass(frozen=True)
class RecallHit:
    """A ranked query view carrying scope, provenance, and score evidence."""

    query_id: str
    memory_id: str
    rank: int
    snippet: str
    scope: RecallScope
    provenance: MemorySource
    score_breakdown: RecallScoreBreakdown

    @property
    def source(self) -> MemorySource:
        """Compatibility view used by the layered-memory walkthrough."""

        return self.provenance

    @property
    def score(self) -> float:
        """Compatibility scalar; new consumers should inspect the breakdown."""

        return self.score_breakdown.total

    @property
    def matched_terms(self) -> tuple[str, ...]:
        return self.score_breakdown.matched_terms

    def to_dict(self) -> dict[str, object]:
        provenance = asdict(self.provenance)
        return {
            "query_id": self.query_id,
            "memory_id": self.memory_id,
            "rank": self.rank,
            "snippet": self.snippet,
            "scope": self.scope.to_dict(),
            "provenance": provenance,
            # Keep the former field as a serialized compatibility alias while
            # teaching that these source fields are provenance, not content.
            "source": provenance,
            "score": self.score,
            "score_breakdown": self.score_breakdown.to_dict(),
            "matched_terms": list(self.matched_terms),
        }


@dataclass(frozen=True)
class RecallResult:
    """Complete observable output of one retrieval operation."""

    query: RecallQuery
    hits: tuple[RecallHit, ...]
    searched_records: int
    candidate_records: int
    empty_reason: str | None = None
    # 被撤回、因而没有进入候选的 conversation 记录数，便于在 trace 中解释“为什么没召回”。
    retracted_records: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query.to_dict(),
            "hits": [hit.to_dict() for hit in self.hits],
            "searched_records": self.searched_records,
            "candidate_records": self.candidate_records,
            "retracted_records": self.retracted_records,
            "empty_reason": self.empty_reason,
        }


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RemoteMemoryCorruptionError(f"invalid timestamp: {value!r}") from exc
    return _utc(parsed)


def _scope_id(user_id: str) -> str:
    normalized = user_id.strip().casefold()
    if not normalized:
        raise RemoteMemoryValidationError("user_id must not be empty")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _clean_text(value: object, *, field_name: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        raise RemoteMemoryValidationError(f"{field_name} must not be empty")
    if len(text) > max_chars:
        raise RemoteMemoryValidationError(
            f"{field_name} exceeds the {max_chars}-character teaching limit"
        )
    return text


def _record_id(value: object, *, field_name: str, generate: bool = True) -> str:
    """写入端统一的记录 ID 校验：None 或空串自动生成，其余必须是字符串且原样保存。

    不做空白归一化，也不做 str() 隐式转换：整数 123 若被写成 "123"，就会和已有的
    字符串 ID 撞在一起，或者让调用方拿到的 ID 与落盘的 ID 不一致。
    generate=False 时（撤回目标）None 和空串都直接拒绝。
    """

    if value is None and generate:
        return uuid.uuid4().hex
    # 先校验类型，再做任何比较：非法对象的 __eq__ 不会被调用
    if not isinstance(value, str):
        raise RemoteMemoryValidationError(f"{field_name} must be a string")
    # 字符串子类固化成内建 str，判空、查重、匹配都只看文本，不受子类的 __eq__/__hash__/__bool__ 影响
    text = str.__str__(value)
    if len(text) == 0:
        if not generate:
            raise RemoteMemoryValidationError(f"{field_name} must be a non-empty string")
        return uuid.uuid4().hex
    return text


def _validate_source(source: MemorySource) -> MemorySource:
    """Reject anonymous or temporally invalid provenance before persistence."""

    return MemorySource(
        source_id=_clean_text(source.source_id, field_name="source_id", max_chars=500),
        source_type=_clean_text(
            source.source_type, field_name="source_type", max_chars=100
        ),
        title=_clean_text(source.title, field_name="source title", max_chars=1_000),
        captured_at=_iso(_parse_timestamp(source.captured_at)),
    )


def _normalize_search_text(text: str) -> str:
    """Normalize width, case, and whitespace without hiding readable input."""

    normalized = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _tokenize(text: str) -> tuple[str, ...]:
    """Return stable lexical features for English and Chinese text.

    This transparent offline baseline is intentionally language-light, not a
    production segmentation claim. English uses word tokens; consecutive
    Chinese text contributes character bi-grams so short topical queries can
    overlap without an optional tokenizer dependency.
    """

    normalized = _normalize_search_text(text)
    terms = set(re.findall(r"[a-z0-9_]+", normalized))
    for segment in re.findall(r"[\u4e00-\u9fff]+", normalized):
        if len(segment) == 1:
            terms.add(segment)
        else:
            terms.update(
                segment[index : index + 2] for index in range(len(segment) - 1)
            )
    return tuple(sorted(terms))


def normalize_recall_query(
    text: str,
    *,
    user_scope: str,
    limit: int,
    query_id: str | None,
    issued_at: datetime,
) -> RecallQuery:
    """Build the sole normalized query object used by downstream stages."""

    readable_text = _clean_text(text, field_name="recall query", max_chars=2_000)
    normalized_text = _normalize_search_text(readable_text)
    terms = _tokenize(normalized_text)
    if not terms:
        raise RemoteMemoryValidationError("recall query has no searchable terms")
    try:
        parsed_limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise RemoteMemoryValidationError("limit must be an integer") from exc
    if not 1 <= parsed_limit <= MAX_RECALL_LIMIT:
        raise RemoteMemoryValidationError(
            f"limit must be between 1 and {MAX_RECALL_LIMIT}"
        )
    return RecallQuery(
        query_id=_clean_text(
            query_id or uuid.uuid4().hex,
            field_name="query_id",
            max_chars=200,
        ),
        text=readable_text,
        normalized_text=normalized_text,
        terms=terms,
        user_scope=_clean_text(
            user_scope,
            field_name="recall user_scope",
            max_chars=200,
        ),
        limit=parsed_limit,
        issued_at=_iso(issued_at),
    )


def build_recall_candidates(
    query: RecallQuery,
    records: Sequence[StoredMemory],
) -> tuple[RecallCandidate, ...]:
    """Create query-dependent candidates without assigning scores or ranks."""

    query_terms = set(query.terms)
    candidates: list[RecallCandidate] = []
    for record in records:
        if record.user_scope != query.user_scope:
            raise RemoteMemoryScopeError(
                f"memory {record.memory_id} belongs to another user scope"
            )
        if record.kind is not MemoryKind.CONVERSATION:
            continue
        searchable_terms = _tokenize(f"{record.summary} {record.content}")
        matched_terms = tuple(sorted(query_terms & set(searchable_terms)))
        if not matched_terms:
            continue
        candidates.append(
            RecallCandidate(
                query_id=query.query_id,
                record=record,
                searchable_terms=searchable_terms,
                matched_terms=matched_terms,
                captured_at=_parse_timestamp(record.source.captured_at),
            )
        )
    return tuple(candidates)


def score_recall_candidate(
    query: RecallQuery,
    candidate: RecallCandidate,
    *,
    as_of: datetime,
) -> RecallScoreBreakdown:
    """Score one candidate and retain each contribution for inspection."""

    if candidate.query_id != query.query_id:
        raise RemoteMemoryValidationError("candidate belongs to another recall query")
    coverage = len(candidate.matched_terms) / len(query.terms)
    age_days = max(
        (_utc(as_of) - candidate.captured_at).total_seconds() / 86_400,
        0.0,
    )
    recency = 1.0 / (1.0 + age_days / RECENCY_WINDOW_DAYS)
    lexical_contribution = LEXICAL_WEIGHT * coverage
    recency_contribution = RECENCY_WEIGHT * recency
    return RecallScoreBreakdown(
        matched_terms=candidate.matched_terms,
        query_term_count=len(query.terms),
        lexical_coverage=round(coverage, 6),
        recency=round(recency, 6),
        lexical_contribution=round(lexical_contribution, 6),
        recency_contribution=round(recency_contribution, 6),
        total=round(lexical_contribution + recency_contribution, 6),
    )


def stable_rank_recall_candidates(
    query: RecallQuery,
    candidates: Sequence[RecallCandidate],
    *,
    as_of: datetime,
) -> tuple[RecallHit, ...]:
    """Score then rank with explicit keys independent of storage iteration."""

    scored = [
        ScoredRecallCandidate(
            candidate=candidate,
            score_breakdown=score_recall_candidate(
                query,
                candidate,
                as_of=as_of,
            ),
        )
        for candidate in candidates
    ]
    scored.sort(
        key=lambda item: (
            -item.score_breakdown.total,
            -item.score_breakdown.lexical_coverage,
            -item.candidate.captured_at.timestamp(),
            item.candidate.record.memory_id,
        )
    )
    return tuple(
        RecallHit(
            query_id=query.query_id,
            memory_id=item.candidate.record.memory_id,
            rank=rank,
            snippet=item.candidate.record.summary,
            scope=RecallScope(
                user_scope=item.candidate.record.user_scope,
                memory_kind=item.candidate.record.kind,
            ),
            provenance=item.candidate.record.source,
            score_breakdown=item.score_breakdown,
        )
        for rank, item in enumerate(scored[: query.limit], start=1)
    )


@contextmanager
def _exclusive_store_lock(path: Path) -> Iterator[None]:
    """Serialize cooperating writers across threads and local processes.

    The lock lives beside the JSONL file and deliberately survives process
    exit. ``flock`` ownership belongs to the open descriptor, so a crash
    releases the lock without relying on stale-lock cleanup. A production
    remote service would provide the same compare-and-insert boundary with a
    unique index or conditional write rather than a local advisory lock.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _split_retracted(
    records: Sequence[StoredMemory],
) -> tuple[list[StoredMemory], list[StoredMemory]]:
    """把完整记录拆成（生效记录，被撤回的目标记录），撤回记录本身两边都不放。"""

    retracted_ids = {
        record.retracts
        for record in records
        if record.kind is MemoryKind.RETRACTION and record.retracts is not None
    }
    active: list[StoredMemory] = []
    retracted: list[StoredMemory] = []
    for record in records:
        if record.kind is MemoryKind.RETRACTION:
            continue
        (retracted if record.memory_id in retracted_ids else active).append(record)
    return active, retracted


class RemoteMemoryStore:
    """Append-only local simulation of a user-scoped remote memory service.

    The JSONL file is durable evidence.  Recall never writes to it: search
    results are derived views that live only for one query.  A real service can
    implement the same methods over an API or database without changing the
    query/hit contract taught below.
    """

    def __init__(self, path: Path, *, user_id: str = "local-user"):
        self.path = Path(path).expanduser().resolve()
        self.user_id = user_id.strip()
        self.user_scope = _scope_id(user_id)

    def append(
        self,
        *,
        kind: MemoryKind,
        content: str,
        summary: str,
        source: MemorySource,
        memory_id: str | None = None,
        stored_at: datetime | None = None,
    ) -> StoredMemory:
        """Persist one immutable record and atomically reject duplicate IDs.

        Validation happens before taking the lock. The duplicate check and
        append stay inside one exclusive critical section, closing the classic
        check-then-act race between concurrent harness retries.
        """

        if MemoryKind(kind) is MemoryKind.RETRACTION:
            # 撤回必须经过 retract() 的目标校验，不能当作普通记录直接追加。
            raise RemoteMemoryValidationError("use retract() to write retraction records")
        record = StoredMemory(
            memory_id=_record_id(memory_id, field_name="memory_id"),
            user_scope=self.user_scope,
            kind=MemoryKind(kind),
            content=_clean_text(
                content, field_name="memory content", max_chars=MAX_RECORD_CHARS
            ),
            summary=_clean_text(summary, field_name="memory summary", max_chars=2_000),
            source=_validate_source(source),
            stored_at=_iso(stored_at),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _exclusive_store_lock(self.path):
            existing_ids = {item.memory_id for item in self.read_all()}
            if record.memory_id in existing_ids:
                raise RemoteMemoryDuplicateError(
                    f"duplicate memory_id: {record.memory_id}"
                )
            self._write_locked(record)
        return record

    def _write_locked(self, record: StoredMemory) -> None:
        """在调用方已持有排他锁时追加一行；append 与 retract 共用这一个写入点。"""

        encoded = (
            json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        descriptor = os.open(
            self.path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            written = os.write(descriptor, encoded)
            if written != len(encoded):
                raise OSError(
                    f"short remote-memory write: {written}/{len(encoded)} bytes"
                )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def retract(
        self,
        memory_id: str,
        *,
        reason: str,
        source: MemorySource,
        retraction_id: str | None = None,
        stored_at: datetime | None = None,
    ) -> StoredMemory:
        """追加一条撤回记录，让目标从召回和 profile 选择中消失，但不改写历史。

        校验与追加放在同一个排他临界区内：
        - 目标必须存在于本用户作用域。每个作用域使用独立的 store 文件，别人的 memory_id
          不在本文件里，因此按未知目标拒绝；文件里混入其他作用域的记录则在 read_all 时直接报错；
        - 不能撤回一条撤回记录，避免“撤回的撤回”让语义变得不可判定；
        - 同一目标已被撤回时幂等返回已有的那条，并发重试也只会落盘一条；
        - 目标按原始 memory_id 精确匹配，与 append 存下的 ID 保持一致。
        """

        # 目标按原始 memory_id 精确匹配：append 存的就是原始 ID，这里不做空白归一化或
        # 长度限制，否则只差空白的两个 ID 会撤错，已存下的长 ID 也撤不掉。
        target_id = _record_id(memory_id, field_name="retract memory_id", generate=False)
        clean_reason = _clean_text(
            reason, field_name="retraction reason", max_chars=2_000
        )
        clean_source = _validate_source(source)
        # 撤回记录的 ID 与普通记录共用一套写入端校验，在加锁前拒绝非字符串。
        new_id = _record_id(retraction_id, field_name="retraction_id")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _exclusive_store_lock(self.path):
            records = self.read_all()
            target = next(
                (record for record in records if record.memory_id == target_id), None
            )
            if target is None:
                raise RemoteMemoryValidationError(
                    f"cannot retract unknown memory_id in this scope: {target_id}"
                )
            if target.kind is MemoryKind.RETRACTION:
                raise RemoteMemoryValidationError(
                    f"cannot retract a retraction record: {target_id}"
                )
            existing = next(
                (record for record in records if record.retracts == target_id), None
            )
            if existing is not None:
                return existing

            record = StoredMemory(
                memory_id=new_id,
                user_scope=self.user_scope,
                kind=MemoryKind.RETRACTION,
                content=clean_reason,
                summary=f"Retracted {target_id}",
                source=clean_source,
                stored_at=_iso(stored_at),
                retracts=target_id,
            )
            if any(item.memory_id == record.memory_id for item in records):
                raise RemoteMemoryDuplicateError(
                    f"duplicate memory_id: {record.memory_id}"
                )
            self._write_locked(record)
        return record

    def active_records(self) -> list[StoredMemory]:
        """返回仍然生效的记录：去掉被撤回的目标，以及撤回记录本身。

        read_all 保持不变，仍是完整的审计视图；召回和 profile 选择改读这里。
        """

        active, _retracted = _split_retracted(self.read_all())
        return active

    def read_all(self) -> list[StoredMemory]:
        if not self.path.exists():
            return []
        records: list[StoredMemory] = []
        for line_number, line in enumerate(_split_jsonl(self.path.read_bytes()), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RemoteMemoryCorruptionError(
                    f"invalid JSON at line {line_number}: {exc.msg}"
                ) from exc
            record = StoredMemory.from_dict(payload)
            if record.user_scope != self.user_scope:
                raise RemoteMemoryScopeError(
                    f"memory {record.memory_id} belongs to another user scope"
                )
            records.append(record)
        if len({record.memory_id for record in records}) != len(records):
            raise RemoteMemoryCorruptionError("duplicate memory_id in durable store")
        return records

    def latest_profile(self) -> StoredMemory | None:
        """Select the newest profile snapshot without treating it as recall."""

        profiles = [
            record for record in self.active_records() if record.kind is MemoryKind.PROFILE
        ]
        if not profiles:
            return None
        return max(
            profiles,
            key=lambda record: (_parse_timestamp(record.source.captured_at), record.memory_id),
        )


class RecallEngine:
    """Orchestrate the explicit recall pipeline over durable records.

    The engine owns sequencing, not hidden retrieval behavior. Each stage is a
    pure function that can be tested or replaced independently:

    normalize_recall_query -> build_recall_candidates
    -> score_recall_candidate -> stable_rank_recall_candidates
    """

    def __init__(self, store: RemoteMemoryStore):
        self.store = store

    def recall(
        self,
        text: str,
        *,
        limit: int = 5,
        query_id: str | None = None,
        as_of: datetime | None = None,
    ) -> RecallResult:
        current = _utc(as_of)
        query = normalize_recall_query(
            text,
            user_scope=self.store.user_scope,
            limit=limit,
            query_id=query_id,
            issued_at=current,
        )
        # 召回只看仍然生效的记录；被撤回的条数单独计数，写进结果供 trace 解释。
        active, retracted = _split_retracted(self.store.read_all())
        conversation_records = [
            record for record in active if record.kind is MemoryKind.CONVERSATION
        ]
        retracted_conversations = sum(
            1 for record in retracted if record.kind is MemoryKind.CONVERSATION
        )
        candidates = build_recall_candidates(query, conversation_records)
        hits = stable_rank_recall_candidates(
            query,
            candidates,
            as_of=current,
        )
        return RecallResult(
            query=query,
            hits=hits,
            searched_records=len(conversation_records),
            candidate_records=len(candidates),
            empty_reason=None if hits else "no_matching_terms",
            retracted_records=retracted_conversations,
        )


def render_recall_context(result: RecallResult) -> str:
    """Render ranked hits while preserving scope, provenance, and score parts.

    A no-match result renders no prompt context. The structured RecallResult
    still exposes the empty reason to tools and traces, but prompt assembly does
    not spend tokens on an empty wrapper.
    """

    if not result.hits:
        return ""
    lines = [
        f'<recalled_context query_id="{html.escape(result.query.query_id)}" '
        f'query="{html.escape(result.query.text)}" '
        f'normalized_query="{html.escape(result.query.normalized_text)}" '
        f'user_scope="{html.escape(result.query.user_scope)}" '
        f'searched_records="{result.searched_records}" '
        f'candidate_records="{result.candidate_records}">'
    ]
    for hit in result.hits:
        breakdown = hit.score_breakdown
        lines.extend(
            [
                (
                    f'  <hit rank="{hit.rank}" '
                    f'memory_id="{html.escape(hit.memory_id)}" '
                    f'user_scope="{html.escape(hit.scope.user_scope)}" '
                    f'memory_kind="{html.escape(hit.scope.memory_kind.value)}">'
                ),
                (
                    f'    <score total="{breakdown.total:.6f}" '
                    f'lexical_coverage="{breakdown.lexical_coverage:.6f}" '
                    f'recency="{breakdown.recency:.6f}" '
                    f'lexical_contribution="{breakdown.lexical_contribution:.6f}" '
                    f'recency_contribution="{breakdown.recency_contribution:.6f}" '
                    f'matched_terms="{html.escape(",".join(breakdown.matched_terms))}"/>'
                ),
                (
                    f'    <provenance '
                    f'source_id="{html.escape(hit.provenance.source_id)}" '
                    f'source_type="{html.escape(hit.provenance.source_type)}" '
                    f'title="{html.escape(hit.provenance.title)}" '
                    f'captured_at="{html.escape(hit.provenance.captured_at)}"/>'
                ),
                f"    <snippet>{html.escape(hit.snippet)}</snippet>",
                "  </hit>",
            ]
        )
    lines.append("</recalled_context>")
    return "\n".join(lines)


def build_system_prompt(store: RemoteMemoryStore) -> str:
    """Inject the latest profile snapshot, not the entire remote store."""

    profile = store.latest_profile()
    profile_block = ""
    if profile:
        profile_block = (
            f'<remote_profile memory_id="{html.escape(profile.memory_id)}" '
            f'source_id="{html.escape(profile.source.source_id)}" '
            f'captured_at="{html.escape(profile.source.captured_at)}">\n'
            f"{html.escape(profile.content)}\n"
            "</remote_profile>\n\n"
        )
    return profile_block + f"""You are a coding agent at {WORKDIR}.

Remote memory rules:
- The profile above is a source-bearing stored snapshot, not user-authored s11 state.
- Call recall_history only when the current task needs earlier conversation context.
- The recall query must be self-contained because retrieval cannot see this chat.
- Recall hits are candidates for this query. Check source and score before using them.
- Do not write recall hits back as new memory merely because they were retrieved.

s10 workspace memory and s11 explicit user memory remain separate ownership layers."""


def _seed_store(store: RemoteMemoryStore, *, as_of: datetime | None = None) -> None:
    """Seed deterministic offline evidence only when the simulated service is empty."""

    if store.read_all():
        return
    current = _utc(as_of)
    store.append(
        kind=MemoryKind.PROFILE,
        memory_id="profile-001",
        content=(
            "Preferred technical languages: Python and TypeScript. "
            "Response style: concise proposals before explanation."
        ),
        summary="Remote profile snapshot",
        source=MemorySource(
            source_id="profile-snapshot-001",
            source_type="profile_snapshot",
            title="Remote profile snapshot",
            captured_at=_iso(current - timedelta(days=1)),
        ),
        stored_at=current - timedelta(days=1),
    )
    examples = [
        (
            "conversation-001",
            2,
            "learn-workbuddy architecture",
            "Designed the learn-workbuddy teaching path from agent loop to desktop harness.",
        ),
        (
            "conversation-002",
            5,
            "Electron process architecture",
            "Compared main, renderer and preload responsibilities with a sidecar boundary.",
        ),
        (
            "conversation-003",
            10,
            "layered memory design",
            "Separated workspace facts, explicit user preferences and remote recall candidates.",
        ),
        (
            "conversation-004",
            15,
            "React state decision",
            "Compared Redux, Zustand and Jotai, then selected Zustand for the project.",
        ),
        (
            "conversation-005",
            20,
            "agent loop contract",
            "Defined explicit model turns, tool dispatch and terminal stop reasons.",
        ),
        (
            "conversation-006",
            30,
            "skills deferred loading",
            "Designed skill discovery before loading complete SKILL.md instructions.",
        ),
    ]
    for memory_id, age_days, title, content in examples:
        captured_at = current - timedelta(days=age_days)
        store.append(
            kind=MemoryKind.CONVERSATION,
            memory_id=memory_id,
            content=content,
            summary=content,
            source=MemorySource(
                source_id=f"transcript-{memory_id}",
                source_type="conversation_transcript",
                title=title,
                captured_at=_iso(captured_at),
            ),
            stored_at=captured_at,
        )


def create_default_store() -> RemoteMemoryStore:
    user_id = os.getenv("WORKBUDDY_USER_ID", "local-user")
    scope = _scope_id(user_id)
    store = RemoteMemoryStore(
        DEFAULT_REMOTE_ROOT / "users" / scope / "records.jsonl", user_id=user_id
    )
    _seed_store(store)
    return store


DEFAULT_STORE: RemoteMemoryStore | None = None
DEFAULT_RECALL: RecallEngine | None = None
SYSTEM: str | None = None


def default_runtime() -> tuple[RemoteMemoryStore, RecallEngine, str]:
    """Initialize seeded interactive state on first use, never on import.

    The chapter CLI still receives the same deterministic seed data.  Library
    users can import the storage and recall types without writing to a default
    home directory merely as a side effect of inspection.
    """

    global DEFAULT_STORE, DEFAULT_RECALL, SYSTEM
    if DEFAULT_STORE is None:
        DEFAULT_STORE = create_default_store()
    if DEFAULT_RECALL is None or DEFAULT_RECALL.store is not DEFAULT_STORE:
        DEFAULT_RECALL = RecallEngine(DEFAULT_STORE)
    # Prompt assembly is a view, not durable state. Rebuild it so a profile
    # written since the previous turn cannot leave the online loop stale.
    SYSTEM = build_system_prompt(DEFAULT_STORE)
    return DEFAULT_STORE, DEFAULT_RECALL, SYSTEM


def recall_history(query: str, limit: int = 5) -> str:
    """Tool adapter returning structured query, hit, source, and score fields."""

    _store, recall, _system = default_runtime()
    result = recall.recall(query, limit=int(limit))
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)


def safe_path(value: str) -> Path:
    path = (WORKDIR / value).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"path escapes workspace: {value}")
    return path


def run_read(path: str) -> str:
    try:
        return safe_path(path).read_text(encoding="utf-8")[:5_000]
    except Exception as exc:
        return f"Error: {exc}"


def run_bash(command: str) -> str:
    # Permission policy is taught in s04.  Keep the standalone memory lesson
    # from demonstrating obvious host-wide destructive commands.
    if any(fragment in command for fragment in ("rm -rf /", "sudo", "shutdown", "reboot")):
        return "Error: dangerous command blocked"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "Error: timeout (30s)"
    output = (result.stdout + result.stderr).strip()
    return output[:5_000] if output else "(no output)"


TOOLS = [
    {
        "name": "recall_history",
        "description": (
            "Search source-bearing remote conversation records. Retrieval cannot see "
            "the current chat, so query must restate the needed historical topic. "
            "Returned hits are query-scoped candidates with source and score."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Self-contained description of the historical context needed.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_RECALL_LIMIT,
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "bash",
        "description": "Run a shell command in the current workspace.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a UTF-8 file inside the current workspace.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
]


TOOL_HANDLERS = {
    "recall_history": recall_history,
    "bash": run_bash,
    "read_file": run_read,
}


def agent_loop(messages: list[dict]) -> None:
    """Run the standard tool loop with structured recall as one tool."""

    active_client, model = runtime_client()
    _store, _recall, system = default_runtime()
    while True:
        response = active_client.messages.create(
            model=model,
            system=system,
            messages=messages,
            tools=TOOLS,
            max_tokens=8_000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            handler = TOOL_HANDLERS.get(block.name)
            output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
            display = str(output)[:150].replace("\n", " ")
            print(f"  \033[36m> {block.name}\033[0m {display}")
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                }
            )
        messages.append({"role": "user", "content": results})


def main() -> None:
    store, _recall, _system = default_runtime()
    print("s12: Remote Memory — stored records vs recalled context")
    print(f"user scope: {store.user_scope}")
    print(f"durable store: {store.path}")
    profile = store.latest_profile()
    if profile:
        print(
            "profile snapshot: "
            f"{profile.memory_id} from {profile.source.source_id} "
            f"at {profile.source.captured_at}"
        )
    print("Try: continue the previous layered memory design discussion")

    messages: list[dict] = []
    while True:
        try:
            query = input("s12 >> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if query.lower() in {"q", "quit", "exit"}:
            break
        if not query:
            continue
        messages.append({"role": "user", "content": query})
        agent_loop(messages)
        text = "".join(
            block.text
            for block in messages[-1]["content"]
            if getattr(block, "type", None) == "text"
        )
        print(text)


if __name__ == "__main__":
    main()
