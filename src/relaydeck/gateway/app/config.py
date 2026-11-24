"""Gateway serving configuration and resource construction."""

from __future__ import annotations

import argparse
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


MIN_SECRET_BYTES = 32


class ServeConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ServeOptions:
    host: str
    port: int
    origin: str
    journal_path: str
    push_path: str
    cursor_secret: bytes
    log_config: str
    ephemeral_secret: bool
    enrollment_store: str = ""
    revoke_spool: str = ""
    host_store: str = ""
    fcm_credentials: str = ""
    identity_config: str = ""
    pairing_store: str = ""


def resolve_serve_options(args: argparse.Namespace) -> ServeOptions:
    """Validate `serve` flags. Raises ServeConfigError on usage errors."""
    if args.host not in LOOPBACK_HOSTS:
        raise ServeConfigError(f"refusing non-loopback bind {args.host!r} (loopback only)")
    if not 1 <= args.port <= 65535:
        raise ServeConfigError(f"bad port {args.port!r}")
    secret_text = args.cursor_secret
    if args.cursor_secret_file:
        try:
            with open(args.cursor_secret_file, encoding="utf-8") as handle:
                secret_text = handle.read().strip()
        except OSError as exc:
            raise ServeConfigError(f"cannot read --cursor-secret-file: {exc}") from exc
    if not secret_text:
        secret_text = os.environ.get("RELAYDECK_CURSOR_SECRET", "")
    ephemeral = not secret_text
    if ephemeral:
        secret = secrets.token_bytes(MIN_SECRET_BYTES)
    else:
        secret = secret_text.encode()
        if len(secret) < MIN_SECRET_BYTES:
            raise ServeConfigError("cursor secret must be >= 32 bytes")
    return ServeOptions(
        host=args.host,
        port=args.port,
        origin=args.origin,
        journal_path=args.journal_path,
        push_path=args.push_path,
        cursor_secret=secret,
        log_config=args.log_config,
        ephemeral_secret=ephemeral,
        enrollment_store=args.enrollment_store,
        revoke_spool=args.revoke_spool,
        host_store=args.host_store,
        fcm_credentials=getattr(args, "fcm_credentials", ""),
        identity_config=getattr(args, "identity_config", ""),
        pairing_store=getattr(args, "pairing_store", ""),
    )


def build_serve_state(options: ServeOptions) -> Any:
    """Build the serving GatewayState: JournalBackend + file-backed
    PushStore when database paths are given, else the in-memory demo
    store (loudly documented by the caller, never silent). With
    --enrollment-store the invitation registry is the shared file the
    operator CLI writes (the documented ceremony); otherwise HTTP-
    created invitations stay in process memory."""
    from contextlib import ExitStack
    from pathlib import Path

    from relaydeck.gateway.app.state import GatewayState
    from relaydeck.gateway.features.access.api import EnrollmentStore, HostStore

    with ExitStack() as cleanup:
        if options.journal_path:
            from relaydeck.gateway.app.backend import JournalBackend
            from relaydeck.host.persistence.command_journal import CommandJournal

            journal = CommandJournal(options.journal_path)
            cleanup.callback(journal.close)
            backend: Any = JournalBackend(journal)
        else:
            from relaydeck.devtools.fakes.gateway import FakeBackend

            backend = FakeBackend(now_ms=lambda: int(time.time() * 1000))
        if options.push_path:
            from relaydeck.gateway.features.notifications.api import PushStore

            push = PushStore(options.push_path)
        else:
            from relaydeck.gateway.features.notifications.api import PushStore

            push = PushStore()
        cleanup.callback(push.close)
        provider = None
        if options.fcm_credentials:
            if not options.push_path:
                raise ServeConfigError("FCM delivery requires a durable --push-path")
            from relaydeck.gateway.features.notifications.api import FcmProvider

            provider = FcmProvider.from_file(Path(options.fcm_credentials).expanduser())
            cleanup.callback(provider.close)
        enrollments = EnrollmentStore(
            path=options.enrollment_store or None,
        )
        identity_provider = None
        if options.identity_config:
            from relaydeck.gateway.features.access.api import OidcProvider

            identity_provider = OidcProvider.from_file(
                Path(options.identity_config).expanduser(), lambda: int(time.time() * 1000)
            )
        pairing = None
        if options.pairing_store:
            from relaydeck.gateway.features.access.api import PairingStore

            pairing = PairingStore(Path(options.pairing_store).expanduser())
            pairing.start(options.origin)
        state = GatewayState(
            backend=backend,
            cursor_secret=options.cursor_secret,
            now_ms=lambda: int(time.time() * 1000),
            public_origin=options.origin,
            push=push,
            push_provider=provider,
            identity_provider=identity_provider,
            pairing=pairing,
            enrollments=enrollments,
            revoke_spool=options.revoke_spool,
            hosts=HostStore(path=Path(options.host_store) if options.host_store else None),
        )

        cleanup.pop_all()
        return state
