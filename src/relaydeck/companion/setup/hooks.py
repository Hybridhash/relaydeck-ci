"""The offline limits hook: install or repair it with the owner's consent."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from relaydeck.companion.setup.steps import SetupReport
from relaydeck.usage.api import HookSource, diagnose_source, install_hook

HOOK_FAILURE_GUIDANCE = {
    "conflict": (
        "Review statusLine, hook and source cache with usage doctor. Custom replacement requires "
        "separate explicit --replace-statusline consent; setup does not replace it."
    ),
    "create_confirmation_required": (
        "Settings creation was not authorized. Re-run and separately confirm creating the settings "
        "file/directory, or create it yourself."
    ),
    "recovery_required": (
        "Settings may have changed. Stop settings writers; review the private prepared "
        "receipt/backup. "
        "Use usage doctor and the field-only usage uninstall before retrying; do not restore the "
        "whole backup."
    ),
    "unreadable": (
        "Check owner-only settings/hook permissions and access; run usage doctor before retrying."
    ),
    "invalid": (
        "Check JSON, file ownership and private paths with usage doctor; correct invalid or unsafe "
        "files before retrying."
    ),
    "busy": "Stop other settings/install writers, then retry the explicit install.",
    "concurrent_edit": (
        "Stop other settings writers and review their changes with usage doctor before retrying."
    ),
    "resource_invalid": (
        "Reinstall the reviewed package resource in the supported Python 3.13 tool environment."
    ),
    "capacity": (
        "Review private hook receipts before another install; do not remove active source receipts "
        "blindly."
    ),
    "changed_since_diagnosis": (
        "The settings file changed while setup was asking. Nothing was written; review it with "
        "usage doctor and run relaydeck usage repair."
    ),
    "not_configured": (
        "Configure limits_settings and a separate private limits_cache for a supported limits "
        "source before requesting installation."
    ),
}


def limits_hook_setup(
    sources: Iterable[HookSource], *, explicit: bool, confirm: Callable[[str], bool]
) -> SetupReport:
    entries = [
        s
        for s in sources
        if s.limits_format == "json-statusline-limits-v1"
        and s.limits_settings is not None
        and s.limits_cache is not None
    ]
    lines: list[str] = []
    if explicit and not entries:
        return SetupReport(
            ("Limits hook: not_configured. " + HOOK_FAILURE_GUIDANCE["not_configured"],),
            1,
        )
    for source in entries:
        assert source.limits_settings is not None and source.limits_cache is not None  # noqa: S101
        diagnosis = diagnose_source(source)
        if diagnosis.reason in ("ok", "cache_stale"):
            lines.append(f"Limits hook for {source.kind}: in place; nothing to change.")
            continue
        repairing = diagnosis.repairable
        if repairing:
            question = (
                f"{diagnosis.summary} Repair it now? RelayDeck will {diagnosis.change}. [y/N] "
            )
        else:
            question = (
                "Stop settings writers. Install offline limits hook (JSON reformatted)? [y/N] "
            )
        allowed = explicit or confirm(question)
        if not allowed:
            lines.append(
                "Limits hook installation skipped (not authorized); setup continues. "
                f"Later: {diagnosis.fix or 'relaydeck usage repair'}"
            )
            continue
        result = install_hook(
            source.limits_settings,
            source.limits_cache,
            confirmed=True,
            replace_statusline=diagnosis.reason == "managed_field_edited",
            expect_statusline=diagnosis.statusline,
        )
        if result.state == "create_confirmation_required" and confirm(
            "Also create missing settings file/directories? [y/N] "
        ):
            result = install_hook(
                source.limits_settings,
                source.limits_cache,
                confirmed=True,
                create_settings=True,
                expect_statusline=diagnosis.statusline,
            )
        if result.state != "installed":
            reason = result.state if result.state in HOOK_FAILURE_GUIDANCE else "failed"
            guidance = HOOK_FAILURE_GUIDANCE.get(
                reason,
                "Review settings and private hook receipts with usage doctor before retrying.",
            )
            return SetupReport(
                (*lines, f"Limits hook: {reason}. {guidance}"),
                1,
            )
        lines.append("Limits hook: installed (invocation is not yet proven).")
    return SetupReport(tuple(lines), 0)
