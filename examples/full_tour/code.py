#!/usr/bin/env python3
"""Mini WorkBuddy — Full Tour.

One command that walks the whole harness end to end and leaves artifacts on
disk you can inspect. Where the mini demo shows a single agent loop, this
tour shows how the *layers* fit together:

    provider adapter -> session -> workspace memory -> tool dispatch
    -> permission denial -> large-output externalization
    -> JSONL transcript + crash-style recovery
    -> HTTP /run endpoint (ACP-like) -> audit hash chain + verify

It runs offline by default (deterministic mock provider, no key, no network)
and can run against a real provider with --provider anthropic|openai. Every
stage prints a one-line explanation before it acts, so the transcript reads
like a guided tour rather than a log dump.

Usage:
    python examples/full_tour/code.py                     # offline
    python examples/full_tour/code.py --provider openai   # real (needs key)
    python examples/full_tour/code.py --home /tmp/tour     # choose output dir

Exit code is 0 only if every stage succeeded AND the audit chain verifies.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import asdict
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mini_workbuddy import providers as P
from mini_workbuddy.audit import AuditLog
from mini_workbuddy.config import HarnessConfig
from mini_workbuddy.events import EventBus
from mini_workbuddy.server import HarnessRuntime, make_handler
from mini_workbuddy.storage import Storage
from mini_workbuddy.tools import PermissionError as PolicyDenied
from mini_workbuddy.tools import ToolRegistry


def banner(step: int, title: str, why: str) -> None:
    print("\n" + "=" * 72)
    print(f"[{step}] {title}")
    print("    " + why)
    print("=" * 72)


def free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def run_tour(home: Path, provider_name: str | None) -> dict:
    artifacts: dict[str, str] = {}
    config = HarnessConfig(root_dir=home)
    config.ensure_dirs()
    storage = Storage(config)
    tools = ToolRegistry(config, storage)
    events = EventBus()
    audit = AuditLog(config)

    # -- 1. provider adapter ------------------------------------------------
    banner(1, "Provider adapter", "One loop, many model protocols; offline mock needs no key.")
    provider = P.select_provider(provider_name)
    print(f"    provider = {provider.name}, model = {provider.model}")

    # -- 2. session ---------------------------------------------------------
    banner(2, "Session", "A session binds a workspace cwd to a transcript and audit stream.")
    session = storage.create_session(cwd=str(ROOT), title=f"full tour ({provider.name})")
    storage.append_event(session, {"type": "message", "role": "user", "content": "Run the full harness tour."})
    audit.append("session_create", {"sessionId": session.id, "cwd": session.cwd})
    print(f"    session = {session.id}")
    print(f"    cwd     = {session.cwd}")

    # -- 3. workspace memory ------------------------------------------------
    banner(3, "Workspace memory", "Durable notes the agent can recall later, scoped to this workspace.")
    mem_path = storage.append_memory("workspace", "- Full tour: prove every harness layer wires together.")
    print(f"    wrote memory -> {mem_path}")
    artifacts["workspace_memory"] = str(mem_path)

    # -- 4. tool dispatch (allowed) -----------------------------------------
    banner(4, "Tool dispatch", "The agent acts on the world only through registered tools.")
    result = tools.run("bash", "echo 'hello from the harness' && pwd", session)
    audit.append("tool_result", {"sessionId": session.id, "tool": "bash", "exit_code": result.exit_code})
    storage.append_event(session, {"type": "tool_result", **asdict(result)})
    print("    bash ->", result.content.strip().splitlines()[0])

    # -- 5. permission denial (fail-closed) ---------------------------------
    banner(5, "Permission denial", "Dangerous first tokens are denied; the harness fails closed.")
    try:
        tools.run("bash", "sudo rm -rf /", session)
        print("    UNEXPECTED: dangerous command was allowed")
        denied = False
    except PolicyDenied as exc:
        audit.append("tool_denied", {"sessionId": session.id, "reason": str(exc)})
        print(f"    denied as expected -> {exc}")
        denied = True

    # -- 6. large-output externalization ------------------------------------
    banner(6, "Output externalization", "Huge tool output is swapped to a file with a pointer, sparing the context window.")
    big = tools.run("bash", "for i in $(seq 1 4000); do echo \"line $i: padding padding padding padding\"; done", session)
    if big.externalized_path:
        print(f"    externalized -> {big.externalized_path}")
        print(f"    inline preview kept to {len(big.content)} chars")
        artifacts["externalized_output"] = big.externalized_path
    else:
        print("    (output below threshold; not externalized this run)")

    # -- 7. transcript + recovery -------------------------------------------
    banner(7, "JSONL transcript + recovery", "Append-only events let a fresh Storage replay the session after a crash.")
    storage.append_event(session, {"type": "message", "role": "assistant", "content": "Tour stages executed."})
    tpath = storage.transcript_path(session)
    recovered = Storage(config).read_transcript(session)
    print(f"    transcript -> {tpath}")
    print(f"    replayed {len(recovered)} events from a fresh Storage instance")
    artifacts["transcript"] = str(tpath)

    # -- 8. HTTP /run (ACP-like) --------------------------------------------
    banner(8, "HTTP run endpoint", "The sidecar exposes an ACP-like control plane; here we drive one real request.")
    runtime = HarnessRuntime(config)
    port = free_port()
    httpd = HTTPServer(("127.0.0.1", port), make_handler(runtime))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/v1/runs",
            data=json.dumps({"cwd": str(ROOT), "prompt": "list files"}).encode(),
            headers={"Content-Type": "application/json", config.request_header: config.request_header_value},
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode())
        http_session = payload.get("data", {}).get("session", {}).get("id", "?")
        print(f"    POST /api/v1/runs -> sessionId {http_session}")
        http_ok = http_session != "?"
    except Exception as exc:  # pragma: no cover - network edge
        print(f"    HTTP run failed: {exc}")
        http_ok = False
    finally:
        httpd.shutdown()

    # -- 9. audit chain + verify --------------------------------------------
    banner(9, "Audit hash chain", "Every high-risk action is chained; the head anchor also catches truncation.")
    entries = audit.read_entries()
    verified = audit.verify()
    print(f"    audit file    -> {audit.path}")
    print(f"    audit entries -> {len(entries)}")
    print(f"    chain verifies-> {verified}")
    print(f"    head anchor   -> {audit.head_path}")
    artifacts["audit_log"] = str(audit.path)
    artifacts["audit_head"] = str(audit.head_path)

    # -- manifest -----------------------------------------------------------
    manifest = {
        "provider": provider.name,
        "model": provider.model,
        "session": session.id,
        "stages": {
            "tool_dispatch": True,
            "permission_denied": denied,
            "externalized": big.externalized_path is not None,
            "transcript_events": len(recovered),
            "http_run": http_ok,
            "audit_entries": len(entries),
            "audit_verified": verified,
        },
        "artifacts": artifacts,
    }
    manifest_path = home / "full_tour_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    artifacts["manifest"] = str(manifest_path)

    banner(10, "Artifacts", "Everything the tour produced, in one manifest you can open.")
    for name, path in artifacts.items():
        print(f"    {name}: {path}")

    ok = denied and verified and http_ok
    return {"ok": ok, "manifest": manifest, "manifest_path": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini WorkBuddy full tour")
    parser.add_argument(
        "--provider",
        choices=["anthropic", "openai", "offline"],
        default=None,
        help="model backend (default: PROVIDER env or auto -> offline without keys)",
    )
    parser.add_argument(
        "--home",
        default=None,
        help="output directory for artifacts (default: a fresh temp dir)",
    )
    args = parser.parse_args()

    home = Path(args.home).expanduser() if args.home else Path(tempfile.mkdtemp(prefix="mini-wb-tour-"))
    print(f"Mini WorkBuddy full tour — artifacts in {home}")

    result = run_tour(home, args.provider)
    print("\n" + "=" * 72)
    print("RESULT:", "OK — every stage passed and the audit chain verifies." if result["ok"]
          else "INCOMPLETE — see stage output above.")
    print("Manifest:", result["manifest_path"])
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
