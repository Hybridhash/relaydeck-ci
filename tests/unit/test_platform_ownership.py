"""Platform moves preserve canonical values and only the declared compatibility import."""

from __future__ import annotations

import importlib
import importlib.util

import pytest


@pytest.mark.parametrize("module", ["transport", "sse", "backoff", "controller"])
def test_old_sync_implementation_paths_are_absent(module: str) -> None:
    assert importlib.util.find_spec("relaydeck.client.features.sync." + module) is None


def test_credential_compatibility_does_not_duplicate_or_export_other_ports() -> None:
    from relaydeck.contracts.storage import CredentialStore

    legacy = importlib.import_module("relaydeck.client.adapters.ports")
    assert legacy.CredentialStore is CredentialStore
    assert legacy.__all__ == ["CredentialStore"]
    for name in (
        "ClipboardPort",
        "LifecyclePort",
        "NotificationPort",
        "PushTap",
        "IdentitySession",
    ):
        assert not hasattr(legacy, name)


def test_terminal_implementations_use_the_canonical_error_and_records() -> None:
    from relaydeck.client.platform.transport import shell_ops
    from relaydeck.client.ports import terminal

    for name in ("ShellOpError", "AttachmentInfo", "LeaseInfo", "ShellGatewayOps"):
        assert getattr(shell_ops, name) is getattr(terminal, name)


def test_transport_implementations_use_the_canonical_error_and_frames() -> None:
    from relaydeck.client.platform.transport import sse, transport
    from relaydeck.client.ports import transport as interfaces

    for name in (
        "TransportError",
        "CommandNotAccepted",
        "GatewayTransport",
        "TerminalSocket",
        "SseFrame",
    ):
        assert getattr(transport, name) is getattr(interfaces, name)
    assert sse.SseFrame is interfaces.SseFrame
