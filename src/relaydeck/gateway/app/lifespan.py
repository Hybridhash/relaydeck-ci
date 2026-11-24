"""Gateway background task lifetime."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from relaydeck.gateway.app.bucket_retirement import BucketRetirement
from relaydeck.gateway.app.revocation import revoke_host_and_close
from relaydeck.gateway.app.state import GatewayState
from relaydeck.gateway.app.terminal_epochs import (
    reconcile_terminal_routes,
)
from relaydeck.gateway.features.access.api import drain_revoke_requests
from relaydeck.gateway.http.middleware.loop_watch import LoopWatch
from relaydeck.host.concurrency.offload import run_blocking

if TYPE_CHECKING:
    from relaydeck.gateway.features.notifications.api import PushWorker


def build_lifespan(
    state: GatewayState, retirement: BucketRetirement
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    async def drain_spool() -> None:
        while True:
            for host_id in await run_blocking(drain_revoke_requests, state.revoke_spool):
                await revoke_host_and_close(state, host_id)
            await asyncio.sleep(max(0.01, state.revoke_poll_s))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Every background task is registered with the stack as soon as it exists, so a failure
        # while starting a later one still cancels and awaits the earlier ones.
        async with AsyncExitStack() as stack:
            stack.push_async_callback(retirement.stop)
            retirement.start()
            stack.push_async_callback(close_identity_provider)
            if state.pictures is not None:
                from relaydeck.gateway.features.pictures.api import run_picture_cleanup

                await asyncio.to_thread(state.pictures.cleanup, state.now_ms())
                picture_task = asyncio.create_task(
                    run_picture_cleanup(state.pictures, state.now_ms)
                )
                stack.push_async_callback(cancel_task, picture_task)
            if state.revoke_spool:
                stack.push_async_callback(cancel_task, asyncio.create_task(drain_spool()))
            from relaydeck.gateway.features.notifications.api import PushWorker

            worker = (
                PushWorker(state.push, state.push_provider, authorized=state.push_delivery_allowed)
                if state.push_provider is not None
                else None
            )
            app.state.push_worker = worker
            state.push_worker = worker
            stack.callback(set_push_worker, None)
            if worker is not None:
                stack.push_async_callback(worker.stop)
                worker.start()
            notification_stop = asyncio.Event()
            notification_task = asyncio.create_task(
                state.notifications.run(
                    notification_stop, state.now_ms, state.emit_notification_async
                )
            )
            stack.push_async_callback(stop_notifications, notification_stop, notification_task)
            terminal_task = asyncio.create_task(reconcile_terminal_routes(state))
            stack.push_async_callback(cancel_task, terminal_task)

            def on_beat(lag: float) -> None:
                state.metrics.record_loop_delay(lag)
                state.metrics.sample_sockets(state.sockets.terminal_count())

            monitor = LoopWatch(on_beat=on_beat)
            monitor.start()
            stack.push_async_callback(monitor.close)
            stack.callback(set_started, False)
            state.started = True
            yield

    async def close_identity_provider() -> None:
        close = getattr(state.identity_provider, "close", None)
        if close is not None:
            await close()

    def set_started(value: bool) -> None:
        state.started = value

    def set_push_worker(worker: PushWorker | None) -> None:
        state.push_worker = worker

    return lifespan


async def cancel_task(task: asyncio.Task[Any]) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def stop_notifications(stop: asyncio.Event, task: asyncio.Task[Any]) -> None:
    stop.set()
    await task
