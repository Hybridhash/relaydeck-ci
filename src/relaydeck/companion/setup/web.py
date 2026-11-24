"""Install the signed web bundle for the installed application version."""

from __future__ import annotations

from relaydeck.companion.setup.context import SetupContext, WebFetchError
from relaydeck.companion.setup.steps import DONE, SKIPPED, TODO, Outcome, Plan, Status, Step
from relaydeck.version import __version__

REASONS = {
    "version": "the version is not a release number",
    "network": "the download did not complete",
    "too_large": "the download is larger than allowed",
    "signature": "the signature did not verify",
    "archive": "the archive is not a valid web bundle",
    "manifest": "the bundle does not match this version",
    "csp_metadata_required": "a compatible web bundle with policy metadata is required",
    "install": "the bundle could not be installed",
}


class WebStep(Step):
    name = "Web app"
    core = True

    def check(self, ctx: SetupContext) -> Status:
        if ctx.config is None:
            return Status(SKIPPED, "Web app: no valid config yet")
        if not ctx.config.web_ui:
            return Status(SKIPPED, "Web app: off; this companion serves the bridge only.")
        if ctx.web.installed(__version__):
            return Status(DONE, f"Web app: signed bundle for {__version__} is installed.")
        return Status(TODO)

    def plan(self, ctx: SetupContext) -> Plan:
        return Plan(
            (
                f"Web app: download the signed release bundle for {__version__} from GitHub, "
                "verify its signature, then install it.",
            ),
            "Install the web app now? [y/N] ",
        )

    def declined(self, ctx: SetupContext) -> Outcome:
        return Outcome(
            ("Web app: not installed. Run `relaydeck web fetch` when you want it.",), exit_code=1
        )

    def apply(self, ctx: SetupContext) -> Outcome:
        try:
            ctx.web.fetch(__version__)
        except WebFetchError as exc:
            reason = REASONS.get(exc.reason, "it failed")
            return Outcome(
                (f"Web app: not installed ({exc.reason}: {reason}). Nothing was changed.",),
                exit_code=1,
            )
        return Outcome((f"Web app: signed bundle for {__version__} installed and verified.",), True)
