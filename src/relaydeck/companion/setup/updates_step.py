"""Offer daily signed-release checks with a default of yes, preserving any explicit configured
choice. Fetch nothing here.
"""

from __future__ import annotations

import tomllib

from relaydeck.companion import config_edit
from relaydeck.companion.setup.context import SetupContext
from relaydeck.companion.setup.files import apply_text_result
from relaydeck.companion.setup.steps import DONE, SKIPPED, TODO, Outcome, Plan, Status, Step
from relaydeck.host.files.private import read_private


def _explicit(ctx: SetupContext) -> bool | None:
    """The config's own `[updates] check`, or None when it has none."""
    table = tomllib.loads(read_private(ctx.config_path)).get("updates")
    value = table.get("check") if isinstance(table, dict) else None
    return value if isinstance(value, bool) else None


class UpdatesStep(Step):
    name = "Update check"

    def check(self, ctx: SetupContext) -> Status:
        if ctx.config is None:
            return Status(SKIPPED, "Update check: no valid config yet")
        try:
            chosen = _explicit(ctx)
        except (ValueError, OSError):
            return Status(SKIPPED, "Update check: the config could not be read")
        if chosen is None:
            return Status(TODO)
        word = "on" if chosen else "off"
        return Status(DONE, f"Update check: {word}, as chosen in the config (not changed).")

    def plan(self, ctx: SetupContext) -> Plan:
        return Plan(
            (
                "RelayDeck can look once a day for a newer signed release by downloading a small "
                "public file from GitHub (see Privacy in the README).",
            ),
            "Check daily for RelayDeck updates? [Y/n] ",
        )

    def apply(self, ctx: SetupContext) -> Outcome:
        return self._write(ctx, True)

    def declined(self, ctx: SetupContext) -> Outcome:
        return self._write(ctx, False)

    def _write(self, ctx: SetupContext, value: bool) -> Outcome:
        try:
            text = read_private(ctx.config_path)
            new = config_edit.set_updates_check(text, value)
            tomllib.loads(new)
        except (ValueError, OSError):
            return Outcome(
                ("Update check: the config could not be edited; add [updates] by hand.",),
                exit_code=1,
            )
        result = apply_text_result(
            ctx.config_path,
            text,
            new,
            action="updates",
            entries=[{"field": "updates.check"}],
        )
        ctx.reload()
        return Outcome(
            tuple(result.lines),
            changed=result.state != "failed",
            exit_code=0 if result.state == "committed" else 1,
            uncertain=result.state == "uncertain",
        )
