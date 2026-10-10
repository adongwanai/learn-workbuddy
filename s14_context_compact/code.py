#!/usr/bin/env python3
from __future__ import annotations
"""
s14_context_compact.py - Context Compaction: Four-Layer Pipeline

Context window management for long conversations.

Four layers, triggered from lightest to heaviest:

  Layer 1: Tool result truncation
           Large tool outputs get truncated to a token budget.
           Cheapest — no messages are lost.

  Layer 2: File content deduplication
           Same file read multiple times → keep only the latest read.
           Cheap — redundant information is removed.

  Layer 3: Message history pruning
           Old messages get dropped, keeping recent N turns.
           Medium — may lose detail from early conversation.

  Layer 4: Full conversation summary
           When all else fails, generate a summary of the entire
           conversation using the model, replacing old messages.
           Expensive — costs one API call.

  ┌──────────────────────────────────────────────────────┐
  │                 Compact Pipeline                     │
  │                                                      │
  │  token_count > threshold?                            │
  │    ├─ L1: truncate large tool_results                │
  │    ├─ L2: dedup file reads (keep latest)             │
  │    ├─ L3: prune old messages (keep recent N)         │
  │    └─ L4: generate summary (model call)              │
  │                                                      │
  │  NEVER compact: facts, pending work, retrieval proof │
  └──────────────────────────────────────────────────────┘

Production harnesses often use: precise tiktoken counting, priority-based pruning,
incremental summarization.
Teaching version uses: 4-chars ≈ 1-token estimation, simple thresholds.

Usage:
    python s14_context_compact/code.py
"""
import codecs
import copy
import hashlib
import json
import math
import os
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path

# Machine-readable learning path metadata. Tests enforce that every
# chapter declares what it inherits and what it adds.
PROGRESSION = {
    "chapter": "s14_context_compact",
    "builds_on": ["s13_output_externalization"],
    "adds": [
        "token pressure detection",
        "structured compaction",
        "protocol-safe tool interaction compaction",
        "hard message-view ceiling",
        "durable state preservation",
        "selected retrieval evidence retention",
        "typed source pointer verification",
    ],
    "preserves": [
        "externalized output pointers",
        "memory facts outside lossy summaries",
    ],
}

# Shared learning entrypoints: --demo is offline; --provider deepseek configures real API env.
import sys as _wb_sys
from pathlib import Path as _wb_Path
_WB_ROOT = _wb_Path(__file__).resolve().parents[1]
if str(_WB_ROOT) not in _wb_sys.path:
    _wb_sys.path.insert(0, str(_WB_ROOT))
from mini_workbuddy.chapter_demo import maybe_run_chapter_demo as _wb_maybe_run_chapter_demo
_wb_maybe_run_chapter_demo(__file__, PROGRESSION)
from mini_workbuddy.chapter_demo import prepare_chapter_provider as _wb_prepare_chapter_provider
# S14 is also a reusable compaction boundary for the comprehensive harness.
# An importer owns its own CLI, so only this chapter's executable may consume
# provider arguments from ``sys.argv``.
if __name__ == "__main__":
    _wb_prepare_chapter_provider()

try:
    import readline
    readline.parse_and_bind('set bind-tty-special-chars off')
    readline.parse_and_bind('set input-meta on')
    readline.parse_and_bind('set output-meta on')
    readline.parse_and_bind('set convert-meta off')
except ImportError:
    pass

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"): os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path.cwd()
MODEL = os.environ.get("MODEL_ID")
_client: Anthropic | None = None

SYSTEM = f"""你是一个桌面 AI 助手, 工作目录: {WORKDIR}
你有文件读写和命令执行工具。回答要简洁。"""


def runtime_client() -> Anthropic:
    """Create the online client lazily so compaction contracts stay keyless."""

    global _client
    if not MODEL:
        raise RuntimeError(
            "MODEL_ID is not set. Copy .env.example to .env and fill in "
            "the provider key and MODEL_ID (see README quick start)."
        )
    if _client is None:
        _client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
    return _client


# ======================================================================
# Token estimation
# ======================================================================

# Real WorkBuddy uses tiktoken for precise counting.
# Teaching version: 4 characters ≈ 1 token (rough but fast).

TOKEN_THRESHOLD = 80_000        # Trigger compaction at 80K tokens
HARD_LIMIT = 120_000            # Hard limit — must compact before this
MAX_TOOL_RESULT_TOKENS = 5_000  # Layer 1: truncate tool results above this
KEEP_RECENT_TURNS = 6           # Layer 3: keep this many recent messages
MAX_RENDERED_DONE_ITEMS = 3     # done 只渲染最新几条，其余折叠为计数


def _required_text(value: str, *, field_name: str) -> str:
    """Reject anonymous durable state before it reaches prompt assembly."""

    normalized = " ".join(str(value).split())
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _zoned_timestamp(value: str, *, field_name: str) -> str:
    """Require an explicit timezone so evidence stays comparable after restart."""

    normalized = _required_text(value, field_name=field_name)
    parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return normalized


def _confirmed_at(value: str) -> str:
    """Validate a durable fact or pending item's confirmation time."""

    return _zoned_timestamp(value, field_name="last_confirmed_at")


MAX_SOURCE_EXCERPT_CHARS = 2_000
MAX_TRANSCRIPT_RECORD_CHARS = 100_000
_SOURCE_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_ARTIFACT_DIGEST_PATTERN = re.compile(r"^(?:[0-9a-fA-F]{12}|[0-9a-fA-F]{64})$")
_EVIDENCE_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class SourcePointerError(ValueError):
    """Base class for source identifiers rejected at the Harness boundary."""


class UnsupportedSourcePointerError(SourcePointerError):
    """Raised when no trusted resolver owns a source pointer scheme."""


class SourceEvidenceCorruptionError(RuntimeError):
    """Raised internally when located bytes contradict their source identity."""


class SourcePointerKind(str, Enum):
    TRANSCRIPT = "transcript"
    ARTIFACT = "artifact"


class SourceResolutionStatus(str, Enum):
    AVAILABLE = "available"
    MISSING = "missing"
    DENIED = "denied"
    CORRUPT = "corrupt"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ParsedSourcePointer:
    """A path-free identifier parsed before any filesystem lookup occurs."""

    raw: str
    kind: SourcePointerKind
    session_id: str
    sequence: int | None = None
    artifact_name: str | None = None
    digest_prefix: str | None = None


@dataclass(frozen=True)
class SourceResolution:
    """Immutable verification result; excerpts are never auto-injected in Prompt."""

    pointer: str
    status: SourceResolutionStatus
    source_type: str | None
    evidence_sha256: str | None = None
    excerpt: str | None = None
    reason: str | None = None

    def to_dict(self, *, include_excerpt: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "pointer": self.pointer,
            "status": self.status.value,
            "source_type": self.source_type,
            "evidence_sha256": self.evidence_sha256,
            "reason": self.reason,
        }
        if include_excerpt:
            payload["excerpt"] = self.excerpt
        return payload


def _source_component(value: str, *, field_name: str) -> str:
    if value in {".", ".."} or not _SOURCE_COMPONENT_PATTERN.fullmatch(value):
        raise SourcePointerError(
            f"{field_name} must be one path-free identifier component"
        )
    return value


def parse_source_pointer(value: str) -> ParsedSourcePointer:
    """Parse only the two evidence schemes owned by earlier chapters.

    Identifiers never carry a user-controlled path.  Filesystem roots belong
    to the trusted resolver configuration, while the pointer contributes only
    validated session, sequence, filename, and digest components.
    """

    raw = str(value)
    if not raw or any(character.isspace() for character in raw):
        raise SourcePointerError("source pointer must be non-empty and path-free")
    scheme = raw.partition(":")[0]
    if scheme not in {kind.value for kind in SourcePointerKind}:
        raise UnsupportedSourcePointerError(f"unsupported source scheme: {scheme}")
    parts = raw.split(":")
    if scheme == SourcePointerKind.TRANSCRIPT.value:
        if len(parts) != 3:
            raise SourcePointerError(
                "transcript pointer must be transcript:<session>:<sequence>"
            )
        session_id = _source_component(parts[1], field_name="transcript session")
        if not parts[2].isdigit() or int(parts[2]) < 1:
            raise SourcePointerError("transcript sequence must be a positive integer")
        return ParsedSourcePointer(
            raw=raw,
            kind=SourcePointerKind.TRANSCRIPT,
            session_id=session_id,
            sequence=int(parts[2]),
        )

    if len(parts) != 4:
        raise SourcePointerError(
            "artifact pointer must be artifact:<session>:<filename>:<digest>"
        )
    session_id = _source_component(parts[1], field_name="artifact session")
    artifact_name = _source_component(parts[2], field_name="artifact filename")
    if not _ARTIFACT_DIGEST_PATTERN.fullmatch(parts[3]):
        raise SourcePointerError("artifact digest must contain 12 or 64 hex characters")
    return ParsedSourcePointer(
        raw=raw,
        kind=SourcePointerKind.ARTIFACT,
        session_id=session_id,
        artifact_name=artifact_name,
        digest_prefix=parts[3].lower(),
    )


class SourcePointerResolver:
    """Resolve source IDs beneath trusted roots with bounded evidence reads."""

    def __init__(
        self,
        *,
        transcript_root: Path,
        artifact_root: Path,
        authorize: Callable[[ParsedSourcePointer], bool] | None = None,
        max_excerpt_chars: int = MAX_SOURCE_EXCERPT_CHARS,
    ):
        if (
            isinstance(max_excerpt_chars, bool)
            or not isinstance(max_excerpt_chars, int)
            or max_excerpt_chars < 1
        ):
            raise ValueError("max_excerpt_chars must be a positive integer")
        if authorize is not None and not callable(authorize):
            raise TypeError("authorize must be callable")
        self.transcript_root = Path(transcript_root).expanduser().resolve()
        self.artifact_root = Path(artifact_root).expanduser().resolve()
        self.authorize = authorize or (lambda _pointer: True)
        self.max_excerpt_chars = int(max_excerpt_chars)

    @staticmethod
    def _result(
        pointer: str,
        status: SourceResolutionStatus,
        *,
        source_type: str | None,
        evidence_sha256: str | None = None,
        excerpt: str | None = None,
        reason: str | None = None,
    ) -> SourceResolution:
        return SourceResolution(
            pointer=pointer,
            status=status,
            source_type=source_type,
            evidence_sha256=evidence_sha256,
            excerpt=excerpt,
            reason=reason,
        )

    def resolve(self, pointer: str) -> SourceResolution:
        raw = str(pointer)
        try:
            parsed = parse_source_pointer(raw)
        except UnsupportedSourcePointerError:
            return self._result(
                raw,
                SourceResolutionStatus.UNSUPPORTED,
                source_type=None,
                reason="unsupported_scheme",
            )
        except SourcePointerError:
            return self._result(
                raw,
                SourceResolutionStatus.CORRUPT,
                source_type=raw.partition(":")[0] or None,
                reason="malformed_pointer",
            )

        try:
            allowed = bool(self.authorize(parsed))
        except PermissionError:
            allowed = False
        if not allowed:
            return self._result(
                raw,
                SourceResolutionStatus.DENIED,
                source_type=parsed.kind.value,
                reason="policy_denied",
            )

        try:
            if parsed.kind is SourcePointerKind.TRANSCRIPT:
                return self._resolve_transcript(parsed)
            return self._resolve_artifact(parsed)
        except PermissionError:
            return self._result(
                raw,
                SourceResolutionStatus.DENIED,
                source_type=parsed.kind.value,
                reason="filesystem_denied",
            )
        except SourceEvidenceCorruptionError as exc:
            return self._result(
                raw,
                SourceResolutionStatus.CORRUPT,
                source_type=parsed.kind.value,
                reason=str(exc),
            )
        except UnicodeDecodeError:
            return self._result(
                raw,
                SourceResolutionStatus.CORRUPT,
                source_type=parsed.kind.value,
                reason="source_not_utf8",
            )
        except FileNotFoundError:
            return self._result(
                raw,
                SourceResolutionStatus.MISSING,
                source_type=parsed.kind.value,
                reason="source_disappeared",
            )
        except OSError:
            return self._result(
                raw,
                SourceResolutionStatus.CORRUPT,
                source_type=parsed.kind.value,
                reason="source_read_failed",
            )

    def _resolve_transcript(self, pointer: ParsedSourcePointer) -> SourceResolution:
        transcript_path = self.transcript_root / f"{pointer.session_id}.jsonl"
        candidate = transcript_path.resolve()
        if candidate.parent != self.transcript_root:
            return self._result(
                pointer.raw,
                SourceResolutionStatus.DENIED,
                source_type=pointer.kind.value,
                reason="ownership_boundary",
            )
        if not candidate.exists():
            return self._result(
                pointer.raw,
                SourceResolutionStatus.MISSING,
                source_type=pointer.kind.value,
                reason="transcript_missing",
            )
        if not candidate.is_file():
            raise SourceEvidenceCorruptionError("transcript_not_a_file")

        assert pointer.sequence is not None
        with candidate.open("r", encoding="utf-8") as handle:
            for expected_sequence, line in enumerate(handle, start=1):
                if len(line) > MAX_TRANSCRIPT_RECORD_CHARS:
                    raise SourceEvidenceCorruptionError("transcript_record_too_large")
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SourceEvidenceCorruptionError(
                        "transcript_invalid_json"
                    ) from exc
                if not isinstance(event, dict):
                    raise SourceEvidenceCorruptionError("transcript_record_not_object")
                sequence = event.get("sequence", expected_sequence)
                session_id = event.get("session_id", pointer.session_id)
                expected_event_id = (
                    f"transcript:{pointer.session_id}:{expected_sequence}"
                )
                event_id = event.get("event_id", expected_event_id)
                if type(sequence) is not int or sequence != expected_sequence:
                    raise SourceEvidenceCorruptionError("transcript_sequence_mismatch")
                if session_id != pointer.session_id:
                    raise SourceEvidenceCorruptionError("transcript_session_mismatch")
                if event_id != expected_event_id:
                    raise SourceEvidenceCorruptionError("transcript_event_id_mismatch")
                if expected_sequence != pointer.sequence:
                    continue
                encoded = line.rstrip("\r\n").encode("utf-8")
                content = event.get("content", event)
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False, sort_keys=True)
                return self._result(
                    pointer.raw,
                    SourceResolutionStatus.AVAILABLE,
                    source_type=pointer.kind.value,
                    evidence_sha256=hashlib.sha256(encoded).hexdigest(),
                    excerpt=content[: self.max_excerpt_chars],
                    reason="verified_transcript_event",
                )
        return self._result(
            pointer.raw,
            SourceResolutionStatus.MISSING,
            source_type=pointer.kind.value,
            reason="transcript_event_missing",
        )

    def _resolve_artifact(self, pointer: ParsedSourcePointer) -> SourceResolution:
        assert pointer.artifact_name is not None
        assert pointer.digest_prefix is not None
        owned_root = (
            self.artifact_root / pointer.session_id / "tool-results"
        ).resolve()
        if not owned_root.is_relative_to(self.artifact_root):
            return self._result(
                pointer.raw,
                SourceResolutionStatus.DENIED,
                source_type=pointer.kind.value,
                reason="ownership_boundary",
            )
        candidate = (owned_root / pointer.artifact_name).resolve()
        if candidate.parent != owned_root:
            return self._result(
                pointer.raw,
                SourceResolutionStatus.DENIED,
                source_type=pointer.kind.value,
                reason="ownership_boundary",
            )
        if not candidate.exists():
            return self._result(
                pointer.raw,
                SourceResolutionStatus.MISSING,
                source_type=pointer.kind.value,
                reason="artifact_missing",
            )
        if not candidate.is_file():
            raise SourceEvidenceCorruptionError("artifact_not_a_file")

        digest = hashlib.sha256()
        decoder = codecs.getincrementaldecoder("utf-8")()
        excerpt_parts: list[str] = []
        excerpt_length = 0
        with candidate.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                digest.update(chunk)
                try:
                    decoded = decoder.decode(chunk)
                except UnicodeDecodeError as exc:
                    raise SourceEvidenceCorruptionError(
                        "artifact_not_utf8"
                    ) from exc
                if excerpt_length < self.max_excerpt_chars:
                    remaining = self.max_excerpt_chars - excerpt_length
                    excerpt_parts.append(decoded[:remaining])
                    excerpt_length += len(decoded[:remaining])
        try:
            tail = decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise SourceEvidenceCorruptionError("artifact_not_utf8") from exc
        if excerpt_length < self.max_excerpt_chars:
            excerpt_parts.append(tail[: self.max_excerpt_chars - excerpt_length])
        actual_digest = digest.hexdigest()
        if not actual_digest.startswith(pointer.digest_prefix):
            raise SourceEvidenceCorruptionError("artifact_digest_mismatch")
        return self._result(
            pointer.raw,
            SourceResolutionStatus.AVAILABLE,
            source_type=pointer.kind.value,
            evidence_sha256=actual_digest,
            excerpt="".join(excerpt_parts),
            reason="verified_artifact",
        )


@dataclass(frozen=True)
class DurableFact:
    """A confirmed fact owned by Memory, not by the lossy message summary."""

    fact_id: str
    content: str
    source_pointer: str
    last_confirmed_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "fact_id", _required_text(self.fact_id, field_name="fact_id"))
        object.__setattr__(self, "content", _required_text(self.content, field_name="content"))
        object.__setattr__(
            self,
            "source_pointer",
            _required_text(self.source_pointer, field_name="source_pointer"),
        )
        object.__setattr__(self, "last_confirmed_at", _confirmed_at(self.last_confirmed_at))


class PendingStatus(str, Enum):
    OPEN = "open"
    BLOCKED = "blocked"
    DONE = "done"


# 只有 harness 调用迁移；done 是终态
_PENDING_TRANSITIONS = {
    PendingStatus.OPEN: {PendingStatus.BLOCKED, PendingStatus.DONE},
    PendingStatus.BLOCKED: {PendingStatus.OPEN, PendingStatus.DONE},
    PendingStatus.DONE: set(),
}


@dataclass(frozen=True)
class PendingItem:
    """Unfinished work that must survive even when its original turn is pruned."""

    item_id: str
    description: str
    source_pointer: str
    last_confirmed_at: str
    reason: str | None = None
    status: str = "open"

    def __post_init__(self) -> None:
        if type(self.status) is not str and type(self.status) is not PendingStatus:
            raise ValueError("status must be a plain str or PendingStatus")
        object.__setattr__(self, "status", PendingStatus(self.status).value)
        object.__setattr__(self, "item_id", _required_text(self.item_id, field_name="item_id"))
        object.__setattr__(
            self,
            "description",
            _required_text(self.description, field_name="description"),
        )
        object.__setattr__(
            self,
            "source_pointer",
            _required_text(self.source_pointer, field_name="source_pointer"),
        )
        object.__setattr__(self, "last_confirmed_at", _confirmed_at(self.last_confirmed_at))


@dataclass(frozen=True)
class RetrievalEvidence:
    """Lossless audit metadata copied from one already-selected memory hit.

    The recalled text may later appear only in a generated summary.  These
    fields therefore keep the independent route back to its source and the
    ranking/conflict decision that admitted it.  S14 does not select hits or
    recompute scores; it only freezes evidence produced by the retrieval path.
    """

    memory_id: str
    source_id: str
    source_type: str
    source_title: str
    captured_at: str
    score: float
    source_rank: int
    conflict_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "memory_id",
            _required_text(self.memory_id, field_name="retrieval memory_id"),
        )
        object.__setattr__(
            self,
            "source_id",
            _required_text(self.source_id, field_name="retrieval source_id"),
        )
        object.__setattr__(
            self,
            "source_type",
            _required_text(self.source_type, field_name="retrieval source_type"),
        )
        object.__setattr__(
            self,
            "source_title",
            # S12 treats titles as presentation metadata: source ID and type are
            # the required identity, while an untitled source remains valid.
            " ".join(str(self.source_title).split()),
        )
        object.__setattr__(
            self,
            "captured_at",
            _zoned_timestamp(self.captured_at, field_name="captured_at"),
        )
        if (
            isinstance(self.score, bool)
            or not isinstance(self.score, (int, float))
            or not math.isfinite(self.score)
            or not 0.0 <= self.score <= 1.0
        ):
            raise ValueError("retrieval score must be finite and between 0 and 1")
        object.__setattr__(self, "score", float(self.score))
        if (
            isinstance(self.source_rank, bool)
            or not isinstance(self.source_rank, int)
            or self.source_rank < 1
        ):
            raise ValueError("retrieval source_rank must be a positive integer")
        if self.conflict_key is not None:
            object.__setattr__(
                self,
                "conflict_key",
                _required_text(
                    self.conflict_key,
                    field_name="retrieval conflict_key",
                ),
            )


def capture_retrieval_evidence(
    selected_hits: Sequence[object],
) -> tuple[RetrievalEvidence, ...]:
    """Copy source/ranking fields from hits selected by an upstream policy.

    Both S12 ``RecallHit`` and S15 ``MemoryContextCandidate`` expose this
    structural shape; their rank attribute is named ``rank`` and
    ``source_rank`` respectively.  Accepting the shape instead of importing a
    later chapter keeps S14 independently runnable.  Callers must pass only
    selected hits: compaction must never rerun scope, confidence, conflict, or
    budget policy against a different context window.
    """

    captured: list[RetrievalEvidence] = []
    for hit in selected_hits:
        provenance = getattr(hit, "provenance", None)
        if provenance is None:
            raise ValueError("selected retrieval hit must carry provenance")
        source_rank = getattr(hit, "source_rank", None)
        if source_rank is None:
            source_rank = getattr(hit, "rank", None)
        captured.append(
            RetrievalEvidence(
                memory_id=getattr(hit, "memory_id", ""),
                source_id=getattr(provenance, "source_id", ""),
                source_type=getattr(provenance, "source_type", ""),
                source_title=getattr(provenance, "title", ""),
                captured_at=getattr(provenance, "captured_at", ""),
                score=getattr(hit, "score", None),
                source_rank=source_rank,
                conflict_key=getattr(hit, "conflict_key", None),
            )
        )
    memory_ids = [item.memory_id for item in captured]
    if len(memory_ids) != len(set(memory_ids)):
        raise ValueError("selected retrieval hit memory ids must be unique")
    return tuple(captured)


@dataclass(frozen=True)
class DurableContextState:
    """Typed state carried around compaction rather than summarized by it."""

    facts: tuple[DurableFact, ...] = ()
    pending_items: tuple[PendingItem, ...] = ()
    retrieval_evidence: tuple[RetrievalEvidence, ...] = ()

    def __post_init__(self) -> None:
        # Type hints do not stop callers from passing lists. Normalize them here
        # so a frozen state cannot still be mutated through a list reference.
        object.__setattr__(self, "facts", tuple(self.facts))
        object.__setattr__(self, "pending_items", tuple(self.pending_items))
        object.__setattr__(self, "retrieval_evidence", tuple(self.retrieval_evidence))
        fact_ids = [item.fact_id for item in self.facts]
        pending_ids = [item.item_id for item in self.pending_items]
        retrieval_ids = [item.memory_id for item in self.retrieval_evidence]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("durable fact ids must be unique")
        if len(pending_ids) != len(set(pending_ids)):
            raise ValueError("pending item ids must be unique")
        if len(retrieval_ids) != len(set(retrieval_ids)):
            raise ValueError("retrieval evidence memory ids must be unique")


def transition_pending_item(
    state: DurableContextState,
    item_id: str,
    new_status: str,
    *,
    last_confirmed_at: str,
    source_pointer: str | None = None,
    reason: str | None = None,
) -> DurableContextState:
    """Harness-owned lifecycle change; returns a new frozen state."""

    # 入口严格校验：拒绝 str 子类/非 str，防止自定义 __eq__ 冒充匹配
    if type(item_id) is not str:
        raise ValueError("item_id must be a plain str")
    if type(new_status) is not str and type(new_status) is not PendingStatus:
        raise ValueError("new_status must be a plain str or PendingStatus")
    if reason is not None and type(reason) is not str:
        raise ValueError("reason must be a plain str or None")
    key = _required_text(item_id, field_name="item_id")
    matches = [item for item in state.pending_items if item.item_id == key]
    if not matches:
        raise KeyError(f"unknown pending item: {key}")
    item = matches[0]
    current, target = PendingStatus(item.status), PendingStatus(new_status)
    if target not in _PENDING_TRANSITIONS[current]:
        raise ValueError(f"illegal pending transition: {current.value} -> {target.value}")
    def _instant(value: str) -> datetime:
        return datetime.fromisoformat(_confirmed_at(value).replace("Z", "+00:00"))

    if _instant(last_confirmed_at) <= _instant(item.last_confirmed_at):
        raise ValueError("last_confirmed_at must be later than the current confirmation")
    pointer = item.source_pointer
    if target is not PendingStatus.OPEN:
        pointer = _required_text(source_pointer or "", field_name="source_pointer")
    elif source_pointer is not None:
        pointer = source_pointer
    if target is PendingStatus.BLOCKED:
        reason = _required_text(reason or "", field_name="reason")
    else:
        reason = None  # 离开 blocked 时清空原因
    updated = replace(
        item,
        status=target.value,
        source_pointer=pointer,
        last_confirmed_at=last_confirmed_at,
        reason=reason,
    )
    return replace(
        state,
        pending_items=tuple(updated if i is item else i for i in state.pending_items),
    )


def resolve_durable_sources(
    state: DurableContextState,
    resolver: SourcePointerResolver,
) -> tuple[SourceResolution, ...]:
    """Resolve each distinct durable pointer once, outside lossy compaction."""

    pointers = dict.fromkeys(
        [item.source_pointer for item in state.facts]
        + [item.source_pointer for item in state.pending_items]
    )
    return tuple(resolver.resolve(pointer) for pointer in pointers)


EMPTY_DURABLE_STATE = DurableContextState()


@dataclass(frozen=True)
class CompactionResult:
    """Lossy messages plus the exact durable state that bypassed the pipeline."""

    messages: list[dict]
    durable_state: DurableContextState
    tokens_before: int
    tokens_after: int
    applied_layers: tuple[str, ...]


class MessageViewLimitExceeded(RuntimeError):
    """Report an irreducible message view before a provider request is made.

    The limit covers ``messages`` only. System prompts, tool definitions, and
    provider-specific token accounting remain separate budgets, so callers
    must not present this estimate as the model's complete context size.
    """

    def __init__(
        self,
        *,
        tokens_before: int,
        tokens_after: int,
        hard_limit: int,
        applied_layers: tuple[str, ...],
    ) -> None:
        self.tokens_before = tokens_before
        self.tokens_after = tokens_after
        self.hard_limit = hard_limit
        self.applied_layers = applied_layers
        super().__init__(
            "message view remains above the hard limit after compaction: "
            f"{tokens_after:,} >= {hard_limit:,} estimated tokens"
        )


def render_durable_context(
    state: DurableContextState,
    *,
    source_resolutions: Sequence[SourceResolution] | None = None,
) -> str:
    """Render durable state without treating unavailable evidence as verified."""

    if not state.facts and not state.pending_items and not state.retrieval_evidence:
        return ""
    resolution_by_pointer: dict[str, SourceResolution] | None = None
    if source_resolutions is not None:
        resolution_by_pointer = {}
        for resolution in source_resolutions:
            if resolution.pointer in resolution_by_pointer:
                raise ValueError(
                    f"duplicate source resolution: {resolution.pointer}"
                )
            resolution_by_pointer[resolution.pointer] = resolution
        expected_pointers = {
            item.source_pointer for item in (*state.facts, *state.pending_items)
        }
        if set(resolution_by_pointer) != expected_pointers:
            raise ValueError(
                "source resolutions must match every durable source pointer exactly"
            )

    def source_status(pointer: str) -> str:
        if resolution_by_pointer is None:
            return ""
        resolution = resolution_by_pointer[pointer]
        if resolution.status is SourceResolutionStatus.AVAILABLE:
            if not resolution.evidence_sha256 or not _EVIDENCE_SHA256_PATTERN.fullmatch(
                resolution.evidence_sha256
            ):
                raise ValueError(
                    f"available source {pointer} must carry a full SHA-256 digest"
                )
            return (
                f"; source_status=available; "
                f"evidence_sha256={resolution.evidence_sha256}"
            )
        return (
            f"; source_status={resolution.status.value}; "
            "evidence_unavailable=true"
        )

    def rendered_pointer(pointer: str) -> str:
        # Keep ordinary IDs readable while preventing control characters in an
        # untrusted pointer from creating new Prompt lines.
        return json.dumps(pointer, ensure_ascii=False)[1:-1]

    lines = ["[Durable context — do not reinterpret as conversation summary]"]
    if state.facts:
        lines.append("Confirmed facts:")
        for fact in state.facts:
            lines.append(
                f"- {fact.fact_id}: {fact.content} "
                f"(source={rendered_pointer(fact.source_pointer)}"
                f"{source_status(fact.source_pointer)}; "
                f"confirmed={fact.last_confirmed_at})"
            )
    if state.pending_items:
        lines.append("Pending work:")
        done = sorted(
            (i for i in state.pending_items if i.status == PendingStatus.DONE.value),
            key=lambda i: datetime.fromisoformat(i.last_confirmed_at.replace("Z", "+00:00")),
            reverse=True,
        )
        shown_done = {id(i) for i in done[:MAX_RENDERED_DONE_ITEMS]}
        for item in state.pending_items:
            if item.status == PendingStatus.DONE.value and id(item) not in shown_done:
                continue
            reason = f"; reason={rendered_pointer(item.reason)}" if item.reason else ""
            lines.append(
                f"- {item.item_id}: {item.description} "
                f"(status={item.status}{reason}; "
                f"source={rendered_pointer(item.source_pointer)}"
                f"{source_status(item.source_pointer)}; "
                f"confirmed={item.last_confirmed_at})"
            )
        if len(done) > MAX_RENDERED_DONE_ITEMS:
            lines.append(f"- done_omitted={len(done) - MAX_RENDERED_DONE_ITEMS}")
    if state.retrieval_evidence:
        lines.append("Selected retrieval evidence:")
        for evidence in state.retrieval_evidence:
            conflict = (
                f"; conflict_winner={evidence.conflict_key}"
                if evidence.conflict_key
                else "; conflict=none"
            )
            lines.append(
                f"- {evidence.memory_id}: "
                f"source={evidence.source_type}:{evidence.source_id}; "
                f"title={evidence.source_title}; captured={evidence.captured_at}; "
                f"score={evidence.score}; rank={evidence.source_rank}{conflict}"
            )
    return "\n".join(lines)


def estimate_tokens(messages: list) -> int:
    """Estimate token count for messages.

    Production harness: tiktoken.encode() for precise count.
    Teaching version: len(text) // 4 as rough estimate.
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += len(json.dumps(block, default=str, ensure_ascii=False)) // 4
                elif hasattr(block, 'model_dump'):
                    total += len(json.dumps(block.model_dump(), default=str, ensure_ascii=False)) // 4
                else:
                    total += len(str(block)) // 4
        total += 4  # role overhead
    return total


class ToolProtocolError(ValueError):
    """Raised when a prompt view contains an invalid tool interaction graph."""


@dataclass(frozen=True)
class _ToolInteraction:
    """Locations of one complete provider tool-use/result interaction."""

    tool_use_message: int
    tool_result_message: int


def _block_field(block: object, field: str, default: object = None) -> object:
    """Read one field from mapping-shaped or SDK-shaped content blocks."""

    if isinstance(block, dict):
        return block.get(field, default)
    return getattr(block, field, default)


def _tool_block_id(block: object) -> tuple[str | None, object]:
    """Return the protocol block type and its correlation ID, if applicable."""

    block_type = _block_field(block, "type")
    if block_type == "tool_use":
        return "tool_use", _block_field(block, "id")
    if block_type == "tool_result":
        return "tool_result", _block_field(block, "tool_use_id")
    return None, None


def validate_tool_protocol(messages: list) -> dict[str, _ToolInteraction]:
    """Validate and index complete tool interactions by correlation ID.

    Compaction is allowed to lose evidence, but never to manufacture a
    provider-invalid prompt. Each tool-use ID must therefore occur exactly
    once on an assistant block and exactly once on a later user result block.
    """

    uses: dict[str, int] = {}
    results: dict[str, int] = {}
    for message_index, message in enumerate(messages):
        role = message.get("role")
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            block_type, raw_id = _tool_block_id(block)
            if block_type is None:
                continue
            if not isinstance(raw_id, str) or not raw_id.strip():
                raise ToolProtocolError(f"{block_type} requires a non-empty correlation ID")
            tool_id = raw_id
            expected_role = "assistant" if block_type == "tool_use" else "user"
            if role != expected_role:
                raise ToolProtocolError(
                    f"{block_type} {tool_id!r} must appear in a {expected_role} message"
                )
            locations = uses if block_type == "tool_use" else results
            if tool_id in locations:
                raise ToolProtocolError(f"duplicate {block_type} ID: {tool_id!r}")
            locations[tool_id] = message_index

    missing_results = sorted(set(uses) - set(results))
    if missing_results:
        raise ToolProtocolError(
            f"tool_use has no matching tool_result: {missing_results[0]!r}"
        )
    unknown_results = sorted(set(results) - set(uses))
    if unknown_results:
        raise ToolProtocolError(
            f"tool_result has no matching tool_use: {unknown_results[0]!r}"
        )

    interactions: dict[str, _ToolInteraction] = {}
    for tool_id, use_message in uses.items():
        result_message = results[tool_id]
        if result_message <= use_message:
            raise ToolProtocolError(
                f"tool_result must follow tool_use for ID {tool_id!r}"
            )
        interactions[tool_id] = _ToolInteraction(use_message, result_message)
    return interactions


def _without_tool_interactions(messages: list, removed_ids: set[str]) -> list:
    """Remove both sides of selected interactions and discard empty messages."""

    if not removed_ids:
        return messages
    filtered: list = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            filtered.append(message)
            continue
        kept_blocks = []
        for block in content:
            block_type, raw_id = _tool_block_id(block)
            if block_type is not None and raw_id in removed_ids:
                continue
            kept_blocks.append(block)
        if kept_blocks:
            copied_message = dict(message)
            copied_message["content"] = kept_blocks
            filtered.append(copied_message)
    return filtered


# ======================================================================
# Layer 1: Tool result truncation
# ======================================================================

def truncate_tool_results(messages: list) -> tuple[list, int]:
    """Layer 1: Truncate tool results that exceed token budget.

    Scans all tool_result blocks. If a result exceeds
    MAX_TOOL_RESULT_TOKENS, truncate it and add a note.

    Returns: (modified messages, tokens saved)
    """
    validate_tool_protocol(messages)
    saved = 0
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            result = str(block.get("content", ""))
            tokens = len(result) // 4
            if tokens > MAX_TOOL_RESULT_TOKENS:
                max_chars = MAX_TOOL_RESULT_TOKENS * 4
                original_len = len(result)
                truncated = result[:max_chars]
                block["content"] = (
                    truncated
                    + f"\n\n[... 已截断: 原始 {original_len} 字符, "
                    f"保留 {max_chars} 字符 ...]"
                )
                saved += tokens - MAX_TOOL_RESULT_TOKENS

    validate_tool_protocol(messages)
    return messages, saved


# ======================================================================
# Layer 2: File content deduplication
# ======================================================================

def dedup_file_reads(messages: list) -> tuple[list, int]:
    """Layer 2: Remove duplicate file reads, keep only the latest.

    When the agent reads the same file multiple times, older reads
    are redundant. We track read_file tool calls and remove older
    results for the same path.

    Returns: (modified messages, tokens saved)
    """
    validate_tool_protocol(messages)

    # Find the latest completed read of each file path. ``_read_path`` is
    # execution metadata, while ``tool_use_id`` is the protocol authority.
    latest_reads: dict[str, str] = {}  # path -> tool-use ID
    for mi, msg in enumerate(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                path = block.get("_read_path")
                tool_id = block.get("tool_use_id")
                if path and isinstance(tool_id, str):
                    latest_reads[path] = tool_id

    # Mark older reads, then remove both their tool_use and tool_result blocks.
    removed_ids: set[str] = set()
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                path = block.get("_read_path")
                tool_id = block.get("tool_use_id")
                if path and tool_id and latest_reads.get(path) != tool_id:
                    removed_ids.add(tool_id)

    old_count = estimate_tokens(messages)
    deduplicated = _without_tool_interactions(messages, removed_ids)
    validate_tool_protocol(deduplicated)
    return deduplicated, old_count - estimate_tokens(deduplicated)


# ======================================================================
# Layer 3: Message history pruning
# ======================================================================

def prune_old_messages(messages: list) -> tuple[list, int]:
    """Layer 3: Drop old messages, keep recent N turns.

    Keeps the first user message (for context) and the most recent
    KEEP_RECENT_TURNS messages. Everything in between is dropped.

    CRITICAL: Cannot leave orphaned tool_result blocks — if we remove
    a tool_use, we must also remove its corresponding tool_result,
    and vice versa. Otherwise the API will error.

    Returns: (pruned messages, tokens saved)
    """
    interactions = validate_tool_protocol(messages)
    if len(messages) <= KEEP_RECENT_TURNS + 1:
        return messages, 0

    old_count = estimate_tokens(messages)

    selected_indices = {0, *range(len(messages) - KEEP_RECENT_TURNS, len(messages))}
    crossing_ids = {
        tool_id
        for tool_id, interaction in interactions.items()
        if ((interaction.tool_use_message in selected_indices)
            != (interaction.tool_result_message in selected_indices))
    }
    selected = [
        message for index, message in enumerate(messages) if index in selected_indices
    ]
    pruned = _without_tool_interactions(selected, crossing_ids)
    validate_tool_protocol(pruned)
    new_count = estimate_tokens(pruned)

    return pruned, old_count - new_count


# ======================================================================
# Layer 4: Full conversation summary
# ======================================================================

def _model_summary(conversation_text: str) -> str:
    """Online adapter kept outside the pure compaction state contract."""

    summary_prompt = (
        "请总结以下对话的关键信息, 用于后续对话的上下文恢复。\n"
        "包括: 讨论的问题, 执行的操作, 得到的结论, 当前任务进度。\n"
        "简洁, 只保留关键信息。"
    )
    response = runtime_client().messages.create(
        model=MODEL,
        system=summary_prompt,
        messages=[{"role": "user", "content": conversation_text}],
        max_tokens=2000,
    )
    return str(response.content[0].text)


def generate_summary(
    messages: list,
    summarizer: Callable[[str], str] | None = None,
) -> tuple[list, int]:
    """Layer 4: Generate a conversation summary replacing old messages.

    Calls the model to summarize the conversation, then replaces
    old messages with the summary. Keeps the most recent few messages
    for continuity.

    This is the most expensive layer — it costs one API call.
    But it can compress tens of thousands of tokens into a few hundred.

    Returns: (summarized messages, tokens saved)
    """
    interactions = validate_tool_protocol(messages)
    if len(messages) <= 4:
        return messages, 0

    old_count = estimate_tokens(messages)

    # Split at a complete interaction boundary. If the raw four-message cut
    # crosses a tool call, expand the recent window back to its tool_use.
    keep_recent = 4
    recent_start = len(messages) - keep_recent
    while True:
        expanded_start = min(
            (
                interaction.tool_use_message
                for interaction in interactions.values()
                if (interaction.tool_use_message < recent_start
                    <= interaction.tool_result_message)
            ),
            default=recent_start,
        )
        if expanded_start == recent_start:
            break
        recent_start = expanded_start
    if recent_start == 0:
        return messages, 0
    to_summarize = messages[:recent_start]
    recent = messages[recent_start:]

    # Build a text representation of old messages for summarization
    convo_text = []
    for msg in to_summarize:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        parts.append(
                            f"[tool_use: {block.get('name', '?')} "
                            f"id={block.get('id', '?')}]"
                        )
                    elif block.get("type") == "tool_result":
                        parts.append(
                            f"[tool_result: id={block.get('tool_use_id', '?')} "
                            f"{str(block.get('content', ''))[:200]}]"
                        )
                elif hasattr(block, 'type'):
                    if block.type == "text":
                        parts.append(block.text)
                    elif block.type == "tool_use":
                        parts.append(
                            f"[tool_use: {block.name} id={getattr(block, 'id', '?')}]"
                        )
                    elif block.type == "tool_result":
                        parts.append(
                            "[tool_result: "
                            f"id={getattr(block, 'tool_use_id', '?')} "
                            f"{str(block.content)[:200]}]"
                        )
            content = " ".join(parts)
        convo_text.append(f"{role}: {content[:500]}")

    try:
        summary = (summarizer or _model_summary)("\n".join(convo_text))
    except Exception:
        # A failed summary is not evidence. Replacing history with an error
        # string would silently discard the only remaining conversation copy.
        return messages, 0

    summary = str(summary).strip()
    if not summary:
        return messages, 0

    summarized = [
        {"role": "user", "content": f"[之前的对话摘要]\n{summary}"},
        {"role": "assistant", "content": "好的, 我已了解之前的对话内容。请继续。"},
    ] + recent

    validate_tool_protocol(summarized)

    new_count = estimate_tokens(summarized)
    if new_count >= old_count:
        return messages, 0

    return summarized, old_count - new_count


# ======================================================================
# Compact dispatcher
# ======================================================================

def compact_context(
    messages: list,
    durable_state: DurableContextState = EMPTY_DURABLE_STATE,
    *,
    summarizer: Callable[[str], str] | None = None,
    verbose: bool = True,
) -> CompactionResult:
    """Compact an isolated message copy while carrying durable state unchanged.

    Tries layers in order: L1 → L2 → L3 → L4.
    Stops as soon as token count drops below the soft threshold. If all four
    layers leave the message view at or above ``HARD_LIMIT``, raises
    ``MessageViewLimitExceeded`` so a caller cannot accidentally send it.

    ``messages`` are a disposable prompt view, so the pipeline may truncate,
    deduplicate, prune, or summarize them. ``durable_state`` is a typed Memory
    input and deliberately bypasses every lossy layer. The deep copy also keeps
    callers' transcript-derived messages unchanged for replay and audit.
    """
    working = copy.deepcopy(messages)
    validate_tool_protocol(working)
    tokens_before = estimate_tokens(working)
    tokens = tokens_before
    applied_layers: list[str] = []

    if tokens < TOKEN_THRESHOLD:
        return CompactionResult(
            messages=working,
            durable_state=durable_state,
            tokens_before=tokens_before,
            tokens_after=tokens,
            applied_layers=(),
        )

    if verbose:
        print(f"\n\033[33m[compact] 当前 token 估算: {tokens:,} (阈值: {TOKEN_THRESHOLD:,})\033[0m")

    # Layer 1: Truncate large tool results
    working, saved = truncate_tool_results(working)
    tokens = estimate_tokens(working)
    if saved > 0:
        applied_layers.append("tool_result_truncation")
    if saved > 0 and verbose:
        print(f"\033[33m[compact] L1 截断工具结果: 节省 {saved:,} tokens, 当前 {tokens:,}\033[0m")
    if tokens < TOKEN_THRESHOLD:
        return CompactionResult(
            working, durable_state, tokens_before, tokens, tuple(applied_layers)
        )

    # Layer 2: Dedup file reads
    working, saved = dedup_file_reads(working)
    tokens = estimate_tokens(working)
    if saved > 0:
        applied_layers.append("file_read_deduplication")
    if saved > 0 and verbose:
        print(f"\033[33m[compact] L2 文件去重: 节省 {saved:,} tokens, 当前 {tokens:,}\033[0m")
    if tokens < TOKEN_THRESHOLD:
        return CompactionResult(
            working, durable_state, tokens_before, tokens, tuple(applied_layers)
        )

    # Layer 3: Prune old messages
    working, saved = prune_old_messages(working)
    tokens = estimate_tokens(working)
    if saved > 0:
        applied_layers.append("message_pruning")
    if saved > 0 and verbose:
        print(f"\033[33m[compact] L3 修剪旧消息: 节省 {saved:,} tokens, 当前 {tokens:,}\033[0m")
    if tokens < TOKEN_THRESHOLD:
        return CompactionResult(
            working, durable_state, tokens_before, tokens, tuple(applied_layers)
        )

    # Layer 4: Generate summary
    working, saved = generate_summary(working, summarizer=summarizer)
    tokens = estimate_tokens(working)
    if saved > 0:
        applied_layers.append("conversation_summary")
    if saved > 0 and verbose:
        print(f"\033[33m[compact] L4 生成摘要: 节省 {saved:,} tokens, 当前 {tokens:,}\033[0m")

    # Compaction is best-effort, but provider admission is fail-closed. A
    # single oversized message or a failed summarizer may be impossible to
    # shrink; returning that view would merely defer failure to the provider.
    if tokens >= HARD_LIMIT:
        raise MessageViewLimitExceeded(
            tokens_before=tokens_before,
            tokens_after=tokens,
            hard_limit=HARD_LIMIT,
            applied_layers=tuple(applied_layers),
        )

    return CompactionResult(
        messages=working,
        durable_state=durable_state,
        tokens_before=tokens_before,
        tokens_after=tokens,
        applied_layers=tuple(applied_layers),
    )


def compact_if_needed(
    messages: list,
    verbose: bool = True,
    *,
    durable_state: DurableContextState = EMPTY_DURABLE_STATE,
    summarizer: Callable[[str], str] | None = None,
) -> list:
    """Compatibility entrypoint returning only the disposable prompt messages."""

    return compact_context(
        messages,
        durable_state,
        summarizer=summarizer,
        verbose=verbose,
    ).messages


# ======================================================================
# Tools (simplified)
# ======================================================================

def run_read(path: str) -> str:
    try:
        p = (WORKDIR / path).resolve()
        if not p.is_relative_to(WORKDIR):
            return f"Error: path escapes workspace"
        return p.read_text()[:20000]  # Allow large reads to test truncation
    except Exception as e:
        return f"Error: {e}"

def run_bash(command: str) -> str:
    import subprocess
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True, timeout=30)
        return (r.stdout + r.stderr).strip()[:5000] or "(no output)"
    except Exception as e:
        return f"Error: {e}"

def run_write(path: str, content: str) -> str:
    try:
        p = (WORKDIR / path).resolve()
        if not p.is_relative_to(WORKDIR):
            return "Error: path escapes workspace"
        p.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


TOOLS = [
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object",
         "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object",
         "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "write_file", "description": "Write content to a file.",
     "input_schema": {"type": "object",
         "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
         "required": ["path", "content"]}},
]

TOOL_HANDLERS = {"read_file": run_read, "bash": run_bash, "write_file": run_write}


# ======================================================================
# Agent Loop with compaction
# ======================================================================

def agent_loop(
    messages: list,
    durable_state: DurableContextState = EMPTY_DURABLE_STATE,
):
    """Agent loop with lossy messages and lossless durable state kept separate."""
    while True:
        result = compact_context(messages, durable_state, verbose=True)
        messages = result.messages

        # Token check display
        tokens = estimate_tokens(messages)
        print(f"\033[90m[tokens: {tokens:,} / {TOKEN_THRESHOLD:,}]\033[0m")

        durable_context = render_durable_context(result.durable_state)
        runtime_system = SYSTEM
        if durable_context:
            runtime_system += f"\n\n{durable_context}"
        response = runtime_client().messages.create(
            model=MODEL, system=runtime_system, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return messages

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            handler = TOOL_HANDLERS.get(block.name)
            output = handler(**block.input) if handler else f"Unknown: {block.name}"

            display = str(output)[:100].replace('\n', ' ')
            print(f"  \033[36m> {block.name}\033[0m {display}")

            # Tag tool results for dedup (Layer 2)
            result_block = {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            }
            if block.name == "read_file":
                result_block["_read_path"] = block.input.get("path", "")

            results.append(result_block)

        messages.append({"role": "user", "content": results})
    return messages


# ======================================================================
# Main
# ======================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("s14: Context Compact — 四层压缩管线")
    print("=" * 60)
    print(f"\033[90m  阈值: {TOKEN_THRESHOLD:,} tokens\033[0m")
    print(f"\033[90m  L1: 工具结果截断 (> {MAX_TOOL_RESULT_TOKENS:,} tokens)\033[0m")
    print(f"\033[90m  L2: 文件内容去重 (同文件只留最新)\033[0m")
    print(f"\033[90m  L3: 消息修剪 (保留最近 {KEEP_RECENT_TURNS} 轮)\033[0m")
    print(f"\033[90m  L4: 全对话摘要 (模型生成)\033[0m")
    print(f"\033[90m  输入 stats 查看当前 token 使用，/pending-demo 演示 pending 生命周期\033[0m")
    print()

    history = []
    while True:
        try:
            query = input("\033[36ms14 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit"):
            break

        if query.strip() == "/pending-demo":
            # harness 侧可信调用点：模型和摘要都不能结案
            demo = DurableContextState(pending_items=(PendingItem(
                "ship-docs", "更新部署文档", "transcript:demo:3", "2026-10-01T09:00:00+00:00"),))
            print("[before] " + render_durable_context(demo).splitlines()[-1])
            demo = transition_pending_item(
                demo, "ship-docs", "done",
                last_confirmed_at="2026-10-01T10:00:00+00:00", source_pointer="transcript:demo:7")
            print("[harness] ship-docs: open -> done")
            print("[after]  " + render_durable_context(demo).splitlines()[-1])
            try:
                transition_pending_item(
                    demo, "ship-docs", "open", last_confirmed_at="2026-10-01T11:00:00+00:00")
            except ValueError:
                print("[harness] ship-docs: done -> open rejected")
            continue

        if query.strip().lower() == "stats":
            tokens = estimate_tokens(history)
            print(f"\033[90m消息数: {len(history)}\033[0m")
            print(f"\033[90mToken 估算: {tokens:,} / {TOKEN_THRESHOLD:,}\033[0m")
            print(f"\033[90m阈值比例: {tokens/TOKEN_THRESHOLD*100:.1f}%\033[0m")
            continue

        history.append({"role": "user", "content": query})
        history = agent_loop(history)

        for block in history[-1]["content"]:
            if getattr(block, "type", None) == "text":
                print(block.text)
        print()
