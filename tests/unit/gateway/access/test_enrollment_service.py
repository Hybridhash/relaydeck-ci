"""The redeem policy shared by the HTTP route and setup's in-process enrollment."""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import pytest

from relaydeck.contracts.pubkeys import fingerprint_of_raw, openssh_to_raw
from relaydeck.gateway.features.access.enrollment_service import RedeemRefusal, redeem
from relaydeck.gateway.features.access.hosts import HostStore
from relaydeck.gateway.features.access.identity import EnrollmentStore
from relaydeck.host.security.enrollment import generate, sign_proof

NOW = 1_000_000


class Rig:
    def __init__(self, account: str = "owner") -> None:
        self.seed, self.public = generate()
        self.fingerprint = fingerprint_of_raw(openssh_to_raw(self.public))
        self.enrollments = EnrollmentStore()
        self.hosts = HostStore()
        self.invitation = self.enrollments.create(
            account, "home", self.public, self.fingerprint, NOW, 60_000
        )
        self.revoked: list[str] = []

    def proof(self, host_id: str = "home-mac", seed: bytes | None = None) -> str:
        raw = sign_proof(seed or self.seed, self.invitation.id, host_id)
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def redeem(self, **over: Any) -> Any:
        async def revoke(host_id: str) -> None:
            self.revoked.append(host_id)
            self.hosts.revoke(host_id)

        args: dict[str, Any] = {
            "enrollment_id": self.invitation.id,
            "code": self.invitation.code,
            "host_id": "home-mac",
            "pubkey": self.public,
            "proof": self.proof(),
            "now_ms": NOW + 1,
        }
        args.update(over)
        return asyncio.run(
            redeem(
                self.enrollments,
                self.hosts,
                args["enrollment_id"],
                args["code"],
                args["host_id"],
                args["pubkey"],
                args["proof"],
                args["now_ms"],
                revoke,
            )
        )


def test_a_valid_redeem_issues_a_token_bound_to_the_key() -> None:
    rig = Rig()
    result = rig.redeem()
    assert result["host_id"] == "home-mac" and result["fingerprint"] == rig.fingerprint
    record = rig.hosts.lookup(result["host_token"])
    assert record is not None and (record.account, record.pubkey) == ("owner", rig.public)
    assert rig.revoked == []


@pytest.mark.parametrize(
    ("over", "code", "reason"),
    [
        ({"enrollment_id": "enr_other"}, "not_found", None),
        ({"code": "AAAAAAAAAAAA"}, "not_found", None),
        ({"now_ms": NOW + 120_000}, "not_found", None),
        ({"pubkey": "ssh-rsa AAAA"}, "forbidden", "pubkey"),
        ({"proof": "@@@"}, "forbidden", "proof"),
        ({"proof": "AAAA"}, "forbidden", "proof"),
        ({"host_id": "other-mac"}, "forbidden", "proof"),
    ],
)
def test_each_refusal_carries_the_route_code_and_reason(
    over: dict[str, Any], code: str, reason: str | None
) -> None:
    rig = Rig()
    result = rig.redeem(**over)
    assert isinstance(result, RedeemRefusal)
    assert (result.code, result.reason) == (code, reason)
    assert rig.hosts.hosts() == ()


def test_a_key_that_is_not_the_invited_one_is_a_fingerprint_mismatch() -> None:
    rig = Rig()
    seed, public = generate()
    result = rig.redeem(pubkey=public, proof=rig.proof(seed=seed))
    assert isinstance(result, RedeemRefusal)
    assert (result.code, result.reason) == ("forbidden", "fingerprint")


def test_an_invitation_is_single_use() -> None:
    rig = Rig()
    assert not isinstance(rig.redeem(), RedeemRefusal)
    again = rig.redeem()
    assert isinstance(again, RedeemRefusal) and again.code == "not_found"


def test_a_host_owned_by_another_account_is_unavailable() -> None:
    rig = Rig(account="owner")
    rig.hosts.enroll("home-mac", "x", "someone-else", "ssh-ed25519 AAAA", "SHA256:x")
    result = rig.redeem()
    assert isinstance(result, RedeemRefusal) and result.message == "host unavailable"


def test_an_existing_record_is_revoked_through_the_callback_before_the_new_token() -> None:
    rig = Rig()
    old = rig.hosts.enroll("home-mac", "home", "owner", rig.public, rig.fingerprint)
    result = rig.redeem()
    assert rig.revoked == ["home-mac"]
    assert rig.hosts.lookup(old) is None and rig.hosts.lookup(result["host_token"]) is not None


def test_five_wrong_codes_burn_the_invitation() -> None:
    rig = Rig()
    for _ in range(5):
        rig.redeem(code="AAAAAAAAAAAA")
    assert isinstance(rig.redeem(), RedeemRefusal)
