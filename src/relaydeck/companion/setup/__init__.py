"""Run explicit, idempotent setup steps after read-only prerequisites. Require consent for
changes and preserve existing configuration through the backed-up, receipted writer.
"""

from __future__ import annotations

from relaydeck.companion import config_edit
from relaydeck.companion.adapters import tailscale
from relaydeck.companion.config import load_config
from relaydeck.companion.inspection import prereqs
from relaydeck.companion.setup import files, hooks, prerequisites, usage_steps
from relaydeck.companion.setup.context import SetupContext, WebFetchError
from relaydeck.companion.setup.files import (
    DEFAULT_HTTPS_PORT,
    DEFAULT_LOCAL_PORT,
    RECEIPT_DIR,
    RESERVED_HTTPS_PORTS,
    ApplyResult,
    apply_text,
    apply_text_result,
    choose_https_port,
    ensure_config,
    template_text,
)
from relaydeck.companion.setup.hooks import HOOK_FAILURE_GUIDANCE, limits_hook_setup
from relaydeck.companion.setup.limits import LimitsCandidate, limits_candidates, offer_limits
from relaydeck.companion.setup.prerequisites import STOPPED_LINE, detect_report, preflight
from relaydeck.companion.setup.runner import default_steps, run_setup, run_steps
from relaydeck.companion.setup.sources import (
    SOURCE_HISTORY_DEFAULT_DAYS,
    SourceCandidate,
    offer_sources,
    source_candidates,
    undo_sources,
)
from relaydeck.companion.setup.steps import SetupReport

_limits_hook_setup = limits_hook_setup

__all__ = [
    "DEFAULT_HTTPS_PORT",
    "DEFAULT_LOCAL_PORT",
    "HOOK_FAILURE_GUIDANCE",
    "RECEIPT_DIR",
    "RESERVED_HTTPS_PORTS",
    "SOURCE_HISTORY_DEFAULT_DAYS",
    "STOPPED_LINE",
    "ApplyResult",
    "LimitsCandidate",
    "SetupContext",
    "SetupReport",
    "SourceCandidate",
    "WebFetchError",
    "apply_text",
    "apply_text_result",
    "choose_https_port",
    "config_edit",
    "files",
    "hooks",
    "prerequisites",
    "usage_steps",
    "default_steps",
    "detect_report",
    "ensure_config",
    "limits_candidates",
    "limits_hook_setup",
    "load_config",
    "offer_limits",
    "offer_sources",
    "preflight",
    "prereqs",
    "run_setup",
    "run_steps",
    "source_candidates",
    "tailscale",
    "template_text",
    "undo_sources",
]
