"""Immediate, offline, content-free installation/cache evidence; never writes."""

from __future__ import annotations

import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from relaydeck.usage.hooks.setup import (
    HOOK_FILENAME,
    HookSetupCheck,
    HookSource,
    cache_state,
    default_hook_path,
)


def doctor_hook(
    settings: Path | None,
    cache: Path | None,
    *,
    hooks_dir: Path | None = None,
    interpreter: Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now = int(time.time() * 1000) if now_ms is None else now_ms
    script = (hooks_dir or default_hook_path().parent) / HOOK_FILENAME
    # New checker per invocation: immediate, independent of runtime's 60s throttle.
    check = HookSetupCheck(settings, cache, script, interpreter=interpreter)
    return {
        "installation": check.check(now),
        "cache": {"state": cache_state(cache, now), "checked_ms": now},
    }


def _diagnosis(source: HookSource, hooks_dir: Path | None, now: int | None) -> dict[str, Any]:
    from relaydeck.usage.hooks.repair import diagnose_source

    return diagnose_source(source, hooks_dir=hooks_dir, now_ms=now).public()


def doctor_sources(
    sources: Iterable[HookSource],
    *,
    hooks_dir: Path | None = None,
    now_ms: int | None = None,
) -> list[dict[str, Any]]:
    return [
        {
            "kind": source.kind,
            "format": source.format,
            **doctor_hook(
                source.limits_settings, source.limits_cache, hooks_dir=hooks_dir, now_ms=now_ms
            ),
            "diagnosis": _diagnosis(source, hooks_dir, now_ms),
        }
        for source in sources
        if source.limits_format == "json-statusline-limits-v1"
    ]
