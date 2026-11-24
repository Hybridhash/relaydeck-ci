"""Redeeming a connector invitation: the checks and the registry write, shared by the HTTP route and
the in-process enrollment `relaydeck setup` does while the companion is stopped."""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from relaydeck.contracts.pubkeys import openssh_to_raw, raw_to_openssh
from relaydeck.contracts.schemas import EnrollmentResult
from relaydeck.gateway.features.access.hosts import HostStore
from relaydeck.gateway.features.access.identity import EnrollmentStore
from relaydeck.host.security.enrollment import verify_proof


@dataclass(frozen=True)
class RedeemRefusal:
    """`code` and `message` are the gateway error to answer with; `reason` goes in its details."""

    code: str
    message: str
    reason: str | None = None


async def redeem(
    enrollments: EnrollmentStore,
    hosts: HostStore,
    enrollment_id: str,
    code: str,
    host_id: str,
    pubkey: str,
    proof: str,
    now_ms: int,
    revoke: Callable[[str], Awaitable[Any]],
) -> EnrollmentResult | RedeemRefusal:
    invitation = enrollments.attempt(enrollment_id, code, now_ms)
    if invitation is None:
        return RedeemRefusal("not_found", "no such enrollment")
    try:
        raw = openssh_to_raw(pubkey)
    except ValueError:
        return RedeemRefusal("forbidden", "bad connector pubkey", "pubkey")
    if raw_to_openssh(raw) != invitation.pubkey:
        return RedeemRefusal("forbidden", "key mismatch", "fingerprint")
    try:
        padded = proof + "=" * (-len(proof) % 4)
        signature = base64.urlsafe_b64decode(padded)
    except ValueError:
        return RedeemRefusal("forbidden", "bad proof", "proof")
    if len(signature) != 64 or not verify_proof(
        invitation.pubkey, enrollment_id, host_id, signature
    ):
        return RedeemRefusal("forbidden", "bad proof", "proof")
    owner = hosts.owner(host_id)
    if owner is not None and owner != invitation.account:
        return RedeemRefusal("not_found", "host unavailable")
    if not enrollments.claim(enrollment_id):
        return RedeemRefusal("not_found", "no such enrollment")
    if hosts.record_for_host(host_id) is not None:
        await revoke(host_id)
    token = hosts.enroll(
        host_id,
        invitation.host_label,
        invitation.account,
        invitation.pubkey,
        invitation.fingerprint,
    )
    return {"host_id": host_id, "host_token": token, "fingerprint": invitation.fingerprint}
