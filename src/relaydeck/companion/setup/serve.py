"""Offer a tailnet-only HTTPS mapping on the configured private port, refusing Funnel and ports
8443/8444. The --yes option may answer this question.
"""

from __future__ import annotations

from relaydeck.companion.adapters import tailscale
from relaydeck.companion.inspection import prereqs
from relaydeck.companion.setup.context import SetupContext
from relaydeck.companion.setup.files import choose_https_port
from relaydeck.companion.setup.steps import (
    CONFLICT,
    DONE,
    SKIPPED,
    TODO,
    Outcome,
    Plan,
    Status,
    Step,
)


class ServeStep(Step):
    name = "Tailscale Serve"
    core = True

    def _route(self, ctx: SetupContext) -> tailscale.PrivateRoute:
        assert ctx.config is not None  # noqa: S101
        return tailscale.PrivateRoute(ctx.config.tailscale_https_port, ctx.config.port, ctx.runner)

    def check(self, ctx: SetupContext) -> Status:
        if ctx.config is None:
            return Status(SKIPPED, "Tailscale Serve: no valid config yet")
        route = self._route(ctx)
        try:
            configured, public = route.inspect()
        except tailscale.TailscaleError:
            return Status(
                CONFLICT,
                "Tailscale Serve could not be confirmed. Recheck its status and configured "
                "HTTPS port before retrying.",
            )
        if public or (configured is not None and configured != route.target):
            return Status(
                CONFLICT, prereqs.GUIDANCE["serve_funnel" if public else "serve_conflict"]
            )
        port = choose_https_port(ctx.config.tailscale_https_port, set(), set())
        if port is None:
            return Status(
                CONFLICT,
                "Configured HTTPS port is reserved; explicitly change tailscale_https_port before "
                "setup. No Serve change was made.",
            )
        if configured == route.target:
            return Status(
                DONE,
                f"HTTPS port {ctx.config.tailscale_https_port} already maps to this companion; "
                "nothing to apply.",
            )
        return Status(TODO)

    def plan(self, ctx: SetupContext) -> Plan:
        assert ctx.config is not None  # noqa: S101
        route = self._route(ctx)
        command = f"tailscale serve --https={ctx.config.tailscale_https_port} --bg {route.target}"
        question = None if ctx.assume_yes else "Apply this serve mapping now? [y/N] "
        return Plan((f"Serve command (tailnet-only, no Funnel): {command}",), question)

    def apply(self, ctx: SetupContext) -> Outcome:
        route = self._route(ctx)
        try:
            route.ensure()  # Recheck after the prompt; never overwrite a raced mapping.
        except tailscale.TailscaleError:
            return Outcome(
                (
                    "Tailscale Serve could not be confirmed. Recheck its status and configured "
                    "HTTPS port before retrying.",
                ),
                exit_code=1,
            )
        if route.created:
            return Outcome(("Serve mapping applied.",), changed=True)
        return Outcome(("Serve already maps to this companion; nothing to apply.",))

    def declined(self, ctx: SetupContext) -> Outcome:
        return Outcome(
            ("Not applied. Re-run setup with --yes, or run the command above yourself.",),
            skipped=True,
        )
