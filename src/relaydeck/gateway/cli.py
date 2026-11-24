"""Operator CLI for gateway serving, public-key-bound enrollment and host revocation. Bind
serving to loopback and make in-memory demo mode explicit.
"""

from __future__ import annotations

import argparse
import sys
import time

from relaydeck.gateway.app.config import ServeConfigError, build_serve_state, resolve_serve_options


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="relaydeck-gateway")
    sub = parser.add_subparsers(dest="command", required=True)
    issue = sub.add_parser("issue-enrollment", help="Mint a pubkey-bound invitation")
    issue.add_argument("--host-label", required=True, help="Display label for the home host")
    issue.add_argument("--pubkey", required=True, help="Connector ssh-ed25519 public line")
    issue.add_argument("--account", default="owner", help="Account to bind the host to")
    issue.add_argument("--origin", default="", help="Public gateway origin for the QR")
    issue.add_argument("--ttl-seconds", type=int, default=600)
    issue.add_argument(
        "--store",
        default="",
        help="Shared invitation file (0600); must match serve --enrollment-store",
    )
    sub.add_parser("list-hosts", help="List enrolled hosts")
    revoke = sub.add_parser("revoke-host", help="Queue a single-host revoke")
    revoke.add_argument("--host", required=True)
    revoke.add_argument(
        "--spool",
        default="",
        help="Revoke spool dir drained by the serving process",
    )
    serve = sub.add_parser("serve", help="Run the gateway server (loopback only)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--origin", default="", help="Public origin advertised in QR codes")
    serve.add_argument("--journal-path", default="", help="Durable command journal (sqlite, 0600)")
    serve.add_argument("--push-path", default="", help="Durable push store (sqlite, 0600)")
    serve.add_argument(
        "--fcm-credentials",
        default="",
        help="Owner-only FCM service-account JSON; disabled unless set",
    )
    serve.add_argument("--identity-config", default="", help="Owner-only reviewed OIDC JSON")
    serve.add_argument(
        "--cursor-secret", default="", help="Metadata cursor HMAC secret (>= 32 bytes)"
    )
    serve.add_argument("--cursor-secret-file", default="", help="File holding the cursor secret")
    serve.add_argument(
        "--log-config",
        default="",
        help="Uvicorn logging config; the default turns framework records into content-free events",
    )
    serve.add_argument(
        "--enrollment-store",
        default="",
        help="Shared invitation file (0600) for operator-issued enrollments",
    )
    serve.add_argument("--revoke-spool", default="", help="Drain local host-revocation requests")
    serve.add_argument("--host-store", default="", help="Private durable host-token hash registry")
    serve.add_argument("--pairing-store", default="", help="Private local device pairing state")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "issue-enrollment":
        from relaydeck.contracts.enrollment import build_qr_payload
        from relaydeck.contracts.pubkeys import fingerprint_of_raw, openssh_to_raw, raw_to_openssh
        from relaydeck.gateway.features.access.api import EnrollmentStore

        try:
            raw = openssh_to_raw(args.pubkey)
        except ValueError as exc:
            print(f"bad pubkey: {exc}")
            return 1
        now_ms = int(time.time() * 1000)
        invitation = EnrollmentStore(path=args.store or None).create(
            args.account,
            args.host_label,
            raw_to_openssh(raw),
            fingerprint_of_raw(raw),
            now_ms,
            args.ttl_seconds * 1000,
        )
        from datetime import UTC, datetime

        expires_at = datetime.fromtimestamp(invitation.expires_ms / 1000, UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        qr = build_qr_payload(args.origin, invitation.id, invitation.code, expires_at)
        print(f"id {invitation.id}")
        print(f"code {invitation.code[:4]}-{invitation.code[4:8]}-{invitation.code[8:]}")
        print(f"fingerprint {invitation.fingerprint}")
        print(f"qr {qr}")
        try:
            import qrcode

            matrix = qrcode.QRCode(border=1)
            matrix.add_data(qr)
            matrix.print_ascii(invert=True)
        except ImportError:
            print("(install the gateway group for ASCII QR rendering)", file=sys.stderr)
        if args.store:
            print(f"persisted to {args.store} (0600 shared store)", file=sys.stderr)
        else:
            print(
                "WARNING: single-process demo store; pass --store <file> matching the "
                "serving gateway's --enrollment-store so the invitation redeems.",
                file=sys.stderr,
            )
        return 0
    if args.command == "list-hosts":
        print("list-hosts: requires the serving process store (no operator channel yet).")
        return 2
    if args.command == "revoke-host":
        if not args.spool:
            print("revoke-host: pass --spool <dir> drained by the serving process.")
            return 2
        from relaydeck.gateway.features.access.api import write_revoke_request

        try:
            request_path = write_revoke_request(args.spool, args.host)
        except ValueError as exc:
            print(f"revoke-host: {exc}")
            return 2
        print(f"revoke queued for {args.host} ({request_path})")
        return 0
    if args.command == "serve":
        import uvicorn

        from relaydeck.gateway.app.bootstrap import create_app

        try:
            options = resolve_serve_options(args)
        except ServeConfigError as exc:
            print(f"serve: {exc}")
            return 2
        if not options.journal_path:
            print(
                "WARNING: in-memory demo store (no --journal-path); "
                "sessions, hosts, journals, and leases die on restart.",
                file=sys.stderr,
            )
        if options.ephemeral_secret:
            print(
                "WARNING: ephemeral cursor secret; cursors die on restart "
                "(set --cursor-secret, --cursor-secret-file, or RELAYDECK_CURSOR_SECRET).",
                file=sys.stderr,
            )
        from relaydeck.gateway.app.bounds import GRACEFUL_SHUTDOWN_S, WS_MAX_SIZE
        from relaydeck.host.observability.exceptions import install_exception_safety
        from relaydeck.host.observability.logging import logging_config

        install_exception_safety()

        state = build_serve_state(options)
        print(f"serving gateway on http://{options.host}:{options.port} (loopback only)")
        uvicorn.run(
            create_app(state),
            host=options.host,
            port=options.port,
            log_config=options.log_config or logging_config(),
            access_log=False,
            # Same limits as the companion: bounded frames and shutdown, no trusted proxy headers.
            ws_max_size=WS_MAX_SIZE,
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
            proxy_headers=False,
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
