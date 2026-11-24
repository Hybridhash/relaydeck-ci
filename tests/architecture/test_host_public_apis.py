"""Mac contexts expose exact public entries without duplicate owner objects."""

import importlib
from pathlib import Path

import pytest

from tests.support.architecture.public_api import entry_violations

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/relaydeck"
GIT_EXPORTS = {
    "BaseStore": "base_store",
    "ChangesError": "results",
    "ChangesService": "service",
    "FileRecord": "results",
    "GitReadPort": "api",
    "GitRunner": "runner",
    "GitUnavailable": "runner",
    "Scope": "results",
    "Snapshot": "results",
    "display": "results",
    "file_totals": "results",
    "source_folders": "api",
}


def test_git_consumers_use_the_public_entry() -> None:
    assert entry_violations(SOURCE, "relaydeck.git") == []


def test_git_public_export_names_are_exact() -> None:
    from relaydeck.git import api

    assert set(api.__all__) == set(GIT_EXPORTS)
    assert len(api.__all__) == len(GIT_EXPORTS)


@pytest.mark.parametrize("name,owner", GIT_EXPORTS.items())
def test_git_exports_are_the_owner_objects(name: str, owner: str) -> None:
    from relaydeck.git import api

    assert getattr(api, name) is getattr(importlib.import_module(f"relaydeck.git.{owner}"), name)


def test_a_context_internal_import_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "sample"
    (root / "reader").mkdir(parents=True)
    (root / "reader/internal.py").write_text("VALUE = 1\n")
    (root / "consumer.py").write_text("from sample.reader.internal import VALUE\n")
    assert entry_violations(root, "sample.reader") == [
        "consumer.py:1: imports sample.reader.internal"
    ]


def test_a_public_entry_import_is_allowed(tmp_path: Path) -> None:
    root = tmp_path / "sample"
    (root / "reader").mkdir(parents=True)
    (root / "reader/api.py").write_text("VALUE = 1\n")
    (root / "consumer.py").write_text("from sample.reader.api import VALUE\n")
    assert entry_violations(root, "sample.reader") == []


USAGE_EXPORTS = {
    "Coverage": "contracts",
    "FORMAT_ID": "limits_plane.providers.tui_usage_pane",
    "HOOK_FILENAME": "hooks.setup",
    "HookFileError": "hooks.files",
    "HookSetupCheck": "hooks.setup",
    "HookSource": "hooks.setup",
    "LIMITS_READERS": "limits",
    "LimitsProvider": "limits_plane.contracts",
    "LimitsScheduler": "limits_plane.scheduler",
    "MAX_KEY_CHARS": "contracts",
    "MAX_QUERY_DAYS": "contracts",
    "MAX_QUERY_ROWS": "contracts",
    "MAX_SOURCES": "contracts",
    "MappingGap": "contracts",
    "NOT_INSTALLED": "limits_plane.probe",
    "PANES_PER_PASS": "linking.process",
    "PASS_WALL_S": "linking.process",
    "PROVIDERS": "limits_plane",
    "PaneCwdObservation": "contracts",
    "PriceTable": "pricing",
    "ProbeFn": "linking.process",
    "ProbeTarget": "limits_plane.probe",
    "ProcObservation": "linking.process",
    "ProcTarget": "linking.process",
    "ProviderSpec": "limits_plane.contracts",
    "READERS": "readers",
    "RETENTION_DAYS": "contracts",
    "RefreshDecision": "limits_plane.contracts",
    "RefreshState": "limits_plane.refresh_state",
    "STATE_FILENAME": "limits_plane",
    "SessionMapping": "contracts",
    "Spawn": "limits_plane.probe",
    "SqliteUsageStore": "store",
    "TICK_MS": "linking.process",
    "TuiBundle": "limits_plane.contracts",
    "TuiRecipe": "limits_plane.contracts",
    "UsageCollector": "collector",
    "UsageConfig": "config",
    "UsageLimitsConfig": "config",
    "UsageQuery": "contracts",
    "UsageSource": "contracts",
    "UsageSourceConfig": "config",
    "UsageUnavailable": "contracts",
    "absolute": "hooks.files",
    "add_actions": "hooks.cli",
    "add_usage_parser": "hooks.cli",
    "cwd_digest": "linking.digests",
    "default_hook_path": "hooks.setup",
    "default_probe": "linking.process",
    "diagnose_source": "hooks.repair",
    "directory": "hooks.files",
    "ensure_probe_folder": "limits_plane.trust_store",
    "install_hook": "hooks.install",
    "limits_sources": "hooks.repair",
    "load_or_create_link_key": "linking.digests",
    "load_price_table": "pricing",
    "offer_trust": "limits_plane.probe",
    "on_path": "limits_plane.probe",
    "probe_folder": "limits_plane.trust_store",
    "read": "hooks.files",
    "recover_stale_tabs": "limits_plane.providers.tui_usage_pane",
    "repo_fingerprint": "linking.digests",
    "run_bounded_command": "limits_plane.scheduler",
    "run_in_terminal": "limits_plane.probe",
    "run_usage": "hooks.cli",
    "session_text": "readers._files",
    "tui_recipe_from": "limits_plane.contracts",
    "upgrade_outdated_hook": "hooks.install",
}


def test_usage_consumers_use_the_public_entry() -> None:
    assert entry_violations(SOURCE, "relaydeck.usage") == []


def test_usage_public_export_names_are_exact() -> None:
    from relaydeck.usage import api

    assert set(api.__all__) == set(USAGE_EXPORTS)
    assert len(api.__all__) == len(USAGE_EXPORTS)


@pytest.mark.parametrize("name,owner", USAGE_EXPORTS.items())
def test_usage_exports_are_the_owner_objects(name: str, owner: str) -> None:
    from relaydeck.usage import api

    assert getattr(api, name) is getattr(importlib.import_module(f"relaydeck.usage.{owner}"), name)
