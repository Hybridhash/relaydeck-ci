"""The cache's advertised limit is checked independently of its estimator."""

import gc
import json
import tracemalloc
from dataclasses import replace
from pathlib import Path

from relaydeck.client.features.usage.loop import CACHE_MAX_BYTES, UsageFeed
from relaydeck.client.features.usage.model import parse_usage_page


def test_four_distinct_large_reports_fit_the_real_retained_memory_budget() -> None:
    base = parse_usage_page(
        json.loads(Path("tests/fixtures/usage/client/page-overall.json").read_text())
    )
    already_tracing = tracemalloc.is_tracing()
    if not already_tracing:
        tracemalloc.start()
    try:
        gc.collect()
        before = tracemalloc.get_traced_memory()[0]
        feed = UsageFeed()
        for query in range(4):
            rows = tuple(
                replace(
                    base.rows[0],
                    model=f"model-{query}-{index}-" + "x" * 50,
                    counters=replace(base.rows[0].counters, input_uncached=index + 1000),
                )
                for index in range(20000)
            )
            page = replace(base, rows=rows, generated_ms=query)
            generation = feed.set_key(query, scope="account-host")
            assert feed.accept(generation, page, query)
            assert feed.state.status == "ready"
        del rows, page
        gc.collect()
        retained = tracemalloc.get_traced_memory()[0] - before
        assert retained <= CACHE_MAX_BYTES
        assert feed.state.page is not None and len(feed.state.page.rows) == 20000
        del feed
        gc.collect()
        assert tracemalloc.get_traced_memory()[0] - before < 128 * 1024
    finally:
        if not already_tracing:
            tracemalloc.stop()
