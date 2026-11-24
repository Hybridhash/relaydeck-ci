"""Owner-only files through pinned descriptors: creation, replacement, reads and refusals."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from relaydeck.host.files import descriptors as pf
from relaydeck.host.files.descriptors import PrivateFileError
from tests.support.guarded_run import run_guarded


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    path = tmp_path.resolve() / "private"
    path.mkdir(mode=0o700)
    return path


def code_of(call: object) -> str:
    with pytest.raises(PrivateFileError) as caught:
        call()  # type: ignore[operator]
    return caught.value.code


def test_create_writes_a_0600_file_exclusively_and_syncs(folder: Path) -> None:
    pf.create_private_path(folder / "a", b"value\n")
    assert (folder / "a").read_bytes() == b"value\n"
    assert stat.S_IMODE((folder / "a").stat().st_mode) == 0o600
    assert code_of(lambda: pf.create_private_path(folder / "a", b"other")) == "exists"
    assert (folder / "a").read_bytes() == b"value\n"


def test_create_makes_missing_folders_0700_and_refuses_a_wider_existing_one(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    pf.create_private_path(root / "x" / "y" / "f", b"1")
    assert stat.S_IMODE((root / "x" / "y").stat().st_mode) == 0o700
    wide = root / "wide"
    wide.mkdir(mode=0o755)
    wide.chmod(0o755)
    assert code_of(lambda: pf.create_private_path(wide / "f", b"1")) == "directory"
    assert not (wide / "f").exists()
    assert stat.S_IMODE(wide.stat().st_mode) == 0o755  # never repaired


def test_a_symlinked_parent_or_ancestor_is_refused(tmp_path: Path, folder: Path) -> None:
    link = tmp_path.resolve() / "link"
    link.symlink_to(folder)
    assert code_of(lambda: pf.create_private_path(link / "f", b"1")) == "directory"
    assert not (folder / "f").exists()
    assert code_of(lambda: pf.read_private_path(link / "f")) == "directory"


def test_a_group_writable_ancestor_is_refused(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    shared = root / "shared"
    shared.mkdir()
    shared.chmod(0o775)
    inner = shared / "inner"
    inner.mkdir(mode=0o700)
    assert code_of(lambda: pf.create_private_path(inner / "f", b"1")) == "directory"


def _tree(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


@pytest.mark.parametrize("mode", [0o777, 0o775, 0o1777])
def test_an_unsafe_existing_ancestor_is_refused_before_anything_is_made_under_it(
    tmp_path: Path, mode: int
) -> None:
    root = tmp_path.resolve()
    wide = root / "wide"
    wide.mkdir()
    wide.chmod(mode)
    before = _tree(root)
    target = wide / "new" / "private"
    assert code_of(lambda: pf.open_private_dir(target, create_missing=True)) == "directory"
    assert code_of(lambda: pf.create_private_path(target / "f", b"1")) == "directory"
    assert _tree(root) == before
    assert stat.S_IMODE(wide.stat().st_mode) == mode


@pytest.mark.parametrize("mode", [0o777, 0o775])
def test_an_unsafe_folder_above_an_existing_private_one_refuses_before_any_creation(
    tmp_path: Path, mode: int
) -> None:
    root = tmp_path.resolve()
    wide = root / "wide"
    wide.mkdir()
    wide.chmod(mode)
    existing = wide / "existing"
    existing.mkdir(mode=0o700)
    before = _tree(root)
    target = existing / "new" / "private"
    assert code_of(lambda: pf.open_private_dir(target, create_missing=True)) == "directory"
    assert code_of(lambda: pf.create_private_path(target / "f", b"1")) == "directory"
    assert _tree(root) == before
    assert stat.S_IMODE(wide.stat().st_mode) == mode
    assert stat.S_IMODE(existing.stat().st_mode) == 0o700


def test_a_refusal_below_a_safe_ancestor_still_leaves_nothing_behind(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    shared = root / "shared"
    shared.mkdir()
    shared.chmod(0o775)
    before = _tree(root)
    assert code_of(lambda: pf.create_private_path(shared / "a" / "b" / "f", b"1")) == "directory"
    assert _tree(root) == before


def test_read_refuses_links_fifos_wrong_modes_and_extra_hard_links(folder: Path) -> None:
    (folder / "real").write_bytes(b"secret")
    (folder / "real").chmod(0o600)
    (folder / "sym").symlink_to(folder / "real")
    assert code_of(lambda: pf.read_private_path(folder / "sym")) == "unsafe"
    (folder / "loose").write_bytes(b"x")
    (folder / "loose").chmod(0o640)
    assert code_of(lambda: pf.read_private_path(folder / "loose")) == "unsafe"
    os.link(folder / "real", folder / "second")
    assert code_of(lambda: pf.read_private_path(folder / "real")) == "unsafe"
    os.mkfifo(folder / "pipe", 0o600)
    assert code_of(lambda: pf.read_private_path(folder / "pipe")) == "unsafe"
    assert code_of(lambda: pf.read_private_path(folder / "absent")) == "missing"


def test_a_fifo_never_blocks_a_read(folder: Path) -> None:
    os.mkfifo(folder / "pipe", 0o600)
    code = (
        "import sys\nfrom pathlib import Path\n"
        "from relaydeck.host.files import descriptors as pf\n"
        "try:\n    pf.read_private_path(Path(sys.argv[1]))\n"
        "except pf.PrivateFileError as exc:\n    print(exc.code)\n"
    )
    assert run_guarded(code, str(folder / "pipe")) == "unsafe"


def test_reads_are_bounded_at_the_limit(folder: Path) -> None:
    (folder / "big").write_bytes(b"x" * 11)
    (folder / "big").chmod(0o600)
    assert code_of(lambda: pf.read_private_path(folder / "big", limit=10)) == "too_large"
    (folder / "ok").write_bytes(b"x" * 10)
    (folder / "ok").chmod(0o600)
    assert pf.read_private_path(folder / "ok", limit=10) == b"x" * 10


def test_replace_swaps_only_the_validated_inode(folder: Path) -> None:
    pf.create_private_path(folder / "f", b"old")
    pf.replace_private_path(folder / "f", b"new")
    assert (folder / "f").read_bytes() == b"new"
    assert sorted(entry.name for entry in folder.iterdir()) == ["f"]
    assert code_of(lambda: pf.replace_private_path(folder / "absent", b"1")) == "missing"
    (folder / "loose").write_bytes(b"x")
    (folder / "loose").chmod(0o644)
    assert code_of(lambda: pf.replace_private_path(folder / "loose", b"1")) == "unsafe"
    assert (folder / "loose").read_bytes() == b"x"


def test_replace_refuses_when_the_target_is_swapped_while_writing(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pf.create_private_path(folder / "f", b"old")
    real_fsync = os.fsync
    swapped = False

    def swap_after_first_sync(fd: int) -> None:
        nonlocal swapped
        real_fsync(fd)
        if not swapped:
            swapped = True
            (folder / "f").unlink()
            (folder / "f").write_bytes(b"intruder")
            (folder / "f").chmod(0o600)

    monkeypatch.setattr(os, "fsync", swap_after_first_sync)
    assert code_of(lambda: pf.replace_private_path(folder / "f", b"new")) == "unsafe"
    monkeypatch.undo()
    assert (folder / "f").read_bytes() == b"intruder"
    assert sorted(entry.name for entry in folder.iterdir()) == ["f"]  # temp removed


@pytest.mark.parametrize("failing", ["write", "fsync", "replace"])
def test_a_failure_at_each_stage_leaves_no_partial_file(
    folder: Path, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    pf.create_private_path(folder / "keep", b"old")

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError(5, "synthetic")

    monkeypatch.setattr(os, failing, boom)
    with pytest.raises(PrivateFileError) as caught:
        pf.replace_private_path(folder / "keep", b"new")
    assert caught.value.code == "io" and "synthetic" not in str(caught.value)
    if failing != "replace":
        with pytest.raises(PrivateFileError):
            pf.create_private_path(folder / "fresh", b"new")
    monkeypatch.undo()
    assert (folder / "keep").read_bytes() == b"old"
    assert not (folder / "fresh").exists()
    assert sorted(entry.name for entry in folder.iterdir()) == ["keep"]


def test_a_directory_sync_failure_after_create_is_reported_as_uncertain(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = os.fsync
    calls = 0

    def second_sync_fails(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:  # the file sync passes, the folder sync fails
            raise OSError(5, "synthetic")
        real(fd)

    monkeypatch.setattr(os, "fsync", second_sync_fails)
    assert code_of(lambda: pf.create_private_path(folder / "f", b"1")) == "uncertain"
    monkeypatch.undo()
    assert (folder / "f").read_bytes() == b"1"
