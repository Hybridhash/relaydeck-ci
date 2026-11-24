"""Connector enrollment routes: create an invitation, poll it, and redeem it with a key proof."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from relaydeck.contracts import paths
from relaydeck.contracts.enrollment import build_qr_payload
from relaydeck.contracts.pubkeys import fingerprint_of_raw, openssh_to_raw, raw_to_openssh
from relaydeck.contracts.schemas import (
    Enrollment,
    EnrollmentCreate,
    EnrollmentRedeem,
    EnrollmentResult,
    EnrollmentStatus,
    SchemaError,
    validate,
)
from relaydeck.gateway.features.access import api as enrollment_service
from relaydeck.gateway.http.helpers import NO_STORE

if TYPE_CHECKING:
    from relaydeck.gateway.features.access.api import Session
    from relaydeck.gateway.http.ports import EnrollmentsCapabilities


def install_enrollments(
    app: FastAPI,
    state: EnrollmentsCapabilities,
    current_session: Callable[[Request], Session | None],
    *,
    error_response: Callable[..., JSONResponse],
    revoke_host_and_close: Callable[[str], Awaitable[tuple[bool, int, int]]],
    utc_z: Callable[[int], str],
) -> None:
    @app.post(paths.ENROLLMENTS, status_code=201)
    async def create_enrollment(request: Request) -> JSONResponse:
        session = current_session(request)
        if session is None:
            return error_response("unauthenticated", "missing or expired token")
        if "hosts.enroll" not in session.capabilities:
            return error_response("unsupported_capability", "hosts.enroll required")
        try:
            body = await request.json()
            validate("relaydeck/gateway/v1/enrollment-create", body)
            create = cast(EnrollmentCreate, body)
        except (ValueError, SchemaError):
            return error_response(
                "forbidden", "malformed enrollment", details={"reason": "malformed"}
            )
        try:
            raw = openssh_to_raw(create["pubkey"])
        except ValueError:
            return error_response("forbidden", "bad connector pubkey", details={"reason": "pubkey"})
        canonical = raw_to_openssh(raw)
        invitation = state.enrollments.create(
            session.account,
            create["host_label"],
            canonical,
            fingerprint_of_raw(raw),
            state.now_ms(),
            create.get("ttl_seconds", 600) * 1000,
        )
        expires_at = utc_z(invitation.expires_ms)
        out: Enrollment = {
            "id": invitation.id,
            "code": invitation.code,
            "qr": build_qr_payload(state.public_origin, invitation.id, invitation.code, expires_at),
            "fingerprint": invitation.fingerprint,
            "expires_at": expires_at,
            "host_label": invitation.host_label,
        }
        validate("relaydeck/gateway/v1/enrollment", out)
        return JSONResponse(status_code=201, content=out, headers=NO_STORE)

    @app.get(paths.ENROLLMENT)
    async def enrollment_status(enrollment_id: str, request: Request) -> JSONResponse:
        session = current_session(request)
        if session is None:
            return error_response("unauthenticated", "missing or expired token")
        status = state.enrollments.status(enrollment_id, session.account, state.now_ms())
        if status is None:
            return error_response("not_found", "no such enrollment")
        out: EnrollmentStatus = {"id": enrollment_id, "status": status}
        validate("relaydeck/gateway/v1/enrollment-status", out)
        return JSONResponse(out, headers=NO_STORE)

    @app.post(paths.ENROLLMENT_REDEEM)
    async def redeem_enrollment(enrollment_id: str, request: Request) -> JSONResponse:
        try:
            body = await request.json()
            validate("relaydeck/gateway/v1/enrollment-redeem", body)
            redeem = cast(EnrollmentRedeem, body)
        except (ValueError, SchemaError):
            return error_response("forbidden", "malformed redeem", details={"reason": "malformed"})
        outcome = await enrollment_service.redeem(
            state.enrollments,
            state.hosts,
            enrollment_id,
            redeem["code"],
            redeem["host_id"],
            redeem["pubkey"],
            redeem["proof"],
            state.now_ms(),
            revoke_host_and_close,
        )
        if isinstance(outcome, enrollment_service.RedeemRefusal):
            return error_response(
                outcome.code,
                outcome.message,
                **({"details": {"reason": outcome.reason}} if outcome.reason else {}),
            )
        out: EnrollmentResult = outcome
        validate("relaydeck/gateway/v1/enrollment-result", out)
        return JSONResponse(out, headers=NO_STORE)
