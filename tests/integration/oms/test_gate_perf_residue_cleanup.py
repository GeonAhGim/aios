"""task-11513: raw concurrent-update seeds must not outlive their test.

FA-15 / INVARIANTS I-07, I-10: reproduce assertion and writer failures
against the real source, then inspect the same shared worker database.
"""
from __future__ import annotations

import pytest

from tests.integration.oms import test_gate_perf_multiinstance as source


@pytest.mark.parametrize("failure", [None, "assertion", "writer", "gather"])
async def test_concurrent_source_cleans_eventless_order_even_on_failure(pool, monkeypatch, failure):
    order_ids = []
    real_insert = source.insert_order
    real_gather = source.asyncio.gather

    async def tracked_insert(*args, **kwargs):
        order_id = await real_insert(*args, **kwargs)
        order_ids.append(order_id)
        return order_id

    async def injected_gather(*args, **kwargs):
        results = await real_gather(*args, **kwargs)
        if failure == "assertion":
            return [False] * len(results)
        if failure == "writer":
            return [RuntimeError("injected writer failure"), *results[1:]]
        if failure == "gather":
            raise RuntimeError("injected gather failure")
        return results

    monkeypatch.setattr(source, "insert_order", tracked_insert)
    monkeypatch.setattr(source.asyncio, "gather", injected_gather)
    if failure:
        expected = AssertionError if failure == "assertion" else RuntimeError
        with pytest.raises(expected):
            await source.test_concurrent_conditional_update_multi_instance_exactly_one_wins(pool)
    else:
        await source.test_concurrent_conditional_update_multi_instance_exactly_one_wins(pool)
    assert len(order_ids) == 1
    async with pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM orders WHERE order_id = $1", order_ids[0]
        ) == 0
