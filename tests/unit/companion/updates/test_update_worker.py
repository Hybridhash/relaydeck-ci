"""The daily check as the companion runs it: when it looks, what it persists first, how it backs
off, that stop is never delayed, and that logs carry codes only. No network, no real clock."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from relaydeck.companion.config import CompanionConfig, load_config
from relaydeck.companion.updates import schedule as update_schedule
from relaydeck.companion.updates.worker import UpdateService, UpdateWorker, build, read_notice
from relaydeck.releases import records as release_records
from tests.support.companion.asgi_companion import config_file
from tests.support.updates.signing import COMMIT, OTHER_COMMIT

NOW = 1_800_000_000.0


def rec(version: str = "0.3.1", commit: str = COMMIT) -> release_records.Record:
    return release_records.Record(version, commit, "2026-12-01", ("A note.",))


class Clock:
    def __init__(self, at: float = NOW) -> None:
        self.at = at

    def __call__(self) -> float:
        return self.at


class Fetcher:
    """A fetch that answers from a queue and counts its calls."""

    def __init__(self, *answers: release_records.Record | release_records.UpdateError) -> None:
        self.answers = list(answers)
        self.calls = 0

    def __call__(self) -> release_records.Record:
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, release_records.UpdateError):
            raise answer
        return answer


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    path = tmp_path.resolve() / "state"
    path.mkdir(mode=0o700)
    return path


def worker(
    state_dir: Path,
    fetch: Callable[[], release_records.Record],
    clock: Clock,
    installed: str = "0.3.0",
) -> UpdateWorker:
    return UpdateWorker(
        state_dir, installed=installed, fetch=fetch, now=clock, rng=lambda low, high: 600
    )


def look(task: UpdateWorker) -> None:
    asyncio.run(task.evaluate(asyncio.Event()))


def test_a_first_look_stores_the_record_and_publishes_the_notice(state_dir: Path) -> None:
    fetcher = Fetcher(rec())
    task = worker(state_dir, fetcher, Clock())
    look(task)
    assert fetcher.calls == 1
    assert task.notice.available == rec() and task.notice.outcome == "ok"
    stored = update_schedule.load_state(state_dir)
    assert stored.record == rec() and stored.last_success == int(NOW) and stored.jitter == 600
    assert stored.next_due == int(NOW) + 86_400 + 600


def test_the_deadline_is_on_disk_before_the_request_starts(state_dir: Path) -> None:
    seen: list[update_schedule.State] = []

    def fetch() -> release_records.Record:
        seen.append(update_schedule.load_state(state_dir))
        return rec()

    look(worker(state_dir, fetch, Clock()))
    assert seen[0].last_attempt == int(NOW) and seen[0].next_due == int(NOW) + 86_400 + 600
    assert seen[0].outcome == "never"


def test_a_restart_before_the_deadline_makes_no_check(state_dir: Path) -> None:
    look(worker(state_dir, Fetcher(rec()), Clock()))
    again = Fetcher()
    look(worker(state_dir, again, Clock(NOW + 3600)))
    assert again.calls == 0


def test_a_crash_after_the_deadline_was_written_does_not_check_again_at_once(
    state_dir: Path,
) -> None:
    update_schedule.save_state(
        state_dir, update_schedule.begin_attempt(update_schedule.State(jitter=5), int(NOW))
    )
    again = Fetcher()
    look(worker(state_dir, again, Clock(NOW + 60)))
    assert again.calls == 0


def test_the_next_day_it_looks_again(state_dir: Path) -> None:
    look(worker(state_dir, Fetcher(rec()), Clock()))
    later = Fetcher(rec("0.3.2", OTHER_COMMIT))
    task = worker(state_dir, later, Clock(NOW + 86_400 + 601))
    look(task)
    assert later.calls == 1 and task.notice.available == rec("0.3.2", OTHER_COMMIT)


def test_network_failures_back_off_six_hours_then_a_day_across_restarts(state_dir: Path) -> None:
    clock = Clock()
    look(worker(state_dir, Fetcher(release_records.UpdateError("network")), clock))
    first = update_schedule.load_state(state_dir)
    assert (first.failures, first.next_due) == (1, int(NOW) + 6 * 3600)
    clock.at = NOW + 6 * 3600
    look(worker(state_dir, Fetcher(release_records.UpdateError("network")), clock))
    second = update_schedule.load_state(state_dir)
    assert (second.failures, second.next_due) == (2, int(clock.at) + 86_400)
    quiet = Fetcher()
    clock.at += 7 * 3600
    look(worker(state_dir, quiet, clock))
    assert quiet.calls == 0


def test_an_older_record_after_a_newer_one_changes_nothing(state_dir: Path) -> None:
    look(worker(state_dir, Fetcher(rec("0.3.2", OTHER_COMMIT)), Clock()))
    before = update_schedule.load_state(state_dir)
    task = worker(state_dir, Fetcher(rec("0.3.1")), Clock(NOW + 90_000))
    look(task)
    after = update_schedule.load_state(state_dir)
    assert after.record == before.record and after.last_success == before.last_success
    assert after.outcome == "older" and task.notice.available == rec("0.3.2", OTHER_COMMIT)


def test_a_conflict_keeps_the_stored_record_and_backs_off(state_dir: Path) -> None:
    look(worker(state_dir, Fetcher(rec()), Clock()))
    task = worker(state_dir, Fetcher(rec(commit=OTHER_COMMIT)), Clock(NOW + 90_000))
    look(task)
    stored = update_schedule.load_state(state_dir)
    assert stored.record == rec() and stored.outcome == "conflict" and stored.failures == 1
    assert task.notice.available == rec()


def test_a_verification_failure_keeps_the_previous_notice(state_dir: Path) -> None:
    look(worker(state_dir, Fetcher(rec()), Clock()))
    task = worker(state_dir, Fetcher(release_records.UpdateError("signature")), Clock(NOW + 90_000))
    look(task)
    assert task.notice.outcome == "signature" and task.notice.available == rec()


def test_a_manual_upgrade_hides_the_notice(state_dir: Path) -> None:
    look(worker(state_dir, Fetcher(rec()), Clock()))
    assert worker(state_dir, Fetcher(), Clock(), installed="0.3.1").notice.available is None


def test_when_the_deadline_cannot_be_written_no_request_is_made(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    missing = tmp_path.resolve() / "absent"
    fetcher = Fetcher()
    task = worker(missing, fetcher, Clock())
    with caplog.at_level(logging.INFO):
        look(task)
    assert fetcher.calls == 0
    assert task.state.outcome == "state" and task.state.next_due == int(NOW) + 6 * 3600


def test_a_clock_that_went_back_is_looked_at_again_in_ten_minutes(state_dir: Path) -> None:
    update_schedule.save_state(
        state_dir,
        update_schedule.State(last_attempt=int(NOW) + 5 * 86_400, next_due=int(NOW) + 6 * 86_400),
    )
    quiet = Fetcher()
    task = worker(state_dir, quiet, Clock())
    look(task)
    assert quiet.calls == 0 and task.state.next_due == int(NOW) + 600
    look(worker(state_dir, Fetcher(rec()), Clock(NOW + 601)))
    assert update_schedule.load_state(state_dir).record == rec()


# -- lifecycle ------------------------------------------------------------------------------------


def test_stop_during_a_blocked_download_returns_at_once_and_writes_nothing(state_dir: Path) -> None:
    started, release = threading.Event(), threading.Event()

    def blocked() -> release_records.Record:
        started.set()
        release.wait(10)
        return rec()

    task = UpdateWorker(
        state_dir, installed="0.3.0", fetch=blocked, now=Clock(), first_delay_s=0, tick_s=0.01
    )

    async def scenario() -> float:
        stop = asyncio.Event()
        running = asyncio.create_task(task.run(stop))
        while not started.is_set():
            await asyncio.sleep(0.01)
        began = time.monotonic()
        stop.set()
        await asyncio.wait_for(running, 2)
        return time.monotonic() - began

    elapsed = asyncio.run(scenario())
    snapshot = (state_dir / update_schedule.STATE_FILE).read_bytes()
    release.set()
    time.sleep(0.2)
    assert elapsed < 1
    assert (state_dir / update_schedule.STATE_FILE).read_bytes() == snapshot
    assert update_schedule.load_state(state_dir).record is None


def test_a_failure_inside_the_worker_is_caught_and_never_raised(
    state_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    task = worker(state_dir, Fetcher(), Clock())  # no answers: the fetch itself fails
    with caplog.at_level(logging.INFO):
        look(task)
    assert task.notice.outcome == "network"


def test_logs_carry_codes_only(state_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    secret = "https://example.invalid/private?token=abc"
    with caplog.at_level(logging.INFO):
        look(worker(state_dir, Fetcher(release_records.UpdateError("network")), Clock()))
        look(worker(state_dir, Fetcher(rec()), Clock(NOW + 90_000)))
    text = " ".join(record.getMessage() for record in caplog.records)
    assert "update_check outcome=network" in text and "update_check outcome=ok" in text
    assert secret not in text and COMMIT not in text and "0.3.1" not in text
    assert str(state_dir) not in text and "A note." not in text


# -- what the companion builds --------------------------------------------------------------------


def make_config(tmp_path: Path, table: str = "") -> CompanionConfig:
    root = tmp_path.resolve() / "cfg"
    root.mkdir(mode=0o700)
    path = config_file(root)
    if table:
        path.write_text(path.read_text() + "\n" + table + "\n")
    return load_config(path)


def test_check_false_never_builds_a_worker_or_makes_a_call(tmp_path: Path) -> None:
    config = make_config(tmp_path, "[updates]\ncheck = false")
    service, task = build(config, None, installed="0.3.0", environ={})
    assert task is None and service.notice().check is False
    assert service.document()["check"] == "off" and service.document()["available"] is None


@pytest.mark.parametrize(
    ("installed", "environ"),
    [("0+unknown", {}), ("0.3.0", {"FLET_WEB_PATH": "/dev/bundle"}), ("0.3.0.dev1", {})],
)
def test_a_developer_or_unknown_version_builds_no_worker_and_shows_nothing(
    tmp_path: Path, installed: str, environ: dict[str, str]
) -> None:
    config = make_config(tmp_path)
    service, task = build(config, None, installed=installed, environ=environ)
    assert task is None
    assert service.notice().outcome == "version" and service.document()["available"] is None


def test_the_fixture_mode_never_checks(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    _service, task = build(config, None, fixture=True, installed="0.3.0", environ={})
    assert task is None


def test_a_normal_install_builds_a_worker(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    service, task = build(config, None, installed="0.3.0", environ={})
    assert task is not None and service.worker is task


def test_the_service_document_names_the_notice_and_builds_instructions_once_a_minute(
    state_dir: Path,
) -> None:
    look(worker(state_dir, Fetcher(rec()), Clock()))
    task = worker(state_dir, Fetcher(), Clock())
    built: list[str] = []
    clock = Clock()

    def instructions(record: release_records.Record) -> list[str]:
        built.append(record.version)
        return ["line one", "line two"]

    service = UpdateService(
        host_id="home-mac",
        installed="0.3.0",
        check=True,
        skipped=None,
        worker=task,
        instructions=instructions,
        now=clock,
    )
    document = service.document()
    assert document["available"] == {
        "version": "0.3.1",
        "issued": "2026-12-01",
        "notes": ["A note."],
    }
    assert document["instructions"] == ["line one", "line two"]
    assert document["last_success"] == update_schedule.moment(int(NOW))
    service.document()
    assert built == ["0.3.1"]
    clock.at += 61
    service.document()
    assert built == ["0.3.1", "0.3.1"]


def test_the_commands_read_the_stored_notice_without_a_worker(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    notice = read_notice(config, installed="0.3.0", environ={})
    assert notice.outcome == "never" and notice.available is None
