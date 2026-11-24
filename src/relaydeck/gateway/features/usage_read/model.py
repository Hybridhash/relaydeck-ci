"""Immutable usage page data and bounded refusals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class UsagePage:
    payload: bytes
    status: Literal[200, 304] = 200
    etag: str | None = None


@dataclass(frozen=True)
class UsageRefusal:
    code: Literal["usage_unavailable", "invalid_cursor", "usage_busy"]
    status: Literal[400, 503]
    retry_after_s: int | None = None


UsageResult = UsagePage | UsageRefusal
