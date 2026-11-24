"""Host module ownership and native-package exclusion."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from relaydeck.companion.updates import schedule
from relaydeck.releases import records
from scripts import build_native


@pytest.mark.parametrize(
    "name",
    [
        "relaydeck.companion.credentials",
        "relaydeck.companion.persistence",
        "relaydeck.companion.deadlines",
        "relaydeck.persistence_contracts",
        "relaydeck.journal",
        "relaydeck.offload",
        "relaydeck.tunnel_queue",
        "relaydeck.logging_setup",
        "relaydeck.minisign",
        "relaydeck.webbundle",
        "relaydeck.webrelease",
        "relaydeck.updates",
    ],
)
def test_old_host_module_paths_do_not_define_duplicate_types(name: str) -> None:
    assert importlib.util.find_spec(name) is None


def test_schedule_uses_the_verified_record_type_and_parser() -> None:
    assert schedule.Record is records.Record
    assert schedule.UpdateError is records.UpdateError
    assert schedule.record_from_doc is records.record_from_doc
    assert schedule.strict_load is records.strict_load
    record = records.Record("1.2.3", "a" * 40, "2026-01-01", ("Synthetic change.",))
    restored = schedule.state_from_doc(schedule.State(record=record).as_doc())
    assert type(restored.record) is records.Record
    assert restored.record == record


@pytest.mark.parametrize("package", ["host", "releases"])
def test_host_packages_are_rejected_from_staging_and_payloads(tmp_path: Path, package: str) -> None:
    assert package in build_native.FORBIDDEN_PACKAGES
    (tmp_path / "app/relaydeck" / package).mkdir(parents=True)
    (tmp_path / "app/main.py").write_text("pass\n")
    with pytest.raises(build_native.BuildError, match=f"relaydeck/{package}"):
        build_native.assert_stage_clean(tmp_path)
    for name in ("module.py", "module.cpython-313.pyc"):
        assert build_native.forbidden_member(f"relaydeck/{package}/{name}")
