"""task-8080/F7 -- `_process_row`'s per-fill `fill_seq` computation.

`record_fill_in_position_ledger`'s `fill_seq` parameter used to be hardcoded
to 1 at every call site; task-7998 (F3, commit 639c6591) already replaced
that with the real venue_ts-ordered fill count
(`inbox_processor._process_row`, `len(fills_for_order)`) so that
`pos_journal.idempotency_key` (`f"fill:{order_id}:{fill_seq}"`, §5) stays
unique per fill instead of colliding across partial fills of the same order.
Split out of `test_inbox_processor.py` to stay under the file's loc_over_500
baseline (task-8046 convention) -- reuses that module's fixtures/helpers.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.data.models.base import Currency, Money
from src.foundation.positions.adapters.postgres_journal_repository import (
    IdempotencyDigestMismatchError,
)
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.order_service import repository as legacy_order_repository
from src.services.order_service.position_ledger import record_fill_in_position_ledger
from tests.integration.oms.conftest import create_test_tenant
from tests.integration.oms.test_inbox_processor import _fill_event, _insert_order, _seed_execution


async def test_ingest_two_partial_fills_assigns_distinct_fill_seq_in_ledger(pool):
    """Two partial fills sharing the same order must land as two distinct
    `pos_journal` rows, `fill_seq=1` then `fill_seq=2`, not collapse into one."""
    user_id = await create_test_tenant(pool)
    execution_id = await _seed_execution(pool, user_id)
    order_id, client_order_id, exchange_order_id = await _insert_order(
        pool, user_id, execution_id=execution_id, quantity=Decimal("10")
    )
    processor = InboxProcessor(pool)

    first = _fill_event(
        client_order_id=client_order_id, exchange_order_id=exchange_order_id, quantity=Decimal("4")
    )
    assert await processor.ingest(first) is True
    second = _fill_event(
        client_order_id=client_order_id, exchange_order_id=exchange_order_id, quantity=Decimal("3")
    )
    assert await processor.ingest(second) is True

    async with pool.acquire() as conn:
        idempotency_keys = await conn.fetch(
            "SELECT idempotency_key FROM pos_journal WHERE source_event_id LIKE $1 "
            "ORDER BY sequence_no",
            f"{order_id}:%",
        )
    assert [r["idempotency_key"] for r in idempotency_keys] == [
        f"fill:{order_id}:1",
        f"fill:{order_id}:2",
    ]


async def test_duplicate_fill_seq_with_different_content_rejected_fail_closed(pool):
    """If `fill_seq` computation ever regresses back to a hardcoded constant,
    two distinct fills would share one `pos_journal.idempotency_key`. The
    ledger must reject that as a digest mismatch (fail-closed), not silently
    overwrite the first fill's entry."""
    user_id = await create_test_tenant(pool)
    execution_id = await _seed_execution(pool, user_id)
    order_id, client_order_id, exchange_order_id = await _insert_order(
        pool, user_id, execution_id=execution_id, quantity=Decimal("10")
    )
    processor = InboxProcessor(pool)

    first = _fill_event(
        client_order_id=client_order_id,
        exchange_order_id=exchange_order_id,
        quantity=Decimal("4"),
        price=Decimal("100"),
    )
    assert await processor.ingest(first) is True

    async with pool.acquire() as conn:
        order = await legacy_order_repository.get_by_order_id(conn, order_id)
    assert order is not None

    with pytest.raises(IdempotencyDigestMismatchError):
        await record_fill_in_position_ledger(
            pool,
            order,
            fill_quantity=Decimal("3"),  # different quantity, same fill_seq=1 as `first`
            fill_price=Money(amount=Decimal("100"), currency=Currency.USDT),
            fill_seq=1,
        )
