"""Offer the login service and wait for its companion and private route when accepted. Declining
configures files only and skips startup, waiting and pairing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from relaydeck.companion.adapters import launchd as service
from relaydeck.companion.config import DEFAULT_CONFIG
from relaydeck.companion.inspection import running
from relaydeck.companion.setup.context import SetupContext
from relaydeck.companion.setup.steps import DONE, SKIPPED, TODO, Outcome, Plan, Status, Step

WAIT_S = 45.0
POLL_S = 0.5
CONFIGURE_ONLY_LINES = (
    "Configured, not running. Start RelayDeck with: relaydeck start --connect-herdr",
    "Pair a device later with: relaydeck pair",
)


def _profile(ctx: SetupContext) -> Path | None:
    """The `--config` the login service must carry: only a profile other than the default one."""
    default = Path(DEFAULT_CONFIG).expanduser().absolute()
    return None if ctx.config_path.absolute() == default else ctx.config_path.absolute()


def service_state(ctx: SetupContext) -> str:
    """done | load | replace | install."""
    return service.agent_state(ctx.home, ctx.uid, ctx.executable, _profile(ctx), ctx.launchctl)


class LoginServiceStep(Step):
    name = "Start at login"

    def check(self, ctx: SetupContext) -> Status:
        if ctx.config is None:
            return Status(SKIPPED, "Start at login: no valid config yet")
        if service_state(ctx) == "done":
            return Status(DONE, "Start at login: the login service is installed and loaded.")
        return Status(TODO)

    def plan(self, ctx: SetupContext) -> Plan:
        state = service_state(ctx)
        plist = service.agent_path(ctx.home)
        runs = " ".join(
            ctx.short(part)
            for part in service.service_arguments(ctx.executable, ctx.home, _profile(ctx))
        )
        starts = " ".join(
            ctx.short(part) for part in service.program_arguments(ctx.executable, _profile(ctx))
        )
        lines = [
            f"Login service: {ctx.short(plist)} runs `{runs}` at login and keeps it "
            f"running; it starts `{starts}`, which also connects to Herdr automatically.",
            f"It is loaded with: launchctl bootstrap gui/{ctx.uid} {ctx.short(plist)}",
        ]
        if state == "replace":
            info = service.status(ctx.home)
            program = ctx.short(str(info.get("program")))
            lines.append(f"It replaces the installed agent that runs `{program}`.")
        if state == "load":
            lines = [
                "Login service: installed but not loaded; setup loads it.",
                lines[1],
            ]
        return Plan(
            tuple(lines), "Start RelayDeck at login and run it now? [y/N] (No = configure only) "
        )

    def declined(self, ctx: SetupContext) -> Outcome:
        ctx.configure_only = True
        return Outcome(CONFIGURE_ONLY_LINES, skipped=True)

    def apply(self, ctx: SetupContext) -> Outcome:
        state = service_state(ctx)
        if state == "replace":
            try:
                ctx.launchctl(
                    ["launchctl", "bootout", f"gui/{ctx.uid}", str(service.agent_path(ctx.home))]
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        outcome = service.install(
            executable=ctx.executable,
            home=ctx.home,
            uid=ctx.uid,
            yes=True,
            load=True,
            runner=ctx.launchctl,
            config=_profile(ctx),
        )
        plist = ctx.short(service.agent_path(ctx.home))
        if outcome.exit_code:
            ctx.configure_only = True
            return Outcome(
                (
                    f"Login service: not completed ({plist}); see `relaydeck service status`.",
                    *CONFIGURE_ONLY_LINES,
                ),
                changed=outcome.changed,
                exit_code=1,
            )
        return Outcome((f"Login service installed and loaded: {plist}",), changed=outcome.changed)


class WaitStep(Step):
    name = "Wait until running"

    def check(self, ctx: SetupContext) -> Status:
        if ctx.config is None or ctx.configure_only:
            return Status(SKIPPED, "Wait: skipped (configure only).")
        return Status(TODO)

    def apply(self, ctx: SetupContext) -> Outcome:
        assert ctx.config is not None  # noqa: S101
        config = ctx.config
        deadline = ctx.clock() + WAIT_S
        while True:
            try:
                body = ctx.companion_state(config.state_dir)
            except (OSError, ValueError):
                body = {"state": "unknown"}
            if running.ready(body, config):
                break
            if ctx.clock() >= deadline:
                log = ctx.short(service.log_dir(ctx.home) / "companion.err.log")
                return Outcome(
                    (
                        "RelayDeck did not become ready in time. The host is otherwise set up; "
                        f"see the log at {log}.",
                    ),
                    exit_code=1,
                )
            ctx.sleep(POLL_S)
        ctx.running = True
        if ctx.skip_route_probe:
            return Outcome(("Private route: not probed (--no-probe).",))
        try:
            probe = ctx.route_probe(config.advertised_origin)
        except Exception:  # noqa: BLE001 - any probe failure is one fixed line
            probe = {"state": "failed"}
        if probe.get("state") != "reachable":
            return Outcome(
                (f"Private route not reachable yet at {config.advertised_origin}.",), exit_code=1
            )
        return Outcome((f"Private route: reachable at {config.advertised_origin}",))
