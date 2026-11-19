"""Coalesced worker flights, subscriber cancellation and bounded shutdown."""

from __future__ import annotations

import asyncio
import functools
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar, cast

from relaydeck.git.locator import LocateError
from relaydeck.git.reader import ReadError
from relaydeck.git.results import ChangesError
from relaydeck.git.runner import GitFailed, GitUnavailable

T = TypeVar("T")
MAX_WAITING = 32


class WorkerFlights:
    def __init__(self, workers: int = 2) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="changes")
        self.flights: dict[tuple[str, ...], asyncio.Future[Any]] = {}
        self._waiting = 0
        self.closed = False
        self._cancel = threading.Event()
        self._local = threading.local()
        self._flight_cancel: dict[tuple[str, ...], threading.Event] = {}
        self._subscribers: dict[tuple[str, ...], int] = {}
        self._joined: asyncio.Future[None] | None = None

    def close(self) -> None:
        """Signal only, and idempotent: queued jobs are dropped, running ones are asked to stop.
        A worker stuck in I/O can still delay interpreter exit; bounded reads are the real fix."""
        self.closed = True
        self._cancel.set()
        for stop in list(self._flight_cancel.values()):
            stop.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def joined(self) -> asyncio.Future[None]:
        """Resolves once every worker thread has exited. The join runs on its own daemon thread,
        so abandoning the wait never leaves a pool thread blocked behind it."""
        if self._joined is not None:
            return self._joined
        self.close()
        loop = asyncio.get_running_loop()
        done: asyncio.Future[None] = loop.create_future()

        def finish() -> None:
            if not done.done():
                done.set_result(None)

        def join() -> None:
            self._pool.shutdown(wait=True)
            try:
                loop.call_soon_threadsafe(finish)
            except RuntimeError:  # the loop is already closed
                pass

        threading.Thread(target=join, name="changes-join", daemon=True).start()
        self._joined = done
        return done

    def schedule(self, flight: tuple[str, ...], fn: Callable[..., object], *args: object) -> None:
        if self.closed or flight in self.flights or len(self.flights) >= MAX_WAITING:
            return
        loop = asyncio.get_running_loop()
        stop = self._flight_cancel.setdefault(flight, threading.Event())
        future = loop.run_in_executor(
            self._pool, functools.partial(self._job, stop, _guarded, fn, *args)
        )
        self.flights[flight] = future
        future.add_done_callback(lambda _: self._forget(flight))

    async def call(self, flight: tuple[str, ...], fn: Callable[..., T], *args: object) -> T:
        if self.closed:
            raise ChangesError("unavailable")
        if self._waiting >= MAX_WAITING:
            raise ChangesError("busy", retry_after_s=2)
        future = self.flights.get(flight)
        if future is None:
            if len(self.flights) >= MAX_WAITING:
                raise ChangesError("busy", retry_after_s=2)
            loop = asyncio.get_running_loop()
            stop = self._flight_cancel.setdefault(flight, threading.Event())
            future = loop.run_in_executor(self._pool, functools.partial(self._job, stop, fn, *args))
            self.flights[flight] = future
            future.add_done_callback(lambda _: self._forget(flight))
        self._waiting += 1
        self._subscribers[flight] = self._subscribers.get(flight, 0) + 1
        try:
            result = await asyncio.shield(future)
        except (GitFailed, ReadError) as exc:
            raise ChangesError("unavailable") from exc
        except GitUnavailable as exc:
            raise ChangesError("git_unavailable") from exc
        except LocateError as exc:
            raise ChangesError(str(exc)) from exc
        except TimeoutError:
            raise
        except OSError:
            raise ChangesError("unavailable") from None  # the message carries a path
        finally:
            self._waiting -= 1
            if flight in self._subscribers:  # gone once the job finished and was forgotten
                left = self._subscribers[flight] - 1
                if left > 0:
                    self._subscribers[flight] = left
                else:
                    del self._subscribers[flight]
                    if not future.done():
                        self._flight_cancel.get(flight, self._cancel).set()  # nobody waits
        return cast(T, result)

    def _job(self, stop: threading.Event, fn: Callable[..., T], *args: object) -> T:
        """Run on a worker thread with this flight's cancel event visible to every git call."""
        self._local.cancel = stop
        try:
            return fn(*args)
        finally:
            self._local.cancel = None

    def _forget(self, flight: tuple[str, ...]) -> None:
        self.flights.pop(flight, None)
        self._flight_cancel.pop(flight, None)
        self._subscribers.pop(flight, None)

    def stop_event(self) -> threading.Event:
        return getattr(self._local, "cancel", None) or self._cancel


def _guarded(fn: Callable[..., object], *args: object) -> None:
    try:
        fn(*args)
    except Exception:  # noqa: BLE001, S110 - background work; its own state already says why
        pass
