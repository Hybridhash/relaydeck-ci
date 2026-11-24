"""Public entry for local usage collection, limits, hooks and linking."""

from relaydeck.usage.collector import UsageCollector
from relaydeck.usage.config import (
    UsageConfig,
    UsageLimitsConfig,
    UsageSourceConfig,
)
from relaydeck.usage.contracts import (
    MAX_KEY_CHARS,
    MAX_QUERY_DAYS,
    MAX_QUERY_ROWS,
    MAX_SOURCES,
    RETENTION_DAYS,
    Coverage,
    MappingGap,
    PaneCwdObservation,
    SessionMapping,
    UsageQuery,
    UsageSource,
    UsageUnavailable,
)
from relaydeck.usage.hooks.cli import (
    add_actions,
    add_usage_parser,
    run_usage,
)
from relaydeck.usage.hooks.files import (
    HookFileError,
    absolute,
    directory,
    read,
)
from relaydeck.usage.hooks.install import (
    install_hook,
    upgrade_outdated_hook,
)
from relaydeck.usage.hooks.repair import (
    diagnose_source,
    limits_sources,
)
from relaydeck.usage.hooks.setup import (
    HOOK_FILENAME,
    HookSetupCheck,
    HookSource,
    default_hook_path,
)
from relaydeck.usage.limits import LIMITS_READERS
from relaydeck.usage.limits_plane import (
    PROVIDERS,
    STATE_FILENAME,
)
from relaydeck.usage.limits_plane.contracts import (
    LimitsProvider,
    ProviderSpec,
    RefreshDecision,
    TuiBundle,
    TuiRecipe,
    tui_recipe_from,
)
from relaydeck.usage.limits_plane.probe import (
    NOT_INSTALLED,
    ProbeTarget,
    Spawn,
    offer_trust,
    on_path,
    run_in_terminal,
)
from relaydeck.usage.limits_plane.providers.tui_usage_pane import (
    FORMAT_ID,
    recover_stale_tabs,
)
from relaydeck.usage.limits_plane.refresh_state import (
    RefreshState,
)
from relaydeck.usage.limits_plane.scheduler import (
    LimitsScheduler,
    run_bounded_command,
)
from relaydeck.usage.limits_plane.trust_store import (
    ensure_probe_folder,
    probe_folder,
)
from relaydeck.usage.linking.digests import (
    cwd_digest,
    load_or_create_link_key,
    repo_fingerprint,
)
from relaydeck.usage.linking.process import (
    PANES_PER_PASS,
    PASS_WALL_S,
    TICK_MS,
    ProbeFn,
    ProcObservation,
    ProcTarget,
    default_probe,
)
from relaydeck.usage.pricing import (
    PriceTable,
    load_price_table,
)
from relaydeck.usage.readers import (
    READERS,
)
from relaydeck.usage.readers._files import (
    session_text,
)
from relaydeck.usage.store import (
    SqliteUsageStore,
)

__all__ = [
    "Coverage",
    "FORMAT_ID",
    "HOOK_FILENAME",
    "HookFileError",
    "HookSetupCheck",
    "HookSource",
    "LIMITS_READERS",
    "LimitsProvider",
    "LimitsScheduler",
    "MAX_KEY_CHARS",
    "MAX_QUERY_DAYS",
    "MAX_QUERY_ROWS",
    "MAX_SOURCES",
    "MappingGap",
    "NOT_INSTALLED",
    "PANES_PER_PASS",
    "PASS_WALL_S",
    "PROVIDERS",
    "PaneCwdObservation",
    "PriceTable",
    "ProbeFn",
    "ProbeTarget",
    "ProcObservation",
    "ProcTarget",
    "ProviderSpec",
    "READERS",
    "RETENTION_DAYS",
    "RefreshDecision",
    "RefreshState",
    "STATE_FILENAME",
    "SessionMapping",
    "Spawn",
    "SqliteUsageStore",
    "TICK_MS",
    "TuiBundle",
    "TuiRecipe",
    "UsageCollector",
    "UsageConfig",
    "UsageLimitsConfig",
    "UsageQuery",
    "UsageSource",
    "UsageSourceConfig",
    "UsageUnavailable",
    "absolute",
    "add_actions",
    "add_usage_parser",
    "cwd_digest",
    "default_hook_path",
    "default_probe",
    "diagnose_source",
    "directory",
    "ensure_probe_folder",
    "install_hook",
    "limits_sources",
    "load_or_create_link_key",
    "load_price_table",
    "offer_trust",
    "on_path",
    "probe_folder",
    "read",
    "recover_stale_tabs",
    "repo_fingerprint",
    "run_bounded_command",
    "run_in_terminal",
    "run_usage",
    "session_text",
    "tui_recipe_from",
    "upgrade_outdated_hook",
]
