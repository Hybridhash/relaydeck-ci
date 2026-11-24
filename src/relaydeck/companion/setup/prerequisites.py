"""Read setup prerequisites using the same checks as doctor, without writing state."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from relaydeck.companion.adapters import tailscale
from relaydeck.companion.config import load_config
from relaydeck.companion.inspection import prereqs
from relaydeck.companion.inspection.detect import DetectReport, run_detect
from relaydeck.companion.inspection.detect import load_catalog as load_detect_catalog
from relaydeck.companion.setup import remediate
from relaydeck.companion.setup.context import SetupContext
from relaydeck.companion.setup.files import DEFAULT_HTTPS_PORT, DEFAULT_LOCAL_PORT
from relaydeck.companion.setup.steps import CONFLICT, DONE, Status, Step

STOPPED_LINE = (
    "Setup stopped: a prerequisite is not ready. Nothing was written and no Serve change was made."
)
STOPPED_AFTER_CHANGES_LINE = (
    "Setup stopped: a prerequisite is not ready. No RelayDeck private configuration was written "
    "and no Serve change was made."
)


def _config_or_none(path: Path, runner: tailscale.Runner) -> Any:
    try:
        return load_config(path, resolve_origin=lambda port: tailscale.private_origin(port, runner))
    except (ValueError, OSError):
        return None


def preflight(
    config_path: Path,
    probes: prereqs.Probes | None = None,
    runner: tailscale.Runner = tailscale.default_runner,
) -> prereqs.Report:
    """The read-only prerequisite report. Shared by setup and `doctor`; writes nothing."""
    current_probes = probes or prereqs.default_probes(runner)
    config = _config_or_none(config_path, current_probes.tailscale)
    usage = getattr(config, "usage", None)
    return prereqs.run_prereqs(
        socket=prereqs.probe_socket(config.herdr_socket if config else None),
        https_port=config.tailscale_https_port if config else DEFAULT_HTTPS_PORT,
        expected_target=f"http://127.0.0.1:{config.port if config else DEFAULT_LOCAL_PORT}",
        probes=current_probes,
        usage_roots=tuple(source.root for source in usage.sources) if usage else (),
    )


def detect_report(config_path: Path) -> DetectReport:
    """`relaydeck setup --detect`: the read-only CLI report.

    Writes nothing and runs no agent CLI.
    """
    return run_detect(
        load_detect_catalog(),
        home=os.path.expanduser("~"),
        environ=os.environ,
        config_path=config_path,
    )


class PrerequisitesStep(Step):
    name = "Prerequisites"
    core = True

    def check(self, ctx: SetupContext) -> Status:
        """The preflight; when a prerequisite fails, the owner is offered each remedy first and
        the preflight runs again, so the gate before any private file is the full report."""

        def recheck() -> prereqs.Report:
            return preflight(ctx.config_path, ctx.probes, ctx.runner)

        ctx.report = recheck()
        done = remediate.Effects()
        if not ctx.report.ready:
            remedied = remediate.remediate(ctx, ctx.report, recheck)
            ctx.report, done = remedied.report, remedied.effects
        lines = (*done.lines(), *ctx.report.lines())
        if ctx.report.ready:
            return Status(DONE, lines=lines)
        return Status(
            CONFLICT, lines=(*lines, STOPPED_AFTER_CHANGES_LINE if done else STOPPED_LINE)
        )
