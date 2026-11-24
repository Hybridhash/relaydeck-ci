"""Standalone usage-hook CLI; the companion CLI wires it separately.

python -m relaydeck.companion.setup.usage_cli --config <private TOML> doctor
Install requires --install-limits-hook; --yes from Serve is never consulted.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from relaydeck.usage.hooks.doctor import doctor_sources
from relaydeck.usage.hooks.install import install_hook, uninstall_hook
from relaydeck.usage.hooks.setup import HookSource
from relaydeck.usage.limits_plane.probe import (
    NOT_TRUSTED,
    ProbeTarget,
    ask_on_terminal,
    doctor_line,
    offer_trust,
    probe_state,
)


def add_usage_parser(subparsers: Any) -> None:
    usage = subparsers.add_parser("usage", help="Offline limits-hook install/uninstall/doctor")
    add_actions(usage.add_subparsers(dest="usage_action", required=True))


def add_actions(subparsers: Any) -> None:
    subparsers.add_parser(
        "doctor", help="Read immediate installation/cache evidence; no probes or writes"
    )
    install = subparsers.add_parser(
        "install", help="Stop other settings writers; JSON will be reformatted"
    )
    install.add_argument(
        "--source", help="Configured kind/format; no directory discovery. Omit for probe folders"
    )
    install.add_argument(
        "--install-limits-hook", action="store_true", help="Separately authorize hook installation"
    )
    install.add_argument(
        "--create-settings", action="store_true", help="Also authorize creating missing settings"
    )
    install.add_argument(
        "--replace-statusline",
        action="store_true",
        help="Also replace a custom field, never chain it",
    )
    repair = subparsers.add_parser(
        "repair",
        help="Put back only RelayDeck's settings field where it went missing; asks first",
    )
    repair.add_argument("--source", help="One configured kind/format; default: every source")
    repair.add_argument("--yes", action="store_true", help="Apply without asking")
    repair.add_argument(
        "--create-settings", action="store_true", help="Also authorize creating missing settings"
    )
    repair.add_argument(
        "--replace-statusline",
        action="store_true",
        help="Also replace a status line you configured yourself",
    )
    uninstall = subparsers.add_parser(
        "uninstall", help="Restore only the compared managed field; preserve later edits"
    )
    uninstall.add_argument("--source", required=True, help="Configured kind/format")


def run_usage(
    args: argparse.Namespace,
    sources: Iterable[HookSource],
    *,
    hooks_dir: Path | None = None,
    probes: Iterable[ProbeTarget] = (),
) -> int:
    entries = tuple(sources)
    targets = tuple(probes)
    if args.usage_action == "doctor":
        report: dict[str, Any] = {"sources": doctor_sources(entries, hooks_dir=hooks_dir)}
        if targets:
            states = [(t, probe_state(t)) for t in targets]
            report["probe_folders"] = [
                {"tool": t.kind, "path": str(t.folder), "state": state} for t, state in states
            ]
            for t, state in states:
                print(doctor_line(t, state), file=sys.stderr)
        print(json.dumps(report, indent=2))
        return 0  # Diagnostic states are data, not infrastructure/prerequisite probes.
    if args.usage_action == "repair":
        code = _run_repair(args, entries, hooks_dir)
        for t in targets:
            if probe_state(t) == NOT_TRUSTED:
                offer_trust(t, ask=ask_on_terminal)
        return code
    if args.usage_action == "install":
        for t in targets:
            offer_trust(t, ask=ask_on_terminal)
        if args.source is None:
            if targets:
                return 0
            print(json.dumps({"state": "not_configured"}))
            return 1
    selected = [s for s in entries if f"{s.kind}/{s.format}" == args.source]
    if (
        len(selected) != 1
        or selected[0].limits_format != "json-statusline-limits-v1"
        or selected[0].limits_settings is None
        or selected[0].limits_cache is None
    ):
        # A tool that has only a probe folder has no hook to install.
        probe_only = len(selected) == 1 and any(t.kind == selected[0].kind for t in targets)
        if probe_only:
            return 0
        print(json.dumps({"state": "not_configured"}))
        return 1
    source = selected[0]
    assert source.limits_settings is not None and source.limits_cache is not None  # noqa: S101
    if args.usage_action == "install":
        result = install_hook(
            source.limits_settings,
            source.limits_cache,
            confirmed=args.install_limits_hook,
            create_settings=args.create_settings,
            replace_statusline=args.replace_statusline,
            hooks_dir=hooks_dir,
        )
    else:
        result = uninstall_hook(
            source.limits_settings, source.limits_cache, confirmed=True, hooks_dir=hooks_dir
        )
    print(json.dumps({"kind": source.kind, "format": source.format, **asdict(result)}))
    return 0 if result.state in ("installed", "uninstalled", "not_installed") else 1


def _ask(diagnoses: Any) -> bool:
    """The single confirmation: print each planned change on stderr, then ask once."""
    import sys

    for d in diagnoses:
        print(f"{d.kind}/{d.format}: {d.summary}", file=sys.stderr)
        print(f"  will: {d.change}", file=sys.stderr)
    if not sys.stdin.isatty():
        return False
    print("Apply these changes? [y/N] ", end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().strip().lower() in ("y", "yes")


def _run_repair(
    args: argparse.Namespace, entries: tuple[HookSource, ...], hooks_dir: Path | None
) -> int:
    from relaydeck.usage.hooks.repair import repair_sources

    selected = [s for s in entries if args.source is None or f"{s.kind}/{s.format}" == args.source]
    if args.source is not None and not selected:
        print(json.dumps({"state": "not_configured"}))
        return 1
    outcomes = repair_sources(
        selected,
        confirm=(lambda _planned: True) if args.yes else _ask,
        create_settings=args.create_settings,
        replace_statusline=args.replace_statusline,
        hooks_dir=hooks_dir,
    )
    print(json.dumps({"repairs": [o.public() for o in outcomes]}, indent=2))
    pending = {
        "needs_consent",
        "not_repairable",
        "confirmation_declined",
        "failed",
        "changed_since_diagnosis",
    }
    return 1 if any(o.action in pending for o in outcomes) else 0
