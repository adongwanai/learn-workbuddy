#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".md", ".py", ".txt", ".yml", ".yaml", ".json", ".example", ".svg"}
SKIP_PARTS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".tmp"}
NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

FORBIDDEN_PATTERNS = [
    "/Users/" + "mac",
    "wxid" + "_",
    "WorkBuddy" + "-darwin",
    "app." + "asar",
    "main/" + "ac" + "p.js",
    "195" + "KB",
    "conversation" + "_search",
    "SOUL" + ".md",
    "IDENTITY" + ".md",
    "USER" + ".md",
    "BOOTSTRAP" + ".md",
    "sk-ant" + "-",
    "逆" + "向",
    "反" + "编译",
    "破" + "解",
]

FORBIDDEN_REGEXES = [
    re.compile(r"\b[0-9a-f]{64}\b", re.IGNORECASE),
]

PUBLIC_OVERSPECIFIED_REGEXES = [
    (re.compile(r"16\s*个\s*内置\s*Agent"), "exact internal agent count"),
    (re.compile(r"35\+\s*(个\s*)?(RPC|领域|domains?)", re.IGNORECASE), "exact-ish RPC domain count"),
    (re.compile(r"40\+\s*(个\s*)?(内置工具|MCP|连接器)", re.IGNORECASE), "exact-ish tool or connector count"),
    (re.compile(r"10\+\s*(个\s*)?(内置技能|Skills?|技能)", re.IGNORECASE), "exact-ish skill count"),
    (re.compile(r"\b(132" + r"KB|28" + r"KB|195" + r"KB)\b", re.IGNORECASE), "private bundle file size"),
    (re.compile(r"\bmain/(index|sidecar-entry)\.js\b"), "private bundle path"),
    (re.compile(r"\bsidecar-entry\.js\b"), "private sidecar filename"),
    (re.compile("conversation" + "_search"), "unverified memory endpoint name"),
    (re.compile(r"云端画像\s*v\d+", re.IGNORECASE), "unverified cloud profile version"),
]

IMAGE_OVERSPECIFIED_REGEXES = PUBLIC_OVERSPECIFIED_REGEXES + [
    (re.compile(r"\b35\+\b"), "exact-ish count in image"),
    (re.compile(r"\b40\+\b"), "exact-ish count in image"),
    (re.compile(r"16\s*个"), "exact-ish count in image"),
]


def iter_files(suffixes: set[str] | None = None) -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if path.is_dir():
            continue
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        if suffixes is not None and path.suffix not in suffixes:
            continue
        files.append(path)
    return sorted(files)


def iter_public_docs() -> list[Path]:
    docs: set[Path] = {
        ROOT / "README.md",
        ROOT / "NOTICE.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md",
    }
    docs.update(ROOT.glob("s[0-9][0-9]_*/README.md"))
    docs.update(ROOT.glob("examples/**/*.md"))
    docs.update(ROOT.glob("docs/legal/**/*.md"))
    return sorted(path for path in docs if path.exists())


def check_python_syntax() -> None:
    failures: list[str] = []
    for path in iter_files({".py"}):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            failures.append(f"{path.relative_to(ROOT)}:{exc.lineno}: {exc.msg}")
    if failures:
        raise SystemExit("Python syntax failed:\n" + "\n".join(failures))
    print(f"ok syntax: {len(iter_files({'.py'}))} Python files")


def run(cmd: list[str], *, env: dict[str, str] | None = None, timeout: int = 30) -> None:
    merged_env = os.environ.copy()
    merged_env["PYTHONPATH"] = str(ROOT)
    if env:
        merged_env.update(env)
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        env=merged_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise SystemExit(
            "Command failed: "
            + " ".join(cmd)
            + "\n"
            + result.stdout[-4000:]
        )
    print("ok run:", " ".join(cmd))


def run_capture(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    timeout: int = 30,
) -> str:
    merged_env = os.environ.copy()
    merged_env["PYTHONPATH"] = str(ROOT)
    if env:
        merged_env.update(env)
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        env=merged_env,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise SystemExit(
            "Command failed: "
            + " ".join(cmd)
            + "\n"
            + result.stdout[-4000:]
        )
    print("ok run:", " ".join(cmd))
    return result.stdout


def run_pytest() -> None:
    run([sys.executable, "-m", "pytest", "-q"], timeout=120)


def run_offline_demos() -> None:
    with tempfile.TemporaryDirectory(prefix="learn-workbuddy-") as tmp:
        env = {
            "MINI_WORKBUDDY_HOME": str(Path(tmp) / "mini"),
            "WORKBUDDY_DEMO_SLEEP_SCALE": "0",
        }
        run([sys.executable, "s03_deferred_loading/code.py"], timeout=30)
        run([sys.executable, "s08_model_routing/code.py"], timeout=30)
        run([sys.executable, "s09_jsonl_transcript/code.py"], timeout=30)
        run([sys.executable, "s13_output_externalization/code.py"], timeout=30)
        demo_output = run_capture(
            [sys.executable, "examples/mini_workbuddy_demo/code.py", "--mode", "offline"],
            env=env,
            timeout=30,
        )
        required = [
            "tool directory:",
            "permission denial:",
            "Externalized:",
            "Transcript events:",
            "Audit verified: True",
        ]
        missing = [item for item in required if item not in demo_output]
        if missing:
            raise SystemExit("Mini demo output missing markers: " + ", ".join(missing))


def run_interactive_smokes() -> None:
    scripts = [
        "s03_deferred_loading/code.py",
        "s08_model_routing/code.py",
        "s09_jsonl_transcript/code.py",
        "s13_output_externalization/code.py",
    ]
    for script in scripts:
        run_capture([sys.executable, script, "--interactive"], input_text="q\n", timeout=10)


def check_project_shape() -> None:
    chapters = sorted(path for path in ROOT.glob("s[0-9][0-9]_*") if path.is_dir())
    if len(chapters) != 24:
        raise SystemExit(f"expected 24 root chapters, found {len(chapters)}")
    missing: list[str] = []
    bad_readmes: list[str] = []
    for chapter in chapters:
        for name in ["README.md", "code.py"]:
            if not (chapter / name).exists():
                missing.append(str((chapter / name).relative_to(ROOT)))
        readme = chapter / "README.md"
        if readme.exists():
            text = readme.read_text(encoding="utf-8")
            expected = chapter.name[:3]
            first_line = text.splitlines()[0] if text.splitlines() else ""
            if not first_line.startswith(f"# {expected}:"):
                bad_readmes.append(
                    f"{readme.relative_to(ROOT)} title should start with '# {expected}:'"
                )
            if "## 代码架构图" not in text or "```mermaid" not in text:
                bad_readmes.append(
                    f"{readme.relative_to(ROOT)} missing code architecture diagram"
                )
    images = sorted(
        path for path in ROOT.rglob("*")
        if path.suffix.lower() in {".svg", ".png", ".jpg", ".jpeg", ".gif"}
        and not any(part in SKIP_PARTS for part in path.parts)
    )
    markdown = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in ROOT.rglob("*.md"))
    missing_images = [path.relative_to(ROOT).as_posix() for path in images if path.name not in markdown]
    readme_diagram_missing: list[str] = []
    for readme in sorted(ROOT.rglob("README.md")):
        if any(part in SKIP_PARTS for part in readme.parts):
            continue
        text = readme.read_text(encoding="utf-8")
        if "## 代码架构图" not in text or "```mermaid" not in text:
            readme_diagram_missing.append(readme.relative_to(ROOT).as_posix())
    if missing or missing_images or bad_readmes or readme_diagram_missing or len(images) != 27:
        raise SystemExit(
            "Project shape failed:\n"
            + "\n".join(missing)
            + ("\n" if missing and (missing_images or bad_readmes or readme_diagram_missing) else "")
            + "\n".join(bad_readmes)
            + ("\n" if bad_readmes and (missing_images or readme_diagram_missing) else "")
            + "\n".join(
                f"README missing code architecture diagram: {item}"
                for item in readme_diagram_missing
            )
            + ("\n" if readme_diagram_missing and missing_images else "")
            + "\n".join(f"unreferenced image: {item}" for item in missing_images)
            + f"\nimage count: {len(images)}"
        )
    print("ok project shape")


def check_lesson_interactivity() -> None:
    missing: list[str] = []
    for path in sorted(ROOT.glob("s[0-9][0-9]_*/code.py")):
        text = path.read_text(encoding="utf-8")
        if "input(" not in text:
            missing.append(path.relative_to(ROOT).as_posix())
    if missing:
        raise SystemExit("Lesson scripts without interactive entrypoints:\n" + "\n".join(missing))
    print("ok lesson interactivity")


def check_svg_integrity() -> None:
    svgs = sorted(ROOT.rglob("*.svg"))
    failures: list[str] = []
    for path in svgs:
        try:
            ET.parse(path)
        except ET.ParseError as exc:
            failures.append(f"{path.relative_to(ROOT)}: {exc}")
    if failures:
        raise SystemExit("SVG XML parse failed:\n" + "\n".join(failures))

    converter = shutil.which("rsvg-convert")
    if converter:
        with tempfile.TemporaryDirectory(prefix="learn-workbuddy-svg-") as tmp:
            for path in svgs:
                out = Path(tmp) / (path.stem + ".png")
                result = subprocess.run(
                    [converter, "-w", "800", "-h", "500", "-o", str(out), str(path)],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=20,
                )
                if result.returncode != 0:
                    failures.append(
                        f"{path.relative_to(ROOT)} render failed:\n{result.stdout[-1000:]}"
                    )
        if failures:
            raise SystemExit("SVG rasterization smoke failed:\n" + "\n".join(failures[:20]))
        print(f"ok svg format+rasterization smoke: {len(svgs)} files")
    else:
        print(f"ok svg XML format: {len(svgs)} files (rsvg-convert not installed, rasterization skipped)")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(url: str, payload: dict | None = None) -> dict:
    headers = {"X-Mini-WorkBuddy-Request": "1"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    with NO_PROXY_OPENER.open(req, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def run_http_smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="mini-workbuddy-server-") as tmp:
        port = free_port()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        env["MINI_WORKBUDDY_HOME"] = str(Path(tmp) / "mini")
        proc = subprocess.Popen(
            [sys.executable, "-m", "mini_workbuddy.server", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            base = f"http://127.0.0.1:{port}"
            deadline = time.time() + 8
            while True:
                try:
                    request_json(base + "/api/v1/health")
                    break
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    if time.time() > deadline:
                        proc.terminate()
                        try:
                            output, _ = proc.communicate(timeout=3)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            output, _ = proc.communicate(timeout=3)
                        raise SystemExit("server did not start\n" + output)
                    time.sleep(0.1)

            result = request_json(
                base + "/api/v1/runs",
                {"cwd": ".", "prompt": "list files"},
            )
            answer = result.get("data", {}).get("answer", "")
            if "README.md" not in answer:
                raise SystemExit("unexpected server answer: " + json.dumps(result)[:1000])
            print("ok smoke: mini_workbuddy HTTP run")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def check_clean_room_scan() -> None:
    hits: list[str] = []
    for path in iter_files(TEXT_SUFFIXES):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in FORBIDDEN_PATTERNS:
            if pattern in text:
                hits.append(f"{path.relative_to(ROOT)} contains {pattern!r}")
        for regex in FORBIDDEN_REGEXES:
            if regex.search(text):
                hits.append(f"{path.relative_to(ROOT)} matches {regex.pattern!r}")
    if hits:
        raise SystemExit("Clean-room scan failed:\n" + "\n".join(hits[:80]))
    print("ok clean-room scan")


def check_public_docs_not_over_specified() -> None:
    hits: list[str] = []
    for path in iter_public_docs():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for regex, reason in PUBLIC_OVERSPECIFIED_REGEXES:
            match = regex.search(text)
            if match:
                hits.append(
                    f"{path.relative_to(ROOT)} has {reason}: {match.group(0)!r}"
                )
    if hits:
        raise SystemExit("Public doc specificity scan failed:\n" + "\n".join(hits[:80]))
    print("ok public doc specificity scan")


def check_images_not_over_specified() -> None:
    hits: list[str] = []
    for path in sorted(ROOT.rglob("*.svg")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for regex, reason in IMAGE_OVERSPECIFIED_REGEXES:
            match = regex.search(text)
            if match:
                hits.append(
                    f"{path.relative_to(ROOT)} has {reason}: {match.group(0)!r}"
                )
    if hits:
        raise SystemExit("Image specificity scan failed:\n" + "\n".join(hits[:80]))
    print("ok image specificity scan")


def cleanup_generated_files() -> None:
    for path in ROOT.rglob("__pycache__"):
        shutil.rmtree(path, ignore_errors=True)
    for path in ROOT.rglob("*.pyc"):
        path.unlink(missing_ok=True)


def main() -> None:
    check_python_syntax()
    run_pytest()
    check_project_shape()
    check_lesson_interactivity()
    check_svg_integrity()
    run_offline_demos()
    run_interactive_smokes()
    run_http_smoke()
    check_clean_room_scan()
    check_public_docs_not_over_specified()
    check_images_not_over_specified()
    cleanup_generated_files()
    print("all checks passed")


if __name__ == "__main__":
    main()
