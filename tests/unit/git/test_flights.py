"""WorkerFlights maps worker failures to fixed errors and leaves cancellation alone."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

import pytest

from relaydeck.git.flights import WorkerFlights
from relaydeck.git.locator import LocateError
from relaydeck.git.reader import ReadError
from relaydeck.git.results import ChangesError
from relaydeck.git.runner import GitFailed, GitUnavailable

SECRET_PATH = "/synthetic/private folder/work"


def _raising(exc: BaseException) -> Callable[[], object]:
    def fn() -> object:
        raise exc

    return fn


@pytest.mark.parametrize(
    "exc",
    [
        FileNotFoundError(2, "No such file", SECRET_PATH),
        PermissionError(13, "Permission denied", SECRET_PATH),
        NotADirectoryError(20, "Not a directory", SECRET_PATH),
        OSError(8, "Exec format error", SECRET_PATH),
    ],
    ids=["missing", "denied", "not_directory", "launch"],
)
def test_an_os_error_in_a_worker_is_unavailable_without_its_text(exc: OSError) -> None:
    async def scenario() -> ChangesError:
        flights = WorkerFlights()
        try:
            with pytest.raises(ChangesError) as caught:
                await flights.call(("job",), _raising(exc))
            return caught.value
        finally:
            flights.close()

    error = asyncio.run(scenario())
    assert error.code == "unavailable"
    assert SECRET_PATH not in str(error) and error.__cause__ is None
    assert error.__suppress_context__


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (GitFailed("failed"), "unavailable"),
        (ReadError("failed"), "unavailable"),
        (GitUnavailable("missing"), "git_unavailable"),
        (LocateError("not_found"), "not_found"),
    ],
)
def test_the_existing_mappings_are_kept(exc: Exception, code: str) -> None:
    async def scenario() -> str:
        flights = WorkerFlights()
        try:
            with pytest.raises(ChangesError) as caught:
                await flights.call(("job",), _raising(exc))
            return caught.value.code
        finally:
            flights.close()

    assert asyncio.run(scenario()) == code


def test_a_timeout_in_a_worker_is_not_rewritten() -> None:
    async def scenario() -> None:
        flights = WorkerFlights()
        try:
            with pytest.raises(TimeoutError):
                await flights.call(("job",), _raising(TimeoutError("late")))
        finally:
            flights.close()

    asyncio.run(scenario())


def test_cancelling_a_waiter_is_not_swallowed() -> None:
    async def scenario() -> bool:
        flights = WorkerFlights()
        release = threading.Event()
        started = threading.Event()

        def hold() -> None:
            started.set()
            release.wait()

        try:
            task = asyncio.ensure_future(flights.call(("job",), hold))
            while not started.is_set():
                await asyncio.sleep(0.005)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return task.cancelled()
        finally:
            release.set()
            flights.close()

    assert asyncio.run(scenario())
