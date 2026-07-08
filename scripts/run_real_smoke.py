#!/usr/bin/env python3
"""Real-API smoke runner — one command to verify live model calls.

CI never calls a real model (cost, flakiness, secrets). This script is the
opt-in counterpart: a reader with a key runs it once to confirm the whole
stack works against a live provider, across both the lesson entrypoints and
the mini harness demo.

Usage:
    # Anthropic (needs ANTHROPIC_API_KEY + MODEL_ID)
    python scripts/run_real_smoke.py

    # OpenAI (needs OPENAI_API_KEY, optional OPENAI_MODEL)
    python scripts/run_real_smoke.py --provider openai

    # Pick which targets to run (default: mini)
    python scripts/run_real_smoke.py --targets mini s01 s24

This intentionally lives outside the pytest suite: it is a manual,
key-required verification, not an automated test. It exits non-zero on the
first failure so it is still CI-composable if a maintainer wires it into a
gated, secret-bearing job.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _provider_ready(provider: str) -> tuple[bool, str]:
    if provider == "anthropic":
        ok = bool(os.getenv("ANTHROPIC_API_KEY") and os.getenv("MODEL_ID"))
        return ok, "ANTHROPIC_API_KEY and MODEL_ID"
    if provider == "openai":
        ok = bool(os.getenv("OPENAI_API_KEY"))
        return ok, "OPENAI_API_KEY"
    return False, provider


def _run(cmd: list[str], stdin: str | None = None, timeout: int = 120) -> int:
    print("\n" + "=" * 72)
    print("$", " ".join(cmd))
    print("=" * 72)
    result = subprocess.run(
        cmd, cwd=ROOT, text=True, input=stdin,
        stdout=None, stderr=subprocess.STDOUT, timeout=timeout,
    )
    return result.returncode


def smoke_mini(provider: str) -> int:
    return _run([
        sys.executable, "examples/mini_workbuddy_demo/code.py",
        "--mode", "real", "--provider", provider,
    ])


def smoke_lesson(script: str, provider: str) -> int:
    """Drive an interactive lesson with one prompt then quit.

    Lessons read the Anthropic env directly (they predate the adapter), so
    the lesson targets only apply to --provider anthropic. For openai, the
    mini demo is the real-call target.
    """
    if provider != "anthropic":
        print(f"[skip] {script}: lesson entrypoints use the Anthropic env; "
              f"use --provider anthropic to smoke them.")
        return 0
    prompt = "List the files in the current directory, then say DONE.\nq\n"
    return _run([sys.executable, script], stdin=prompt)


TARGETS = {
    "mini": lambda p: smoke_mini(p),
    "s01": lambda p: smoke_lesson("s01_agent_loop/code.py", p),
    "s24": lambda p: smoke_lesson("s24_comprehensive/code.py", p),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["anthropic", "openai"], default="anthropic")
    parser.add_argument("--targets", nargs="+", default=["mini"],
                        choices=sorted(TARGETS), help="which smokes to run")
    args = parser.parse_args()

    ready, needed = _provider_ready(args.provider)
    if not ready:
        raise SystemExit(
            f"Real smoke with --provider {args.provider} requires {needed}. "
            "Copy .env.example to .env and fill it in."
        )

    print(f"Real API smoke — provider={args.provider}, targets={args.targets}")
    for name in args.targets:
        code = TARGETS[name](args.provider)
        if code != 0:
            raise SystemExit(f"[FAIL] target '{name}' exited {code}")
        print(f"[ok] {name}")
    print("\nAll real-API smokes passed.")


if __name__ == "__main__":
    main()
