"""Offline stub for the `anthropic` SDK.

Purpose: let tests import every chapter's `code.py` (and exercise pure
functions like permission checks) without a network connection, an API
key, or the real SDK installed.

Usage (in-process):

    sys.path.insert(0, str(ROOT / "tests" / "stubs"))

Usage (subprocess):

    env["PYTHONPATH"] = f"{ROOT}/tests/stubs:{ROOT}"

The stub deliberately fails loudly if any test accidentally triggers a
real model call: `messages.create()` raises RuntimeError instead of
silently returning fake completions. Tests should exercise harness
mechanisms (permissions, hooks, memory, audit), never fake model output.
"""

from __future__ import annotations

from typing import Any


class _Messages:
    def create(self, **kwargs: Any):  # pragma: no cover - guard rail
        raise RuntimeError(
            "stub anthropic SDK: messages.create() must not be called in offline tests"
        )


class Anthropic:
    def __init__(self, **kwargs: Any) -> None:
        self.messages = _Messages()


class APIError(Exception):
    pass


class APIStatusError(APIError):
    pass
