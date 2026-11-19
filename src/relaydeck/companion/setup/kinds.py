"""Which installed agent CLIs devices may start. Default No; independent of usage questions."""

from __future__ import annotations

from relaydeck.companion import config_edit
from relaydeck.companion.setup import prerequisites
from relaydeck.companion.setup.context import SetupContext
from relaydeck.companion.setup.files import apply_text_result
from relaydeck.companion.setup.steps import DONE, SKIPPED, TODO, Outcome, Plan, Status, Step
from relaydeck.contracts.catalog import AGENT_KIND, MAX_AGENT_KINDS
from relaydeck.host.files.private import read_private


def detected_kinds(ctx: SetupContext) -> list[str]:
    """Agent CLIs Herdr knows and that are installed, in catalog order."""
    report = (ctx.source_detect or prerequisites.detect_report)(ctx.config_path)
    kinds: list[str] = []
    for row in report.rows:
        if row.herdr_detects and row.binary is not None and AGENT_KIND.fullmatch(row.id):
            kinds.append(row.id)
    return kinds[:MAX_AGENT_KINDS]


class AgentKindsStep(Step):
    """Writes exactly the kinds the owner was asked about, onto the config they were asked
    about; an agent installed meanwhile is never added."""

    name = "Agent kinds"

    def __init__(self) -> None:
        self._approved: list[str] | None = None
        self._snapshot: str | None = None

    def check(self, ctx: SetupContext) -> Status:
        if ctx.config is None:
            return Status(SKIPPED, "agent kinds: no valid config yet")
        if ctx.config.agent_kinds:
            return Status(DONE, "Agent kinds: already set in the config (not changed).")
        if not detected_kinds(ctx):
            return Status(
                SKIPPED, "Agent kinds: no installed agent CLI detected; nothing to allow."
            )
        return Status(TODO)

    def plan(self, ctx: SetupContext) -> Plan:
        kinds = detected_kinds(ctx)
        try:
            self._snapshot = read_private(ctx.config_path)
            self._approved = kinds
        except (ValueError, OSError):
            self._snapshot = self._approved = None
        listed = ", ".join(kinds)
        return Plan(
            ("Devices can start only the agent kinds listed in the config.",),
            f"Allow these agents to be started from your devices: {listed}? [y/N] ",
        )

    def declined(self, ctx: SetupContext) -> Outcome:
        return Outcome(
            ("Agent kinds: not allowed; add agent_kinds to the config later if you want them.",),
            skipped=True,
        )

    def apply(self, ctx: SetupContext) -> Outcome:
        if self._approved is None or self._snapshot is None:
            return Outcome(
                ("Agent kinds: the config could not be read; add agent_kinds by hand.",),
                exit_code=1,
            )
        try:
            text = read_private(ctx.config_path)
        except (ValueError, OSError):
            text = None
        if text != self._snapshot:
            return Outcome(
                ("Agent kinds: the config changed after the question; run relaydeck setup again.",),
                exit_code=1,
            )
        still = set(detected_kinds(ctx))
        kinds = [kind for kind in self._approved if kind in still]
        if not kinds:
            return Outcome(
                ("Agent kinds: none of the approved agents is installed now; none allowed.",),
                skipped=True,
            )
        try:
            new = config_edit.set_top_level(
                text, "agent_kinds", config_edit.render_string_list(kinds)
            )
        except ValueError:
            return Outcome(
                ("Agent kinds: the config line could not be edited; add agent_kinds by hand.",),
                exit_code=1,
            )
        result = apply_text_result(
            ctx.config_path,
            text,
            new,
            action="agent-kinds",
            entries=[{"kind": kind} for kind in kinds],
        )
        ctx.reload()
        return Outcome(
            tuple(result.lines),
            changed=result.state != "failed",
            exit_code=0 if result.state == "committed" else 1,
            uncertain=result.state == "uncertain",
        )
