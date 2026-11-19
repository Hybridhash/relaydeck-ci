"""Mac companion foreground lifecycle and opt-in private-route diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import faulthandler
import json
import os
import signal
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from relaydeck.companion.app.lifecycle import AlreadyRunning, request_stop, supervise
from relaydeck.companion.app.runtime import CompanionRuntime
from relaydeck.companion.config import DEFAULT_CONFIG, CompanionConfig, load_config
from relaydeck.companion.inspection.running import probe_private
from relaydeck.companion.inspection.status import status
from relaydeck.companion.setup import preflight
from relaydeck.companion.setup.usage_cli import run_configured_usage
from relaydeck.usage.api import add_usage_parser
from relaydeck.version import __version__


@contextmanager
def thread_dumps() -> Iterator[None]:
    """Operator-only stack dump to the existing foreground log; never includes locals."""
    previous = signal.getsignal(signal.SIGUSR1)
    faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True)
    try:
        yield
    finally:
        faulthandler.unregister(signal.SIGUSR1)
        signal.signal(signal.SIGUSR1, previous)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="relaydeck-companion")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Owner-only companion TOML")
    parser.add_argument("--version", action="version", version=f"relaydeck {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    service = sub.add_parser("service-run", help="Run the installed companion with bounded logs")
    service.add_argument("--log-directory", required=True, type=Path)
    add_usage_parser(sub)
    start = sub.add_parser(
        "start", help="Foreground gateway + dynamic Flet; never install a service"
    )
    source = start.add_mutually_exclusive_group()
    source.add_argument(
        "--connect-herdr",
        action="store_true",
        help="Authorize this run's local Herdr attachment resources",
    )
    source.add_argument(
        "--fixture", action="store_true", help="Explicit synthetic source; never production proof"
    )
    sub.add_parser("status", help="Read readiness of this state directory's exclusive owner")
    stop = sub.add_parser("stop", help="Request this instance to stop; never signal a PID or Herdr")
    stop.add_argument("--timeout", type=float, default=12, help="Bounded wait; no kill fallback")
    doctor = sub.add_parser(
        "doctor", help="Check local prerequisites without provisioning anything"
    )
    doctor.add_argument(
        "--probe-private",
        action="store_true",
        help="Check configured HTTPS route with certificate verification",
    )
    pairing = sub.add_parser("pairing", help="Explicit Mac-local device pairing authorization")
    actions = pairing.add_subparsers(dest="pairing_action", required=True)
    window = actions.add_parser("open", help="Open a bounded window for an enrolled Mac")
    window.add_argument("--ttl-seconds", type=int, default=300)
    actions.add_parser("list", help="Inspect requests and compare matching codes")
    for action in ("approve", "deny"):
        decision = actions.add_parser(action, help=f"Explicitly {action} a matching device request")
        decision.add_argument(
            "--request",
            default=None,
            help=(
                "Request id; approve may omit it when exactly one pending request has this code"
                if action == "approve"
                else "Request id (required for deny)"
            ),
        )
        decision.add_argument("--code", required=True, help="Matching XXX-XXX code from the device")
    actions.add_parser("close", help="Discard the window, requests and unused approvals")
    push = sub.add_parser("push", help="Web push maintenance; every action asks for confirmation")
    push_actions = push.add_subparsers(dest="push_action", required=True)
    push_actions.add_parser(
        "web-rotate",
        help="Replace the web push key; disables sending and removes all web subscriptions",
    )
    return parser


WEB_ROTATE_PHRASE = "rotate web push"


def _web_rotate(config: CompanionConfig) -> int:
    """Replace the VAPID key after a typed confirmation (no --yes). Refuses without a terminal, and
    while the companion runs, because a running process still holds the old key."""
    from relaydeck.gateway.features.notifications.api import WebPushStore, rotate

    if not sys.stdin.isatty():
        print("push web-rotate needs an interactive terminal; nothing was changed.")
        return 2
    if status(config.state_dir).get("state") != "stopped":
        print("Stop the companion first (relaydeck-companion stop); nothing was changed.")
        return 1
    print("This turns web push sending off, deletes every web push subscription and queued")
    print("message, and replaces the VAPID key. Each browser or home-screen app must resubscribe.")
    typed = input(f'Type "{WEB_ROTATE_PHRASE}" to continue: ').strip()
    if typed != WEB_ROTATE_PHRASE:
        print("Not confirmed; nothing was changed.")
        return 1
    store = WebPushStore(config.state_dir)
    try:
        result = rotate(config.state_dir, store)
    finally:
        store.close()
    print(f"Web push key replaced; {result['subscriptions_removed']} subscription(s) removed.")
    return 0


class WebBundleMissing(RuntimeError):
    """No signed bundle for this version and no developer override."""


def _update_object(config: CompanionConfig) -> dict[str, Any]:
    """The additive `update` object of status and doctor: stored facts only, no network."""
    from relaydeck.companion.updates import schedule as update_schedule
    from relaydeck.companion.updates.worker import read_notice

    try:
        return update_schedule.status_object(read_notice(config))
    except Exception:  # noqa: BLE001 - the notice must never break status or doctor
        return update_schedule.status_object(
            update_schedule.Notice(__version__, config.updates.check, None, None, None, "state")
        )


def _select_web_bundle() -> None:
    """Select a verified signed bundle for this installed version unless FLET_WEB_PATH
    explicitly overrides it. Refuse an absent bundle rather than serve stock Flet or another
    version.
    """
    import os

    from relaydeck.companion.app.hosted_web.headers import load_policy
    from relaydeck.releases import web as webrelease
    from relaydeck.releases.bundles import BundleError

    def compatible(path: Path) -> bool:
        try:
            load_policy(path)
        except BundleError:
            return False
        return True

    explicit = os.environ.get("FLET_WEB_PATH")
    signed = None if explicit else webrelease.installed_signed(__version__, compatible=compatible)
    selected = Path(explicit) if explicit else signed.path if signed is not None else None
    if selected is None:
        raise WebBundleMissing("csp_metadata_required")
    try:
        load_policy(selected)
    except (BundleError, ValueError):
        raise WebBundleMissing("csp_metadata_required") from None
    if not explicit:
        os.environ["FLET_WEB_PATH"] = str(selected)
        print("Web bundle: signed release for this version")


def main(argv: list[str] | None = None) -> int:
    from relaydeck.companion.adapters.service_output import configure_child

    configure_child()
    if argv is None and os.environ.get("RELAYDECK_SERVICE_OUTPUT") != "1":
        # Console calls own logging; embedded calls keep their caller's configuration.
        import logging.config

        from relaydeck.host.observability.exceptions import install_exception_safety
        from relaydeck.host.observability.logging import logging_config

        install_exception_safety()
        logging.config.dictConfig(logging_config())
    args = build_parser().parse_args(argv)
    if args.command == "service-run":
        from relaydeck.companion.adapters.service_output import main as service_main

        return service_main(sys.argv[1:] if argv is None else argv)
    if args.command == "usage":
        return run_configured_usage(args)  # Before full config, secrets and prerequisite probes.
    try:
        config = load_config(Path(args.config).expanduser().absolute())
        if args.command == "pairing":
            from relaydeck.companion.pairing.service import run_pairing

            return run_pairing(config, args)
        if args.command == "push":
            return _web_rotate(config)
        if args.command == "status":
            print(
                json.dumps({**status(config.state_dir), "update": _update_object(config)}, indent=2)
            )
            return 0
        if args.command == "stop":
            if not 0 < args.timeout <= 60:
                raise ValueError("stop timeout must be greater than zero and at most 60 seconds")
            stopped = asyncio.run(request_stop(config.state_dir, timeout=args.timeout))
            print(
                (
                    "stop acknowledged; companion stopping"
                    if status(config.state_dir)["state"] == "stopping"
                    else "companion stopped"
                )
                if stopped
                else "stop not acknowledged; inspect the foreground process; no PID was signalled"
            )
            return 0 if stopped else 1
        if args.command == "doctor":
            config.secrets()
            report: dict[str, Any] = {
                "version": __version__,
                # the same read-only results as `relaydeck setup`.
                "prerequisites": preflight(Path(args.config).expanduser().absolute()).as_doc(),
                "advertised_origin": config.advertised_origin,
                "local_endpoint": config.local_endpoint,
                "secrets": "Provided private files validated; no credentials created.",
                "pairing": "Credentials present; start validates their enrollment binding."
                if config.connector_credentials.exists()
                else "Enroll this Mac's connector with a public-key-bound invitation.",
                "herdr": "Socket present; startup checks protocol 22."
                if Path(config.herdr_socket).exists()
                else "Herdr socket unavailable; start Herdr separately and check its path.",
                "device_authorization": (
                    "Approve a device on this Mac with a matching code (relaydeck pairing)."
                ),
                "identity": (
                    "No separate account login; a device signs in by pairing with this Mac."
                ),
                "push": "Native push/provider integration and device proof remain required.",
                "update": _update_object(config),
                "private_route": asyncio.run(probe_private(config.advertised_origin))
                if args.probe_private
                else {
                    "state": "unverified",
                    "message": "Run doctor --probe-private; repeat access on the phone.",
                },
            }
            print(json.dumps(report, indent=2))
            return 1 if report["private_route"]["state"] == "failed" else 0

        async def run() -> int:
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
            try:
                return await supervise(
                    config.state_dir,
                    lambda: CompanionRuntime(
                        config,
                        connect_herdr=args.connect_herdr,
                        fixture=args.fixture,
                        config_path=Path(args.config).expanduser().absolute(),
                    ),
                    stop,
                )
            finally:
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.remove_signal_handler(sig)

        if config.web_ui:
            try:
                _select_web_bundle()
            except WebBundleMissing as missing:
                print(
                    "companion: csp_metadata_required"
                    if os.environ.get("RELAYDECK_SERVICE_OUTPUT") == "1"
                    else missing
                )
                print(
                    "Check FLET_WEB_PATH or unset it and run relaydeck web fetch."
                    if os.environ.get("FLET_WEB_PATH")
                    else "Run relaydeck web fetch to install the web bundle for this version."
                )
                return 2
        print(f"Foreground companion: {config.advertised_origin}; backend {config.local_endpoint}")
        with thread_dumps():
            return asyncio.run(run())
    except (ValueError, OSError, RuntimeError, AlreadyRunning) as exc:
        if os.environ.get("RELAYDECK_SERVICE_OUTPUT") == "1":
            from relaydeck.host.observability.exceptions import exception_category

            print(f"companion_failure category={exception_category(exc)}")
        else:
            print(f"companion: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
