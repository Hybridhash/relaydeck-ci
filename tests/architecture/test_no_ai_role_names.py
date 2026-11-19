"""No AI agent or role names in tracked files.

Every tracked text file outside temp/ is scanned for the names as whole words and inside
identifiers, so "solid" or "console" never match. The pattern is built from
split strings so this file itself stays clean and needs no exclusion.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUFFIXES = {
    ".py",
    ".md",
    ".toml",
    ".json",
    ".sh",
    ".js",
    ".cjs",
    ".html",
    ".txt",
    ".yml",
    ".yaml",
}
SCOPES = ("src", "tests", "scripts", "docs", "plugin", "examples")
TOP_FILES = ("README.md", "pyproject.toml")
SKIP_DIRS = {"__pycache__", "node_modules", "build", "dist"}
# Split so this file itself never contains the banned words.
WORDS = ("as" + "tra", "no" + "va", "s" + "ol", "son" + "net", "op" + "us", "ch" + "ief")
# A word is matched alone and inside identifiers (snake, camel and upper case), never inside a
# longer ordinary word: "solid" and "console" stay clean.
BANNED = re.compile(
    "|".join(
        (
            r"(?<![A-Za-z])(?:" + "|".join(WORDS) + r")(?![a-z])",
            r"(?<![A-Z])(?:" + "|".join(w.capitalize() for w in WORDS) + r")(?![a-z])",
            r"(?<![A-Z])(?:" + "|".join(w.upper() for w in WORDS) + r")(?![A-Z])",
        )
    )
)


def _tracked_files() -> list[Path]:
    found: list[Path] = []
    for scope in SCOPES:
        root = ROOT / scope
        if not root.is_dir():
            continue
        found += [
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix in SUFFIXES and not (SKIP_DIRS & set(path.parts))
        ]
    for name in TOP_FILES:
        path = ROOT / name
        if path.is_file():
            found.append(path)
    return sorted(found)


def test_scan_covers_the_tracked_roots() -> None:
    files = _tracked_files()
    assert Path(__file__).resolve() in files
    assert ROOT / "README.md" in files
    assert ROOT / "pyproject.toml" in files
    assert any(path.parent.name == "relaydeck" and path.suffix == ".py" for path in files)


def _hits(path: Path, text: str) -> list[str]:
    return [
        f"{path.relative_to(ROOT)}:{number}"
        for number, line in enumerate(text.splitlines(), 1)
        if BANNED.search(line)
    ]


def test_no_ai_role_names_in_tracked_files() -> None:
    hits: list[str] = []
    for path in _tracked_files():
        hits += _hits(path, path.read_text(encoding="utf-8", errors="replace"))
    assert hits == []


def test_the_detector_matches_whole_words_and_identifiers() -> None:
    assert len(WORDS) == 6
    first, last = WORDS[0], WORDS[5]
    assert _hits(ROOT / "src" / "x.py", "the " + first + " review") != []
    assert _hits(ROOT / "src" / "x.py", "THE " + last.upper() + " RUNS") != []
    assert _hits(ROOT / "src" / "x.py", first.upper() + "_COMMENT_EXECUTED") != []
    assert _hits(ROOT / "src" / "x.py", "printf " + first.upper() + "_X") != []
    assert _hits(ROOT / "src" / "x.py", "def check_" + first + "_reply()") != []
    assert _hits(ROOT / "src" / "x.py", "class " + first.capitalize() + "Reply:") != []
    assert _hits(ROOT / "src" / "x.py", "reviewed" + last.capitalize()) != []
    assert _hits(ROOT / "src" / "x.py", "solid console opencode " + WORDS[2] + "id") == []
    assert (
        _hits(ROOT / "src" / "x.py", "SOLID CONSOLE UNSOLVED " + WORDS[2].capitalize() + "ution")
        == []
    )
