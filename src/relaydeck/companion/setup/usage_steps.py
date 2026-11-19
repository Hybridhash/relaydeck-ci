"""Usage sources, plan limits, probe-folder trust and the limits hook. Every item is its own
question, default No, and `--yes` never answers one.
"""

from __future__ import annotations

from relaydeck.companion.setup import prerequisites
from relaydeck.companion.setup.context import SetupContext
from relaydeck.companion.setup.files import EXIT_UNCERTAIN
from relaydeck.companion.setup.hooks import limits_hook_setup
from relaydeck.companion.setup.limits import offer_limits
from relaydeck.companion.setup.probes import offer_setup_trust
from relaydeck.companion.setup.sources import offer_sources
from relaydeck.companion.setup.steps import SKIPPED, Outcome, Plan, Status, Step


def _outcome(exit_code: int) -> Outcome:
    return Outcome(
        exit_code=1 if exit_code else 0,
        uncertain=exit_code == EXIT_UNCERTAIN,
    )


class _UsageStep(Step):
    def check(self, ctx: SetupContext) -> Status:
        if ctx.config is None:
            return Status(SKIPPED, f"{self.name}: no valid config yet")
        return super().check(ctx)


class SourcesStep(_UsageStep):
    name = "Usage sources"

    def plan(self, ctx: SetupContext) -> Plan:
        return Plan()

    def apply(self, ctx: SetupContext) -> Outcome:
        assert ctx.config is not None  # noqa: S101
        _lines, code = offer_sources(
            ctx.config_path,
            confirm=ctx.ask,
            detect_fn=ctx.source_detect or prerequisites.detect_report,
            retention_days=ctx.config.usage.retention_days if ctx.config.usage else None,
            emit=ctx.say,
        )
        if code == 0:
            ctx.reload()  # the file may have changed
        return _outcome(code)


class LimitsStep(_UsageStep):
    name = "Plan limits"

    def apply(self, ctx: SetupContext) -> Outcome:
        _lines, code = offer_limits(
            ctx.config_path,
            confirm=ctx.ask,
            detect_fn=ctx.source_detect or prerequisites.detect_report,
            emit=ctx.say,
        )
        if code == 0:
            ctx.reload()
        return _outcome(code)


class TrustStep(_UsageStep):
    name = "Probe folder trust"

    def apply(self, ctx: SetupContext) -> Outcome:
        assert ctx.config is not None  # noqa: S101
        offer_setup_trust(
            ctx.probe_targets(ctx.config_path, ctx.config.state_dir),
            ask=ctx.ask,
            say=ctx.say,
            interactive=not ctx.assume_yes,
            spawn=ctx.trust_spawn,
            which=ctx.trust_which,
        )
        return Outcome()


class HookStep(_UsageStep):
    name = "Limits hook"

    def apply(self, ctx: SetupContext) -> Outcome:
        assert ctx.config is not None  # noqa: S101
        report = limits_hook_setup(
            ctx.config.usage.sources if ctx.config.usage else (),
            explicit=ctx.install_limits_hook,
            confirm=ctx.confirm,
        )
        for line in report.lines:
            ctx.say(line)
        return Outcome(exit_code=report.exit_code)
