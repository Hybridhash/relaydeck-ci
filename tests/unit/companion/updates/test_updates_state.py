"""The stored record, its offer rules, the persisted state and the schedule. Pure; no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from relaydeck.companion.updates import schedule as update_schedule
from relaydeck.releases import records as release_records
from tests.support.updates.signing import COMMIT, OTHER_COMMIT

NOW = 1_800_000_000


def rec(version: str = "0.3.1", commit: str = COMMIT) -> release_records.Record:
    return release_records.Record(version, commit, "2026-12-01", ("A note.",))


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    path = tmp_path.resolve() / "state"
    path.mkdir(mode=0o700)
    return path


# -- offer rules ---------------------------------------------------------------------------------


def test_a_newer_record_is_stored_as_the_new_high_water_mark() -> None:
    assert release_records.decide(None, rec()) == "store"
    assert release_records.decide(rec("0.3.1"), rec("0.3.2")) == "store"
    assert release_records.decide(rec("0.9.0"), rec("0.10.0")) == "store"


def test_the_same_version_and_commit_is_a_valid_reverification() -> None:
    assert release_records.decide(rec(), rec()) == "refresh"


def test_the_same_version_with_another_commit_is_a_conflict() -> None:
    assert release_records.decide(rec(), rec(commit=OTHER_COMMIT)) == "conflict"


def test_an_older_record_after_a_newer_one_is_ignored() -> None:
    assert release_records.decide(rec("0.3.2"), rec("0.3.1")) == "older"


def test_a_stored_record_is_offered_only_while_newer_than_what_is_installed() -> None:
    stored = rec("0.3.1")
    assert release_records.offered(stored, (0, 3, 0)) == stored
    assert release_records.offered(stored, (0, 3, 1)) is None  # after a manual upgrade it hides
    assert release_records.offered(stored, (0, 4, 0)) is None
    assert release_records.offered(None, (0, 3, 0)) is None
    assert release_records.offered(stored, None) is None


def test_an_attempt_keeps_the_record_and_only_a_good_one_refreshes_last_success() -> None:
    state = update_schedule.State(record=rec("0.3.2"), last_success=5, jitter=100)
    older = update_schedule.finish_attempt(state, NOW, "older")
    assert older.record == rec("0.3.2") and older.last_success == 5
    conflict = update_schedule.finish_attempt(state, NOW, "conflict")
    assert conflict.record == rec("0.3.2") and conflict.last_success == 5
    good = update_schedule.finish_attempt(state, NOW, "ok", rec("0.3.3"))
    assert good.record == rec("0.3.3") and good.last_success == NOW


# -- schedule ------------------------------------------------------------------------------------


def test_the_next_look_is_a_day_plus_jitter_after_success_or_a_quiet_outcome() -> None:
    state = update_schedule.State(jitter=900)
    for outcome in ("ok", "signature", "record", "older", "version", "state"):
        assert update_schedule.finish_attempt(state, NOW, outcome).next_due == NOW + 86_400 + 900


def test_network_failures_back_off_six_hours_then_a_day_and_success_resets() -> None:
    state = update_schedule.State(jitter=900)
    first = update_schedule.finish_attempt(state, NOW, "network")
    assert (first.failures, first.next_due) == (1, NOW + 6 * 3600)
    second = update_schedule.finish_attempt(first, NOW, "network")
    assert (second.failures, second.next_due) == (2, NOW + 86_400)
    third = update_schedule.finish_attempt(second, NOW, "network")
    assert third.next_due == NOW + 86_400
    assert update_schedule.finish_attempt(third, NOW, "ok").failures == 0


def test_a_conflict_backs_off_like_a_network_failure() -> None:
    assert (
        update_schedule.finish_attempt(update_schedule.State(), NOW, "conflict").next_due
        == NOW + 6 * 3600
    )


def test_the_deadline_is_written_before_the_request_and_is_a_full_day_ahead() -> None:
    begun = update_schedule.begin_attempt(update_schedule.State(jitter=300), NOW)
    assert (begun.last_attempt, begun.next_due) == (NOW, NOW + 86_400 + 300)


def test_jitter_is_created_once_on_the_first_write() -> None:
    first = update_schedule.with_jitter(update_schedule.State(), lambda low, high: 1234)
    assert first.jitter == 1234
    assert update_schedule.with_jitter(first, lambda low, high: 9).jitter == 1234
    seen: list[tuple[int, int]] = []

    def pick(low: int, high: int) -> int:
        seen.append((low, high))
        return 0

    update_schedule.with_jitter(update_schedule.State(), pick)
    assert seen == [(0, 3601)]


def test_a_never_checked_state_is_due_and_a_future_deadline_is_not() -> None:
    assert update_schedule.is_due(update_schedule.State(), NOW)
    assert not update_schedule.is_due(update_schedule.State(next_due=NOW + 1), NOW)
    assert update_schedule.is_due(update_schedule.State(next_due=NOW), NOW)


def test_a_stored_deadline_within_25_hours_of_the_last_attempt_is_kept() -> None:
    state = update_schedule.State(last_attempt=NOW - 3600, next_due=NOW - 3600 + 25 * 3600)
    assert update_schedule.repair(state, NOW) == state


def test_a_deadline_beyond_25_hours_of_the_last_attempt_is_cut_back() -> None:
    state = update_schedule.State(last_attempt=NOW - 100, next_due=NOW + 10 * 86_400)
    assert update_schedule.repair(state, NOW).next_due == NOW - 100 + 25 * 3600


def test_a_clock_that_went_back_looks_again_in_ten_minutes() -> None:
    state = update_schedule.State(
        last_attempt=NOW + 3 * 86_400, last_success=NOW + 86_400, next_due=NOW + 4 * 86_400
    )
    fixed = update_schedule.repair(state, NOW)
    assert fixed.next_due == NOW + 600
    assert fixed.last_attempt == NOW and fixed.last_success == NOW


def test_a_slightly_future_last_attempt_is_only_clamped() -> None:
    state = update_schedule.State(last_attempt=NOW + 60, next_due=NOW + 3600)
    fixed = update_schedule.repair(state, NOW)
    assert fixed.last_attempt == NOW and fixed.next_due == NOW + 3600


# -- the state file ------------------------------------------------------------------------------


def test_state_round_trips_through_the_private_file(state_dir: Path) -> None:
    state = update_schedule.State(NOW + 5, NOW, NOW - 9, "ok", rec(), 321, 2)
    update_schedule.save_state(state_dir, state)
    assert update_schedule.load_state(state_dir) == state
    assert (state_dir / update_schedule.STATE_FILE).stat().st_mode & 0o777 == 0o600


def test_no_file_means_never_checked_and_creates_nothing(tmp_path: Path) -> None:
    missing = tmp_path.resolve() / "absent"
    assert update_schedule.load_state(missing) == update_schedule.State()
    assert not missing.exists()


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"next_due": 1}',
        b'{"next_due":-1,"last_attempt":null,"last_success":null,"outcome":"ok","record":null,'
        b'"jitter":0,"failures":0}',
        b'{"next_due":null,"last_attempt":null,"last_success":null,"outcome":"zzz","record":null,'
        b'"jitter":0,"failures":0}',
        b'{"next_due":null,"last_attempt":null,"last_success":null,"outcome":"ok","record":{"a":1},'
        b'"jitter":0,"failures":0}',
        b'{"next_due":null,"last_attempt":null,"last_success":null,"outcome":"ok","record":null,'
        b'"jitter":99999,"failures":0}',
    ],
)
def test_a_corrupt_state_is_reset_to_never_checked_with_the_state_outcome(
    state_dir: Path, body: bytes
) -> None:
    path = state_dir / update_schedule.STATE_FILE
    path.write_bytes(body)
    path.chmod(0o600)
    assert update_schedule.load_state(state_dir) == update_schedule.State(outcome="state")


def test_a_group_readable_state_file_is_not_trusted(state_dir: Path) -> None:
    update_schedule.save_state(state_dir, update_schedule.State(record=rec()))
    (state_dir / update_schedule.STATE_FILE).chmod(0o644)
    assert update_schedule.load_state(state_dir) == update_schedule.State(outcome="state")


def test_a_state_folder_that_does_not_exist_cannot_be_written(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        update_schedule.save_state(tmp_path.resolve() / "absent", update_schedule.State())


# -- what is told --------------------------------------------------------------------------------


def test_the_status_object_has_nulls_when_never_checked_and_no_invented_dates() -> None:
    notice = update_schedule.notice_from(update_schedule.State(), "0.3.0", True)
    assert update_schedule.status_object(notice) == {
        "installed": "0.3.0",
        "available": None,
        "check": "on",
        "last_attempt": None,
        "last_success": None,
        "outcome": None,
    }


def test_the_status_object_carries_the_commit_and_notes_when_available() -> None:
    state = update_schedule.State(last_attempt=NOW, last_success=NOW, outcome="ok", record=rec())
    shown = update_schedule.status_object(update_schedule.notice_from(state, "0.3.0", True))
    assert shown["available"] == {
        "version": "0.3.1",
        "commit": COMMIT,
        "issued": "2026-12-01",
        "notes": ["A note."],
    }
    assert shown["last_success"] == update_schedule.moment(NOW) and shown["outcome"] == "ok"


def test_with_the_check_off_nothing_is_offered() -> None:
    state = update_schedule.State(record=rec())
    notice = update_schedule.notice_from(state, "0.3.0", False)
    assert notice.available is None and update_schedule.status_object(notice)["check"] == "off"


def test_a_developer_run_or_an_unknown_version_is_skipped() -> None:
    assert update_schedule.skip_reason("0.3.0", {}) is None
    assert update_schedule.skip_reason("0.3.0", {"FLET_WEB_PATH": "/dev/bundle"}) == "version"
    assert update_schedule.skip_reason("0+unknown", {}) == "version"
    assert update_schedule.skip_reason("0.3.0.dev1", {}) == "version"


# -- hostile or damaged state never raises --------------------------------------------------------

BASE = update_schedule.State().as_doc()


def write_doc(state_dir: Path, doc: object) -> None:
    path = state_dir / update_schedule.STATE_FILE
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
    path.chmod(0o600)


@pytest.mark.parametrize(
    "damage",
    [
        {"outcome": []},
        {"outcome": {}},
        {"outcome": 5},
        {"outcome": None},
        {"outcome": True},
        {"last_success": 10**30},
        {"last_attempt": 10**30},
        {"next_due": 10**30},
        {"next_due": update_schedule.MAX_TIMESTAMP + 1},
        {"last_success": -1},
        {"last_success": 1.5},
        {"last_success": True},
        {"last_success": "now"},
        {"jitter": []},
        {"failures": {}},
        {"failures": True},
        {"record": []},
        {"record": {"schema": []}},
        {
            "record": release_records.Record("0.3.1", COMMIT, "2026-12-01", ()).as_doc()
            | {"notes": [[]]}
        },
        {
            "record": release_records.Record("0.3.1", COMMIT, "2026-12-01", ()).as_doc()
            | {"version": []}
        },
    ],
)
def test_a_damaged_field_resets_to_the_state_outcome_and_never_raises(
    state_dir: Path, damage: dict[str, object]
) -> None:
    write_doc(state_dir, BASE | damage)
    assert update_schedule.load_state(state_dir) == update_schedule.State(outcome="state")


@pytest.mark.parametrize("depth", [100, 10_000, 200_000])
def test_absurd_nesting_is_malformed_input_not_a_crash(state_dir: Path, depth: int) -> None:
    write_doc(state_dir, "[" * depth + "]" * depth)
    assert update_schedule.load_state(state_dir) == update_schedule.State(outcome="state")
    write_doc(state_dir, '{"outcome": ' + "[" * depth + "]" * depth + "}")
    assert update_schedule.load_state(state_dir) == update_schedule.State(outcome="state")


def test_the_largest_formattable_time_is_accepted_and_one_more_is_not(state_dir: Path) -> None:
    write_doc(state_dir, BASE | {"last_success": update_schedule.MAX_TIMESTAMP})
    assert update_schedule.load_state(state_dir).last_success == update_schedule.MAX_TIMESTAMP
    assert update_schedule.moment(update_schedule.MAX_TIMESTAMP) == "9999-12-31T23:59:59Z"
    assert update_schedule.moment(update_schedule.MAX_TIMESTAMP + 1) is None
    assert update_schedule.moment(10**30) is None and update_schedule.moment(-1) is None


@pytest.mark.parametrize("depth", [100, 200_000])
def test_a_record_nested_past_the_limit_is_a_record_refusal(depth: int) -> None:
    with pytest.raises(release_records.UpdateError) as caught:
        release_records.locate(("[" * depth).encode())
    assert caught.value.outcome == "record"
