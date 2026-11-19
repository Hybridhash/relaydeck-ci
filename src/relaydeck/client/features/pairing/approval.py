"""Pairing phases and safely quoted approval commands."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

PHASE_SETUP = "setup"
PHASE_READY = "ready"
PHASE_WAITING = "waiting"
PHASE_APPROVED = "approved"
PHASE_CONNECTING = "connecting"
PHASE_FAILED = "failed"

CLI = "relaydeck pairing"
OPEN_COMMAND = f"{CLI} open --ttl-seconds 120"

# Server-issued ids embedded in a shell command: strict allowlist.
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _valid_token(value: str) -> bool:
    return _TOKEN_RE.fullmatch(value) is not None


@dataclass(frozen=True)
class ConnectState:
    """Immutable Connect view model; the application maps real protocol states onto it."""

    endpoint: str = ""
    host_id: str = ""
    device_label: str = ""
    phase: str = PHASE_SETUP
    message: str = ""
    request_id: str = ""
    matching_code: str = ""
    fingerprint: str = ""
    scope: str = ""
    expiry: str = ""
    busy: bool = False


def valid_request(state: ConnectState) -> bool:
    """Both ids present and allowlisted: safe to display and quote."""
    return _valid_token(state.request_id) and _valid_token(state.matching_code)


def approve_command(state: ConnectState) -> str:
    """Documented approve command; substitutions are always shell-quoted. Safe ids render bare
    (shlex quotes only when needed).
    """
    rid = state.request_id or "<request-id>"
    code = state.matching_code or "<XXX-XXX>"
    return f"{CLI} approve --request {shlex.quote(rid)} --code {shlex.quote(code)}"
