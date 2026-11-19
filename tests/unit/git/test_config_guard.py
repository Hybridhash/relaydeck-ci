"""ConfigGuard refuses repository configuration that could run a helper or widen a read."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from relaydeck.git.config_guard import ConfigGuard, UnsupportedConfig, unsafe
from relaydeck.git.runner import GitRunner


def _repo(root: Path) -> Path:
    repo = root / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def _check(repo: Path) -> None:
    git = str(repo / ".git")
    ConfigGuard(GitRunner.create()).check(str(repo), git, git)


def test_plain_repository_passes(tmp_path: Path) -> None:
    _check(_repo(tmp_path))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("filter.x.clean", "helper"),
        ("filter.x.smudge", "helper"),
        ("filter.x.process", "helper"),
        ("filter.lfs.required", "true"),
        ("diff.x.textconv", "helper"),
        ("diff.x.command", "helper"),
        ("core.fsmonitor", "/tmp/helper"),
        ("core.sshCommand", "helper"),
        ("core.worktree", "/elsewhere"),
        ("credential.helper", "store"),
        ("include.path", "other.cfg"),
        ("includeIf.gitdir:/x/.path", "other.cfg"),
        ("gpg.program", "helper"),
    ],
)
def test_unsafe_keys_are_refused(tmp_path: Path, key: str, value: str) -> None:
    repo = _repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
    with pytest.raises(UnsupportedConfig):
        _check(repo)


def test_boolean_fsmonitor_is_allowed() -> None:
    assert not unsafe("core.fsmonitor", "true")
    assert not unsafe("core.fsmonitor", "false")
    assert unsafe("core.fsmonitor", "/bin/helper")


def test_included_file_keys_are_seen(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    extra = tmp_path / "extra.cfg"
    extra.write_text('[diff "y"]\n\ttextconv = helper\n')
    subprocess.run(["git", "-C", str(repo), "config", "include.path", str(extra)], check=True)
    with pytest.raises(UnsupportedConfig):
        _check(repo)


def test_object_alternates_are_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / ".git" / "objects" / "info").mkdir(parents=True, exist_ok=True)
    (repo / ".git" / "objects" / "info" / "alternates").write_text("/elsewhere/objects\n")
    with pytest.raises(UnsupportedConfig, match="alternates"):
        _check(repo)


def test_runner_options_do_not_trip_the_guard() -> None:
    for key, value in (
        ("diff.external", ""),
        ("core.hookspath", "/dev/null"),
        ("core.fsmonitor", "false"),
    ):
        assert not unsafe(key, value)


@pytest.mark.parametrize("line", ["*.txt filter=x", "*.bin diff=hex", "* merge=ours"])
def test_info_attributes_assigning_drivers_are_refused(tmp_path: Path, line: str) -> None:
    repo = _repo(tmp_path)
    (repo / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text(line + "\n")
    with pytest.raises(UnsupportedConfig, match="info_attributes"):
        _check(repo)


def test_plain_info_attributes_pass(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("*.png binary\n")
    _check(repo)


@pytest.mark.parametrize("kind", ["fifo", "symlink_to_fifo", "directory"])
def test_unsafe_info_attributes_file_types_are_refused_without_blocking(
    tmp_path: Path, kind: str
) -> None:
    import os
    import time

    repo = _repo(tmp_path)
    info = repo / ".git" / "info"
    info.mkdir(parents=True, exist_ok=True)
    target = info / "attributes"
    if kind == "fifo":
        os.mkfifo(target)
    elif kind == "symlink_to_fifo":
        os.mkfifo(tmp_path / "pipe")
        target.symlink_to(tmp_path / "pipe")
    else:
        target.mkdir()
    begun = time.monotonic()
    with pytest.raises(UnsupportedConfig, match="info_attributes"):
        _check(repo)
    assert time.monotonic() - begun < 2
