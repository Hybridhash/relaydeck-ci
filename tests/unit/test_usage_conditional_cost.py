"""Report validation avoids payload transfer/parsing and caps retained client pages."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import relaydeck.client.features.usage.presentation as presentation_usage
from relaydeck.client.features.usage.api import UsageQuery, fetch_usage
from relaydeck.client.features.usage.loop import UsageFeed
from relaydeck.client.features.usage.model import parse_usage_page
from relaydeck.gateway.features.usage_read.api import UsageService
from relaydeck.usage.config import UsageConfig
from relaydeck.usage.contracts import UsageQuery as ServerQuery
from relaydeck.usage.store import SqliteUsageStore
from tests.support.usage.results import wire_page


def test_conditional_report_checks_generation_binding_and_expiry(tmp_path: Path) -> None:
    async def scenario() -> None:
        now = 1791676800000
        store = SqliteUsageStore(tmp_path / "usage.db", "UTC", clock=lambda: now)
        service = UsageService(
            store, UsageConfig(timezone="UTC", sources=()), "h1", tmp_path, lambda: now
        )
        query, selector = ServerQuery("h1", "2026-10-05", "2026-10-11"), "days:7"
        try:
            first = await wire_page(service, query, "account", "device", selector=selector)
            tag = first.headers["etag"]
            for _ in range(3):
                same = await wire_page(
                    service, query, "account", "device", selector=selector, if_none_match=tag
                )
                assert same.status_code == 304 and same.body == b""
            other = await wire_page(
                service, query, "account", "other-device", selector=selector, if_none_match=tag
            )
            assert other.status_code == 200 and other.headers["etag"] != tag
            assert service.store is not None
            with service.store._transaction() as db:
                db.execute(
                    "INSERT INTO meta VALUES('data_generation','1') ON CONFLICT(key) "
                    "DO UPDATE SET value=CAST(value AS INTEGER)+1"
                )
            moved = await wire_page(
                service, query, "account", "device", selector=selector, if_none_match=tag
            )
            assert moved.status_code == 200 and moved.headers["etag"] != tag
        finally:
            await service.close()
            assert service.store is not None
            service.store.close()

    asyncio.run(scenario())


def page() -> Any:
    raw = json.loads(Path("tests/fixtures/usage/client/page-overall.json").read_text())
    return replace(parse_usage_page(raw), report_id='"report"')


def test_unchanged_refresh_reuses_page_without_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        previous = page()

        async def fetch(query: UsageQuery) -> dict[str, object]:
            assert query.report_id == previous.report_id
            return {"not_modified": True, "report_id": query.report_id}

        def parse_forbidden(_: object) -> None:
            pytest.fail("unchanged response was parsed")

        monkeypatch.setattr("relaydeck.client.features.usage.api.parse_usage_page", parse_forbidden)
        assert (
            await fetch_usage(
                fetch,
                UsageQuery(previous.host_id, days=7),
                is_current=lambda: True,
                previous=previous,
            )
            is previous
        )
        feed = UsageFeed()
        generation = feed.set_key("query")
        feed.accept(generation, previous, 1)
        for _ in range(100):
            feed.accept(generation, previous, 2)
        assert feed.retry_delay() == 60

    asyncio.run(scenario())


def test_page_cache_is_bounded_by_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("relaydeck.client.features.usage.loop.CACHE_MAX_BYTES", 250)
    monkeypatch.setattr("relaydeck.client.features.usage.loop.page_bytes", lambda _: 150)
    feed = UsageFeed()
    for index in range(4):
        generation = feed.set_key(index, scope="scope")
        feed.accept(generation, page(), index)
    assert len(feed._cache) == 1 and sum(feed._sizes.values()) <= 250


def test_aggregate_memo_keeps_clock_fresh_and_tracks_page_and_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from relaydeck.client.features.usage import selectors
    from relaydeck.client.screens.usage import model as screen_model
    from relaydeck.client.screens.usage.memo import PageMemo

    current = page()
    memo = PageMemo()
    calls: list[int] = []
    original = selectors.totals

    def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(selectors, "totals", counted)
    monkeypatch.setattr("relaydeck.client.features.usage.api.totals", counted)
    now = 1791676800000

    def build(page_value: Any, flt: selectors.Filter, at_ms: int) -> Any:
        return screen_model.build(
            page_value,
            state=presentation_usage.STATE_READY,
            flt=flt,
            now_ms=at_ms,
            width=screen_model.WIDTH_COMPACT,
            memo=memo,
        )

    flt = selectors.Filter(period="today")
    first = build(current, flt, now)
    cold_calls = len(calls)
    second = build(current, flt, now + 60_000)
    warm_calls = len(calls) - cold_calls
    assert warm_calls < cold_calls and first.range_text != second.range_text
    changed_filter = replace(flt, provider="kind-a")
    changed = build(current, changed_filter, now)
    fresh = screen_model.build(
        current,
        state=presentation_usage.STATE_READY,
        flt=changed_filter,
        now_ms=now,
        width=screen_model.WIDTH_COMPACT,
    )
    assert changed == fresh
    before_page = len(calls)
    build(replace(current, generated_ms=current.generated_ms + 1), flt, now)
    assert len(calls) - before_page >= cold_calls


def test_real_large_pages_obey_the_retained_byte_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from relaydeck.client.features.usage.loop import page_bytes

    original = page()
    large = replace(
        original,
        rows=tuple(replace(original.rows[0], model=f"model-{index}") for index in range(10000)),
    )
    size = page_bytes(large)
    limit = size * 2 + 4096
    monkeypatch.setattr("relaydeck.client.features.usage.loop.CACHE_MAX_BYTES", limit)
    feed = UsageFeed()
    for index in range(4):
        generation = feed.set_key(index, scope="scope")
        feed.accept(generation, replace(large, generated_ms=index), index)
    assert sum(feed._sizes.values()) <= limit and len(feed._cache) <= 2


def test_conditional_admission_cap_serves_cached_as_of_data_not_an_unvalidated_304(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        now = 1791676800000
        store = SqliteUsageStore(tmp_path / "usage.db", "UTC", clock=lambda: now)
        service = UsageService(
            store, UsageConfig(timezone="UTC", sources=()), "h1", tmp_path, lambda: now
        )
        query = ServerQuery("h1", "2026-10-05", "2026-10-11")
        try:
            first = await wire_page(service, query, "account", "device")
            monkeypatch.setattr("relaydeck.gateway.features.usage_read.service.MAX_INFLIGHT", 0)

            def unavailable(*_args: Any, **_kwargs: Any) -> None:
                pytest.fail("admission fallback attempted report validation")

            monkeypatch.setattr(service, "_validate", unavailable)
            cached = await wire_page(
                service, query, "account", "device", if_none_match=first.headers["etag"]
            )
            assert cached.status_code == 200 and cached.body == first.body
            assert cached.headers["etag"] == first.headers["etag"]
            other = await wire_page(
                service, query, "account", "different-device", if_none_match=first.headers["etag"]
            )
            assert other.status_code == 503
        finally:
            await service.close()
            store.close()

    asyncio.run(scenario())
