"""Public feature entries expose declared canonical names without wrappers."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

EXPECTED = {
    "relaydeck.client.features.management.api": ["HostsSource", "ManagementController"],
    "relaydeck.client.features.pairing.api": [
        "ConnectState",
        "OPEN_COMMAND",
        "PHASE_APPROVED",
        "PHASE_CONNECTING",
        "PHASE_FAILED",
        "PHASE_READY",
        "PHASE_SETUP",
        "PHASE_WAITING",
        "PairingFlow",
        "approve_command",
        "discard_pending",
        "valid_request",
    ],
    "relaydeck.client.features.rename.api": [
        "RenameSource",
        "card_label",
        "shown_title",
        "space_label",
    ],
    "relaydeck.client.features.update_notice.api": ["UpdateNotice", "dot_visible"],
    "relaydeck.client.features.usage.api": [
        "ALL",
        "Attention",
        "Bucket",
        "Chart",
        "Counts",
        "CoverageEntry",
        "Filter",
        "Kind",
        "LimitWindow",
        "LimitsRefresh",
        "NEAR_LIMIT_PERCENT",
        "ProviderLimits",
        "RANK_COMPACT",
        "RANK_REGULAR",
        "Row",
        "STATE_CAPACITY",
        "STATE_DISABLED",
        "STATE_LOADING",
        "STATE_NO_PROVIDERS",
        "STATE_OFFLINE",
        "STATE_PAGE_TOO_LARGE",
        "STATE_READY",
        "STATE_UNAVAILABLE",
        "STATUS_CONFLICT",
        "STATUS_LOADING",
        "STATUS_NEAR_LIMIT",
        "STATUS_NOT_REPORTED",
        "STATUS_NO_READING",
        "STATUS_OK",
        "STATUS_READING_UNAVAILABLE",
        "STATUS_SETUP_NEEDED",
        "STATUS_STALE",
        "STATUS_UNMANAGED",
        "STATUS_WINDOW_ENDED",
        "UNLINKED",
        "Usage",
        "UsageFetch",
        "UsagePage",
        "as_of",
        "attention",
        "chart",
        "console_card",
        "counts",
        "credit_chart_page",
        "credit_space_buckets",
        "credits_by_kind",
        "credits_chart_text",
        "credits_text",
        "date_range",
        "filtered_rows",
        "host_day",
        "latest_limit",
        "limits",
        "money",
        "monogram",
        "providers",
        "rank",
        "relative",
        "reset",
        "screen_state",
        "share",
        "space_buckets",
        "tokens_chart_text",
        "tokens_text",
        "totals",
        "usage_of",
        "window_bounds",
        "window_label",
        "zone_label",
    ],
    "relaydeck.client.features.rename.ui": ["use_rename_flow"],
    "relaydeck.client.features.usage.ui": ["now_ms", "use_usage_feed"],
}
CANONICAL = {
    "relaydeck.client.features.management.api": {
        "HostsSource": "relaydeck.client.features.management.source",
        "ManagementController": "relaydeck.client.features.management.controller",
    },
    "relaydeck.client.features.pairing.api": {
        "ConnectState": "relaydeck.client.features.pairing.approval",
        "OPEN_COMMAND": "relaydeck.client.features.pairing.approval",
        "PHASE_APPROVED": "relaydeck.client.features.pairing.approval",
        "PHASE_CONNECTING": "relaydeck.client.features.pairing.approval",
        "PHASE_FAILED": "relaydeck.client.features.pairing.approval",
        "PHASE_READY": "relaydeck.client.features.pairing.approval",
        "PHASE_SETUP": "relaydeck.client.features.pairing.approval",
        "PHASE_WAITING": "relaydeck.client.features.pairing.approval",
        "PairingFlow": "relaydeck.client.features.pairing.service",
        "approve_command": "relaydeck.client.features.pairing.approval",
        "discard_pending": "relaydeck.client.features.pairing.service",
        "valid_request": "relaydeck.client.features.pairing.approval",
    },
    "relaydeck.client.features.rename.api": {
        "RenameSource": "relaydeck.client.features.rename.source",
        "card_label": "relaydeck.client.features.rename.rename_wiring",
        "shown_title": "relaydeck.client.features.rename.rename_wiring",
        "space_label": "relaydeck.client.features.rename.rename_wiring",
    },
    "relaydeck.client.features.update_notice.api": {
        "UpdateNotice": "relaydeck.client.features.update_notice.model",
        "dot_visible": "relaydeck.client.features.update_notice.model",
    },
    "relaydeck.client.features.usage.api": {
        "ALL": "relaydeck.client.features.usage.selectors",
        "Attention": "relaydeck.client.features.usage.selectors",
        "Bucket": "relaydeck.client.features.usage.selectors",
        "Chart": "relaydeck.client.features.usage.selectors",
        "Counts": "relaydeck.client.features.usage.selectors",
        "CoverageEntry": "relaydeck.client.features.usage.model",
        "Filter": "relaydeck.client.features.usage.selectors",
        "Kind": "relaydeck.client.features.usage.model",
        "LimitWindow": "relaydeck.client.features.usage.selectors",
        "LimitsRefresh": "relaydeck.client.features.usage.model",
        "NEAR_LIMIT_PERCENT": "relaydeck.client.features.usage.selectors",
        "ProviderLimits": "relaydeck.client.features.usage.selectors",
        "RANK_COMPACT": "relaydeck.client.features.usage.selectors",
        "RANK_REGULAR": "relaydeck.client.features.usage.selectors",
        "Row": "relaydeck.client.features.usage.model",
        "STATE_CAPACITY": "relaydeck.client.features.usage.presentation",
        "STATE_DISABLED": "relaydeck.client.features.usage.presentation",
        "STATE_LOADING": "relaydeck.client.features.usage.presentation",
        "STATE_NO_PROVIDERS": "relaydeck.client.features.usage.presentation",
        "STATE_OFFLINE": "relaydeck.client.features.usage.presentation",
        "STATE_PAGE_TOO_LARGE": "relaydeck.client.features.usage.presentation",
        "STATE_READY": "relaydeck.client.features.usage.presentation",
        "STATE_UNAVAILABLE": "relaydeck.client.features.usage.presentation",
        "STATUS_CONFLICT": "relaydeck.client.features.usage.selectors",
        "STATUS_LOADING": "relaydeck.client.features.usage.loop",
        "STATUS_NEAR_LIMIT": "relaydeck.client.features.usage.selectors",
        "STATUS_NOT_REPORTED": "relaydeck.client.features.usage.selectors",
        "STATUS_NO_READING": "relaydeck.client.features.usage.selectors",
        "STATUS_OK": "relaydeck.client.features.usage.selectors",
        "STATUS_READING_UNAVAILABLE": "relaydeck.client.features.usage.selectors",
        "STATUS_SETUP_NEEDED": "relaydeck.client.features.usage.selectors",
        "STATUS_STALE": "relaydeck.client.features.usage.selectors",
        "STATUS_UNMANAGED": "relaydeck.client.features.usage.selectors",
        "STATUS_WINDOW_ENDED": "relaydeck.client.features.usage.selectors",
        "UNLINKED": "relaydeck.client.features.usage.selectors",
        "Usage": "relaydeck.client.features.usage.selectors",
        "UsagePage": "relaydeck.client.features.usage.model",
        "as_of": "relaydeck.client.features.usage.presentation",
        "attention": "relaydeck.client.features.usage.selectors",
        "chart": "relaydeck.client.features.usage.selectors",
        "console_card": "relaydeck.client.features.usage.selectors",
        "counts": "relaydeck.client.features.usage.selectors",
        "credit_chart_page": "relaydeck.client.features.usage.selectors",
        "credit_space_buckets": "relaydeck.client.features.usage.selectors",
        "credits_by_kind": "relaydeck.client.features.usage.selectors",
        "credits_chart_text": "relaydeck.client.features.usage.selectors",
        "credits_text": "relaydeck.client.features.usage.selectors",
        "date_range": "relaydeck.client.features.usage.selectors",
        "filtered_rows": "relaydeck.client.features.usage.selectors",
        "host_day": "relaydeck.client.features.usage.model",
        "latest_limit": "relaydeck.client.features.usage.selectors",
        "limits": "relaydeck.client.features.usage.selectors",
        "money": "relaydeck.client.features.usage.presentation",
        "monogram": "relaydeck.client.features.usage.selectors",
        "providers": "relaydeck.client.features.usage.selectors",
        "rank": "relaydeck.client.features.usage.selectors",
        "relative": "relaydeck.client.features.usage.presentation",
        "reset": "relaydeck.client.features.usage.presentation",
        "screen_state": "relaydeck.client.features.usage.presentation",
        "share": "relaydeck.client.features.usage.selectors",
        "space_buckets": "relaydeck.client.features.usage.selectors",
        "tokens_chart_text": "relaydeck.client.features.usage.selectors",
        "tokens_text": "relaydeck.client.features.usage.selectors",
        "totals": "relaydeck.client.features.usage.selectors",
        "usage_of": "relaydeck.client.features.usage.selectors",
        "window_bounds": "relaydeck.client.features.usage.selectors",
        "window_label": "relaydeck.client.features.usage.selectors",
        "zone_label": "relaydeck.client.features.usage.presentation",
    },
    "relaydeck.client.features.rename.ui": {
        "use_rename_flow": "relaydeck.client.features.rename.rename_flow"
    },
    "relaydeck.client.features.usage.ui": {
        "now_ms": "relaydeck.client.features.usage.hooks",
        "use_usage_feed": "relaydeck.client.features.usage.hooks",
    },
}


@pytest.mark.parametrize(("entry", "names"), EXPECTED.items())
def test_public_exports_match_the_declared_list(entry: str, names: list[str]) -> None:
    module = importlib.import_module(entry)
    assert module.__all__ == names
    assert len(module.__all__) == len(set(module.__all__))


@pytest.mark.parametrize(
    ("entry", "name", "owner"),
    [(entry, name, owner) for entry, names in CANONICAL.items() for name, owner in names.items()],
)
def test_public_names_are_the_canonical_objects(entry: str, name: str, owner: str) -> None:
    assert getattr(importlib.import_module(entry), name) is getattr(
        importlib.import_module(owner), name
    )


@pytest.mark.parametrize("entry", EXPECTED)
def test_public_entries_have_no_lazy_exports(entry: str) -> None:
    module = importlib.import_module(entry)
    assert module.__file__ is not None
    tree = ast.parse(Path(module.__file__).read_text())
    assert not any(
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "__getattr__"
        for node in ast.walk(tree)
    )
    if entry != "relaydeck.client.features.usage.api":
        assert all(
            isinstance(node, ast.Import | ast.ImportFrom)
            or isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            or isinstance(node, ast.Assign)
            and all(
                isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
            )
            for node in tree.body
        )


def test_raw_usage_factory_remains_in_its_app_owner() -> None:
    from relaydeck.client.app.resources import gateway_usage_fetch
    from relaydeck.client.features.usage import api

    assert not hasattr(api, "gateway_usage_fetch")
    assert gateway_usage_fetch.__module__ == "relaydeck.client.app.resources"
