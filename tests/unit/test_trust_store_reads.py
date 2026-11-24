"""Folder-trust readers open one regular file, read a bounded amount and fail closed."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from relaydeck.usage.limits_plane.trust_store import (
    MAX_BYTES,
    folder_is_trusted,
    trusted_folders,
)
from tests.support.guarded_run import run_guarded

_PROBE = """
import sys
from pathlib import Path
from relaydeck.usage.limits_plane.trust_store import folder_is_trusted, trusted_folders
store, folder = sys.argv[1:3]
print(trusted_folders(store), folder_is_trusted(store, folder))
"""


def _json_store(path: Path, folder: Path, decision: str = "trusted") -> Path:
    document = {"schema_version": 1, "projects": {str(folder): {"decision": decision}}}
    path.write_text(json.dumps(document))
    return path


def _hashed_store(root: Path, folder: Path, body: bytes) -> Path:
    digest = hashlib.sha256(str(folder.resolve()).encode()).hexdigest()[:16]
    (root / digest).mkdir(parents=True)
    target = root / digest / "permissions.yaml"
    target.write_bytes(body)
    return target


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    path = tmp_path / "work folder"
    path.mkdir()
    return path


def test_a_regular_json_store_is_read(tmp_path: Path, folder: Path) -> None:
    store = _json_store(tmp_path / "trust.json", folder)
    assert trusted_folders(str(store)) == (folder.resolve(),)
    assert folder_is_trusted(str(store), folder)


def test_a_regular_hashed_store_is_read(tmp_path: Path, folder: Path) -> None:
    root = tmp_path / "hashed"
    _hashed_store(root, folder, b"workspaceTrusted: true\n")
    assert folder_is_trusted(str(root), folder)


def test_a_fifo_store_is_unknown_promptly(tmp_path: Path, folder: Path) -> None:
    os.mkfifo(tmp_path / "trust.json")
    root = tmp_path / "hashed"
    digest = hashlib.sha256(str(folder.resolve()).encode()).hexdigest()[:16]
    (root / digest).mkdir(parents=True)
    os.mkfifo(root / digest / "permissions.yaml")
    for store in (tmp_path / "trust.json", root):
        assert run_guarded(_PROBE, str(store), str(folder)) == "None False"


def test_a_linked_store_is_unknown(tmp_path: Path, folder: Path) -> None:
    real = _json_store(tmp_path / "real.json", folder)
    link = tmp_path / "trust.json"
    link.symlink_to(real)
    assert trusted_folders(str(link)) is None and not folder_is_trusted(str(link), folder)
    root = tmp_path / "hashed"
    target = _hashed_store(root, folder, b"workspaceTrusted: true\n")
    moved = target.with_name("real.yaml")
    target.rename(moved)
    target.symlink_to(moved)
    assert not folder_is_trusted(str(root), folder)


def test_a_store_past_the_bound_is_unknown(tmp_path: Path, folder: Path) -> None:
    store = tmp_path / "trust.json"
    _json_store(store, folder)
    body = store.read_bytes()
    store.write_bytes(body + b" " * (MAX_BYTES - len(body) + 1))
    assert trusted_folders(str(store)) is None
    store.write_bytes(body + b" " * (MAX_BYTES - len(body)))
    assert trusted_folders(str(store)) == (folder.resolve(),)
    root = tmp_path / "hashed"
    target = _hashed_store(root, folder, b"workspaceTrusted: true\n" + b"#" * MAX_BYTES)
    assert not folder_is_trusted(str(root), folder)
    assert target.stat().st_size > MAX_BYTES


def test_a_store_that_grows_while_read_is_cut_off_by_the_bound(
    tmp_path: Path, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _json_store(tmp_path / "trust.json", folder)
    real_fstat = os.fstat

    def grow(fd: int) -> os.stat_result:
        info = real_fstat(fd)
        with store.open("ab") as handle:
            handle.write(b" " * (MAX_BYTES + 1))
        return info

    monkeypatch.setattr(os, "fstat", grow)
    assert trusted_folders(str(store)) is None


def test_the_file_opened_is_the_file_read(
    tmp_path: Path, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _json_store(tmp_path / "trust.json", folder)
    real_open = os.open

    def swap_after_open(path: str, flags: int, *args: int) -> int:
        fd = real_open(path, flags, *args)
        os.replace(path, f"{path}.moved")
        os.mkfifo(path)
        return fd

    monkeypatch.setattr(os, "open", swap_after_open)
    assert trusted_folders(str(store)) == (folder.resolve(),)


def test_an_undecodable_or_unreadable_store_is_unknown(tmp_path: Path, folder: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"\xff\xfe{")
    assert trusted_folders(str(bad)) is None
    assert trusted_folders(str(tmp_path / "missing.json")) is None
    assert not folder_is_trusted(str(tmp_path / "missing.json"), folder)
