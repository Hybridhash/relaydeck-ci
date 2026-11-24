"""Expose validated question handoff and scoped prompt construction."""

from relaydeck.client.features.ask_agent.handoff import AskHandoff, Identity, PendingAsk
from relaydeck.client.features.ask_agent.model import (
    DEFAULT_QUESTION,
    PRESETS,
    AskScope,
    build_prompt,
    eligibility,
    short_oid,
)
from relaydeck.client.features.ask_agent.target import target, validate

__all__ = [
    "AskHandoff",
    "AskScope",
    "DEFAULT_QUESTION",
    "Identity",
    "PRESETS",
    "PendingAsk",
    "build_prompt",
    "eligibility",
    "short_oid",
    "target",
    "validate",
]
