"""Command entry point for companion operations, installation, service management and web
bundles.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from relaydeck.companion.adapters.service_output import configure_child

configure_child()

if TYPE_CHECKING:
    from relaydeck.companion.inspection import readiness
    from relaydeck.companion.inspection.detect import DetectReport
    from relaydeck.companion.setup.context import LocalWeb


COMPANION_COMMANDS = ("start", "stop", "status", "doctor", "pairing", "usage")
INSTALL_COMMANDS = ("setup", "service", "web", "pair")


def _first_command(argv: Sequence[str]) -> str | None:
    """The first positional token, skipping `--config` and its value."""
    skip_value = False
    for token in argv:
        if skip_value:
            skip_value = False
            continue
        if token == "--config":  # noqa: S105 - CLI flag, not a credential
            skip_value = True
            continue
        if token.startswith("-"):
            continue
        return token
    return None


def build_parser() -> argparse.ArgumentParser:
    from relaydeck.companion.config import DEFAULT_CONFIG
    from relaydeck.version import __version__

    parser = argparse.ArgumentParser(
        prog="relaydeck",
        description=(
            "RelayDeck Mac companion and install tools. Companion commands: "
            + ", ".join(COMPANION_COMMANDS)
            + " (see relaydeck-companion --help)."
        ),
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Owner-only companion TOML")
    parser.add_argument("--version", action="version", version=f"relaydeck {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("setup", help="First-run checks; creates the owner-only config")
    setup.add_argument(
        "--install-limits-hook",
        action="store_true",
        help="Separately authorize limits-hook install",
    )
    setup.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Answer only the Tailscale Serve question and the install/open questions for a "
            "missing prerequisite; every other choice is still asked"
        ),
    )
    setup.add_argument(
        "--web-archive",
        default=None,
        help="Install the web app from this local release archive (with --web-signature)",
    )
    setup.add_argument(
        "--web-signature", default=None, help="The archive's .minisig file (with --web-archive)"
    )
    setup.add_argument(
        "--detect",
        action="store_true",
        help="Read-only: list the agent CLIs, their states and next steps; writes nothing",
    )
    setup.add_argument(
        "--check",
        action="store_true",
        help="Read-only readiness report: what is ready, what is not, and the next step",
    )
    setup.add_argument(
        "--undo-sources",
        action="store_true",
        help="Remove only the usage sources setup added that are still unchanged",
    )
    setup.add_argument(
        "--no-probe", action="store_true", help="Do not probe the private HTTPS route at the end"
    )
    setup.add_argument(
        "--re-enroll",
        action="store_true",
        help="Replace this Mac's connector enrollment (asks first; the old token stops working)",
    )
    pair = sub.add_parser(
        "pair", help="Pair a device: open a window and approve by typing its code"
    )
    pair.add_argument(
        "--ttl", type=int, default=300, help="Seconds the window stays open (max 300)"
    )
    agent = sub.add_parser("service", help="Opt-in per-user launchd agent")
    agent_actions = agent.add_subparsers(dest="action", required=True)
    install = agent_actions.add_parser("install", help="Write the LaunchAgent (needs --yes)")
    install.add_argument("--yes", action="store_true")
    install.add_argument("--load", action="store_true", help="Also bootstrap it (needs --yes)")
    uninstall = agent_actions.add_parser("uninstall", help="Remove the LaunchAgent (needs --yes)")
    uninstall.add_argument("--yes", action="store_true")
    uninstall.add_argument("--unload", action="store_true", help="Also bootout (needs --yes)")
    agent_actions.add_parser("status", help="Read the LaunchAgent file (no launchctl)")
    web = sub.add_parser("web", help="Installed web bundles")
    web_actions = web.add_subparsers(dest="action", required=True)
    bundle = web_actions.add_parser("install", help="Copy and verify a built web bundle")
    bundle.add_argument("dir", help="Built web directory (holds main.dart.js)")
    fetch = web_actions.add_parser(
        "fetch", help="Download, verify and install the signed web bundle for a release"
    )
    fetch.add_argument("--version", default=None, help="Release version (default: installed)")
    fetch.add_argument("--archive", default=None, help="Local release archive (offline)")
    fetch.add_argument("--signature", default=None, help="Its .minisig file (with --archive)")
    web_actions.add_parser("list", help="Installed bundles; the newest verified is current")
    for name in COMPANION_COMMANDS:
        sub.add_parser(name, help="Companion command (delegated; see relaydeck-companion --help)")
    return parser


def _emit_line(line: str) -> None:
    """Prints one setup line at once: a source preview lands right before its own question."""
    print(line, flush=True)


def _confirm(prompt: str) -> bool:
    """A question marked [Y/n] is answered yes by an empty reply; every other one needs a y."""
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return False
    if "[Y/n]" in prompt and answer == "":
        return True
    return answer in ("y", "yes")


def _yes_no(value: bool | None) -> str:
    return "unknown" if value is None else ("yes" if value else "no")


def detect_lines(report: DetectReport) -> list[str]:
    """Return detection-table lines within 100 columns, keeping discovery, installation and
    configuration states distinct.
    """

    lines = [
        f"{'CLI':<13} {'Detects':<7} {'Hook':<14} {'Installed':<9} {'Adapter':<32} "
        f"{'Configured':<10}",
    ]
    for row in report.rows:
        lines.append(
            f"{row.id:<13} {_yes_no(row.herdr_detects):<7} {row.herdr_hook:<14} "
            f"{_yes_no(row.binary is not None):<9} {row.adapter:<32} "
            f"{_yes_no(row.configured):<10}"
        )
        lines.append(f"  -> {row.next_step}")
    lines.extend(
        [
            "Legend:",
            "  Detects  Herdr has a detection manifest for the CLI.",
            "  Hook     Herdr integration hook: current, outdated, not installed.",
            "           none = Herdr has no hook for it; unknown = Herdr status unavailable.",
            "  Adapter  RelayDeck usage reader format, or not yet.",
        ]
    )
    counts = {
        step: sum(1 for row in report.rows if row.next_step == step)
        for step in sorted({row.next_step for row in report.rows})
    }
    lines.append("Summary: " + ", ".join(f"{count} {step}" for step, count in counts.items()))
    for note in report.notes:
        lines.append(note)
    if report.config_status != "ok":
        lines.append(f"Configuration: {report.config_status} (configured column is unknown)")
    lines.append("Nothing was written. Run `relaydeck setup` to add sources, with your consent.")
    return lines


def readiness_lines(report: readiness.Readiness, home: str) -> list[str]:
    """Return one readiness item per line with its next step below it, abbreviating the home
    directory as ~.
    """
    from relaydeck.companion.inspection import readiness

    _SYMBOL = {readiness.OK: "✓", readiness.WARN: "!", readiness.FAIL: "✗"}

    lines: list[str] = []
    for item in report.items:
        lines.append(f"{_SYMBOL[item.state]} {item.number:>2}. {item.title}: {item.reason}")
        if item.next_step:
            lines.append(f"       next: {item.next_step}")
    lines.append(
        "Ready to pair."
        if report.ready_to_pair
        else "Not ready to pair yet; fix the items marked above."
    )
    return [line.replace(home, "~") if home else line for line in lines]


def _check_report(config_path: Path) -> readiness.Readiness:
    """Read prerequisite, Serve, companion, web bundle and usage readiness without changing
    configuration.
    """
    from relaydeck.companion.adapters import tailscale
    from relaydeck.companion.config import load_config
    from relaydeck.companion.inspection import prereqs, readiness
    from relaydeck.companion.setup import detect_report

    try:
        config = load_config(config_path)
    except (OSError, ValueError):
        config = None
    socket = prereqs.probe_socket(config.herdr_socket if config else None)
    https_port = config.tailscale_https_port if config else 8445
    target = None
    if config is not None:
        target = tailscale.PrivateRoute(
            config.tailscale_https_port, config.port, tailscale.default_runner
        ).target
    report = prereqs.run_prereqs(
        socket=socket,
        https_port=https_port,
        probes=prereqs.default_probes(),
        expected_target=target,
    )
    usage = detect_report(config_path) if config is not None and config.usage is not None else None
    return readiness.run_check(config_path, prereq_report=report, usage=usage)


def _pending_remedies(config_path: Path) -> tuple[str, ...]:
    """What an interactive setup would offer for the prerequisites as they are now (read-only)."""
    from relaydeck.companion import setup as setup_flow
    from relaydeck.companion.setup import remediate

    try:
        return remediate.pending_actions(setup_flow.preflight(config_path))
    except Exception:  # noqa: BLE001 - a note for the no-terminal path never fails it
        return ()


def _update_section(config_path: Path) -> list[str]:
    """The update-check lines for `setup --check`; nothing when there is no loadable config."""
    from relaydeck.companion.config import load_config
    from relaydeck.companion.updates.report import update_lines

    try:
        config = load_config(config_path)
        return update_lines(config, config_path)
    except Exception:  # noqa: BLE001 - the update section never breaks the readiness report
        return []


def _local_web(args: argparse.Namespace, config_path: Path) -> tuple[LocalWeb | None, str]:
    """Return the offline web source or a refusal. Accept the archive and signature together or
    neither, and refuse them for reports, undo runs or profiles with the web app off.
    """
    from relaydeck.companion.config import load_config
    from relaydeck.companion.setup.context import LocalWeb

    archive, signature = args.web_archive, args.web_signature
    if archive is None and signature is None:
        return None, ""
    if (archive is None) != (signature is None):
        return None, "setup refused: --web-archive and --web-signature go together"
    if args.check or args.detect or args.undo_sources:
        return None, "setup refused: --web-archive does not apply to a report or an undo"
    archive_path, signature_path = Path(archive).expanduser(), Path(signature).expanduser()
    if not archive_path.is_file() or not signature_path.is_file():
        return None, "setup refused: the web archive or its signature is not a file"
    try:
        web_ui = load_config(config_path).web_ui
    except (OSError, ValueError):
        web_ui = True  # no profile yet: the template turns the web app on
    if not web_ui:
        return None, "setup refused: this profile has web_ui = false, so there is no web app"
    return LocalWeb(archive_path, signature_path), ""


def _run_install(args: argparse.Namespace) -> int:
    from relaydeck.companion import setup as setup_flow
    from relaydeck.companion.adapters import launchd as service_agent
    from relaydeck.companion.config import DEFAULT_CONFIG, load_config
    from relaydeck.companion.inspection import readiness
    from relaydeck.companion.pairing.wizard import run_pair
    from relaydeck.companion.setup import detect_report, run_setup
    from relaydeck.releases import bundles as webbundle
    from relaydeck.version import __version__

    config_path = Path(args.config).expanduser().absolute()
    home_text = os.path.expanduser("~")
    local_web: LocalWeb | None = None
    if args.command == "setup":
        local_web, refusal = _local_web(args, config_path)
        if refusal:
            print(refusal)
            return 2
    if args.command == "setup" and getattr(args, "detect", False):
        for line in detect_lines(detect_report(config_path)):
            print(line)
        return 0
    if args.command == "setup" and getattr(args, "check", False):
        check = _check_report(config_path)
        print(f"relaydeck {__version__}")
        print("\n".join(readiness_lines(check, home_text)))
        notice_lines = _update_section(config_path)
        if notice_lines:
            print("\n".join(line.replace(home_text, "~") for line in notice_lines))
        return check.exit_code
    if args.command == "setup" and getattr(args, "undo_sources", False):
        undo_lines, undo_code = setup_flow.undo_sources(config_path)
        print("\n".join(undo_lines))
        return undo_code
    if args.command == "setup" and not sys.stdin.isatty():
        # Without a terminal, setup cannot ask questions or write configuration, even with --yes.
        pending = readiness.pending_decisions(config_path)
        print("\n".join(readiness_lines(pending, home_text)))
        candidates, _unsupported = setup_flow.source_candidates(
            detect_report(config_path), home_text
        )
        if candidates:
            print("Pending source choices (answer them in a terminal):")
            for candidate in candidates:
                print(
                    f"  - {candidate.display_name} ({candidate.kind}) at {candidate.root_display}"
                )
        offered = _pending_remedies(config_path)
        if offered:
            print("Setup would offer, asking first:")
            for line in offered:
                print(f"  - {line}")
        print("Pending decisions: run relaydeck setup in a terminal to answer its prompts.")
        print("Nothing was written.")
        return 1
    if args.command == "setup":
        effects: dict[str, Any] = {} if local_web is None else {"web": local_web}
        report = run_setup(
            config_path=config_path,
            assume_yes=args.yes,
            install_limits_hook=args.install_limits_hook,
            confirm=_confirm,
            emit_line=_emit_line,
            re_enroll=args.re_enroll,
            skip_route_probe=args.no_probe,
            **effects,
        )
        if report.lines:
            print("\n".join(report.lines), flush=True)
        return report.exit_code
    if args.command == "pair":
        if not sys.stdin.isatty():
            print("relaydeck pair needs a terminal; nothing was changed.")
            return 2
        try:
            config = load_config(config_path)
        except (OSError, ValueError):
            print("The configuration cannot be loaded. Run: relaydeck setup")
            return 2
        return run_pair(config, prompt=input, say=_emit_line, ttl_s=args.ttl)
    if args.command == "service":
        home = Path.home()
        uid = os.getuid()
        if args.action == "status":
            print(json.dumps(service_agent.status(home), indent=2, sort_keys=True))
            return 0
        if args.action == "install":
            executable = Path(sys.argv[0]).expanduser().resolve()
            default = Path(DEFAULT_CONFIG).expanduser().absolute()
            outcome = service_agent.install(
                executable=executable,
                home=home,
                uid=uid,
                yes=args.yes,
                load=args.load,
                config=None if config_path == default else config_path,
            )
        else:
            outcome = service_agent.uninstall(home=home, uid=uid, yes=args.yes, unload=args.unload)
        print("\n".join(outcome.lines))
        return outcome.exit_code
    if args.action == "list":
        rows = webbundle.list_bundles()
        if not rows:
            print("No installed web bundles.")
        for row in rows:
            flag = (
                "current" if row["current"] else ("verified" if row["verified"] else "unverified")
            )
            print(f"{row['build_id']}  {flag}  installed {row['installed_at']}  {row['path']}")
        return 0
    if args.action == "fetch":
        from relaydeck.releases import web as webrelease
        from relaydeck.version import __version__ as installed_version

        if bool(args.archive) != bool(args.signature):
            print("web fetch refused: --archive and --signature go together")
            return 2
        version = args.version or installed_version
        try:
            if args.archive:
                signed = webrelease.install_archive(
                    Path(args.archive), Path(args.signature), version
                )
            else:
                signed = webrelease.fetch_release(version)
        except webrelease.ReleaseError as exc:
            print(f"web fetch refused: {exc.reason}")
            return 1
        shown = signed.path
        try:
            shown = Path("~") / signed.path.relative_to(Path.home())
        except ValueError:
            pass
        print(f"Installed signed web bundle {signed.version} at {shown} (signature verified).")
        return 0
    try:
        record = webbundle.install(Path(args.dir))
    except webbundle.BundleError as exc:
        print(f"web install refused: {exc}")
        return 1
    print(f"Installed {record.build_id} at {record.path} (repair markers verified on the copy).")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if _first_command(args_list) == "service-run":
        from relaydeck.companion.adapters.service_output import main as service_main

        return service_main(args_list)
    if argv is None and os.environ.get("RELAYDECK_SERVICE_OUTPUT") != "1":
        # Explicit argument lists are embedded/test calls; console entry points own logging.
        import logging.config

        from relaydeck.host.observability.exceptions import install_exception_safety
        from relaydeck.host.observability.logging import logging_config

        install_exception_safety()
        logging.config.dictConfig(logging_config())
    import relaydeck.companion.cli as companion_cli

    if _first_command(args_list) in COMPANION_COMMANDS:
        return companion_cli.main(args_list)
    args = build_parser().parse_args(args_list)
    if args.command in INSTALL_COMMANDS:
        return _run_install(args)
    return companion_cli.main(args_list)


if __name__ == "__main__":
    raise SystemExit(main())
