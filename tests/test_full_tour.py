"""Smoke test for the full-tour example.

The tour is the repo's most integration-heavy artifact: provider adapter,
session, memory, tool dispatch, permission denial, externalization, JSONL
recovery, HTTP run, and audit — all in one run. This test pins that the
offline path stays green end to end, exits 0, verifies the audit chain,
and emits a manifest whose per-stage flags are all truthy.

Kept offline-only: no key, no network beyond loopback HTTP, deterministic.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_full_tour_offline_runs_green(root: Path, tmp_path: Path) -> None:
    home = tmp_path / "tour-home"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    # Force offline regardless of any ambient keys in the runner.
    env["PROVIDER"] = "offline"
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MODEL_ID"):
        env.pop(key, None)

    result = subprocess.run(
        [sys.executable, "examples/full_tour/code.py", "--home", str(home), "--provider", "offline"],
        cwd=root,
        env=env,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout[-3000:]

    manifest_path = home / "full_tour_manifest.json"
    assert manifest_path.exists(), "tour did not write its manifest"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["provider"] == "offline"
    stages = manifest["stages"]
    assert stages["tool_dispatch"] is True
    assert stages["permission_denied"] is True
    assert stages["externalized"] is True
    assert stages["http_run"] is True
    assert stages["audit_verified"] is True
    assert stages["transcript_events"] >= 2
    assert stages["audit_entries"] >= 5

    # Every artifact the manifest points to must actually exist on disk.
    for name, path in manifest["artifacts"].items():
        assert Path(path).exists(), f"missing artifact {name}: {path}"
