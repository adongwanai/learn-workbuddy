#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SENSITIVE_ENV_KEYS = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_CHAT_API_KEY",
}


@dataclass(frozen=True)
class LessonTarget:
    chapter: str
    script: Path


@dataclass(frozen=True)
class Case:
    case_id: str
    provider: str
    kind: str
    command: list[str]
    stdin: str | None = None
    timeout: int = 180
    env: dict[str, str] | None = None


@dataclass
class CaseResult:
    case_id: str
    provider: str
    kind: str
    result: str
    compliance: int
    execution_quality: int
    overall: int
    duration_seconds: float
    exit_code: int
    stdout_path: str
    evidence: list[str]
    improvements: list[str]
    command: list[str]


def discover_model_lessons() -> list[LessonTarget]:
    lessons: list[LessonTarget] = []
    for script in sorted(ROOT.glob("s[0-9][0-9]_*/code.py")):
        text = script.read_text(encoding="utf-8")
        if "from anthropic import Anthropic" in text and "client.messages.create" in text:
            lessons.append(LessonTarget(chapter=script.parent.name, script=script))
    return lessons


def provider_ready(provider: str) -> bool:
    if provider == "deepseek":
        return bool(os.getenv("DEEPSEEK_API_KEY"))
    if provider == "openai-chat":
        return bool(os.getenv("OPENAI_CHAT_API_KEY") or os.getenv("OPENAI_API_KEY"))
    if provider == "anthropic":
        return bool(os.getenv("ANTHROPIC_API_KEY") and os.getenv("MODEL_ID"))
    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    return False


def redact_env(env: dict[str, str]) -> dict[str, str]:
    return {k: ("<redacted>" if k in SENSITIVE_ENV_KEYS and v else v) for k, v in env.items()}


def base_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    return env


def build_cases(providers: list[str], max_lessons: int | None = None) -> list[Case]:
    lessons = discover_model_lessons()
    if max_lessons is not None:
        lessons = lessons[:max_lessons]

    cases: list[Case] = []
    for provider in providers:
        cases.append(
            Case(
                case_id=f"{provider}::mini",
                provider=provider,
                kind="mini",
                command=[
                    sys.executable,
                    "examples/mini_workbuddy_demo/code.py",
                    "--mode",
                    "real",
                    "--provider",
                    provider,
                    "--max-turns",
                    "6",
                ],
                env={"MINI_WORKBUDDY_HOME": tempfile.mkdtemp(prefix=f"model-bench-{provider}-mini-")},
            )
        )
        cases.append(
            Case(
                case_id=f"{provider}::full",
                provider=provider,
                kind="full",
                command=[
                    sys.executable,
                    "examples/full_tour/code.py",
                    "--provider",
                    provider,
                    "--home",
                    tempfile.mkdtemp(prefix=f"model-bench-{provider}-full-"),
                ],
            )
        )

        if provider in {"deepseek", "anthropic"}:
            for lesson in lessons:
                stdin = "List the files in the current directory, then say DONE.\nq\n"
                if lesson.chapter == "s22_automation_scheduler":
                    stdin = "/list\nq\n"
                cases.append(
                    Case(
                        case_id=f"{provider}::lesson::{lesson.chapter}",
                        provider=provider,
                        kind="lesson",
                        command=[
                            sys.executable,
                            lesson.script.relative_to(ROOT).as_posix(),
                            "--provider",
                            provider,
                        ],
                        stdin=stdin,
                        timeout=240,
                        env={"WORKBUDDY_HOME": tempfile.mkdtemp(prefix=f"model-bench-{provider}-{lesson.chapter}-")},
                    )
                )
    return cases


def score_output(case: Case, exit_code: int, stdout: str, duration: float, stdout_path: Path) -> CaseResult:
    evidence: list[str] = []
    improvements: list[str] = []
    output_tail = stdout[-4000:]

    if exit_code == 0:
        result = "pass"
        compliance = 5
        execution_quality = 5
        overall = 5
        evidence.append(f"exit_code=0 for {case.case_id}")
    else:
        result = "fail"
        compliance = 1
        execution_quality = 1
        overall = 1
        evidence.append(f"exit_code={exit_code} for {case.case_id}")
        improvements.append("[workflow] inspect stdout artifact and add a regression test for the failing path")

    if case.kind == "mini":
        required = ["Audit verified: True", "Transcript events:"]
        missing = [item for item in required if item not in stdout]
        if missing:
            result = "fail"
            compliance = min(compliance, 2)
            execution_quality = min(execution_quality, 2)
            overall = min(overall, 2)
            improvements.append("[eval] mini case missing required harness markers: " + ", ".join(missing))
        else:
            evidence.append("mini markers present: Audit verified + Transcript events")

    if case.kind == "full":
        if "RESULT: OK" not in stdout or "chain verifies-> True" not in stdout:
            result = "fail"
            compliance = min(compliance, 2)
            execution_quality = min(execution_quality, 2)
            overall = min(overall, 2)
            improvements.append("[workflow] full tour did not complete all stages or audit verification")
        else:
            evidence.append("full tour markers present: RESULT OK + audit chain verifies")

    if case.kind == "lesson":
        lesson_ok = "DONE" in stdout.upper()
        if case.case_id.endswith("s22_automation_scheduler"):
            lesson_ok = "暂无自动化任务" in stdout or "ID" in stdout
        if not lesson_ok:
            result = "fail"
            compliance = min(compliance, 3)
            execution_quality = min(execution_quality, 3)
            overall = min(overall, 3)
            improvements.append("[capability] lesson did not reach the scripted DONE marker")
        else:
            evidence.append("lesson reached DONE marker")

    if "Traceback" in output_tail:
        result = "fail"
        compliance = min(compliance, 1)
        execution_quality = min(execution_quality, 1)
        overall = min(overall, 1)
        improvements.append("[workflow] Python traceback found in model benchmark output")

    return CaseResult(
        case_id=case.case_id,
        provider=case.provider,
        kind=case.kind,
        result=result,
        compliance=compliance,
        execution_quality=execution_quality,
        overall=overall,
        duration_seconds=round(duration, 3),
        exit_code=exit_code,
        stdout_path=str(stdout_path),
        evidence=evidence,
        improvements=improvements,
        command=case.command,
    )


def run_case(case: Case, run_dir: Path, dry_run: bool) -> CaseResult:
    stdout_dir = run_dir / "stdout"
    stdout_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = stdout_dir / (case.case_id.replace("::", "__").replace("/", "_") + ".txt")

    if dry_run:
        stdout = f"DRY RUN: {' '.join(case.command)}\n"
        stdout_path.write_text(stdout, encoding="utf-8")
        return CaseResult(
            case_id=case.case_id,
            provider=case.provider,
            kind=case.kind,
            result="dry-run",
            compliance=0,
            execution_quality=0,
            overall=0,
            duration_seconds=0.0,
            exit_code=0,
            stdout_path=str(stdout_path),
            evidence=["dry-run matrix generation only"],
            improvements=[],
            command=case.command,
        )

    env = base_env()
    if case.env:
        env.update(case.env)
    start = time.monotonic()
    try:
        proc = subprocess.run(
            case.command,
            cwd=ROOT,
            env=env,
            input=case.stdin,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=case.timeout,
        )
        duration = time.monotonic() - start
        stdout = proc.stdout
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stdout += f"\nTIMEOUT after {case.timeout}s\n"
        exit_code = 124

    stdout_path.write_text(stdout, encoding="utf-8", errors="replace")
    return score_output(case, exit_code, stdout, duration, stdout_path)


def write_reports(run_dir: Path, cases: list[Case], results: list[CaseResult], dry_run: bool) -> None:
    passed = sum(1 for r in results if r.result == "pass")
    failed = sum(1 for r in results if r.result == "fail")
    dry = sum(1 for r in results if r.result == "dry-run")
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "dry_run_cases": dry,
        "providers": sorted({case.provider for case in cases}),
    }
    payload = {
        "summary": summary,
        "cases": [asdict(result) for result in results],
    }
    stats_path = run_dir / "latest-stats.json"
    stats_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Model Benchmark Results",
        "",
        f"- generated_at: {summary['generated_at']}",
        f"- dry_run: {summary['dry_run']}",
        f"- total: {summary['total']}",
        f"- passed: {summary['passed']}",
        f"- failed: {summary['failed']}",
        "",
        "| case | result | compliance | execution | overall | seconds | stdout |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for result in results:
        stdout_rel = Path(result.stdout_path).relative_to(run_dir)
        lines.append(
            f"| {result.case_id} | {result.result} | {result.compliance} | "
            f"{result.execution_quality} | {result.overall} | {result.duration_seconds:.3f} | "
            f"`{stdout_rel.as_posix()}` |"
        )
    (run_dir / "latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    review_lines = ["# Model Benchmark Review", ""]
    for result in results:
        if result.result != "fail":
            continue
        review_lines.extend([
            f"## {result.case_id}",
            "",
            "Evidence:",
            *[f"- {item}" for item in result.evidence],
            "",
            "Improvements:",
            *[f"- {item}" for item in (result.improvements or ["[eval] inspect stdout artifact"])],
            "",
        ])
    if len(review_lines) == 2:
        review_lines.append("No failing cases.")
    (run_dir / "review.md").write_text("\n".join(review_lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run real-model benchmark cases for learn-workbuddy.")
    parser.add_argument(
        "--providers",
        nargs="+",
        choices=["deepseek", "openai-chat", "anthropic", "openai"],
        default=["deepseek", "openai-chat"],
    )
    parser.add_argument("--output", default=None, help="output directory; defaults to benchmark-runs/<timestamp>")
    parser.add_argument("--max-lessons", type=int, default=None, help="limit lesson cases for quick smoke runs")
    parser.add_argument("--dry-run", action="store_true", help="write the benchmark matrix without calling models")
    parser.add_argument("--keep-going", action="store_true", help="continue after failures (default true for reports)")
    return parser.parse_args()


def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=True)
    except Exception:
        pass

    args = parse_args()
    run_dir = Path(args.output).expanduser() if args.output else (
        ROOT / "benchmark-runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    missing = [provider for provider in args.providers if not provider_ready(provider)]
    if missing and not args.dry_run:
        raise SystemExit(
            "Missing provider keys for: "
            + ", ".join(missing)
            + ". Fill .env or pass env vars, or use --dry-run."
        )

    cases = build_cases(args.providers, max_lessons=args.max_lessons)
    print(f"model benchmark: {len(cases)} cases -> {run_dir}")
    results: list[CaseResult] = []
    for idx, case in enumerate(cases, start=1):
        print(f"[{idx}/{len(cases)}] {case.case_id}")
        result = run_case(case, run_dir, args.dry_run)
        print(f"  {result.result} exit={result.exit_code} seconds={result.duration_seconds:.3f}")
        results.append(result)

    write_reports(run_dir, cases, results, args.dry_run)
    print("reports:")
    print(" ", run_dir / "latest.md")
    print(" ", run_dir / "latest-stats.json")
    print(" ", run_dir / "review.md")

    if any(result.result == "fail" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
