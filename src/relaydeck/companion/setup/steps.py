"""What a setup step is: it reports its state, shows its plan, then applies it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from relaydeck.companion.setup.context import SetupContext

DONE = "done"
TODO = "todo"
BLOCKED = "blocked"
CONFLICT = "conflict"
SKIPPED = "skipped"


@dataclass(frozen=True)
class Status:
    """`reason` is one fixed line; it never carries file contents or secret values."""

    state: str
    reason: str = ""
    lines: tuple[str, ...] = ()  # printed instead of `reason` when a step has more to show


@dataclass(frozen=True)
class Plan:
    """The lines shown before the question, and the question itself (None when nothing is asked)."""

    lines: tuple[str, ...] = ()
    question: str | None = None


@dataclass(frozen=True)
class Outcome:
    lines: tuple[str, ...] = ()
    changed: bool = False
    exit_code: int = 0
    skipped: bool = False
    uncertain: bool = False  # a write may have committed: nothing that depends on it may run


@dataclass(frozen=True)
class SetupReport:
    lines: tuple[str, ...]
    exit_code: int


class Step:
    """A core step stops the run when it is blocked, conflicted or fails; an optional one is
    reported and the run goes on."""

    name = ""
    core = False

    def check(self, ctx: SetupContext) -> Status:
        return Status(TODO)

    def plan(self, ctx: SetupContext) -> Plan:
        return Plan()

    def apply(self, ctx: SetupContext) -> Outcome:
        raise NotImplementedError

    def declined(self, ctx: SetupContext) -> Outcome:
        return Outcome((f"{self.name}: skipped (not authorized).",), skipped=True)
