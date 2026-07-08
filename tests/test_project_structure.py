from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import ast
from pathlib import Path


TEXT_SUFFIXES = {".md", ".py", ".txt", ".yml", ".yaml", ".json", ".example", ".svg"}
SKIP_PARTS = {".git", ".venv", "venv", "__pycache__", ".tmp", "benchmark-runs"}

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


def iter_text_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_dir() or any(part in SKIP_PARTS for part in path.parts):
            continue
        if path.suffix in TEXT_SUFFIXES:
            files.append(path)
    return sorted(files)


def iter_public_docs(root: Path) -> list[Path]:
    docs: set[Path] = {
        root / "README.md",
        root / "NOTICE.md",
        root / "CONTRIBUTING.md",
        root / ".github" / "PULL_REQUEST_TEMPLATE.md",
    }
    docs.update(root.glob("s[0-9][0-9]_*/README.md"))
    docs.update(root.glob("examples/**/*.md"))
    docs.update(root.glob("docs/legal/**/*.md"))
    return sorted(path for path in docs if path.exists())


def test_root_contains_exactly_24_main_chapters(root: Path) -> None:
    chapters = sorted(path.name for path in root.glob("s[0-9][0-9]_*") if path.is_dir())
    assert len(chapters) == 24
    assert chapters[0] == "s01_agent_loop"
    assert chapters[-1] == "s24_comprehensive"
    for chapter in chapters:
        assert (root / chapter / "README.md").exists(), chapter
        assert (root / chapter / "code.py").exists(), chapter


def test_chapter_readmes_include_code_architecture_diagrams(root: Path) -> None:
    readmes = sorted(root.glob("s[0-9][0-9]_*/README.md"))
    assert len(readmes) == 24
    missing: list[str] = []
    for readme in readmes:
        text = readme.read_text(encoding="utf-8")
        if "## 代码架构图" not in text or "```mermaid" not in text:
            missing.append(readme.relative_to(root).as_posix())
    assert missing == []


def test_every_readme_includes_a_code_architecture_diagram(root: Path) -> None:
    readmes = sorted(
        path for path in root.rglob("README.md")
        if not any(part in SKIP_PARTS for part in path.parts)
        and ".pytest_cache" not in path.parts
    )
    missing: list[str] = []
    for readme in readmes:
        text = readme.read_text(encoding="utf-8")
        if "## 代码架构图" not in text or "```mermaid" not in text:
            missing.append(readme.relative_to(root).as_posix())
    assert missing == []


def test_chapter_titles_match_directory_numbers(root: Path) -> None:
    mismatches: list[str] = []
    for readme in sorted(root.glob("s[0-9][0-9]_*/README.md")):
        expected = readme.parent.name[:3]
        first_line = readme.read_text(encoding="utf-8").splitlines()[0]
        if not first_line.startswith(f"# {expected}:"):
            mismatches.append(f"{readme.relative_to(root)} starts with {first_line!r}")
    assert mismatches == []


def test_chapter_code_declares_progressive_learning_contract(root: Path) -> None:
    chapters = sorted(path for path in root.glob("s[0-9][0-9]_*") if path.is_dir())
    previous: str | None = None
    failures: list[str] = []
    for chapter in chapters:
        code = chapter / "code.py"
        module = ast.parse(code.read_text(encoding="utf-8"), filename=str(code))
        progression = None
        for node in module.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "PROGRESSION":
                        progression = ast.literal_eval(node.value)
        if progression is None:
            failures.append(f"{code.relative_to(root)} missing PROGRESSION")
            previous = chapter.name
            continue
        if progression.get("chapter") != chapter.name:
            failures.append(f"{code.relative_to(root)} has mismatched chapter")
        builds_on = progression.get("builds_on")
        if chapter.name.startswith("s01_"):
            if builds_on != []:
                failures.append(f"{code.relative_to(root)} should start with no builds_on")
        elif builds_on != [previous]:
            failures.append(
                f"{code.relative_to(root)} should build on {previous}, got {builds_on!r}"
            )
        for key in ["adds", "preserves"]:
            values = progression.get(key)
            if not isinstance(values, list) or not values:
                failures.append(f"{code.relative_to(root)} has empty {key}")
        previous = chapter.name
    assert failures == []


def test_chapter_readmes_explain_delta_from_previous_chapter(root: Path) -> None:
    missing: list[str] = []
    for readme in sorted(root.glob("s[0-9][0-9]_*/README.md")):
        text = readme.read_text(encoding="utf-8")
        if readme.parent.name.startswith("s01_"):
            continue
        if not re.search(r"(相对|上一章|前一章|s\d{2})", text):
            missing.append(readme.relative_to(root).as_posix())
    assert missing == []


def test_all_images_are_referenced_from_markdown(root: Path) -> None:
    images = sorted(
        path for path in root.rglob("*")
        if path.suffix.lower() in {".svg", ".png", ".jpg", ".jpeg", ".gif"}
        and not any(part in SKIP_PARTS for part in path.parts)
    )
    markdown = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in root.rglob("*.md"))
    missing = [path.relative_to(root).as_posix() for path in images if path.name not in markdown]
    assert len(images) == 27
    assert missing == []


def test_main_chapter_images_exist_and_render_links_are_relative(root: Path) -> None:
    for readme in sorted(root.glob("s[0-9][0-9]_*/README.md")):
        text = readme.read_text(encoding="utf-8")
        refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
        assert refs, f"{readme.relative_to(root)} should include at least one diagram"
        for ref in refs:
            if ref.startswith(("http://", "https://")):
                continue
            assert (readme.parent / ref).exists(), f"broken image ref {ref} in {readme}"


def test_svg_files_are_well_formed_and_clean(root: Path) -> None:
    hits: list[str] = []
    for path in sorted(root.rglob("*.svg")):
        ET.parse(path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        for regex, reason in IMAGE_OVERSPECIFIED_REGEXES:
            match = regex.search(text)
            if match:
                hits.append(f"{path.relative_to(root)} has {reason}: {match.group(0)!r}")
    assert hits == []


def test_required_public_project_files_exist(root: Path) -> None:
    required = [
        "README.md",
        "NOTICE.md",
        "LICENSE",
        "CONTRIBUTING.md",
        ".env.example",
        ".github/workflows/ci.yml",
        ".github/PULL_REQUEST_TEMPLATE.md",
        "docs/legal/clean-room.md",
        "scripts/verify.py",
        "examples/mini_workbuddy_demo/code.py",
    ]
    missing = [path for path in required if not (root / path).exists()]
    assert missing == []


def test_clean_room_statement_is_prominent(root: Path) -> None:
    statement = "本教程基于 WorkBuddy 的架构设计与公开文档编写。代码为 Python 教学实现，非源码提取。"
    assert statement in (root / "README.md").read_text(encoding="utf-8")
    assert statement in (root / "NOTICE.md").read_text(encoding="utf-8")


def test_clean_room_scan_has_no_high_risk_strings(root: Path) -> None:
    forbidden = [
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
    digest = re.compile(r"\b[0-9a-f]{64}\b", re.IGNORECASE)
    hits: list[str] = []
    for path in iter_text_files(root):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in forbidden:
            if pattern in text:
                hits.append(f"{path.relative_to(root)} contains {pattern!r}")
        if digest.search(text):
            hits.append(f"{path.relative_to(root)} contains sha256-like digest")
    assert hits == []


def test_public_tutorial_docs_avoid_over_specific_private_claims(root: Path) -> None:
    hits: list[str] = []
    for path in iter_public_docs(root):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for regex, reason in PUBLIC_OVERSPECIFIED_REGEXES:
            match = regex.search(text)
            if match:
                hits.append(f"{path.relative_to(root)} has {reason}: {match.group(0)!r}")
    assert hits == []
