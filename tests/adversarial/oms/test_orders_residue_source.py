"""task-11767: reproduce the source shape without polluting the worker DB.

FA-15 replay parity and INVARIANTS I-07/I-10: the wired gate must fail
on the actual cross-tenant test seed, even though its assertions pass.
"""
import os
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import pytest

from scripts import replay_verify
from tests.adversarial.oms.test_cross_tenant_isolation import (
    isolated_event_pool,  # noqa: F401 -- shared disposable fixture
)
from tests.adversarial.oms.test_cross_tenant_isolation import (
    test_list_order_events_cross_tenant_does_not_leak_existence as run_source,
)
from tests.support.orders_residue import check_orders


async def test_actual_source_has_incident_digest_but_shared_worker_stays_clean(
    isolated_event_pool,  # noqa: F811 -- imported fixture
):
    await run_source(isolated_event_pool)
    report = await replay_verify.verify(
        isolated_event_pool, as_of=datetime.now(UTC), hours=24
    )
    mismatches = [m for m in report.mismatches if m.domain == "orders"]
    assert len(mismatches) == 1
    assert mismatches[0].replayed_digest.startswith("b5073f2d")
    async with isolated_event_pool.acquire() as conn:
        database = await conn.fetchval("SELECT current_database()")
    parts = urlsplit(os.environ["DATABASE_URL"])
    clone_url = urlunsplit(parts._replace(path="/" + database))
    with pytest.raises(AssertionError, match="source reproduction"):
        await check_orders(clone_url, "source reproduction")
    await check_orders(os.environ["DATABASE_URL"], "shared worker after isolated source")
