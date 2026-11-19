"""The shared seams moved to neutral packages and the old names that stay are the same objects."""

from __future__ import annotations

import importlib
import importlib.util
import logging

import pytest

import relaydeck.contracts.storage as storage_contracts
from relaydeck.contracts import enrollment, protocol, storage
from relaydeck.host.observability import request_context
from relaydeck.host.security import enrollment as proofs

SEED = bytes(range(32))
PROOF_MESSAGE = (
    "72656c61796465636b2d656e726f6c6c6d656e742d763100656e725f3100686f6d652d6d6163"
    "0003a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8"
)
SIGNATURE = (
    "7ba4a2a16f95ee4f775db07d31a04faa058648c7835efdd223dc4b635f5a303056e2a024b6f0d40d"
    "d0bd214d24f00d64b63959ee3c9b7c435c9692cbab32a105"
)
PUBLIC_LINE = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAOhB7/zzhC+HXDdGOdLwJln5NYwm6UNXx3chmQSVTG4"
FINGERPRINT = "SHA256:lbmsoA0yIEcEiVDRnMWuzm+nV+3ZEEpVIURqFoeSspg"
QR = (
    "RELAYDECK-ENROLL:1:eyJndyI6Imh0dHBzOi8vZ3cuZXhhbXBsZSIsImlkIjoiZW5yXzEiLCJjb2RlIjoiQUJDREVGR0hK"
    "S01OIiwiZXhwIjoiMjAzMC0wMS0wMVQwMDowMDowMFoiLCJ2IjoxfQ"
)


def test_the_credential_store_keeps_its_old_name_as_the_same_class() -> None:
    from relaydeck.host.persistence import sqlite_store as persistence

    assert storage_contracts.CredentialStore is storage.CredentialStore
    assert persistence.CredentialStore is storage.CredentialStore
    assert storage.CredentialStore.__module__ == "relaydeck.contracts.storage"


def test_the_request_context_is_one_object_under_every_name() -> None:
    from relaydeck.gateway.http import errors
    from relaydeck.gateway.http.middleware import request_id
    from relaydeck.host.observability import logging as logging_setup

    assert request_id.RequestIdFilter is request_context.RequestIdFilter
    assert request_id.current_request_id is request_context.current_request_id
    assert logging_setup.RequestIdFilter is request_context.RequestIdFilter
    assert errors.current_request_id is request_context.current_request_id
    assert request_context.current.name == "relaydeck_request_id"
    assert request_context.current.get() == "-"


def test_the_filter_stamps_the_context_value_on_a_record() -> None:
    record = logging.LogRecord("n", logging.INFO, __file__, 1, "m", None, None)
    token = request_context.current.set("req-12345678")
    try:
        assert request_context.RequestIdFilter().filter(record) is True
        assert record.request_id == "req-12345678"  # type: ignore[attr-defined]
        assert request_context.current_request_id() == "req-12345678"
    finally:
        request_context.current.reset(token)


def test_the_expected_protocol_is_one_value_everywhere() -> None:
    from relaydeck.connector import herdr
    from relaydeck.gateway.http import hosts

    assert protocol.EXPECTED_PROTOCOL == 22
    assert herdr.EXPECTED_PROTOCOL is protocol.EXPECTED_PROTOCOL
    assert hosts.EXPECTED_PROTOCOL is protocol.EXPECTED_PROTOCOL


@pytest.mark.parametrize(
    "old",
    [
        "relaydeck.companion.private",
        "relaydeck.companion.private_files",
        "relaydeck.companion.trusted",
        "relaydeck.connector.pubkey",
    ],
)
def test_the_moved_modules_leave_no_second_copy_behind(old: str) -> None:
    assert importlib.util.find_spec(old) is None


def test_the_host_modules_are_the_ones_the_importers_use() -> None:
    from relaydeck.companion.inspection import prereqs
    from relaydeck.companion.updates import schedule
    from relaydeck.connector import enrollment as connector_enrollment
    from relaydeck.git import runner
    from relaydeck.host import executables
    from relaydeck.host.files import descriptors, private

    assert connector_enrollment.PrivateFileError is descriptors.PrivateFileError
    assert schedule.write_json is private.write_json
    assert schedule.read_private_path is descriptors.read_private_path
    assert runner.trusted_executable is executables.trusted_executable
    assert prereqs.trusted_executable is executables.trusted_executable


def test_the_enrollment_proof_message_is_byte_for_byte_unchanged() -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = Ed25519PrivateKey.from_private_bytes(SEED).public_key().public_bytes_raw()
    assert enrollment.proof_message("enr_1", "home-mac", raw).hex() == PROOF_MESSAGE
    assert enrollment.PROOF_DOMAIN == b"relaydeck-enrollment-v1"


def test_the_enrollment_signature_and_key_forms_are_unchanged() -> None:
    signature = proofs.sign_proof(SEED, "enr_1", "home-mac")
    assert signature.hex() == SIGNATURE
    assert proofs.verify_proof(PUBLIC_LINE, "enr_1", "home-mac", signature) is True
    assert proofs.verify_proof(PUBLIC_LINE, "enr_1", "other-mac", signature) is False
    assert enrollment.fingerprint_of_openssh(PUBLIC_LINE) == FINGERPRINT


def test_the_enrollment_qr_payload_is_unchanged() -> None:
    payload = enrollment.build_qr_payload(
        "https://gw.example", "enr_1", "ABCDEFGHJKMN", "2030-01-01T00:00:00Z"
    )
    assert payload == QR
    assert enrollment.parse_qr_payload(QR)["code"] == "ABCDEFGHJKMN"


def test_the_signing_side_is_the_only_one_that_needs_cryptography() -> None:
    import ast
    import inspect

    source = inspect.getsource(enrollment)
    imported = {
        n.module for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ImportFrom) and n.module
    } | {a.name for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Import) for a in n.names}
    assert not any(name.startswith("cryptography") for name in imported)
