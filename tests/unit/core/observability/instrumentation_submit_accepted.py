"""Accepted submission setup for instrumentation tests."""

from uuid import uuid4

import pytest

from src.data.models.trading import Order, OrderStatus
from src.services.order_service import repository as repository_module
from src.services.order_service.submit import submit_order
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.unit.core.observability.test_instrumentation_points import (
    _allow_gate,
    _FakePool,
    _order,
    _SpyMetrics,
)


async def submit_accepted_order(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Order, _SpyMetrics]:
    order = _order()

    async def fake_insert(conn: object, order: Order, *, user_id: object) -> Order:
        return order

    async def fake_update_from_exchange(
        conn: object, order: Order, *, expected_status: object, actor_subject_id: object
    ) -> Order:
        return order

    monkeypatch.setattr(repository_module, "insert", fake_insert)
    monkeypatch.setattr(
        repository_module, "update_from_exchange_with_fill_event", fake_update_from_exchange
    )

    adapter = FakeExchangeAdapter(place_order_result_status=OrderStatus.SUBMITTED)
    spy = _SpyMetrics()

    result = await submit_order(
        order,
        user_id=uuid4(),
        adapter=adapter,
        pool=_FakePool(),
        metrics=spy,
        pre_submit_gate=_allow_gate,
    )

    return result, spy
