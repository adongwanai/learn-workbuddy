from __future__ import annotations

import json
import os
import stat
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import HarnessConfig, workspace_id


TRANSCRIPT_SCHEMA_VERSION = 1
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_RESERVED_TRANSCRIPT_FIELDS = frozenset(
    {
        "schema_version",
        "sequence",
        "recorded_at",
        "timestamp",
        "session_id",
        "event_id",
    }
)
_TRANSCRIPT_LOCKS: dict[Path, threading.Lock] = {}
_TRANSCRIPT_LOCKS_GUARD = threading.Lock()


class TranscriptCorruptionError(RuntimeError):
    """Raised when complete transcript evidence cannot be replayed safely."""


class TranscriptValidationError(ValueError):
    """Raised when a caller tries to spoof the transcript envelope."""


@dataclass
class SessionRecord:
    id: str
    cwd: str
    title: str
    created_at: int
    updated_at: int


def _transcript_lock(path: Path) -> threading.Lock:
    """Return one lock for each transcript path in this Python process.

    ``SafeThreadingHTTPServer`` may handle two prompts for the same session at
    once.  Sharing the lock across ``Storage`` instances keeps the
    read-sequence-append operation linear inside that runtime process.
    """

    key = path.resolve()
    with _TRANSCRIPT_LOCKS_GUARD:
        return _TRANSCRIPT_LOCKS.setdefault(key, threading.Lock())


class Storage:
    def __init__(self, config: HarnessConfig) -> None:
        self.config = config
        self.config.ensure_dirs()

    def create_session(self, cwd: str, title: str = "Untitled") -> SessionRecord:
        now = int(time.time() * 1000)
        record = SessionRecord(id=str(uuid.uuid4()), cwd=cwd, title=title, created_at=now, updated_at=now)
        self.write_session_record(record)
        return record

    def write_session_record(self, record: SessionRecord) -> None:
        self.config.sessions_dir.mkdir(parents=True, exist_ok=True)
        (self.config.sessions_dir / f"{record.id}.json").write_text(
            json.dumps(asdict(record), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def load_session(self, session_id: str) -> SessionRecord:
        path = self.config.sessions_dir / f"{session_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return SessionRecord(**data)

    def list_sessions(self) -> list[SessionRecord]:
        records: list[SessionRecord] = []
        for path in sorted(self.config.sessions_dir.glob("*.json")):
            try:
                records.append(SessionRecord(**json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
        return records

    def transcript_path(self, record: SessionRecord) -> Path:
        project_dir = self.config.projects_dir / workspace_id(record.cwd)
        project_dir.mkdir(parents=True, exist_ok=True)
        return project_dir / f"{record.id}.jsonl"

    def append_event(
        self,
        record: SessionRecord,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist one event and return the exact versioned envelope written.

        Returning the Storage-owned identity lets Audit and live event
        projections point back to durable transcript evidence.  Callers still
        cannot supply those reserved fields themselves.
        """

        path = self.transcript_path(record)
        reserved = sorted(_RESERVED_TRANSCRIPT_FIELDS.intersection(event))
        if reserved:
            raise TranscriptValidationError(
                "event payload contains reserved envelope fields: "
                + ", ".join(reserved)
            )

        # The sequence is derived and persisted under one lock.  This makes
        # transcript order evidence rather than a timestamp-based guess when
        # concurrent HTTP handlers append to the same session.
        with _transcript_lock(path):
            events, partial_tail_offset = self._read_transcript_unlocked(
                path,
                record,
            )
            if partial_tail_offset is not None:
                # A final record without a newline never crossed the complete
                # JSONL-record boundary.  Remove only those uncommitted bytes
                # before continuing; complete malformed records fail closed.
                self._truncate_partial_tail_unlocked(path, partial_tail_offset)

            sequence = len(events) + 1
            now = datetime.now(timezone.utc)
            envelope = {
                **deepcopy(event),
                "schema_version": TRANSCRIPT_SCHEMA_VERSION,
                "sequence": sequence,
                "recorded_at": now.isoformat(),
                "timestamp": int(now.timestamp() * 1000),
                "session_id": record.id,
                "event_id": f"transcript:{record.id}:{sequence}",
            }
            payload = (
                json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n"
            ).encode("utf-8")
            self._append_transcript_unlocked(path, payload)
            return envelope

    def read_transcript(self, record: SessionRecord, limit: int = 1000) -> list[dict[str, Any]]:
        """Return the last ``limit`` events; a non-positive limit is empty."""

        path = self.transcript_path(record)
        with _transcript_lock(path):
            events, _ = self._read_transcript_unlocked(path, record)
        return events[-limit:] if limit > 0 else []

    def _read_transcript_unlocked(
        self,
        path: Path,
        record: SessionRecord,
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Validate complete records and locate one uncommitted tail.

        Legacy runtime records without an evidence envelope remain readable;
        derived fields are filled in memory.  Once a new record is appended,
        it uses the versioned envelope.  A malformed line ending in ``\n`` is
        durable evidence corruption and must never be silently skipped.
        """

        if not path.exists():
            return [], None

        raw = self._read_bytes(path)
        lines = raw.splitlines(keepends=True)
        events: list[dict[str, Any]] = []
        byte_offset = 0

        for line_number, line in enumerate(lines, start=1):
            line_start = byte_offset
            byte_offset += len(line)

            # The newline is the JSONL commit boundary.  Our writer emits the
            # object and newline in one payload, so an unterminated final line
            # is the only crash fragment that can be discarded automatically.
            if line_number == len(lines) and not line.endswith(b"\n"):
                return events, line_start
            if not line.strip():
                continue

            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TranscriptCorruptionError(
                    f"{path.name}:{line_number}: invalid complete JSON record"
                ) from exc
            if not isinstance(event, dict):
                raise TranscriptCorruptionError(
                    f"{path.name}:{line_number}: record must be a JSON object"
                )

            expected_sequence = len(events) + 1
            sequence = event.get("sequence", expected_sequence)
            if type(sequence) is not int or sequence != expected_sequence:
                raise TranscriptCorruptionError(
                    f"{path.name}:{line_number}: expected sequence "
                    f"{expected_sequence}, got {sequence!r}"
                )

            session_id = event.get("session_id", record.id)
            if session_id != record.id:
                raise TranscriptCorruptionError(
                    f"{path.name}:{line_number}: expected session_id "
                    f"{record.id!r}, got {session_id!r}"
                )

            expected_event_id = f"transcript:{record.id}:{expected_sequence}"
            event_id = event.get("event_id", expected_event_id)
            if event_id != expected_event_id:
                raise TranscriptCorruptionError(
                    f"{path.name}:{line_number}: expected event_id "
                    f"{expected_event_id!r}, got {event_id!r}"
                )

            schema_version = event.get(
                "schema_version",
                TRANSCRIPT_SCHEMA_VERSION,
            )
            if schema_version != TRANSCRIPT_SCHEMA_VERSION:
                raise TranscriptCorruptionError(
                    f"{path.name}:{line_number}: unsupported schema_version "
                    f"{schema_version!r}"
                )

            # Compatibility defaults let sessions written by the original
            # mini runtime replay without rewriting their historical lines.
            event.setdefault("schema_version", TRANSCRIPT_SCHEMA_VERSION)
            event.setdefault("sequence", expected_sequence)
            event.setdefault("session_id", record.id)
            event.setdefault("event_id", expected_event_id)
            events.append(event)

        return events, None

    @staticmethod
    def _read_bytes(path: Path) -> bytes:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
        try:
            Storage._require_regular_file(descriptor, path)
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 64 * 1024):
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    @staticmethod
    def _append_transcript_unlocked(path: Path, payload: bytes) -> None:
        created = not path.exists()
        descriptor = os.open(
            path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY | _NOFOLLOW,
            0o600,
        )
        try:
            Storage._require_regular_file(descriptor, path)
            Storage._write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if created:
            # ``fsync(file)`` persists bytes; syncing the parent also persists
            # the first directory entry so a newly created transcript survives
            # a crash at the same documented durability boundary.
            Storage._fsync_directory(path.parent)

    @staticmethod
    def _truncate_partial_tail_unlocked(path: Path, offset: int) -> None:
        descriptor = os.open(path, os.O_WRONLY | _NOFOLLOW)
        try:
            Storage._require_regular_file(descriptor, path)
            os.ftruncate(descriptor, offset)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written == 0:
                raise OSError("short write while persisting transcript")
            view = view[written:]

    @staticmethod
    def _require_regular_file(descriptor: int, path: Path) -> None:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(f"transcript path is not a regular file: {path}")

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":  # pragma: no cover - directory fsync is POSIX-only
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def tool_result_path(self, record: SessionRecord, tool_call_id: str) -> Path:
        base = self.transcript_path(record).with_suffix("")
        path = base / "tool-results"
        path.mkdir(parents=True, exist_ok=True)
        return path / f"{tool_call_id}.txt"

    def append_memory(self, scope: str, content: str) -> Path:
        safe_scope = scope.replace("/", "_").replace(" ", "_") or "workspace"
        path = self.config.memory_dir / f"{safe_scope}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(content.rstrip() + "\n")
        return path

    def read_memory(self, scope: str) -> str:
        safe_scope = scope.replace("/", "_").replace(" ", "_") or "workspace"
        path = self.config.memory_dir / f"{safe_scope}.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")
