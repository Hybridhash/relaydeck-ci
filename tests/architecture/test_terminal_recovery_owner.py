"""Recovery values have one owner and each session keeps its own wake event."""

from dataclasses import fields

import relaydeck.client.features.terminal.sessions as sessions
from relaydeck.client.features.terminal import controller, gap_recovery
from relaydeck.client.features.terminal.session import recovery_state


def test_controller_and_sessions_share_the_canonical_recovery_values() -> None:
    assert controller.GapRecovery is recovery_state.GapRecovery is gap_recovery.GapRecovery
    assert controller.WATCH_RETRY_DELAYS_S is gap_recovery.WATCH_RETRY_DELAYS_S
    assert controller.GAP_STABLE_S is gap_recovery.GAP_STABLE_S
    for name in ("DRIVE_DEFERRED", "DRIVE_EXHAUSTED", "DRIVE_REACQUIRE"):
        assert getattr(sessions, name) is getattr(gap_recovery, name)
    budget = next(
        field for field in fields(controller.TerminalController) if field.name == "gap_recovery"
    )
    assert budget.default_factory is gap_recovery.GapRecovery


def test_session_recovery_allocates_distinct_canonical_budgets() -> None:
    first, second = recovery_state.RecoveryState(), recovery_state.RecoveryState()
    first._init_recovery()
    second._init_recovery()
    assert type(first.gap_recovery) is type(second.gap_recovery) is gap_recovery.GapRecovery
    assert first.gap_recovery is not second.gap_recovery
    assert first.gap_recovery.wake is not second.gap_recovery.wake
    first.gap_recovery.cancel_wait()
    assert first.gap_recovery.wake.is_set()
    assert not second.gap_recovery.wake.is_set()
