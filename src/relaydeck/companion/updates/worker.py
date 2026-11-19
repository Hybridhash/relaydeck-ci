"""Own daily signed-release checks and publish notices without affecting terminal service.
Network work has its own deadline and cannot publish after shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from relaydeck.companion.config import DEFAULT_CONFIG, CompanionConfig
from relaydeck.companion.updates import instructions as update_instructions
from relaydeck.companion.updates import schedule as update_schedule
from relaydeck.releases import records as release_records
from relaydeck.version import __version__

LOG = logging.getLogger(__name__)
SCHEMA = "relaydeck/gateway/v1/updates"
INSTRUCTION_TTL_S = 60


class UpdateWorker:
    def __init__(
        self,
        state_dir: Path,
        *,
        installed: str,
        fetch: Callable[[], release_records.Record],
        now: Callable[[], float] = time.time,
        rng: Callable[[int, int], int] = random.randrange,
        first_delay_s: float = update_schedule.FIRST_DELAY_S,
        tick_s: float = update_schedule.TICK_S,
    ) -> None:
        self._dir = state_dir
        self._installed = installed
        self._fetch = fetch
        self._now = now
        self._rng = rng
        self._first_delay_s = first_delay_s
        self._tick_s = tick_s
        self.state = update_schedule.load_state(state_dir)
        self.notice = update_schedule.notice_from(self.state, installed, True)

    def _publish(self) -> None:
        self.notice = update_schedule.notice_from(self.state, self._installed, True)

    def _persist(self, state: update_schedule.State) -> bool:
        try:
            update_schedule.save_state(self._dir, state)
        except (OSError, ValueError):
            LOG.warning("update_check outcome=state")
            return False
        return True

    async def run(self, stop: asyncio.Event) -> None:
        try:
            if await self._wait(stop, self._first_delay_s):
                return
            while not stop.is_set():
                await self.evaluate(stop)
                if await self._wait(stop, self._tick_s):
                    return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - this feature never affects the companion
            LOG.warning("update_check outcome=state")

    @staticmethod
    async def _wait(stop: asyncio.Event, seconds: float) -> bool:
        """True when stop was set before the time passed."""
        try:
            await asyncio.wait_for(stop.wait(), seconds)
        except TimeoutError:
            return False
        return True

    async def evaluate(self, stop: asyncio.Event) -> None:
        """One pass: look only when the persisted deadline has passed."""
        now = int(self._now())
        state = update_schedule.repair(self.state, now)
        if state != self.state:
            self.state = state
            self._persist(state)  # a repaired clock fix must outlive a restart
        if not update_schedule.is_due(state, now):
            return
        state = update_schedule.with_jitter(state, self._rng)
        started = update_schedule.begin_attempt(state, now)
        if not self._persist(started):
            # No deadline could be recorded, so no request is made; try again in six hours.
            self.state = replace(
                state, next_due=now + update_schedule.RETRY_S, outcome="state", last_attempt=now
            )
            self._publish()
            return
        self.state = started
        outcome, record = await self._look(stop)
        if stop.is_set() or outcome is None:
            return
        final = self._after(started, now, outcome, record)
        self._persist(final)
        self.state = final
        self._publish()
        LOG.info("update_check outcome=%s", final.outcome)

    @staticmethod
    def _after(
        state: update_schedule.State, now: int, outcome: str, record: release_records.Record | None
    ) -> update_schedule.State:
        if outcome != "ok" or record is None:
            return update_schedule.finish_attempt(state, now, outcome)
        decision = release_records.decide(state.record, record)
        if decision == "store":
            return update_schedule.finish_attempt(state, now, "ok", record)
        if decision == "refresh":
            return update_schedule.finish_attempt(state, now, "ok")
        return update_schedule.finish_attempt(state, now, decision)

    async def _look(self, stop: asyncio.Event) -> tuple[str | None, release_records.Record | None]:
        """(outcome, record) from the blocking fetch, or (None, None) when stop came first."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[tuple[str, release_records.Record | None]] = loop.create_future()

        def deliver(result: tuple[str, release_records.Record | None]) -> None:
            if not future.done():
                future.set_result(result)

        def work() -> None:
            result: tuple[str, release_records.Record | None]
            try:
                result = ("ok", self._fetch())
            except release_records.UpdateError as exc:
                result = (exc.outcome, None)
            except Exception:  # noqa: BLE001 - any other failure is a plain network outcome
                result = ("network", None)
            try:
                loop.call_soon_threadsafe(deliver, result)
            except RuntimeError:  # the loop is gone: the worker was abandoned
                pass

        threading.Thread(target=work, daemon=True, name="update-check").start()
        stopper = asyncio.ensure_future(stop.wait())
        racers: set[asyncio.Future[Any]] = {future, stopper}
        done, _ = await asyncio.wait(racers, return_when=asyncio.FIRST_COMPLETED)
        stopper.cancel()
        if future not in done:
            return None, None
        return future.result()


class UpdateService:
    """What the route and the status commands read: the worker's last published result, with the
    update instructions built on demand and kept for a minute."""

    def __init__(
        self,
        *,
        host_id: str,
        installed: str,
        check: bool,
        skipped: str | None,
        worker: UpdateWorker | None,
        instructions: Callable[[release_records.Record], list[str]],
        now: Callable[[], float] = time.time,
    ) -> None:
        self.host_id = host_id
        self._installed = installed
        self._check = check
        self._skipped = skipped
        self.worker = worker
        self._instructions = instructions
        self._now = now
        self._cached: tuple[str, float, list[str]] | None = None

    def notice(self) -> update_schedule.Notice:
        if self.worker is not None and self._check and self._skipped is None:
            return self.worker.notice
        shown = update_schedule.Notice(self._installed, self._check, None, None, None, "never")
        return replace(shown, outcome=self._skipped or "never")

    def _lines(self, record: release_records.Record) -> list[str]:
        cached = self._cached
        stamp = self._now()
        if (
            cached is not None
            and cached[0] == record.commit
            and stamp - cached[1] < INSTRUCTION_TTL_S
        ):
            return list(cached[2])
        lines = self._instructions(record)
        self._cached = (record.commit, stamp, lines)
        return list(lines)

    def document(self) -> dict[str, Any]:
        """The route body. Blocking (it may ask launchd), so the route runs it in a thread."""
        notice = self.notice()
        found = notice.available
        return {
            "schema": SCHEMA,
            "installed": notice.installed,
            "available": None
            if found is None
            else {"version": found.version, "issued": found.issued, "notes": list(found.notes)},
            "check": "on" if notice.check else "off",
            "last_success": update_schedule.moment(notice.last_success),
            "instructions": None if found is None else (self._lines(found) or None),
        }


def build(
    config: CompanionConfig,
    config_path: Path | None,
    *,
    fixture: bool = False,
    installed: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[UpdateService, UpdateWorker | None]:
    """The route's source and, when a check should run, its worker. No worker (and no network)
    when `[updates] check = false`, for a developer run or an unknown version, or in fixture
    mode."""
    installed = installed or __version__
    environ = os.environ if environ is None else environ
    skipped = update_schedule.skip_reason(installed, environ)
    check = config.updates.check
    worker = None
    if check and skipped is None and not fixture:
        worker = UpdateWorker(
            config.state_dir,
            installed=installed,
            fetch=lambda: release_records.fetch_record(clock=time.monotonic),
        )
    profile = config_path or Path(DEFAULT_CONFIG).expanduser().absolute()
    home = Path.home()
    executable = Path(sys.argv[0]).expanduser().resolve()

    def instructions(record: release_records.Record) -> list[str]:
        return update_instructions.instructions(
            record, config_path=profile, home=home, uid=os.getuid(), executable=executable
        )

    service = UpdateService(
        host_id=config.host_id,
        installed=installed,
        check=check,
        skipped=skipped,
        worker=worker,
        instructions=instructions,
    )
    return service, worker


def read_notice(
    config: CompanionConfig,
    *,
    installed: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> update_schedule.Notice:
    """The stored notice for the commands, read-only; it never fetches and creates nothing."""
    installed = installed or __version__
    environ = os.environ if environ is None else environ
    skipped = update_schedule.skip_reason(installed, environ)
    check = config.updates.check
    state = update_schedule.load_state(config.state_dir)
    if skipped is not None or not check:
        return update_schedule.Notice(
            installed, check, None, state.last_attempt, state.last_success, skipped or "never"
        )
    return update_schedule.notice_from(state, installed, True)
