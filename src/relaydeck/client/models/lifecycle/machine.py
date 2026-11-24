"""Apply lifecycle transitions while distinguishing process-retaining configuration changes from
pause and death.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AppPhase(Enum):
    FOREGROUND = "foreground"
    BACKGROUND = "background"
    PAUSED = "paused"
    DEAD = "dead"


class LifecycleError(Exception):
    """An illegal lifecycle transition was attempted."""

    def __init__(self, phase: AppPhase, action: str) -> None:
        super().__init__(f"cannot {action} from {phase.value}")
        self.phase = phase
        self.action = action


CONFIG_ROTATE = "rotate"
CONFIG_KEYBOARD = "keyboard-open"


@dataclass(frozen=True)
class ConfigChange:
    """A configuration change that keeps the process. `kind` is `rotate` or `keyboard-open`."""

    kind: str
    phase: AppPhase


class LifecycleMachine:
    """Enforced app-phase transitions with a transition log for tests."""

    def __init__(self) -> None:
        self._phase = AppPhase.FOREGROUND
        self.transitions: list[tuple[str, str, str]] = []

    @property
    def phase(self) -> AppPhase:
        return self._phase

    def _move(self, target: AppPhase, action: str) -> AppPhase:
        self.transitions.append((self._phase.value, target.value, action))
        self._phase = target
        return self._phase

    def on_background(self) -> AppPhase:
        """App backgrounded: FOREGROUND -> BACKGROUND.

        The caller releases UI subscriptions (SSE, terminal sockets) as
        part of handling this event.
        """
        if self._phase is not AppPhase.FOREGROUND:
            raise LifecycleError(self._phase, "background")
        return self._move(AppPhase.BACKGROUND, "background")

    def apply_pause_policy(self) -> AppPhase:
        """Pause policy applied: BACKGROUND -> PAUSED.

        The caller invalidates the writer immediately, persists
        drafts per the storage policy, and attempts the lease release.
        """
        if self._phase is not AppPhase.BACKGROUND:
            raise LifecycleError(self._phase, "apply pause policy")
        return self._move(AppPhase.PAUSED, "pause_policy")

    def on_resume_complete(self) -> AppPhase:
        """Ordered resume flow finished: BACKGROUND/PAUSED -> FOREGROUND. Called only after the
        resume flow grants control (current state fetched first).
        """
        if self._phase not in (AppPhase.BACKGROUND, AppPhase.PAUSED):
            raise LifecycleError(self._phase, "resume")
        return self._move(AppPhase.FOREGROUND, "resume")

    def on_process_death(self) -> AppPhase:
        """Process death: any live phase -> DEAD.

        All in-memory state (writers, outboxes, queues, cursors,
        snapshots) is lost with the process.
        """
        if self._phase is AppPhase.DEAD:
            raise LifecycleError(self._phase, "die")
        return self._move(AppPhase.DEAD, "process_death")

    def on_cold_start_complete(self) -> AppPhase:
        """Cold-start resume flow finished: DEAD -> FOREGROUND.

        Same ordered steps as resume, from route-only restore.
        """
        if self._phase is not AppPhase.DEAD:
            raise LifecycleError(self._phase, "cold start")
        return self._move(AppPhase.FOREGROUND, "cold_start")

    def config_change(self, kind: str) -> ConfigChange:
        """Record a rotation/keyboard configuration change. Not a transition: the phase is
        unchanged and the process (with every state holder) is retained.
        """
        if kind not in (CONFIG_ROTATE, CONFIG_KEYBOARD):
            raise LifecycleError(self._phase, f"config change {kind!r}")
        if self._phase is AppPhase.DEAD:
            raise LifecycleError(self._phase, f"config change {kind!r}")
        return ConfigChange(kind=kind, phase=self._phase)
