"""Frozen metadata for a contextual question. No source text enters this value."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from relaydeck.client.models.input.paste_policy import contains_only_safe_scalars
from relaydeck.contracts.prompts import MAX_PROMPT_CHARS

DEFAULT_QUESTION = "What does this change do, and why did you make it?"
PRESETS = ("Why this change?", "Any risk?", "Is it tested?")


def eligibility(status: str) -> str:
    if status in ("idle", "done", "working"):
        return ""
    if status == "blocked":
        return "The agent is waiting for an answer."
    return "No available recipient."


def path_label(path: str) -> str:
    if not path or path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", path):
        raise ValueError("Repository-relative path required.")
    if any(part in ("", ".", "..") for part in path.split("/")):
        raise ValueError("Repository-relative path required.")
    return "".join(
        f"\\u{ord(c):04x}" if unicodedata.category(c) in ("Cc", "Cs", "Zl", "Zp") else c
        for c in path
    )


def short_oid(value: str | None) -> str:
    if value is None or re.fullmatch(r"[a-fA-F0-9]{7,64}", value) is None:
        raise ValueError("Comparison is unavailable.")
    return value[:8]


@dataclass(frozen=True, slots=True, repr=False)
class AskScope:
    kind: Literal["branch", "file", "hunk", "line", "commit"]
    section: Literal["working", "branch"]
    snapshot: str
    head: str | None
    base: str | None = None
    file_id: str | None = None
    path: str | None = field(default=None, repr=False)
    start: int | None = None
    end: int | None = None
    old: bool = False
    page: int | None = None
    hunk: int | None = None
    row: int | None = None
    commit: str | None = None

    @property
    def comparison(self) -> str:
        if self.commit is not None:  # the commit, or a file, hunk or line of it
            return f"Commit: {short_oid(self.commit)}"
        if self.section == "working":
            return f"working tree vs {short_oid(self.head) if self.head else 'empty tree'}"
        return f"{short_oid(self.head)} vs {short_oid(self.base)}"

    @property
    def label(self) -> str:
        if self.kind == "commit":
            return f"This commit · {short_oid(self.commit)}"
        name = path_label(self.path) if self.path is not None else "This branch"
        numbers = ""
        if self.start is not None and self.end is not None:
            numbers = f" · {'old lines' if self.old else 'lines'} {self.start}–{self.end}"
        where = f"Commit {short_oid(self.commit)}" if self.commit else self.section.title()
        return f"{name}{numbers} · {where}"


def build_prompt(scope: AskScope, question: str) -> str:
    if not contains_only_safe_scalars(question):
        raise ValueError("The question contains unsupported characters.")
    question = " ".join(question.split())
    if not question:
        raise ValueError("Enter a question.")
    comparison = scope.comparison
    if scope.kind == "branch":
        subject = "the working changes" if scope.section == "working" else "this branch"
        context = comparison
    elif scope.kind == "commit":
        subject, context = "this commit", comparison
    else:
        if scope.path is None:
            raise ValueError("File is unavailable.")
        subject = path_label(scope.path)
        context = comparison if scope.commit else f"{scope.section.title()}: {comparison}"
        if scope.kind in ("hunk", "line"):
            if scope.start is None or scope.end is None or not 1 <= scope.start <= scope.end:
                raise ValueError("Line range is unavailable.")
            subject += f", {'old lines' if scope.old else 'lines'} {scope.start}–{scope.end}"
    result = f"About {subject} ({context}): {question}"
    if len(result) > MAX_PROMPT_CHARS:
        raise ValueError("The question and context are too long.")
    if not contains_only_safe_scalars(result) or any(c in result for c in "\r\n\t"):
        raise ValueError("The question must be one safe line.")
    return result
