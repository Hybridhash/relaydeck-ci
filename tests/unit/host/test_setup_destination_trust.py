"""Absent, safe or unsafe: what stands where an installer is about to write."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from relaydeck.host import executables as trusted


def folder(path: Path, mode: int = 0o700) -> Path:
    path.mkdir(exist_ok=True)
    path.chmod(mode)
    return path


def binary(path: Path, mode: int = 0o755) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(mode)
    return path


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return folder(tmp_path.resolve() / "home")


def test_nothing_there_and_a_safe_parent_is_absent(home: Path) -> None:
    assert trusted.inspect_executable(home / ".local" / "bin" / "herdr") == "absent"


def test_nothing_there_and_no_parent_yet_is_absent_when_the_nearest_folder_is_safe(
    home: Path,
) -> None:
    assert trusted.inspect_executable(home / "a" / "b" / "c" / "herdr") == "absent"


def test_a_safe_executable_is_safe(home: Path) -> None:
    bin_dir = folder(folder(home / ".local") / "bin")
    binary(bin_dir / "herdr")
    assert trusted.inspect_executable(bin_dir / "herdr") == "safe"


@pytest.mark.parametrize("mode", [0o775, 0o757, 0o666])
def test_a_present_file_that_others_can_write_or_that_is_setuid_is_unsafe(
    home: Path, mode: int
) -> None:
    bin_dir = folder(folder(home / ".local") / "bin")
    binary(bin_dir / "herdr", mode)
    assert trusted.inspect_executable(bin_dir / "herdr") == "unsafe"


def test_a_present_file_that_is_not_executable_is_unsafe(home: Path) -> None:
    bin_dir = folder(home / "bin")
    binary(bin_dir / "herdr", 0o644)
    assert trusted.inspect_executable(bin_dir / "herdr") == "unsafe"


def test_a_dangling_link_is_unsafe_not_absent(home: Path) -> None:
    bin_dir = folder(home / "bin")
    (bin_dir / "herdr").symlink_to(home / "nowhere")
    assert trusted.inspect_executable(bin_dir / "herdr") == "unsafe"


def test_a_link_to_a_writable_target_is_unsafe(home: Path) -> None:
    target = binary(folder(home / "real", 0o777) / "herdr")
    bin_dir = folder(home / "bin")
    (bin_dir / "herdr").symlink_to(target)
    assert trusted.inspect_executable(bin_dir / "herdr") == "unsafe"


@pytest.mark.parametrize("mode", [0o777, 0o775, 0o757])
def test_an_absent_file_under_a_folder_others_can_write_is_unsafe(home: Path, mode: int) -> None:
    bin_dir = folder(home / "bin", mode)
    assert trusted.inspect_executable(bin_dir / "herdr") == "unsafe"


def test_an_unsafe_folder_higher_up_makes_a_missing_path_unsafe(home: Path) -> None:
    upper = folder(home / ".local", 0o777)
    assert trusted.inspect_executable(upper / "bin" / "herdr") == "unsafe"


def test_a_present_file_under_an_unsafe_folder_is_unsafe(home: Path) -> None:
    bin_dir = folder(home / "bin")
    binary(bin_dir / "herdr")
    bin_dir.chmod(0o777)
    assert trusted.inspect_executable(bin_dir / "herdr") == "unsafe"


def test_a_folder_owned_by_someone_else_is_unsafe(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = folder(home / "bin")
    monkeypatch.setattr(os, "getuid", lambda: 4242)
    assert trusted.inspect_executable(bin_dir / "herdr") == "unsafe"


def test_a_relative_path_is_unsafe() -> None:
    assert trusted.inspect_executable(Path("bin/herdr")) == "unsafe"


def test_inspecting_changes_nothing(home: Path) -> None:
    bin_dir = folder(home / "bin", 0o777)
    before = sorted(p.name for p in home.rglob("*"))
    trusted.inspect_executable(bin_dir / "herdr")
    assert sorted(p.name for p in home.rglob("*")) == before
    assert bin_dir.stat().st_mode & 0o777 == 0o777
