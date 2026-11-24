"""Connector CLI (runs on the home Mac alongside Herdr)."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from relaydeck.connector.bootstrap import run_link


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="relaydeck-connector")
    sub = parser.add_subparsers(dest="command", required=True)
    keygen = sub.add_parser("keygen", help="Provision a fresh connector identity")
    keygen.add_argument("--force", action="store_true", help="Replace existing keys")
    sub.add_parser("fingerprint", help="Print the local key fingerprint")
    enroll = sub.add_parser("enroll", help="Redeem an enrollment invitation")
    enroll.add_argument("--gateway", default="", help="Gateway base URL (or from --qr)")
    enroll.add_argument("--host", required=True, help="Host id")
    enroll.add_argument("--id", default="", help="Invitation id (or use --qr)")
    enroll.add_argument("--code", default="", help="Claim code (or use --qr)")
    enroll.add_argument("--qr", default="", help="Scanned invitation reference")
    sub.add_parser("status", help="Show enrollment status")
    sub.add_parser("revoke", help="Wipe local credentials")
    run = sub.add_parser("run", help="Run the connector link (fixture or live Herdr)")
    run.add_argument("--herdr-socket", default="~/.config/herdr/herdr.sock")
    run.add_argument(
        "--journal-path",
        default="~/.config/relaydeck/connector-journal.db",
        help="Connector intent journal (sqlite, 0600)",
    )
    run.add_argument(
        "--fixture",
        action="store_true",
        help="Dispatch against the synthetic fixture (no Herdr contact)",
    )
    run.add_argument(
        "--live-herdr",
        action="store_true",
        help="Dispatch against the real Herdr daemon at --herdr-socket "
        "(REQUIRES-OWNER-APPROVAL: also needs RELAYDECK_ALLOW_LIVE_HERDR=1)",
    )
    run.add_argument(
        "--once",
        action="store_true",
        help="Single session: push state, serve one dispatch, exit 0",
    )
    run.add_argument(
        "--once-timeout", type=float, default=10.0, help="Seconds --once waits for a dispatch"
    )
    run.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="Reconnect attempts before exit 1 (default: forever)",
    )
    run.add_argument(
        "--log-config", default="", help="Logging config file (.json dictConfig, else fileConfig)"
    )
    run.add_argument("--qr", default="", help="First-run redeem: scanned invitation reference")
    run.add_argument(
        "--gateway", default="", help="First-run redeem: gateway base URL (or from --qr)"
    )
    run.add_argument("--host", default="", help="First-run redeem: host id")
    run.add_argument(
        "--local-gateway",
        default="",
        help="Explicit same-Mac loopback endpoint; does not change enrolled private HTTPS origin",
    )
    run.add_argument("--id", default="", help="First-run redeem: invitation id (or use --qr)")
    run.add_argument("--code", default="", help="First-run redeem: claim code (or use --qr)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "keygen":
        from relaydeck.connector.enrollment import generate_keys, load_keys, save_keys

        if load_keys() is not None and not args.force:
            print("keys exist (use --force to replace)")
            return 1
        fresh = generate_keys()
        path = save_keys(fresh)
        print(f"keys at {path}")
        print(f"fingerprint {fresh.fingerprint}")
        print(f"pubkey {fresh.public_openssh}")
        return 0
    if args.command == "fingerprint":
        from relaydeck.connector.enrollment import load_keys

        found = load_keys()
        if found is None:
            print("no keys (run keygen first)")
            return 1
        print(found.fingerprint)
        return 0
    if args.command == "enroll":
        from relaydeck.connector.enrollment import (
            ConnectorCreds,
            claim_enrollment,
            load_keys,
            parse_invitation,
            save_creds,
        )

        gateway, invitation_id, code = args.gateway, args.id, args.code
        if args.qr:
            try:
                origin, invitation_id, code, _ = parse_invitation(args.qr)
            except Exception as exc:  # noqa: BLE001 - CLI reports any failure
                print(f"enroll failed: bad invitation: {exc}")
                return 1
            gateway = gateway or origin
        if not gateway:
            print("enroll failed: gateway URL required (--gateway or QR origin)")
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
            token, fingerprint = asyncio.run(
                claim_enrollment(post_json, gateway, args.host, invitation_id, code, present)
            )
        except Exception as exc:  # noqa: BLE001 - CLI reports any failure
            print(f"enroll failed: {exc}")
            return 1
        path = save_creds(ConnectorCreds(gateway, args.host, token))
        print(f"enrolled {args.host} [{fingerprint}]; credentials at {path}")
        return 0
    if args.command == "status":
        from relaydeck.connector.enrollment import load_creds, load_keys

        creds = load_creds()
        if creds is None:
            print("not enrolled")
            return 1
        maybe_keys = load_keys()
        fingerprint = maybe_keys.fingerprint if maybe_keys else "unknown"
        print(f"host={creds.host_id} gateway={creds.gateway_url} enrolled=yes fp={fingerprint}")
        return 0
    if args.command == "revoke":
        from relaydeck.connector.enrollment import wipe_creds

        if wipe_creds():
            print("local credentials wiped (also revoke at the gateway)")
        else:
            print("not enrolled")
        return 0
    if args.command == "run":
        return run_link(args, configure_logging=argv is None)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
