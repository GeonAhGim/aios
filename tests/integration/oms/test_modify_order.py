"""L4-17 `application/modify_order.py` 통합테스트 — 실 TEST_DATABASE_URL.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-17 DoD("NH
supports_modify=False 거부"), §4.2 MODIFY_REQUESTED 행.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import OrderStatus, OrderType
from src.exchanges.common.adapter import UnsupportedCapabilityError
from src.services.oms.adapters.order_repository import OrderNotFoundError
from src.services.oms.application.modify_order import modify_order
from src.services.oms.contracts.v1_commands import ModifyOrderCommand
from src.services.oms.domain.errors import InvalidOrderTransitionError, OrderValidationError
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile
from tests.integration.oms.conftest import create_test_user


def _profile(**overrides: object) -> VenueCapabilityProfile:
    defaults: dict[str, object] = {
        "venue": "bitget",
        "asset_classes": [AssetClass.CRYPTO],
        "order_types": {OrderType.MARKET, OrderType.LIMIT},
        "time_in_force": {"GTC", "IOC"},
        "supports_client_order_id": True,
        "client_order_id_max_len": 40,
        "client_order_id_charset": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "id_policy": "STABLE",
        "supports_modify": True,
        "supports_cancel": "YES",
        "supports_ws_orders": True,
        "supports_batch": False,
        "price_tick": {},
        "qty_lot": {},
        "min_notional": {},
        "rate_limits": {},
        "submit_timeout": TimeoutBudget(),
        "query_timeout": TimeoutBudget(),
        "market_hours": None,
        "max_open_orders_per_symbol": 20,
        "verified": "DOC_ONLY",
    }
    defaults.update(overrides)
    return VenueCapabilityProfile(**defaults)  # type: ignore[arg-type]


def _nh_profile(**overrides: object) -> VenueCapabilityProfile:
    """NH — 문서 근거만(§3.2 확정값): supports_modify=False."""
    defaults: dict[str, object] = {
        "venue": "nh", "supports_modify": False, "supports_cancel": "UNVERIFIED",
    }
    defaults.update(overrides)
    return _profile(**defaults)


async def _insert_order(
    pool,
    user_id: uuid.UUID,
    *,
    status: str = "ACKNOWLEDGED",
    order_type: str = "LIMIT",
    quantity: Decimal = Decimal("1"),
    filled_quantity: Decimal = Decimal("0"),
    exchange_order_id: str | None = "EX-1",
) -> uuid.UUID:
    price = Decimal("100") if order_type == "LIMIT" else None
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO orders (
                user_id, client_order_id, strategy_id, strategy_version, symbol,
                exchange, side, order_type, quantity, price, status, filled_quantity,
                exchange_order_id
            ) VALUES ($1, $2, 'oms-modify-test', '1.0.0', 'BTC/USDT', 'bitget', 'BUY',
                      $3, $4, $5, $6, $7, $8)
            RETURNING order_id
            """,
            user_id,
            f"oms-modify-{uuid.uuid4().hex}",
            order_type,
            quantity,
            price,
            status,
            filled_quantity,
            exchange_order_id,
        )


def _command(order_id: uuid.UUID, tenant_id: uuid.UUID, **overrides: object) -> ModifyOrderCommand:
    defaults: dict[str, object] = {
        "command_id": uuid.uuid4(),
        "trace_id": uuid.uuid4(),
        "order_id": order_id,
        "tenant_id": tenant_id,
        "reason": "TEST_MODIFY",
        "actor_subject_id": tenant_id,
        "issued_at": datetime.now(timezone.utc),
        "new_price": Decimal("110"),
        "new_quantity": None,
    }
    defaults.update(overrides)
    return ModifyOrderCommand(**defaults)  # type: ignore[arg-type]


async def test_modify_order_acknowledged_limit_self_loop_and_enqueues_outbox(pool):
    user_id = await create_test_user(pool)
    order_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED")
    cmd = _command(order_id, user_id, new_price=Decimal("110"), new_quantity=Decimal("2"))

    result = await modify_order(cmd, pool=pool, profile=_profile())

    assert result.status == OrderStatus.ACKNOWLEDGED  # 자기루프 — 상태 불변
    assert result.version == 1
    async with pool.acquire() as conn:
        outbox_row = await conn.fetchrow(
            "SELECT * FROM order_command_outbox WHERE order_id = $1", order_id
        )
        event_count = await conn.fetchval(
            "SELECT count(*) FROM order_events WHERE order_id = $1 AND event = 'MODIFY_REQUESTED'",
            order_id,
        )
    assert outbox_row is not None
    assert outbox_row["command_type"] == "MODIFY"
    payload = outbox_row["payload"]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    assert payload["changes"] == {"price": "110", "size": "2"}
    assert event_count == 1


async def test_modify_order_nh_supports_modify_false_rejected_with_zero_writes(pool):
    """DoD — NH `supports_modify=False`는 DB에 손대지 않고(0행) fail-closed
    거부한다. 거래소 호출은 이 계층에서 애초에 일어나지 않는다(구조적 0회)."""
    user_id = await create_test_user(pool)
    order_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED")
    cmd = _command(order_id, user_id)

    with pytest.raises(UnsupportedCapabilityError) as exc_info:
        await modify_order(cmd, pool=pool, profile=_nh_profile())
    assert exc_info.value.capability == "modify_order"
    assert exc_info.value.adapter == "nh"

    async with pool.acquire() as conn:
        outbox_count = await conn.fetchval(
            "SELECT count(*) FROM order_command_outbox WHERE order_id = $1", order_id
        )
        version = await conn.fetchval("SELECT version FROM orders WHERE order_id = $1", order_id)
    assert outbox_count == 0
    assert version == 0


async def test_modify_order_market_type_rejected(pool):
    """negative — 시장가 주문은 정정 불가(§4.2 "LIMIT & profile.supports_modify")."""
    user_id = await create_test_user(pool)
    order_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED", order_type="MARKET")
    cmd = _command(order_id, user_id)

    with pytest.raises(OrderValidationError) as exc_info:
        await modify_order(cmd, pool=pool, profile=_profile())
    assert exc_info.value.reason == "MARKET_NOT_MODIFIABLE"

    async with pool.acquire() as conn:
        version = await conn.fetchval("SELECT version FROM orders WHERE order_id = $1", order_id)
    assert version == 0


async def test_modify_order_no_changes_rejected(pool):
    """negative — new_price/new_quantity 둘 다 없으면 거부(애플리케이션 계층 검증)."""
    user_id = await create_test_user(pool)
    order_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED")
    cmd = _command(order_id, user_id, new_price=None, new_quantity=None)

    with pytest.raises(OrderValidationError) as exc_info:
        await modify_order(cmd, pool=pool, profile=_profile())
    assert exc_info.value.reason == "MODIFY_NO_CHANGES"


async def test_modify_order_partially_filled_rejected(pool):
    """negative(편차 문서화) — outbox_commands.send_modify(L4-14)가 ACKNOWLEDGED
    만 실제로 처리하므로, 이 리프는 PARTIALLY_FILLED 정정 요청을 접수 시점에
    fail-closed 거부한다(모듈 docstring 참조 — 조용한 no-op보다 안전)."""
    user_id = await create_test_user(pool)
    order_id = await _insert_order(
        pool, user_id, status="PARTIALLY_FILLED", filled_quantity=Decimal("0.3")
    )
    cmd = _command(order_id, user_id)

    with pytest.raises(InvalidOrderTransitionError):
        await modify_order(cmd, pool=pool, profile=_profile())


async def test_modify_order_terminal_order_rejected(pool):
    user_id = await create_test_user(pool)
    order_id = await _insert_order(
        pool, user_id, status="FILLED", filled_quantity=Decimal("1")
    )
    cmd = _command(order_id, user_id)

    with pytest.raises(InvalidOrderTransitionError):
        await modify_order(cmd, pool=pool, profile=_profile())


async def test_modify_order_wrong_tenant_raises_not_found(pool):
    owner_id = await create_test_user(pool)
    other_id = await create_test_user(pool)
    order_id = await _insert_order(pool, owner_id, status="ACKNOWLEDGED")
    cmd = _command(order_id, other_id)

    with pytest.raises(OrderNotFoundError):
        await modify_order(cmd, pool=pool, profile=_profile())
