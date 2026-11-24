"""Configured numeric-usage sources, limits providers and collection options."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UsageSourceConfig:
    kind: str
    format: str
    root: Path
    display_name: str
    limits_format: str | None = None
    limits_settings: Path | None = None
    limits_cache: Path | None = None


@dataclass(frozen=True)
class UsageLimitsConfig:
    """One owner-configured `[[usage.limits]]` entry for the limits plane."""

    kind: str
    format: str
    command: tuple[str, ...]
    min_interval_seconds: int = 300
    timeout_seconds: int = 15
    enabled: bool = True
    # manual-only blocks are never time-due (min_interval ignored).
    on_demand: bool = False
    # An on-demand block is also read in the background, at most this often and only when its tool
    # was used after the last reading; 0 = manual only.
    auto_interval_seconds: int = 0


@dataclass(frozen=True)
class UsageConfig:
    timezone: str
    sources: tuple[UsageSourceConfig, ...]
    price_tables: tuple[Path, ...] = ()
    retention_days: int = 90
    limits: tuple[UsageLimitsConfig, ...] = ()
    inferred_linking: bool = True
