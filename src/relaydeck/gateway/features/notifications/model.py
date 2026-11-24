"""Content-free notification shapes, eligibility errors, retention limits and retry policy."""

from __future__ import annotations

from dataclasses import dataclass

# TLDs and names that are never valid VAPID subjects (placeholders or local-only names).
_UNROUTABLE_SUFFIXES = (".invalid", ".localhost", ".local", ".test")


def valid_vapid_subject(value: object) -> bool:
    """A mailto: address on a routable domain, or an https: URL. Placeholder domains are refused."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or any(char.isspace() for char in value)
    ):
        return False
    if value.startswith("https://"):
        host = value.removeprefix("https://").split("/", 1)[0].split("?", 1)[0].lower()
        return "@" not in host and "." in host and not host.endswith(_UNROUTABLE_SUFFIXES)
    if not value.startswith("mailto:"):
        return False
    address = value.removeprefix("mailto:")
    local, at, domain = address.partition("@")
    if not at or not local or "@" in domain or "." not in domain:
        return False
    domain = domain.lower()
    if domain == "localhost" or domain.endswith(_UNROUTABLE_SUFFIXES):
        return False
    return all(label and not label.startswith("-") for label in domain.split("."))


# Exact wire key set. Asserted equal to the extension guard's
# ALLOWED_KEYS by the lock-screen audit test (gateway stays stdlib-only
# so it never inherits the extension's flet dependency).
HINT_KEYS = frozenset(
    {
        "notification_id",
        "device_ref",
        "host_ref",
        "session_ref",
        "event_ref",
        "category",
        "created_at",
        "expires_at",
    }
)
HINT_CATEGORIES = frozenset({"attention", "completion"})
# the choices a device can make, mapped onto the stored category names. The default
# for a device without a row is every category.
PREF_UI_TO_CATEGORY = {"blocked": "attention", "done": "completion"}
PREF_CATEGORY_TO_UI = {value: key for key, value in PREF_UI_TO_CATEGORY.items()}
PLATFORMS = frozenset({"android"})

LINK_PURPOSE_OPEN = "open-event"
LINK_TOKEN_PREFIX = "rdl_"  # noqa: S105 - public prefix

DEFAULT_PUSH_TTL_MS = 24 * 60 * 60 * 1000
TERMINAL_KEEP_MS = 24 * 60 * 60 * 1000
LINK_KEEP_MS = 24 * 60 * 60 * 1000
DEVICE_TOMBSTONE_MS = 30 * 24 * 60 * 60 * 1000
BACKOFF_BASE_MS = 30_000
BACKOFF_CAP_MS = 15 * 60_000

MAX_DEVICE_ID_CHARS = 128
MAX_TOKEN_CHARS = 4096
MAX_EVENT_ID_CHARS = 256
MAX_HOST_ID_CHARS = 128
MAX_SESSION_ID_CHARS = 128
MAX_VALUE_LEN = 512


class PushDenied(Exception):
    """Eligibility/validation refusal with a gateway envelope code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DeviceRecord:
    account: str
    device_id: str
    platform: str
    token: str
    device_ref: str
    status: str  # "active" | "tombstoned"
    replaced_by: str
    registered_ms: int
    updated_ms: int


def backoff_ms(attempts: int) -> int:
    """Exponential backoff after `attempts` failures (attempts >= 1):
    30s, 60s, 120s, ... capped at 15 minutes."""
    if attempts < 1:
        return 0
    return min(BACKOFF_CAP_MS, BACKOFF_BASE_MS * int(2 ** (attempts - 1)))
