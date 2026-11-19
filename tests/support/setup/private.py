"""The two setup steps that make the private files: prerequisites and the private-files step."""

from __future__ import annotations

from relaydeck.companion.setup.prerequisites import PrerequisitesStep
from relaydeck.companion.setup.private_step import PrivateFilesStep
from relaydeck.companion.setup.steps import Step


def private_steps() -> list[Step]:
    return [PrerequisitesStep(), PrivateFilesStep()]
