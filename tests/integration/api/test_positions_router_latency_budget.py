"""LB-19 / FA-6: latency budget 음성 검증 — test_positions_router.py 분할 sibling."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.support.positions_router import (
    BASE,
    _create_account,
    _open_position,
    _register,
)
from tests.support.positions_router import client as client
from tests.support.positions_router import pool as pool


async def test_list_positions_latency_budget_zero_rejects_invalid_budget(
    client,
    pool,
) -> None:
    """음성 — budget_ms ≤ 0 은 ValueError 를 던진다 (PerfBudget 불변식)."""
    from tests.support.perf_detection import assert_async_p99_ms

    headers, tenant_id = await _register(client)
    account_id = await _create_account(pool, tenant_id)
    await _open_position(pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1"))

    with pytest.raises(ValueError):
        await assert_async_p99_ms(
            lambda: client.get(BASE, headers=headers),
            budget_ms=0,
        )


async def test_list_positions_latency_budget_negative_rejects_invalid_budget(
    client,
    pool,
) -> None:
    """음성 — budget_ms < 0 은 ValueError 를 던진다."""
    from tests.support.perf_detection import assert_async_p99_ms

    headers, tenant_id = await _register(client)
    account_id = await _create_account(pool, tenant_id)
    await _open_position(pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1"))

    with pytest.raises(ValueError):
        await assert_async_p99_ms(
            lambda: client.get(BASE, headers=headers),
            budget_ms=-1.0,
        )
