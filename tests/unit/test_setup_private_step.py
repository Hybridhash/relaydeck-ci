"""Step 2 of setup: the config, two secrets, the connector key and this Mac's enrollment."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

import pytest

from relaydeck.companion import config_edit
from relaydeck.companion.app import lifecycle
from relaydeck.companion.config import load_config
from relaydeck.companion.setup import private_step
from relaydeck.companion.setup.context import SetupContext
from relaydeck.companion.setup.files import template_text
from relaydeck.companion.setup.private_state import inspect
from relaydeck.connector.enrollment import (
    ConnectorCreds,
    EnrollmentError,
    generate_keys,
    load_creds,
    load_keys,
    publish_keys,
    save_creds,
    save_keys,
)
from relaydeck.gateway.features.access.hosts import HostStore
from tests.support.setup.private import private_steps
from tests.support.setup.rig import SetupRig

YES = {"Create these private files": True}
KEY = ".config/relaydeck/connector_key.json"


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SetupRig:
    return SetupRig(tmp_path, monkeypatch)


def configured(rig: SetupRig) -> SetupRig:
    rig.answers.rules = dict(YES)
    assert rig.run(steps=private_steps()).exit_code == 0, rig.text
    rig.said.clear()
    rig.answers.asked.clear()
    return rig


def folder(rig: SetupRig) -> Path:
    return rig.config_path.parent


def hosts(rig: SetupRig) -> HostStore:
    return HostStore.observe(rig.config().state_dir / "hosts.json")


# -- todo: one question, exactly the expected files -------------------------------------------


def test_a_fresh_home_gets_exactly_the_private_files_after_one_question(rig: SetupRig) -> None:
    rig.answers.rules = dict(YES)
    report = rig.run(steps=private_steps())
    assert report.exit_code == 0, rig.text
    assert rig.answers.count("Create these private files") == 1 and len(rig.answers.asked) == 1
    snapshot = rig.snapshot()
    created = {
        ".config",
        ".config/relaydeck",
        ".config/relaydeck/companion.toml",
        ".config/relaydeck/cookie.secret",
        ".config/relaydeck/cursor.secret",
        ".config/relaydeck/connector.json",
        ".config/relaydeck/connector_key.json",
        "Library",
        "Library/Application Support",
        "Library/Application Support/RelayDeck",
        "Library/Application Support/RelayDeck/hosts.json",
        "Library/Application Support/RelayDeck/companion.lock",
    }
    assert set(snapshot) == created
    for name, (mode, body) in snapshot.items():
        assert mode == (0o600 if body is not None else mode), name
    for name in (".config/relaydeck", "Library/Application Support/RelayDeck"):
        assert snapshot[name][0] == 0o700
    assert "present (not changed)" not in rig.text.split("Private files:")[1]
    assert "https://mac-a.synthetic-tailnet.example.ts.net:8445" in rig.config_path.read_text()


def test_the_two_secrets_are_long_random_and_different(rig: SetupRig) -> None:
    configured(rig)
    config = rig.config()
    cookie, cursor = config.secrets()
    assert len(cookie) >= 32 and len(cursor) >= 32 and cookie != cursor


def test_the_credentials_hold_the_token_the_registry_accepts(rig: SetupRig) -> None:
    configured(rig)
    config = rig.config()
    creds = load_creds(str(config.connector_credentials))
    keys = load_keys(str(rig.home / KEY))
    assert creds is not None and keys is not None
    record = hosts(rig).lookup(creds.host_token)
    assert record is not None and record.host_id == config.host_id
    assert (record.account, record.pubkey, record.fingerprint) == (
        "owner",
        keys.public_openssh,
        keys.fingerprint,
    )
    assert creds.gateway_url == config.advertised_origin


# -- done: a second run changes nothing and asks nothing --------------------------------------


def test_a_second_run_writes_nothing_and_asks_nothing(rig: SetupRig) -> None:
    configured(rig)
    before = rig.snapshot()
    report = rig.run(steps=private_steps())
    assert report.exit_code == 0
    assert rig.snapshot() == before
    assert rig.answers.asked == []
    assert rig.text.count("present (not changed)") == 5


# -- secrets that are not safe are conflicts, nothing is written or changed -------------------


@pytest.mark.parametrize("which", ["cookie.secret", "cursor.secret"])
@pytest.mark.parametrize("fault", ["symlink", "group", "short", "hardlink", "fifo"])
def test_an_unsafe_existing_secret_is_a_conflict_and_nothing_changes(
    rig: SetupRig, which: str, fault: str
) -> None:
    rig.make_config_folder()
    target = folder(rig) / which
    other = folder(rig) / "other"
    if fault == "symlink":
        other.write_text("x" * 40)
        other.chmod(0o600)
        target.symlink_to(other)
    elif fault == "group":
        target.write_text("x" * 40)
        target.chmod(0o640)
    elif fault == "short":
        target.write_text("short")
        target.chmod(0o600)
    elif fault == "hardlink":
        other.write_text("x" * 40)
        other.chmod(0o600)
        os.link(other, target)
    else:
        os.mkfifo(target, 0o600)
    before = rig.snapshot()
    rig.answers.rules = dict(YES)
    report = rig.run(steps=private_steps())
    assert report.exit_code == 1
    assert rig.snapshot() == before
    assert rig.answers.asked == []
    assert "Setup stopped" in rig.text


def test_equal_secrets_are_a_conflict(rig: SetupRig) -> None:
    rig.make_config_folder()
    for name in ("cookie.secret", "cursor.secret"):
        (folder(rig) / name).write_text("e" * 40)
        (folder(rig) / name).chmod(0o600)
    before = rig.snapshot()
    assert rig.run(steps=private_steps()).exit_code == 1
    assert rig.snapshot() == before and "equals" in rig.text


def test_an_existing_valid_secret_is_kept_and_only_the_other_is_made(rig: SetupRig) -> None:
    rig.make_config_folder()
    cookie = folder(rig) / "cookie.secret"
    cookie.write_text("keep-me-" + "k" * 40 + "\n")
    cookie.chmod(0o600)
    rig.answers.rules = dict(YES)
    assert rig.run(steps=private_steps()).exit_code == 0
    assert cookie.read_text().startswith("keep-me-")
    assert (folder(rig) / "cursor.secret").is_file()
    assert "cookie secret: present (not changed)" in rig.text
    assert "cursor secret: created" in rig.text


def test_a_wider_config_folder_is_a_conflict_and_is_not_repaired(rig: SetupRig) -> None:
    rig.make_config_folder(0o755)
    assert rig.run(steps=private_steps()).exit_code == 1
    assert stat.S_IMODE(folder(rig).stat().st_mode) == 0o755
    assert not rig.config_path.exists()
    assert "owner-only" in rig.text


# -- the config's address -----------------------------------------------------------------------


def template_config(rig: SetupRig) -> None:
    from relaydeck.companion.setup import template_text

    rig.make_config_folder()
    rig.config_path.write_text(template_text())
    rig.config_path.chmod(0o600)


def test_a_template_address_gets_its_own_question_and_is_rewritten_with_backup(
    rig: SetupRig,
) -> None:
    template_config(rig)
    rig.answers.rules = {**YES, "address": True}
    report = rig.run(steps=private_steps())
    assert report.exit_code == 0, rig.text
    assert rig.answers.count("address") == 1
    assert "mac-a.synthetic-tailnet.example.ts.net:8445" in rig.config_path.read_text()
    assert any(entry.name.startswith("companion.toml.bak-") for entry in folder(rig).iterdir())
    assert (folder(rig).parent.parent / ".config" / "relaydeck" / "setup-receipts").is_dir()


def test_declining_the_address_rewrite_is_a_conflict_that_writes_nothing_more(
    rig: SetupRig,
) -> None:
    template_config(rig)
    before = rig.config_path.read_text()
    rig.answers.rules = {**YES, "address": False}
    report = rig.run(steps=private_steps())
    assert report.exit_code == 1
    assert rig.config_path.read_text() == before
    assert not (folder(rig) / "cookie.secret").exists()
    assert "template address" in rig.text


# -- the host registry and the credentials -------------------------------------------------------


def test_a_foreign_host_record_is_a_conflict_fixed_with_re_enroll(rig: SetupRig) -> None:
    configured(rig)
    config = rig.config()
    (config.connector_credentials).unlink()
    other = generate_keys()
    store = HostStore(path=config.state_dir / "hosts.json")
    store.enroll(config.host_id, "x", "owner", other.public_openssh, other.fingerprint)
    before = rig.snapshot()
    report = rig.run(steps=private_steps())
    assert report.exit_code == 1 and "--re-enroll" in rig.text
    assert rig.snapshot() == before


def test_an_interrupted_run_is_a_conflict_and_never_issues_a_second_token(rig: SetupRig) -> None:
    configured(rig)
    config = rig.config()
    config.connector_credentials.unlink()  # the record exists with this key, no credentials
    before = hosts(rig).record_for_host(config.host_id)
    assert rig.run(steps=private_steps()).exit_code == 1
    assert "--re-enroll" in rig.text
    assert hosts(rig).record_for_host(config.host_id) == before
    assert not config.connector_credentials.exists()


def test_a_record_owned_by_another_account_is_refused_even_with_re_enroll(rig: SetupRig) -> None:
    configured(rig)
    config = rig.config()
    keys = load_keys(str(rig.home / KEY))
    assert keys is not None
    store = HostStore(path=config.state_dir / "hosts.json")
    store.enroll(config.host_id, "x", "someone-else", keys.public_openssh, keys.fingerprint)
    before = rig.snapshot()
    report = rig.run(steps=private_steps(), re_enroll=True)
    assert report.exit_code == 1 and "another account" in rig.text
    assert rig.snapshot() == before


def test_credentials_whose_token_is_not_in_the_registry_are_a_conflict(rig: SetupRig) -> None:
    configured(rig)
    config = rig.config()
    save_creds(
        ConnectorCreds(config.advertised_origin, config.host_id, "rdh_not_in_registry"),
        str(config.connector_credentials),
    )
    assert rig.run(steps=private_steps()).exit_code == 1
    assert "--re-enroll" in rig.text and "rdh_not_in_registry" not in rig.text


def test_credentials_for_another_origin_are_a_conflict(rig: SetupRig) -> None:
    configured(rig)
    config = rig.config()
    creds = load_creds(str(config.connector_credentials))
    assert creds is not None
    save_creds(
        ConnectorCreds("https://elsewhere.example.ts.net", creds.host_id, creds.host_token),
        str(config.connector_credentials),
    )
    assert rig.run(steps=private_steps()).exit_code == 1
    assert "another host or address" in rig.text


def test_credentials_without_the_connector_key_are_refused_and_no_key_is_made(
    rig: SetupRig,
) -> None:
    configured(rig)
    (rig.home / KEY).unlink()
    report = rig.run(steps=private_steps(), re_enroll=True)
    assert report.exit_code == 1 and "key is missing" in rig.text
    assert not (rig.home / KEY).exists()


def test_an_unreadable_registry_is_a_conflict_not_a_traceback(rig: SetupRig) -> None:
    configured(rig)
    registry = rig.config().state_dir / "hosts.json"
    registry.write_text("{not json")
    registry.chmod(0o600)
    report = rig.run(steps=private_steps())
    assert report.exit_code == 1 and "registry cannot be read" in rig.text


def test_a_corrupt_key_is_never_replaced(rig: SetupRig) -> None:
    configured(rig)
    key = rig.home / KEY
    key.write_text("{}")
    key.chmod(0o600)
    before = key.read_bytes()
    assert rig.run(steps=private_steps()).exit_code == 1
    assert key.read_bytes() == before


def test_a_key_whose_public_half_does_not_match_the_seed_is_refused(rig: SetupRig) -> None:
    configured(rig)
    key = rig.home / KEY
    one, two = generate_keys(), generate_keys()
    from relaydeck.connector.enrollment import ConnectorKeys

    save_keys(ConnectorKeys(one.seed_b64, two.public_openssh), str(key))
    assert rig.run(steps=private_steps()).exit_code == 1
    assert "not a valid private key" in rig.text


# -- --re-enroll ---------------------------------------------------------------------------------


def break_enrollment(rig: SetupRig) -> None:
    config = rig.config()
    save_creds(
        ConnectorCreds(config.advertised_origin, config.host_id, "rdh_stale"),
        str(config.connector_credentials),
    )


def test_re_enroll_replaces_the_token_and_the_old_one_stops_working(rig: SetupRig) -> None:
    configured(rig)
    config = rig.config()
    old = load_creds(str(config.connector_credentials))
    assert old is not None
    rig.answers.rules = {"Re-enroll": True}
    assert rig.run(steps=private_steps(), re_enroll=True).exit_code == 0, rig.text
    new = load_creds(str(config.connector_credentials))
    assert new is not None and new.host_token != old.host_token
    fresh = hosts(rig)
    assert fresh.lookup(old.host_token) is None and fresh.lookup(new.host_token) is not None
    assert rig.answers.count("Devices will need to pair again") == 0
    assert not (config.state_dir / "revoke").exists()  # the revoke spool is never used


def test_re_enroll_also_replaces_credentials_that_do_not_match(rig: SetupRig) -> None:
    configured(rig)
    break_enrollment(rig)
    rig.answers.rules = {"Re-enroll": True}
    assert rig.run(steps=private_steps(), re_enroll=True).exit_code == 0, rig.text
    config = rig.config()
    creds = load_creds(str(config.connector_credentials))
    assert creds is not None and hosts(rig).lookup(creds.host_token) is not None


def test_re_enroll_declined_changes_nothing(rig: SetupRig) -> None:
    configured(rig)
    break_enrollment(rig)
    before = rig.snapshot()
    assert rig.run(steps=private_steps(), re_enroll=True).exit_code == 1
    assert rig.snapshot() == before


def test_re_enroll_with_durable_sign_ins_ends_the_restored_ones_and_says_so(
    rig: SetupRig,
) -> None:
    configured(rig)
    break_enrollment(rig)
    advanced: list[str] = []

    class Provider:
        def advance_restore_generation(self, name: str) -> None:
            advanced.append(name)

    rig.environ["RELAYDECK_DURABLE_SIGNINS"] = "1"
    rig.answers.rules = {"Re-enroll": True}
    report = rig.run(steps=private_steps(), re_enroll=True, provider_factory=lambda: Provider())
    assert report.exit_code == 0, rig.text
    assert advanced == ["authentication"]
    assert rig.answers.count("Devices will need to pair again") == 1


def test_a_failing_key_store_is_reported_without_its_detail(rig: SetupRig) -> None:
    configured(rig)
    break_enrollment(rig)

    class Provider:
        def advance_restore_generation(self, name: str) -> None:
            raise RuntimeError("keychain-secret-detail")

    rig.environ["RELAYDECK_DURABLE_SIGNINS"] = "1"
    rig.answers.rules = {"Re-enroll": True}
    report = rig.run(steps=private_steps(), re_enroll=True, provider_factory=lambda: Provider())
    assert report.exit_code == 1 and "keychain-secret-detail" not in rig.text


# -- the state lock -----------------------------------------------------------------------------


def test_a_second_setup_gets_setup_busy_and_writes_nothing(rig: SetupRig) -> None:
    rig.answers.rules = dict(YES)
    configured_dir = rig.home / "Library" / "Application Support" / "RelayDeck"
    # A lock holder that publishes nothing looks like a starting process.
    with lifecycle.state_lock(configured_dir):
        report = rig.run(steps=private_steps())
    assert report.exit_code == 1 and "Setup busy" in rig.text
    assert not (folder(rig) / "connector.json").exists()
    assert not (folder(rig) / "cookie.secret").exists()


def test_a_running_companion_is_stopped_only_after_a_yes_and_then_setup_goes_on(
    rig: SetupRig,
) -> None:
    configured(rig)
    break_enrollment(rig)
    state_dir = rig.config().state_dir
    stack = ExitStack()
    stack.enter_context(lifecycle.state_lock(state_dir))
    rig.companion.override = {"state": "setup_required", "instance": "someone"}

    def stop(directory: Path) -> bool:
        rig.stops.append(directory)
        stack.close()
        return True

    rig.answers.rules = {"Re-enroll": True, "Stop RelayDeck": True}
    try:
        report = rig.run(steps=private_steps(), re_enroll=True, request_stop=stop)
    finally:
        stack.close()
    assert report.exit_code == 0, rig.text
    assert rig.stops == [state_dir] and rig.answers.count("Stop RelayDeck") == 1


def test_a_running_companion_that_is_not_stopped_blocks_the_write(rig: SetupRig) -> None:
    configured(rig)
    break_enrollment(rig)
    before = rig.snapshot()
    state_dir = rig.config().state_dir
    rig.companion.override = {"state": "setup_required", "instance": "someone"}
    rig.answers.rules = {"Re-enroll": True, "Stop RelayDeck": False}
    with lifecycle.state_lock(state_dir):
        report = rig.run(steps=private_steps(), re_enroll=True)
    assert report.exit_code == 1 and rig.stops == []
    assert "relaydeck stop" in rig.text
    after = rig.snapshot()
    assert {k: v for k, v in after.items() if not k.endswith("companion.lock")} == {
        k: v for k, v in before.items() if not k.endswith("companion.lock")
    }


def test_the_wait_for_the_lock_is_bounded(rig: SetupRig) -> None:
    configured(rig)
    break_enrollment(rig)
    state_dir = rig.config().state_dir
    rig.companion.override = {"state": "setup_required", "instance": "someone"}
    rig.answers.rules = {"Re-enroll": True, "Stop RelayDeck": True}
    with lifecycle.state_lock(state_dir):
        report = rig.run(steps=private_steps(), re_enroll=True)  # the stop never lets go
    assert report.exit_code == 1 and "did not stop in time" in rig.text
    assert rig.clock.t - 1000.0 <= 16.0


def test_a_companion_that_starts_while_setup_holds_the_lock_cannot_run(rig: SetupRig) -> None:
    configured(rig)
    state_dir = rig.config().state_dir
    seen: list[str] = []

    class RacingLock:
        def __init__(self, directory: Path) -> None:
            self.inner = lifecycle.state_lock(directory)

        def __enter__(self) -> int:
            fd = self.inner.__enter__()
            try:
                with lifecycle.StateLease(state_dir):
                    seen.append("started")
            except lifecycle.AlreadyRunning:
                seen.append("refused")
            return fd

        def __exit__(self, *args: Any) -> None:
            self.inner.__exit__(*args)

    break_enrollment(rig)
    rig.answers.rules = {"Re-enroll": True}
    report = rig.run(steps=private_steps(), re_enroll=True, state_lock=RacingLock)
    assert report.exit_code == 0 and seen == ["refused"]


def test_a_config_changed_after_the_lock_is_taken_writes_nothing_under_the_other_folder(
    rig: SetupRig,
) -> None:
    configured(rig)
    old = rig.config().state_dir
    other = rig.home / "other-state"
    rig.answers.rules = {"Re-enroll": True}

    @contextmanager
    def lock_then_edit(path: Path) -> Iterator[int]:
        assert path == old
        with lifecycle.state_lock(path) as fd:
            body = rig.config_path.read_text()
            moved = config_edit.set_top_level(
                body, "state_dir", config_edit.render_string(str(other))
            )
            rig.config_path.write_text(moved)
            # another companion owns the newly named folder for the whole run
            with lifecycle.state_lock(other):
                yield fd

    report = rig.run(steps=private_steps(), re_enroll=True, state_lock=lock_then_edit)
    assert report.exit_code == 1 and "run relaydeck setup again" in rig.text
    assert not (other / "hosts.json").exists()
    assert str(other) not in rig.text


def test_two_profiles_share_one_connector_key_whichever_setup_publishes_first(
    rig: SetupRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.answers.rules = {**YES, "Set this Mac": True}
    folder = rig.make_config_folder()
    second = folder / "second.toml"
    body = config_edit.set_top_level(
        template_text(), "state_dir", config_edit.render_string(str(rig.home / "second-state"))
    )
    body = config_edit.set_top_level(
        body,
        "connector_credentials",
        config_edit.render_string(str(folder / "second-creds.json")),
    )
    second.write_text(body)
    second.chmod(0o600)
    real = publish_keys
    raced = False

    def publish_after_the_other_profile(keys: Any, path: str) -> Any:
        nonlocal raced
        if not raced:
            raced = True
            # the other profile finds the key missing too, and finishes its whole setup first
            assert rig.run(steps=private_steps(), config_path=second).exit_code == 0, rig.text
        return real(keys, path)

    monkeypatch.setattr(private_step, "publish_keys", publish_after_the_other_profile)
    assert rig.run(steps=private_steps()).exit_code == 0, rig.text
    keys = load_keys(str(rig.home / KEY))
    assert keys is not None
    for config in (load_config(second), rig.config()):
        creds = load_creds(str(config.connector_credentials))
        assert creds is not None
        record = HostStore.observe(config.state_dir / "hosts.json").lookup(creds.host_token)
        assert record is not None and record.pubkey == keys.public_openssh


def test_publishing_a_key_never_replaces_an_existing_one(tmp_path: Path) -> None:
    path = tmp_path / "relaydeck" / "connector_key.json"
    first, second = generate_keys(), generate_keys()
    assert publish_keys(first, str(path)) == first
    assert publish_keys(second, str(path)) == first
    assert load_keys(str(path)) == first
    path.write_text("not a key")
    path.chmod(0o600)
    with pytest.raises(EnrollmentError):
        publish_keys(second, str(path))
    assert path.read_text() == "not a key"


# -- failure injection: every stage fails, the rerun resumes --------------------------------------


@pytest.mark.parametrize("stage", ["write", "fsync", "replace"])
def test_a_failing_write_stage_leaves_no_partial_file_and_the_rerun_finishes(
    rig: SetupRig, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    configured(rig)
    break_enrollment(rig)
    rig.answers.rules = {"Re-enroll": True}

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError(5, "synthetic")

    with monkeypatch.context() as patch:
        patch.setattr(os, stage, boom)
        failed = rig.run(steps=private_steps(), re_enroll=True)
    assert failed.exit_code == 1
    leftovers = [e.name for e in folder(rig).iterdir() if e.name.endswith(".tmp")]
    assert leftovers == []
    rig.said.clear()
    done = rig.run(steps=private_steps(), re_enroll=True)
    assert done.exit_code == 0, rig.text
    creds = load_creds(str(rig.config().connector_credentials))
    assert creds is not None and hosts(rig).lookup(creds.host_token) is not None


@pytest.mark.parametrize("stage", ["write", "fsync"])
def test_a_failure_creating_the_secrets_removes_only_what_it_made_and_resumes(
    rig: SetupRig, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    rig.make_config_folder()
    rig.answers.rules = dict(YES)
    calls = 0
    real = getattr(os, stage)

    def fail_on_second(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise OSError(5, "synthetic")
        return real(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, stage, fail_on_second)
        assert rig.run(steps=private_steps()).exit_code == 1
    names = {entry.name for entry in folder(rig).iterdir()}
    assert not any(name.endswith(".tmp") for name in names)
    assert rig.run(steps=private_steps()).exit_code == 0, rig.text
    assert rig.config().secrets()[0] != rig.config().secrets()[1]


# -- a failed credentials write: revoke the host token only when nothing holds it -----------------


def _write_creds_through(monkeypatch: pytest.MonkeyPatch, fault: str) -> None:
    """Make the real credentials writer fail the way a disk does: `write` before anything is in
    place, `dir_sync` after the file is in place but its folder could not be synced."""
    real = private_step.save_creds

    def failing_write(*args: object, **kwargs: object) -> None:
        raise OSError(5, "synthetic")

    def failing_dir_sync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(5, "synthetic")

    def writer(creds: ConnectorCreds, path: str) -> Path:
        with monkeypatch.context() as patch:
            if fault == "write":
                patch.setattr(os, "write", failing_write)
            else:
                patch.setattr(os, "fsync", failing_dir_sync)
            return real(creds, path)

    monkeypatch.setattr(private_step, "save_creds", writer)


def test_a_credentials_write_that_certainly_failed_revokes_the_host_token(
    rig: SetupRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.answers.rules = dict(YES)
    _write_creds_through(monkeypatch, "write")
    assert rig.run(steps=private_steps()).exit_code == 1
    assert "credentials could not be written" in rig.text
    assert hosts(rig).record_for_host(rig.config().host_id) is None


def test_a_credentials_write_of_uncertain_outcome_keeps_the_host_token(
    rig: SetupRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.answers.rules = dict(YES)
    _write_creds_through(monkeypatch, "dir_sync")
    assert rig.run(steps=private_steps()).exit_code == 1
    assert "credentials could not be written" in rig.text
    config = rig.config()
    creds = load_creds(str(config.connector_credentials))
    assert creds is not None
    record = hosts(rig).lookup(creds.host_token)
    assert record is not None and record.host_id == config.host_id


@pytest.mark.parametrize("uncertain", [False, True])
def test_the_revoke_decision_follows_the_error_type_and_never_its_text(
    rig: SetupRig, monkeypatch: pytest.MonkeyPatch, uncertain: bool
) -> None:
    def refuse(creds: ConnectorCreds, path: str) -> Path:
        if uncertain:
            from relaydeck.connector.enrollment import UncertainWriteError

            raise UncertainWriteError("the write failed")
        raise OSError("the outcome is uncertain")

    rig.answers.rules = dict(YES)
    monkeypatch.setattr(private_step, "save_creds", refuse)
    assert rig.run(steps=private_steps()).exit_code == 1
    kept = hosts(rig).record_for_host(rig.config().host_id) is not None
    assert kept is uncertain


# -- nothing secret is ever printed or receipted ---------------------------------------------------


def test_no_secret_token_seed_code_or_home_path_reaches_output_or_receipts(rig: SetupRig) -> None:
    template_config(rig)
    rig.answers.rules = {**YES, "address": True}
    assert rig.run(steps=private_steps()).exit_code == 0
    config = rig.config()
    creds = load_creds(str(config.connector_credentials))
    keys = load_keys(str(rig.home / KEY))
    assert creds is not None and keys is not None
    cookie, cursor = config.secrets()
    secrets_seen = [
        creds.host_token,
        keys.seed_b64,
        cookie.decode(),
        cursor.decode(),
    ]
    haystack = rig.text + "\n".join(rig.answers.asked)
    receipts = folder(rig) / "setup-receipts"
    for receipt in receipts.glob("*") if receipts.exists() else []:
        haystack += receipt.read_text()
    for value in secrets_seen:
        assert value not in haystack
    assert str(rig.home) not in rig.text


def test_a_failure_prints_no_os_error_text_or_path(
    rig: SetupRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.answers.rules = dict(YES)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError(13, "synthetic-secret-os-text", str(rig.home))

    monkeypatch.setattr(os, "write", boom)
    assert rig.run(steps=private_steps()).exit_code == 1
    assert "synthetic-secret-os-text" not in rig.text and str(rig.home) not in rig.text


# -- read-only inspection ------------------------------------------------------------------------


def test_inspecting_writes_and_creates_nothing_even_for_a_missing_home(rig: SetupRig) -> None:
    before = rig.snapshot()
    ctx = SetupContext(
        config_path=rig.config_path,
        confirm=rig.answers,
        home=rig.home,
        runner=rig.serve,
    )
    found = inspect(ctx)
    assert found.work and found.config.state == "create"
    assert rig.snapshot() == before


def test_the_registry_observer_creates_no_folder_for_a_missing_state_dir(tmp_path: Path) -> None:
    missing = tmp_path.resolve() / "no" / "such" / "hosts.json"
    store = HostStore.observe(missing)
    assert store.hosts() == () and not missing.parent.exists()
    with pytest.raises(RuntimeError):
        store.enroll("h")
