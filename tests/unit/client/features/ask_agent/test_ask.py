"""Context formatting keeps source material out of questions."""

from dataclasses import replace

import pytest

from relaydeck.client.features.ask_agent.model import AskScope, build_prompt, eligibility

BASE = AskScope("branch", "branch", "snapshot", "a" * 40, "b" * 40)


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (BASE, "About this branch (aaaaaaaa vs bbbbbbbb): Why now?"),
        (
            replace(BASE, section="working"),
            "About the working changes (working tree vs aaaaaaaa): Why now?",
        ),
        (
            replace(BASE, section="working", head=None),
            "About the working changes (working tree vs empty tree): Why now?",
        ),
        (
            replace(BASE, kind="file", path="pkg/a.py"),
            "About pkg/a.py (Branch: aaaaaaaa vs bbbbbbbb): Why now?",
        ),
        (
            replace(BASE, kind="hunk", path="pkg/a.py", start=2, end=8),
            "About pkg/a.py, lines 2–8 (Branch: aaaaaaaa vs bbbbbbbb): Why now?",
        ),
        (
            replace(BASE, kind="line", path="old.py", start=8, end=8, old=True),
            "About old.py, old lines 8–8 (Branch: aaaaaaaa vs bbbbbbbb): Why now?",
        ),
        (
            replace(BASE, kind="commit", commit="c" * 40),
            "About this commit (Commit: cccccccc): Why now?",
        ),
    ],
)
def test_formats(scope: AskScope, expected: str) -> None:
    assert build_prompt(scope, "  Why\n\t now?  ") == expected


@pytest.mark.parametrize("question", [" \t\n", "bad\x1btext", "\ud800", "x" * 16000])
def test_invalid_question(question: str) -> None:
    with pytest.raises(ValueError):
        build_prompt(BASE, question)


def test_path_spelling_and_controls() -> None:
    scope = replace(BASE, kind="file", path="pkg/two  words\n雪.py")
    assert r"pkg/two  words\u000a雪.py" in build_prompt(scope, "Why?")
    assert "pkg/two  words" in scope.label


@pytest.mark.parametrize("path", ["/tmp/a", "../a", "a/../b", "a//b", "C:\\a", ""])
def test_path_is_relative(path: str) -> None:
    with pytest.raises(ValueError):
        build_prompt(replace(BASE, kind="file", path=path), "Why?")


@pytest.mark.parametrize("status", ["idle", "done", "working", "blocked", "unknown", "exited", ""])
def test_eligibility(status: str) -> None:
    assert bool(eligibility(status)) == (status not in ("idle", "done", "working"))
