"""Offer immediate device pairing through the shared local pairing wizard."""

from __future__ import annotations

from relaydeck.companion.pairing.wizard import run_pair
from relaydeck.companion.setup.context import SetupContext
from relaydeck.companion.setup.steps import SKIPPED, TODO, Outcome, Plan, Status, Step


class PairStep(Step):
    name = "Pair a device"

    def check(self, ctx: SetupContext) -> Status:
        if ctx.config is None or ctx.configure_only or not ctx.running:
            return Status(SKIPPED, "Pair a device: skipped (RelayDeck is not running).")
        return Status(TODO)

    def plan(self, ctx: SetupContext) -> Plan:
        return Plan((), "Pair a device now? [y/N] ")

    def declined(self, ctx: SetupContext) -> Outcome:
        return Outcome(("Pair later with: relaydeck pair",), skipped=True)

    def apply(self, ctx: SetupContext) -> Outcome:
        assert ctx.config is not None  # noqa: S101
        if ctx.pair is not None:
            code = ctx.pair(ctx)
        else:
            code = run_pair(
                ctx.config,
                prompt=ctx.prompt,
                say=ctx.say,
                status=ctx.companion_state,
                clock=ctx.now,
                sleep=ctx.sleep,
            )
        if code == 130:  # cancelled by the owner: not a setup failure
            return Outcome(("Pair later with: relaydeck pair",))
        return Outcome(exit_code=code)
