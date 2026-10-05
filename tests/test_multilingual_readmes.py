from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULES = [ROOT, *sorted(ROOT.glob("s[0-9][0-9]_*/"))]
CHINESE_NAVIGATION = "[中文](README.md) · [English](README.en.md)"
ENGLISH_NAVIGATION = "[Chinese](README.md) · [English](README.en.md)"
README_NAMES = ("README.md", "README.en.md")
SVG_LINK = re.compile(r"\]\(([^)]+\.svg)\)")
SVG_TEXT = re.compile(r"<(?:text|tspan)\b[^>]*>([^<>]*)</(?:text|tspan)>")
NON_ENGLISH_TEXT = re.compile(r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]")


def module_readmes() -> list[tuple[Path, Path, Path]]:
    return [
        (module, module / "README.md", module / "README.en.md")
        for module in MODULES
    ]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def structural_signature(text: str) -> tuple[tuple[int, ...], int, int, int, int]:
    headings: list[int] = []
    in_fence = False
    mermaid_blocks = 0
    for line in text.splitlines():
        if line.startswith("```"):
            if line == "```mermaid":
                mermaid_blocks += 1
            in_fence = not in_fence
            continue
        if not in_fence:
            match = re.match(r"^(#{1,6})\s+", line)
            if match:
                headings.append(len(match.group(1)))
    return (
        tuple(headings),
        text.count("```") // 2,
        mermaid_blocks,
        len(re.findall(r"!\[[^\]]*\]\([^)]*\)", text)),
        len(re.findall(r"\]\(\.\./s\d{2}_[^)]*", text)),
    )


def local_file_links(path: Path) -> list[Path]:
    links: list[Path] = []
    in_fence = False
    for line in read_text(path).splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", line):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target_path = target.split("#", 1)[0]
            if not target_path:
                continue
            links.append((path.parent / target_path).resolve())
    return links


def test_repository_has_one_homepage_and_24_course_modules() -> None:
    assert len(MODULES) == 25
    assert MODULES[0] == ROOT
    assert MODULES[1].name == "s01_agent_loop"
    assert MODULES[-1].name == "s24_comprehensive"


def test_every_module_has_chinese_and_english_readmes() -> None:
    for module, chinese, english in module_readmes():
        assert chinese.is_file(), module
        assert english.is_file(), module


def test_every_readme_has_the_same_bilingual_navigation() -> None:
    for module, chinese, english in module_readmes():
        chinese_lines = read_text(chinese).splitlines()[:8]
        english_lines = read_text(english).splitlines()[:8]
        assert CHINESE_NAVIGATION in chinese_lines, f"{module.name}: {chinese.name}"
        assert ENGLISH_NAVIGATION in english_lines, f"{module.name}: {english.name}"


def test_local_markdown_links_exist_in_both_languages() -> None:
    broken: list[str] = []
    for _, chinese, english in module_readmes():
        for readme in (chinese, english):
            for target in local_file_links(readme):
                if not target.exists():
                    broken.append(f"{readme.relative_to(ROOT)} -> {target}")
    assert broken == []


def test_english_readmes_preserve_module_structure() -> None:
    mismatches: list[str] = []
    for module, chinese, english in module_readmes():
        if structural_signature(read_text(chinese)) != structural_signature(
            read_text(english)
        ):
            mismatches.append(module.relative_to(ROOT).as_posix() or ".")
    assert mismatches == []


def test_english_readmes_use_the_expected_filename() -> None:
    unexpected = [
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("README.*.md")
        if path.name not in README_NAMES
    ]
    assert unexpected == []


def test_english_readmes_do_not_contain_generation_placeholders() -> None:
    templates = (
        "This section explains ",
        "This lesson focuses on ",
        "Common mistakes include ",
        "The problem is how to provide ",
        "The teaching solution makes ",
        "is part of the same executable boundary",
    )
    for _, _, english in module_readmes():
        text = read_text(english)
        for template in templates:
            assert template not in text, f"{english}: {template!r}"


def test_english_readmes_use_english_svg_variants() -> None:
    for module, _, english in module_readmes():
        text = read_text(english)
        for target in SVG_LINK.findall(text):
            assert target.endswith("-en.svg"), f"{module.name}: {target}"
            assert (english.parent / target).is_file(), f"{module.name}: {target}"


def test_english_readmes_preserve_markdown_tables() -> None:
    for module, chinese, english in module_readmes():
        chinese_tables = sum(
            line.lstrip().startswith("|") for line in read_text(chinese).splitlines()
        )
        english_tables = sum(
            line.lstrip().startswith("|") for line in read_text(english).splitlines()
        )
        assert english_tables == chinese_tables, module


def test_english_readmes_contain_no_chinese_outside_language_navigation() -> None:
    violations: list[str] = []
    for _, _, english in module_readmes():
        for number, line in enumerate(read_text(english).splitlines(), 1):
            if line.strip() in {CHINESE_NAVIGATION, ENGLISH_NAVIGATION}:
                continue
            if NON_ENGLISH_TEXT.search(line):
                violations.append(f"{english.relative_to(ROOT)}:{number}")
    assert violations == []


def test_english_svg_visible_text_contains_no_chinese() -> None:
    for _, _, english in module_readmes():
        for target in SVG_LINK.findall(read_text(english)):
            svg = (english.parent / target).read_text(encoding="utf-8")
            visible_text = "\n".join(SVG_TEXT.findall(svg))
            assert not re.search(r"[\u3400-\u9fff]", visible_text), target
