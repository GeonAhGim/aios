"""L4-09 `application/submit_order.py` 통합테스트 — 실 TEST_DATABASE_URL.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-09 DoD("gate
DENY 시 0행"), §2-C 시그니처, §5.2. 동시 50 submit → 1행은
`tests/adversarial/oms/test_concurrent_submit.py`(적대적 전용 파일).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide, OrderStatus, OrderType
from src.services.oms.application.submit_order import OrderSubmitDeniedError, submit_order
from src.services.oms.contracts.v1_commands import IdempotencyScope, SubmitOrderCommand
from src.services.oms.domain.errors import IdempotencyDigestMismatchError, UnknownSymbolError
from src.services.oms.domain.symbol_registry import SymbolRegistry
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext
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


def _registry() -> SymbolRegistry:
    reg = SymbolRegistry()
    reg.register(
        "BTC/USDT", "bitget", "BTCUSDT",
        tick=Decimal("0.1"), lot=Decimal("0.0001"), min_notional=Decimal("5"), quote_ccy="USDT",
    )
    return reg


async def _create_running_execution(pool, user_id: uuid.UUID) -> int:
    strategy_id = f"oms-submit-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', $3::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id, user_id, json.dumps({}),
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 100, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id, user_id,
        )
    return row["id"]


def _command(user_id: uuid.UUID, execution_id: int, **overrides: object) -> SubmitOrderCommand:
    scope = IdempotencyScope(
        tenant_id=user_id, account_ref="acct-1", provider="bitget", strategy_id="s1",
        strategy_version="1.0.0", execution_id=execution_id, intent_seq=1,
        window_start=datetime.now(timezone.utc),
    )
    defaults: dict[str, object] = {
        "command_id": uuid.uuid4(),
        "trace_id": uuid.uuid4(),
        "scope": scope,
        "symbol": "BTC/USDT",
        "side": OrderSide.BUY,
        "order_type": OrderType.MARKET,
        "quantity": Decimal("0.01"),
        "asset_class": AssetClass.CRYPTO,
        "actor_subject_id": user_id,
        "issued_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return SubmitOrderCommand(**defaults)  # type: ignore[arg-type]


async def _allow_gate(context: OrderContext) -> GateDecision:
    return GateDecision(outcome=GateOutcome.ALLOW)


async def _deny_gate(context: OrderContext) -> GateDecision:
    return GateDecision(outcome=GateOutcome.DENY, reason_codes=("RISK_KILL_SWITCH_ACTIVE_TEST",))


async def test_submit_order_new_creates_validated_row_and_enqueues_outbox(pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    cmd = _command(user_id, execution_id)

    result = await submit_order(
        cmd, pool=pool, profile=_profile(), registry=_registry(), pre_submit_gate=_allow_gate,
    )

    assert result.status == OrderStatus.VALIDATED
    async with pool.acquire() as conn:
        order_row = await conn.fetchrow("SELECT * FROM orders WHERE order_id = $1", result.order_id)
        outbox_row = await conn.fetchrow(
            "SELECT * FROM order_command_outbox WHERE order_id = $1", result.order_id
        )
        event_count = await conn.fetchval(
            "SELECT count(*) FROM order_events WHERE order_id = $1", result.order_id
        )
    assert order_row["status"] == "VALIDATED"
    assert order_row["venue_symbol"] == "BTCUSDT"
    assert event_count == 1
    assert outbox_row is not None
    assert outbox_row["command_type"] == "SUBMIT"
    payload = outbox_row["payload"]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    assert payload["order"]["order_id"] == str(result.order_id)
    assert payload["order"]["client_order_id"] == result.client_order_id


async def test_submit_order_existing_replay_returns_same_order_no_extra_row(pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    cmd = _command(user_id, execution_id)

    first = await submit_order(
        cmd, pool=pool, profile=_profile(), registry=_registry(), pre_submit_gate=_allow_gate,
    )
    second = await submit_order(
        cmd, pool=pool, profile=_profile(), registry=_registry(), pre_submit_gate=_allow_gate,
    )

    assert first.order_id == second.order_id
    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE order_id = $1", first.order_id
        )
        outbox_count = await conn.fetchval(
            "SELECT count(*) FROM order_command_outbox WHERE order_id = $1", first.order_id
        )
    assert order_count == 1
    assert outbox_count == 1


async def test_submit_order_gate_deny_leaves_zero_rows(pool):
    """negative — DoD "gate DENY 시 0행": claim이 먼저 INSERT한 임시 CREATED
    행까지 tx 전체가 롤백된다(FK 순서상 orders가 먼저 생기지만 최종 0행)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    cmd = _command(user_id, execution_id)

    with pytest.raises(OrderSubmitDeniedError):
        await submit_order(
            cmd, pool=pool, profile=_profile(), registry=_registry(), pre_submit_gate=_deny_gate,
        )

    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
        outbox_count = await conn.fetchval(
            "SELECT count(*) FROM order_command_outbox oco JOIN orders o "
            "ON o.order_id = oco.order_id WHERE o.execution_id = $1",
            execution_id,
        )
    assert order_count == 0
    assert outbox_count == 0


async def test_submit_order_digest_mismatch_raises_and_rolls_back(pool):
    """negative — 같은 scope, 다른 내용(quantity)의 재시도는 상위 버그 신호로
    거부되고, 실패한 시도의 임시 CREATED 행은 남지 않는다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    cmd1 = _command(user_id, execution_id, quantity=Decimal("0.01"))
    await submit_order(
        cmd1, pool=pool, profile=_profile(), registry=_registry(), pre_submit_gate=_allow_gate,
    )
    cmd2 = cmd1.model_copy(update={"command_id": uuid.uuid4(), "quantity": Decimal("0.02")})

    with pytest.raises(IdempotencyDigestMismatchError):
        await submit_order(
            cmd2, pool=pool, profile=_profile(), registry=_registry(), pre_submit_gate=_allow_gate,
        )

    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
    assert order_count == 1


async def test_submit_order_unknown_symbol_writes_nothing(pool):
    """negative — 미등록 심볼은 registry가 fail-closed로 거부, DB 쓰기 이전에
    걸러진다(어떤 행도 남지 않음)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    cmd = _command(user_id, execution_id, symbol="ETH/USDT")

    with pytest.raises(UnknownSymbolError):
        await submit_order(
            cmd, pool=pool, profile=_profile(), registry=_registry(), pre_submit_gate=_allow_gate,
        )

    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
    assert order_count == 0
