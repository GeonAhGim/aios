"""Regression for esc-ci-pytest_perf: the "pristine" replay template must not
inherit committed order/journal rows from the live DB.

Negative: a template cloned from a dirty live DB trips
``assert_no_replay_window_leftovers`` in every ``isolated_replay_db_url`` test.
"""

from __future__ import annotations

from datetime import timedelta

import asyncpg

from tests.integration.eventstore._replay_verify_support import (
    _clock,
    assert_no_replay_window_leftovers,
)
from tests.integration.eventstore.conftest import (
    _PRISTINE_REPLAY_TEMPLATE_URL,
    _clone_dsn,
)


async def test_pristine_template_has_no_window_leftovers() -> None:
    pool = await asyncpg.create_pool(
        _clone_dsn(_PRISTINE_REPLAY_TEMPLATE_URL), min_size=1, max_size=2
    )
    try:
        await assert_no_replay_window_leftovers(
            pool, as_of=_clock() + timedelta(minutes=1), hours=1
        )
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM order_events") == 0
            assert await conn.fetchval("SELECT count(*) FROM ledger_account") > 0
    finally:
        await pool.close()
