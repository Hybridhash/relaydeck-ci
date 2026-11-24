"""`relaydeck setup`: a fixed list of steps. A core step that is blocked, conflicted or fails stops
the run and the rest are listed as not reached; an optional step is reported and the run goes on."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from relaydeck.companion.adapters import tailscale
from relaydeck.companion.inspection import prereqs
from relaydeck.companion.inspection.detect import DetectReport
from relaydeck.companion.setup.context import SetupContext
from relaydeck.companion.setup.kinds import AgentKindsStep
from relaydeck.companion.setup.login import LoginServiceStep, WaitStep
from relaydeck.companion.setup.pair import PairStep
from relaydeck.companion.setup.prerequisites import PrerequisitesStep
from relaydeck.companion.setup.private_step import PrivateFilesStep
from relaydeck.companion.setup.probes import load_probe_targets
from relaydeck.companion.setup.serve import ServeStep
from relaydeck.companion.setup.steps import (
    BLOCKED,
    CONFLICT,
    DONE,
    SKIPPED,
    Outcome,
    SetupReport,
    Step,
)
from relaydeck.companion.setup.updates_step import UpdatesStep
from relaydeck.companion.setup.usage_steps import HookStep, LimitsStep, SourcesStep, TrustStep
from relaydeck.companion.setup.web import WebStep
from relaydeck.usage.api import ProbeTarget, Spawn

INTERRUPTED = 130


def default_steps() -> list[Step]:
    return [
        PrerequisitesStep(),
        PrivateFilesStep(),
        AgentKindsStep(),
        UpdatesStep(),
        SourcesStep(),
        LimitsStep(),
        TrustStep(),
        HookStep(),
        ServeStep(),
        WebStep(),
        LoginServiceStep(),
        WaitStep(),
        PairStep(),
    ]


def _run_step(ctx: SetupContext, step: Step) -> tuple[Outcome, bool]:
    """(outcome, whether the run must stop here)."""
    status = step.check(ctx)
    if status.state in (DONE, SKIPPED):
        for line in status.lines or (status.reason,):
            ctx.say(line)
        return Outcome(), False
    if status.state in (BLOCKED, CONFLICT):
        for line in status.lines or (status.reason,):
            ctx.say(line)
        return Outcome(exit_code=1), step.core
    plan = step.plan(ctx)
    for line in plan.lines:
        ctx.say(line)
    if plan.question is None or ctx.confirm(plan.question):
        outcome = step.apply(ctx)
        if outcome.changed and not any(item.startswith(step.name) for item in ctx.committed):
            ctx.committed.append(step.name)
        ctx.reload()
    else:
        outcome = step.declined(ctx)
    for line in outcome.lines:
        ctx.say(line)
    stop = outcome.uncertain or (step.core and outcome.exit_code != 0)
    return outcome, stop


def run_steps(ctx: SetupContext, steps: Sequence[Step] | None = None) -> SetupReport:
    chosen = list(steps if steps is not None else default_steps())
    ctx.reload()
    exit_code = 0
    attention: list[str] = []
    current = ""
    stopped: str | None = None
    try:
        for index, step in enumerate(chosen):
            current = step.name
            outcome, stop = _run_step(ctx, step)
            if outcome.exit_code:
                exit_code = 1
                attention.append(step.name)
            if stop:
                stopped = step.name
                rest = [later.name for later in chosen[index + 1 :]]
                if rest:
                    ctx.say("Not reached: " + ", ".join(rest) + ".")
                break
    except KeyboardInterrupt:
        if ctx.committed:
            ctx.say("Already changed before the interrupt: " + ", ".join(ctx.committed) + ".")
        else:
            ctx.say("Nothing had been changed by this run before the interrupt.")
        ctx.say(
            f"Interrupted during '{current}'; part of that step may have been done. Run relaydeck "
            "setup again: it shows what is in place and does only what is missing."
        )
        return _report(ctx, INTERRUPTED)
    if not ctx.summary:
        return _report(ctx, exit_code)
    if stopped is not None:
        ctx.say(f"Setup stopped at '{stopped}'.")
    elif attention:
        ctx.say("Setup finished; these need attention: " + ", ".join(attention) + ".")
    elif ctx.configure_only:
        ctx.say("Setup finished: configured, not running.")
    else:
        ctx.say("Setup finished.")
    if ctx.config is not None and stopped is None:
        ctx.say(f"Device sign-ins are {ctx.sign_ins}.")
    for note in ctx.report.warnings if ctx.report is not None else ():
        if note in prereqs.GIT_WARNINGS.values():
            ctx.say(note + ".")
    return _report(ctx, exit_code)


def _report(ctx: SetupContext, exit_code: int) -> SetupReport:
    return SetupReport(() if ctx.emit is not None else tuple(ctx.lines), exit_code)


def run_setup(
    *,
    config_path: Path,
    assume_yes: bool,
    confirm: Callable[[str], bool],
    runner: tailscale.Runner = tailscale.default_runner,
    probes: prereqs.Probes | None = None,
    install_limits_hook: bool = False,
    source_confirm: Callable[[str], bool] | None = None,
    source_detect: Callable[[Path], DetectReport] | None = None,
    emit_line: Callable[[str], None] | None = None,
    probe_targets: Callable[[Path, Path], Sequence[ProbeTarget]] = load_probe_targets,
    trust_spawn: Spawn | None = None,
    trust_which: Callable[[str], Any] | None = None,
    steps: Sequence[Step] | None = None,
    **effects: Any,
) -> SetupReport:
    """`effects` names any other SetupContext field (home, web, launchctl, clock, sleep, ...)."""
    ctx = SetupContext(
        config_path=config_path,
        confirm=confirm,
        emit=emit_line,
        assume_yes=assume_yes,
        runner=runner,
        probes=probes,
        install_limits_hook=install_limits_hook,
        source_confirm=source_confirm,
        source_detect=source_detect,
        probe_targets=probe_targets,
        trust_spawn=trust_spawn,
        trust_which=trust_which,
        **effects,
    )
    return run_steps(ctx, steps)
