"""Standalone usage-hook CLI; the companion CLI wires it separately.

python -m relaydeck.companion.setup.usage_cli --config <private TOML> doctor
Install requires --install-limits-hook; --yes from Serve is never consulted.
"""

from __future__ import annotations

import argparse
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from relaydeck.companion.setup.probes import load_probe_targets
from relaydeck.usage import api as fs
from relaydeck.usage.api import MAX_SOURCES, READERS, add_actions, run_usage


@dataclass(frozen=True)
class ConfiguredHookSource:
    kind: str
    format: str
    limits_format: str | None
    limits_settings: Path | None
    limits_cache: Path | None


def _configured_path(row: dict[str, Any], key: str, *, required: bool = False) -> Path | None:
    value = row.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value:
        raise fs.HookFileError("invalid")
    result = Path(value)
    fs.absolute(result)
    if not result.name:
        raise fs.HookFileError("invalid")
    return result


def load_hook_sources(path: Path) -> tuple[ConfiguredHookSource, ...]:
    """Read only hook metadata; full config loading may invoke auto-origin probes.
    Transcript-root/infra readiness is deliberately independent of hook diagnosis.
    """
    from relaydeck.companion.config import USAGE_KEYS, USAGE_SOURCE_KEYS, agent_kinds_from

    with fs.directory(path.parent) as fd:
        image = fs.read(fd, path.name, private=True)
    if image.raw is None:
        raise fs.HookFileError("invalid")
    body = tomllib.loads(image.raw.decode())
    kinds = agent_kinds_from(body.get("agent_kinds", []))
    usage = body.get("usage")
    if usage is None:
        return ()
    if not isinstance(usage, dict) or set(usage) - USAGE_KEYS:
        raise fs.HookFileError("invalid")
    rows = usage.get("sources")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_SOURCES:
        raise fs.HookFileError("invalid")
    sources: list[ConfiguredHookSource] = []
    keys: set[tuple[str, str]] = set()
    caches: set[Path] = set()
    limits_kinds: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) - USAGE_SOURCE_KEYS:
            raise fs.HookFileError("invalid")
        kind, format_id = row.get("kind"), row.get("format")
        if (
            not isinstance(kind, str)
            or kind not in kinds
            or not isinstance(format_id, str)
            or format_id not in READERS
            or (kind, format_id) in keys
        ):
            raise fs.HookFileError("invalid")
        keys.add((kind, format_id))
        limits = row.get("limits_format")
        if limits != "json-statusline-limits-v1":
            if "limits_settings" in row or "limits_cache" in row:
                raise fs.HookFileError("invalid")
            continue

        settings = _configured_path(row, "limits_settings")
        cache = _configured_path(row, "limits_cache", required=True)
        assert cache is not None  # noqa: S101
        if kind in limits_kinds or cache in caches:
            raise fs.HookFileError("invalid")
        limits_kinds.add(kind)
        caches.add(cache)
        sources.append(ConfiguredHookSource(kind, format_id, limits, settings, cache))
    return tuple(sources)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Explicit owner-configured TOML")
    add_actions(parser.add_subparsers(dest="usage_action", required=True))
    args = parser.parse_args(argv)
    return run_configured_usage(args)


def run_configured_usage(args: argparse.Namespace) -> int:
    """Companion alias must call this BEFORE loading full config/preflight/secrets."""
    try:
        sources = load_hook_sources(Path(args.config).expanduser().absolute())
    except (OSError, ValueError, TypeError, RecursionError, fs.HookFileError):
        print(json.dumps({"state": "config_unavailable"}))
        return 2
    return run_usage(args, sources, probes=load_probe_targets(Path(args.config).expanduser()))


if __name__ == "__main__":
    raise SystemExit(main())
