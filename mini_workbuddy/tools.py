from __future__ import annotations

import itertools
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import HarnessConfig
from .storage import SessionRecord, Storage


@dataclass
class ToolResult:
    tool_call_id: str
    name: str
    content: str
    externalized_path: str | None = None
    exit_code: int | None = None


class PermissionError(RuntimeError):
    pass


class ToolRegistry:
    def __init__(self, config: HarnessConfig, storage: Storage) -> None:
        self.config = config
        self.storage = storage
        self._id_counter = itertools.count(1)
        self._id_lock = threading.Lock()
        self._tools: dict[str, Callable[[str, SessionRecord], ToolResult]] = {
            "bash": self._bash,
            "read_file": self._read_file,
            "tool_search": self._tool_search,
        }

    def names(self) -> list[str]:
        return sorted(self._tools)

    def run(self, name: str, argument: str, session: SessionRecord) -> ToolResult:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        return self._tools[name](argument, session)

    def _bash(self, command: str, session: SessionRecord) -> ToolResult:
        self._check_command(command)
        try:
            completed = subprocess.run(
                command,
                cwd=session.cwd,
                shell=True,
                text=True,
                capture_output=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            # NB: subprocess.TimeoutExpired is NOT a subclass of builtin TimeoutError.
            # Convert it so MiniAgent's error boundary (which catches TimeoutError)
            # reports "Tool failed" instead of crashing the whole prompt.
            raise TimeoutError(f"command timed out after {exc.timeout:.0f}s: {command[:80]}") from exc
        content = completed.stdout
        if completed.stderr:
            content += ("\n--- stderr ---\n" + completed.stderr)
        return self._maybe_externalize("bash", command, content, session, completed.returncode)

    def _read_file(self, path: str, session: SessionRecord) -> ToolResult:
        target = self._resolve_session_path(path, session)
        content = target.read_text(encoding="utf-8", errors="replace")
        return self._maybe_externalize("read_file", path, content, session, 0)

    def _tool_search(self, query: str, session: SessionRecord) -> ToolResult:
        descriptions = {
            "bash": "Run a shell command in the session cwd. Dangerous commands are denied.",
            "read_file": "Read a UTF-8 text file by absolute or cwd-relative path.",
            "tool_search": "Search available deferred tools by name or description.",
        }
        needle = query.lower().strip()
        rows = [
            f"- {name}: {desc}"
            for name, desc in descriptions.items()
            if not needle or needle in name.lower() or needle in desc.lower()
        ]
        return ToolResult(tool_call_id=self._tool_call_id("tool_search"), name="tool_search", content="\n".join(rows))

    def _check_command(self, command: str) -> None:
        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            # Unparseable input (e.g. unbalanced quotes) is denied, not crashed on.
            # Fail-closed: if the harness cannot understand a command, it must not run it.
            raise PermissionError(f"command could not be parsed safely: {exc}") from exc
        denied = {"rm", "sudo", "shutdown", "reboot", "mkfs", "dd"}
        if tokens and tokens[0] in denied:
            raise PermissionError(f"command denied by mini harness policy: {tokens[0]}")
        if any(part in command for part in [" > /dev/", " /etc/passwd", " ~/.ssh"]):
            raise PermissionError("command touches a protected path")

    def _resolve_session_path(self, path: str, session: SessionRecord) -> Path:
        cwd = Path(session.cwd).expanduser().resolve(strict=True)
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate
        target = candidate.resolve(strict=True)
        if not target.is_relative_to(cwd):
            raise PermissionError(f"path escapes session cwd: {path}")
        if not target.is_file():
            raise PermissionError(f"path is not a regular file: {path}")
        return target

    def _maybe_externalize(
        self,
        name: str,
        argument: str,
        content: str,
        session: SessionRecord,
        exit_code: int | None,
    ) -> ToolResult:
        call_id = self._tool_call_id(name)
        if len(content.encode("utf-8")) <= self.config.tool_result_threshold:
            return ToolResult(tool_call_id=call_id, name=name, content=content, exit_code=exit_code)
        path = self.storage.tool_result_path(session, call_id)
        path.write_text(content, encoding="utf-8")
        preview = content[:6_000] + "\n\n...[externalized output]...\n\n" + content[-24_000:]
        pointer = f"\n\nFull output written to: {path}"
        return ToolResult(tool_call_id=call_id, name=name, content=preview + pointer, externalized_path=str(path), exit_code=exit_code)

    def _tool_call_id(self, name: str) -> str:
        with self._id_lock:
            counter = next(self._id_counter)
        return f"call_{name}_{time.time_ns()}_{counter}"
