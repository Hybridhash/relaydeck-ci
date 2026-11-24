"""The setup steps that existed before the private-file, web, login and pairing steps, so tests of
the usage, hook and Serve behaviour keep exercising exactly those and nothing else."""

from __future__ import annotations

from typing import Any

from relaydeck.companion.setup import SetupReport, run_setup
from relaydeck.companion.setup.prerequisites import PrerequisitesStep
from relaydeck.companion.setup.serve import ServeStep
from relaydeck.companion.setup.steps import Step
from relaydeck.companion.setup.usage_steps import HookStep, LimitsStep, SourcesStep, TrustStep


def legacy_steps() -> list[Step]:
    return [
        PrerequisitesStep(),
        SourcesStep(),
        LimitsStep(),
        TrustStep(),
        HookStep(),
        ServeStep(),
    ]


def run_legacy(**kwargs: Any) -> SetupReport:
    return run_setup(steps=legacy_steps(), summary=False, **kwargs)
