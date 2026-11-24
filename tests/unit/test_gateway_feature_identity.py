"""Public gateway entries retain their canonical store and error objects."""

from __future__ import annotations

from importlib import import_module

import pytest


@pytest.mark.parametrize(
    ("owner", "names"),
    [
        ("identity", ("Session", "SessionStore", "EnrollmentStore", "TicketStore", "TicketGrant")),
        ("hosts", ("HostRecord", "HostStore")),
        ("pairing", ("PairingError", "PairingStore")),
        ("authorization", ("AuthorizationError", "AuthorizationFlow", "IdentityProvider")),
        ("enrollment_service", ("RedeemRefusal",)),
    ],
)
def test_access_exports_have_one_identity(owner: str, names: tuple[str, ...]) -> None:
    public = import_module("relaydeck.gateway.features.access.api")
    canonical = import_module(f"relaydeck.gateway.features.access.{owner}")
    for name in names:
        assert getattr(public, name) is getattr(canonical, name)


@pytest.mark.parametrize(
    ("owner", "names"),
    [
        ("leases", ("Attachment", "LeaseError", "LeaseRecord", "LeaseStore")),
        ("ring", ("RingStore", "StreamFanout")),
        ("sockets", ("SocketRegistry",)),
        ("cleanup", ("TunnelCleanup",)),
    ],
)
def test_terminal_exports_have_one_identity(owner: str, names: tuple[str, ...]) -> None:
    public = import_module("relaydeck.gateway.features.terminals.api")
    canonical = import_module(f"relaydeck.gateway.features.terminals.{owner}")
    for name in names:
        assert getattr(public, name) is getattr(canonical, name)


def test_backend_error_is_shared_by_production_and_fake() -> None:
    canonical = import_module("relaydeck.gateway.ports")
    production = import_module("relaydeck.gateway.app.backend")
    fake = import_module("relaydeck.devtools.fakes.gateway")
    assert production.BackendError is fake.BackendError is canonical.BackendError


@pytest.mark.parametrize(
    ("owner", "names"),
    [
        ("events", ("EventLogIndex",)),
        ("inventory", ("InventoryView",)),
        ("metadata_follower", ("MetadataFollower",)),
        ("metadata_streams", ("MetadataStreams",)),
    ],
)
def test_metadata_exports_have_one_identity(owner: str, names: tuple[str, ...]) -> None:
    public = import_module("relaydeck.gateway.features.metadata.api")
    canonical = import_module(f"relaydeck.gateway.features.metadata.{owner}")
    for name in names:
        assert getattr(public, name) is getattr(canonical, name)


@pytest.mark.parametrize(
    ("owner", "names"),
    [
        ("adapters.sqlite_store", ("PushStore",)),
        ("adapters.webpush_store", ("WebPushStore",)),
        ("adapters.webpush", ("EndpointRejected",)),
        ("model", ("PushDenied",)),
        ("service", ("ProviderPort",)),
    ],
)
def test_notification_exports_have_one_identity(owner: str, names: tuple[str, ...]) -> None:
    public = import_module("relaydeck.gateway.features.notifications.api")
    canonical = import_module(f"relaydeck.gateway.features.notifications.{owner}")
    for name in names:
        assert getattr(public, name) is getattr(canonical, name)


def test_notification_pump_and_worker_share_the_structural_store_port() -> None:
    from typing import get_type_hints

    from relaydeck.gateway.features.notifications.ports import OutboxStore
    from relaydeck.gateway.features.notifications.service import pump_outbox
    from relaydeck.gateway.features.notifications.worker import PushWorker

    assert get_type_hints(pump_outbox)["store"] is OutboxStore
    assert get_type_hints(PushWorker.__init__)["store"] is OutboxStore


def test_usage_configuration_values_share_their_canonical_identity() -> None:
    public = import_module("relaydeck.usage.api")
    canonical = import_module("relaydeck.usage.config")
    consumer = import_module("relaydeck.companion.config")
    for name in ("UsageConfig", "UsageLimitsConfig", "UsageSourceConfig"):
        assert getattr(public, name) is getattr(canonical, name) is getattr(consumer, name)


def test_git_read_serializer_is_the_public_canonical_function() -> None:
    public = import_module("relaydeck.gateway.features.git_read.api")
    canonical = import_module("relaydeck.gateway.features.git_read.service")
    assert public.file_entry is canonical.file_entry
    assert public.CURSOR_TTL_MS == canonical.CURSOR_TTL_MS


def test_usage_read_and_store_exports_have_one_identity() -> None:
    public = import_module("relaydeck.gateway.features.usage_read.api")
    service = import_module("relaydeck.gateway.features.usage_read.service")
    model = import_module("relaydeck.gateway.features.usage_read.model")
    assert public.UsageService is service.UsageService
    assert public.UsagePage is model.UsagePage
    assert public.UsageRefusal is model.UsageRefusal
    assert (
        import_module("relaydeck.usage.api").SqliteUsageStore
        is import_module("relaydeck.usage.store").SqliteUsageStore
    )
