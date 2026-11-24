"""The host registry's read-only view and its pinned write."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from relaydeck.gateway.features.access.hosts import HostStore
from relaydeck.host.files import private


@pytest.fixture
def state(tmp_path: Path) -> Path:
    path = tmp_path.resolve() / "state"
    path.mkdir(mode=0o700)
    return path


def test_the_observer_reads_what_the_store_wrote_and_writes_nothing(state: Path) -> None:
    store = HostStore(path=state / "hosts.json")
    token = store.enroll("h1", "Home", "owner", "ssh-ed25519 AAAA", "SHA256:x")
    before = (state / "hosts.json").read_bytes()
    seen = HostStore.observe(state / "hosts.json")
    assert seen.lookup(token) is not None and seen.owner("h1") == "owner"
    for call in (lambda: seen.enroll("h2"), lambda: seen.revoke("h1")):
        with pytest.raises(RuntimeError):
            call()
    assert (state / "hosts.json").read_bytes() == before


def test_the_observer_creates_nothing_for_a_missing_file_or_folder(tmp_path: Path) -> None:
    missing = tmp_path.resolve() / "a" / "b" / "hosts.json"
    assert HostStore.observe(missing).hosts() == ()
    assert not missing.parent.exists()
    folder = tmp_path.resolve() / "empty"
    folder.mkdir(mode=0o700)
    assert HostStore.observe(folder / "hosts.json").hosts() == ()
    assert list(folder.iterdir()) == []


def test_the_observer_refuses_an_unreadable_registry(state: Path) -> None:
    (state / "hosts.json").write_text("{nope")
    (state / "hosts.json").chmod(0o600)
    with pytest.raises(ValueError):
        HostStore.observe(state / "hosts.json")


def test_the_registry_write_goes_through_a_pinned_parent(state: Path, tmp_path: Path) -> None:
    store = HostStore(path=state / "hosts.json")
    store.enroll("h1")
    moved = tmp_path.resolve() / "elsewhere"
    moved.mkdir(mode=0o700)
    os.rename(state, tmp_path.resolve() / "state-moved")
    (tmp_path.resolve() / "state").symlink_to(moved)
    with pytest.raises(OSError):
        store.enroll("h2")
    assert list(moved.iterdir()) == []


def test_a_wider_state_folder_or_a_linked_registry_is_refused_for_writes(state: Path) -> None:
    store = HostStore(path=state / "hosts.json")
    store.enroll("h1")
    (state / "hosts.json").unlink()
    (state / "real").write_text("{}")
    (state / "hosts.json").symlink_to(state / "real")
    with pytest.raises(OSError):
        store.enroll("h2")
    assert (state / "real").read_text() == "{}"


def test_new_parents_are_made_0755_whatever_the_umask(tmp_path: Path) -> None:
    previous = os.umask(0o077)
    try:
        leaf = tmp_path.resolve() / "p" / "q" / "leaf"
        private.private_dir(leaf)
    finally:
        os.umask(previous)
    assert oct((leaf.parent).stat().st_mode & 0o777) == "0o755"
    assert oct(leaf.stat().st_mode & 0o777) == "0o700"
