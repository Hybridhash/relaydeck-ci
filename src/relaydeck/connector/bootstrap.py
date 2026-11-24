"""Connector link composition and lifetime."""

from __future__ import annotations

import asyncio
from typing import Any

LIVE_HERDR_ENV = "RELAYDECK_ALLOW_LIVE_HERDR"


def run_link(args: Any, *, configure_logging: bool = False) -> int:
    """Run the connector link. Exit 0 clean (shutdown / --once done),
    1 runtime failure (not enrolled, credential dead, retries out,
    source failure), 2 usage error (no source, both sources, or live
    Herdr without owner authorization)."""
    import logging
    import logging.config
    import os
    import signal

    from relaydeck.host.observability.exceptions import install_exception_safety
    from relaydeck.host.observability.logging import logging_config

    if configure_logging:
        install_exception_safety()

    if bool(args.fixture) == bool(args.live_herdr):
        print("refusing to run: pass exactly one of --fixture or --live-herdr")
        return 2
    if args.live_herdr and os.environ.get(LIVE_HERDR_ENV) != "1":
        print(
            "refusing live Herdr: owner authorization missing "
            f"(set {LIVE_HERDR_ENV}=1 with --live-herdr)"
        )
        return 2
    if args.log_config:
        try:
            if args.log_config.endswith(".json"):
                import json as _json

                with open(args.log_config, encoding="utf-8") as handle:
                    logging.config.dictConfig(_json.load(handle))
            else:
                logging.config.fileConfig(args.log_config, disable_existing_loggers=False)
        except Exception as exc:  # noqa: BLE001 - CLI reports any failure
            print(f"bad --log-config: {exc}")
            return 2
    elif configure_logging:
        config = logging_config()
        config["loggers"] = {}  # the connector only needs the shared handler and format
        config["root"] = {"level": "INFO", "handlers": ["default"]}
        logging.config.dictConfig(config)

    from relaydeck.connector.enrollment import (
        ConnectorCreds,
        claim_enrollment,
        load_creds,
        load_keys,
        parse_invitation,
        save_creds,
    )

    creds = load_creds()
    if creds is None and (args.qr or args.id or args.code):
        gateway, invitation_id, code = args.gateway, args.id, args.code
        if args.qr:
            try:
                origin, invitation_id, code, _ = parse_invitation(args.qr)
            except Exception as exc:  # noqa: BLE001 - CLI reports any failure
                print(f"enroll failed: bad invitation: {exc}")
                return 1
            gateway = gateway or origin
        if not gateway or not args.host:
            print("enroll failed: --gateway (or --qr) and --host are required for first-run redeem")
            return 1
        present = load_keys()
        if present is None:
            print("enroll failed: no keys (run keygen first)")
            return 1

        async def post_json(url: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=body)
                try:
                    return response.status_code, response.json()
                except ValueError:
                    return response.status_code, {}

        try:
            token, _ = asyncio.run(
                claim_enrollment(post_json, gateway, args.host, invitation_id, code, present)
            )
        except Exception as exc:  # noqa: BLE001 - CLI reports any failure
            print(f"enroll failed: {exc}")
            return 1
        creds = ConnectorCreds(gateway, args.host, token)
        save_creds(creds)
        print(f"enrolled {args.host}; starting link")
    if creds is None:
        print("not enrolled (run enroll first, or pass --qr/--id/--code for first-run redeem)")
        return 1

    from relaydeck.contracts.endpoints import origin as validate_origin

    try:
        gateway_endpoint = (
            validate_origin(args.local_gateway, loopback=True)
            if args.local_gateway
            else creds.gateway_url
        )
    except ValueError as exc:
        print(f"bad --local-gateway: {exc}")
        return 2

    from relaydeck.connector.link import ConnectorLink, LinkConfig

    live_source = None
    if args.live_herdr:
        from relaydeck.connector.herdr import HerdrAdapter
        from relaydeck.connector.source import HerdrSource

        live_source = HerdrSource(HerdrAdapter(socket_path=args.herdr_socket))
        source: Any = live_source
    else:
        from relaydeck.devtools.fakes.connector import FixtureAdapter

        source = FixtureAdapter()
    link = ConnectorLink(
        LinkConfig(
            gateway_url=gateway_endpoint,
            host_id=creds.host_id,
            host_token=creds.host_token,
            journal_path=args.journal_path,
            once=args.once,
            once_timeout_s=args.once_timeout,
            max_retries=args.max_retries,
        ),
        source,
    )

    async def serve() -> int:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError, ValueError):
                # Non-main thread or unsupported loop: KeyboardInterrupt
                # handling below still shuts down cleanly.
                pass
        if live_source is not None:
            from relaydeck.connector.herdr import HerdrError

            try:
                bootstrapped = await live_source.bootstrap()
            except (HerdrError, OSError) as exc:
                print(f"live Herdr bootstrap failed: {exc}")
                await live_source.close()
                return 1
            print(
                f"live Herdr bootstrap epoch={bootstrapped.epoch} "
                f"seeds={len(bootstrapped.seed_events)} "
                f"panes={len(live_source.pane_subscribed)}"
            )
        try:
            outcome = await link.run(stop)
        finally:
            if live_source is not None:
                await live_source.close()
        print(
            f"link {outcome.reason} "
            f"(sessions={outcome.sessions} dispatches={outcome.dispatches} "
            f"results={outcome.results} events={outcome.pushed_events} "
            f"outputs={outcome.pushed_outputs})"
        )
        return 0 if outcome.reason in ("shutdown", "once-done") else 1

    try:
        return asyncio.run(serve())
    except KeyboardInterrupt:
        print("link shutdown (interrupt)")
        return 0
