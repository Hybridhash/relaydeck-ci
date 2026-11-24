"""BaseStore: private, atomic, scoped and path-free."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from relaydeck.git.base_store import MAX_BYTES, BaseStore
from relaydeck.git.locator import Worktree
from tests.support.guarded_run import run_guarded

WT = Worktree("/w/a", "/w/a/.git", "/w/a/.git", (1, 2, 1, 3, 1, 3))
OTHER = Worktree("/w/b", "/w/b/.git", "/w/b/.git", (1, 4, 1, 5, 1, 5))


def test_saved_choice_survives_a_restart(tmp_path: Path) -> None:
    BaseStore(str(tmp_path)).put("acct", "h1", WT, "refs/remotes/origin/main")
    saved = BaseStore(str(tmp_path)).get("acct", "h1", WT)
    assert saved is not None and saved[0] == "refs/remotes/origin/main"


def test_choice_is_scoped_by_account_host_and_worktree(tmp_path: Path) -> None:
    store = BaseStore(str(tmp_path))
    store.put("acct", "h1", WT, "refs/heads/main")
    assert store.get("other", "h1", WT) is None
    assert store.get("acct", "h2", WT) is None
    assert store.get("acct", "h1", OTHER) is None


def test_files_are_private_and_hold_no_paths_or_names(tmp_path: Path) -> None:
    BaseStore(str(tmp_path)).put("acct", "h1", WT, "refs/heads/main")
    for name in os.listdir(tmp_path):
        info = (tmp_path / name).stat()
        assert info.st_mode & 0o077 == 0
        data = (tmp_path / name).read_bytes()
        assert b"/w/a" not in data and b"acct" not in data


def test_only_branch_refs_are_accepted(tmp_path: Path) -> None:
    store = BaseStore(str(tmp_path))
    for bad in ("main", "refs/tags/v1", "refs/heads/" + "x" * 2000):
        with pytest.raises(ValueError):
            store.put("acct", "h1", WT, bad)


def test_worktree_key_is_stable_and_distinct(tmp_path: Path) -> None:
    store = BaseStore(str(tmp_path))
    assert store.worktree_key(WT) == BaseStore(str(tmp_path)).worktree_key(WT)
    assert store.worktree_key(WT) != store.worktree_key(OTHER)


def test_symlinked_or_shared_store_is_refused(tmp_path: Path) -> None:
    BaseStore(str(tmp_path))
    target = tmp_path / "elsewhere"
    target.write_bytes(b"{}")
    (tmp_path / "changes-bases.json").symlink_to(target)
    with pytest.raises(OSError):
        BaseStore(str(tmp_path)).get("acct", "h1", WT)


def test_concurrent_saves_keep_a_valid_file(tmp_path: Path) -> None:
    store = BaseStore(str(tmp_path))
    threads = [
        threading.Thread(target=store.put, args=("acct", f"h{n}", WT, "refs/heads/main"))
        for n in range(20)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all((store.get("acct", f"h{n}", WT) or ("",))[0] == "refs/heads/main" for n in range(20))


def test_each_save_gets_a_new_revision(tmp_path: Path) -> None:
    store = BaseStore(str(tmp_path))
    store.put("acct", "h1", WT, "refs/heads/main")
    first = store.get("acct", "h1", WT)
    store.put("acct", "h1", WT, "refs/heads/main")
    second = store.get("acct", "h1", WT)
    assert first is not None and second is not None
    assert first[0] == second[0] == "refs/heads/main" and first[1] != second[1]


def test_recreated_repository_does_not_inherit_the_saved_base(tmp_path: Path) -> None:
    store = BaseStore(str(tmp_path))
    store.put("acct", "h1", WT, "refs/heads/main")
    recreated = Worktree(WT.root, WT.gitdir, WT.common_dir, (9, 9, 9, 9, 9, 9))
    assert store.get("acct", "h1", recreated) is None
    assert store.worktree_key(recreated) != store.worktree_key(WT)


def test_clearing_returns_the_worktree_to_the_default(tmp_path: Path) -> None:
    store = BaseStore(str(tmp_path))
    store.put("acct", "h1", WT, "refs/heads/release")
    store.put("acct", "h1", OTHER, "refs/heads/release")
    store.clear("acct", "h1", WT)
    assert BaseStore(str(tmp_path)).get("acct", "h1", WT) is None
    assert store.get("acct", "h1", OTHER) is not None
    store.clear("acct", "h1", WT)  # clearing nothing is harmless


_PROBE = """
import sys
from relaydeck.git.base_store import BaseStore
from relaydeck.git.locator import Worktree
wt = Worktree("/w/a", "/w/a/.git", "/w/a/.git", (1, 2, 1, 3, 1, 3))
try:
    store = BaseStore(sys.argv[1])
    store.get("acct", "h1", wt)
except PermissionError as exc:
    print("refused", exc)
else:
    print("read")
"""


def _private(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    path.chmod(0o600)
    return path


def test_a_fifo_in_place_of_the_choices_is_refused_promptly(tmp_path: Path) -> None:
    BaseStore(str(tmp_path))
    os.mkfifo(tmp_path / "changes-bases.json", 0o600)
    assert run_guarded(_PROBE, str(tmp_path)) == "refused unsafe_store"


def test_a_fifo_in_place_of_the_key_is_refused_promptly(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "changes-bases.key", 0o600)
    assert run_guarded(_PROBE, str(tmp_path)) == "refused unsafe_store"


@pytest.mark.parametrize("name", ["changes-bases.json", "changes-bases.key"])
def test_a_link_in_place_of_a_store_file_is_refused_and_left_alone(
    tmp_path: Path, name: str
) -> None:
    BaseStore(str(tmp_path))
    target = _private(tmp_path / "elsewhere", os.urandom(32) if name.endswith("key") else b"{}")
    (tmp_path / name).unlink(missing_ok=True)
    (tmp_path / name).symlink_to(target)
    before = target.read_bytes()
    with pytest.raises(PermissionError, match="unsafe_store"):
        BaseStore(str(tmp_path)).get("acct", "h1", WT)
    assert target.read_bytes() == before and (tmp_path / name).is_symlink()


def test_a_choices_file_past_the_bound_is_refused_not_cut_and_rewritten(tmp_path: Path) -> None:
    store = BaseStore(str(tmp_path))
    big = _private(tmp_path / "changes-bases.json", b" " * (MAX_BYTES + 1))
    with pytest.raises(PermissionError, match="unsafe_store"):
        store.put("acct", "h1", WT, "refs/heads/main")
    assert big.stat().st_size == MAX_BYTES + 1


def test_a_choices_file_at_the_bound_is_still_read(tmp_path: Path) -> None:
    store = BaseStore(str(tmp_path))
    _private(tmp_path / "changes-bases.json", b" " * MAX_BYTES)
    assert store.get("acct", "h1", WT) is None


def test_an_unsafe_existing_key_is_not_replaced(tmp_path: Path) -> None:
    key = _private(tmp_path / "changes-bases.key", os.urandom(32))
    key.chmod(0o644)
    before = key.read_bytes()
    with pytest.raises(PermissionError, match="unsafe_store"):
        BaseStore(str(tmp_path))
    assert key.read_bytes() == before


def test_the_file_opened_is_the_file_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = BaseStore(str(tmp_path))
    store.put("acct", "h1", WT, "refs/heads/main")
    real_open = os.open

    def swap_after_open(path: str, flags: int, *args: int) -> int:
        fd = real_open(path, flags, *args)
        if str(path).endswith("changes-bases.json"):
            os.replace(path, f"{path}.moved")
            os.mkfifo(path, 0o600)
        return fd

    monkeypatch.setattr(os, "open", swap_after_open)
    saved = store.get("acct", "h1", WT)
    assert saved is not None and saved[0] == "refs/heads/main"
