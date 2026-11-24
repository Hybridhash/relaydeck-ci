"""A kind counts as installed only when the program Herdr runs for it is on PATH.

Synthetic PATH lookups only; nothing is executed and no path reaches the wire.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from relaydeck.companion.inspection.detect import CatalogError, launch_programs, load_catalog
from relaydeck.connector.catalog import discover_agent_catalog
from relaydeck.connector.herdr import HerdrAdapter, HerdrError
from relaydeck.connector.journal import ConnectorDispatch
from relaydeck.connector.source import HerdrSource
from relaydeck.contracts.catalog import catalog_from_wire
from relaydeck.contracts.schemas import validate
from relaydeck.devtools.fakes.connector import FixtureAdapter
from relaydeck.host.persistence.command_journal import CommandJournal
from tests.support.creation.command_journal import NOW, _dispatch_msg
from tests.support.host_harness import run

CATALOG = load_catalog()
PROGRAMS = launch_programs(CATALOG)
ENTRIES = {entry.id: entry for entry in CATALOG.entries}
# The kinds whose launched program is not their id, and the kinds Herdr cannot start.
DIFFERENT = {"kiro": "kiro-cli", "cursor": "cursor-agent", "antigravity": "agy"}
UNSUPPORTED = {"prime-agent", "command-code", "crush"}


def _on_path(*names: str) -> Any:
    return lambda name: f"/synthetic/{name}" if name in names else None


def _entry(kind: str, on_path: Any) -> dict[str, Any]:
    entries: list[dict[str, Any]] = discover_agent_catalog(
        NOW, kinds=(kind,), programs=PROGRAMS, which=on_path
    )["entries"]
    return entries[0]


def test_the_launched_program_table_is_pinned() -> None:
    assert {
        entry_id: entry.launch_program
        for entry_id, entry in ENTRIES.items()
        if entry.launch_program != entry_id
    } == DIFFERENT
    assert {entry_id for entry_id, entry in ENTRIES.items() if not entry.herdr_starts} == (
        UNSUPPORTED
    )
    assert PROGRAMS["agy"] == "agy" and PROGRAMS["kiro-cli"] == "kiro-cli"
    assert PROGRAMS["kimi"] == "kimi"
    assert all(PROGRAMS[kind] is None for kind in UNSUPPORTED)


@pytest.mark.parametrize("kind", sorted(ENTRIES))
def test_each_catalog_kind_needs_exactly_its_launched_program(kind: str) -> None:
    entry = ENTRIES[kind]
    if not entry.herdr_starts:
        everything = _on_path(kind, *entry.binaries)
        assert _entry(kind, everything) == {
            "kind": kind,
            "available": False,
            "reason": "unsupported",
        }
        return
    program = entry.launch_program
    misleading = ({kind, *entry.binaries} - {program}) or {"unrelated-tool"}
    wrong = _entry(kind, _on_path(*misleading))
    assert (wrong["available"], wrong["reason"]) == (False, "not_installed")
    right = _entry(kind, _on_path(program))
    assert (right["available"], right["reason"]) == (True, "installed")
    neither = _entry(kind, _on_path())
    assert (neither["available"], neither["reason"]) == (False, "not_installed")


def test_the_ide_launcher_alone_does_not_make_the_cli_kind_installed() -> None:
    assert _entry("kiro", _on_path("kiro"))["available"] is False
    assert _entry("cursor", _on_path("cursor"))["available"] is False
    assert _entry("kimi", _on_path("kimi-code"))["available"] is False
    assert _entry("kiro", _on_path("kiro-cli"))["available"] is True


def test_aliases_of_a_catalog_kind_use_the_same_program() -> None:
    assert _entry("kiro-cli", _on_path("kiro-cli"))["available"] is True
    assert _entry("agy", _on_path("agy"))["available"] is True
    assert _entry("agy", _on_path("antigravity"))["available"] is False


def test_a_configured_kind_without_a_catalog_entry_is_looked_up_by_its_name() -> None:
    assert _entry("agent-x", _on_path("agent-x"))["available"] is True
    assert _entry("agent-x", _on_path("agent-y"))["available"] is False


def test_only_the_configured_kinds_are_reported_and_each_program_is_looked_up_once() -> None:
    calls: list[str] = []

    def which(name: str) -> str | None:
        calls.append(name)
        return None

    doc = discover_agent_catalog(
        NOW, kinds=("kiro", "kiro-cli", "claude"), programs=PROGRAMS, which=which
    )
    assert [entry["kind"] for entry in doc["entries"]] == ["kiro", "kiro-cli", "claude"]
    assert calls == ["kiro-cli", "claude"]
    assert discover_agent_catalog(NOW, kinds=(), programs=PROGRAMS, which=which)["entries"] == []


def test_unsupported_kinds_are_not_looked_up_and_the_wire_stays_path_free() -> None:
    calls: list[str] = []

    def which(name: str) -> str | None:
        calls.append(name)
        return f"/synthetic/private/{name}"

    doc = discover_agent_catalog(NOW, kinds=("crush", "kiro"), programs=PROGRAMS, which=which)
    assert calls == ["kiro-cli"]
    assert "/synthetic/" not in json.dumps(doc)
    snapshot = json.loads(Path("tests/fixtures/gateway/snapshot.json").read_text())
    snapshot["host"]["agent_catalog"] = doc
    validate("relaydeck/gateway/v1/snapshot", snapshot)
    available = catalog_from_wire(doc)
    assert available is not None and available.available == ("kiro",)


def test_a_failed_lookup_is_an_error_not_an_empty_picker() -> None:
    def denied(name: str) -> str | None:
        raise OSError("synthetic lookup failure")

    doc = discover_agent_catalog(NOW, kinds=("kiro",), programs=PROGRAMS, which=denied)
    assert doc["status"] == "error" and doc["entries"] == []


def test_the_program_table_cannot_be_changed_after_startup() -> None:
    assert isinstance(PROGRAMS, MappingProxyType)
    with pytest.raises(TypeError):
        PROGRAMS["kiro"] = "kiro"  # type: ignore[index]


@pytest.mark.parametrize(
    "line",
    [
        'launch_program = "bin/tool"',
        'launch_program = ""',
        "launch_program = 1",
        'herdr_starts = "no"',
    ],
)
def test_the_catalog_rejects_a_bad_launch_key(line: str) -> None:
    text = (
        'catalog_version = 1\n[[cli]]\nid = "tool"\ndisplay_name = "Tool"\n'
        f'tier = "D"\nbinaries = ["tool"]\ndefault_roots = ["/synthetic"]\n{line}\n'
    )
    with pytest.raises(CatalogError):
        load_catalog(text)


def _path_with(directory: Path, monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    for old in directory.iterdir():
        old.unlink()
    for name in names:
        (directory / name).write_text("#!/bin/sh\nexit 0\n")
        (directory / name).chmod(0o700)
    monkeypatch.setenv("PATH", str(directory))


def _source(kinds: tuple[str, ...]) -> HerdrSource:
    return HerdrSource(
        HerdrAdapter(socket_path="/nonexistent/synthetic.sock"),
        clock=lambda: 1000,
        agent_kinds=kinds,
        launch_programs=PROGRAMS,
    )


def test_snapshot_and_preflight_use_the_same_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(("kiro", "crush", "claude"))
    source._snapshot = json.loads(
        Path("tests/fixtures/upstream/session_snapshot.json").read_text()
    )["result"]["snapshot"]
    source._agents = []
    for present in (("kiro",), ("kiro-cli",), ("kiro", "kiro-cli", "crush")):
        _path_with(tmp_path, monkeypatch, *present)
        wire = source.snapshot_document("home-mac")["host"]["agent_catalog"]
        reported = {e["kind"]: e["available"] for e in wire["entries"]}
        for kind in ("kiro", "crush", "claude"):
            try:
                source.verify_agent_kind(kind)
                allowed = True
            except HerdrError as exc:
                assert exc.code == "agent_kind_unavailable"
                allowed = False
            assert allowed is reported[kind]
    assert reported == {"kiro": True, "crush": False, "claude": False}


def test_a_refused_kind_creates_no_tab_or_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _path_with(bin_dir, monkeypatch, "kiro")
    gate = _source(("kiro",))

    class Guarded(FixtureAdapter):
        def verify_agent_kind(self, kind: str) -> None:
            gate.verify_agent_kind(kind)

    async def scenario() -> None:
        journal = CommandJournal(tmp_path / "commands.db", clock=lambda: NOW)
        try:
            source = Guarded()
            dispatch = ConnectorDispatch(journal, source, host_id="home-mac", clock=lambda: NOW)
            for new in (False, True):
                payload: dict[str, Any] = {"name": "qa", "kind": "kiro"}
                if new:
                    payload["new_workspace"] = {"directory": "/synthetic", "label": "qa"}
                message = {
                    **_dispatch_msg(
                        f"refused-{new}",
                        "agent.start",
                        {"workspace_mode": "new"} if new else {"workspace_id": "w1"},
                        payload,
                    ),
                    "host_epoch": "epoch-7",
                }
                result = await dispatch.dispatch(message)
                assert result["status"] == "failed"
                assert result["error_code"] == "agent_kind_unavailable"
                assert "result" not in result or result["result"].get("type") != "partial_creation"
            assert not source.calls and not source._tabs and not source._agents
        finally:
            journal.close()

    run(scenario())
