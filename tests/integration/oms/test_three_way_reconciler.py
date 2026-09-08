"""L4-24 `application/three_way_reconciler.py` 통합테스트 — 실 TEST_DATABASE_URL.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-24 DoD — REC-001/
002/003/006 각각 재현 + MATERIAL 등급 동안 같은 계정의 신규 submit_order가
DENY, 해소 후 재허용(배선 증명: `reconcile_account`의 `activate_safety_control`
호출을 지우면 DENY 단언이 실패한다). 금액 비교는 전부 `Decimal(...)`(DoD (c)).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order as ProviderOrder
from src.data.models.trading import OrderSide, OrderStatus, OrderType
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.services.oms.application.submit_order import OrderSubmitDeniedError, submit_order
from src.services.oms.application.three_way_reconciler import reconcile_account
from src.services.oms.contracts.v1_commands import OrderIdempotencyScope, SubmitOrderCommand
from src.services.oms.domain.symbol_registry import SymbolRegistry
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.oms.conftest import create_test_tenant, seed_entity_context


class _ScriptedAdapter(FakeExchangeAdapter):
    """`get_open_orders()`만 스크립트로 제어한다 — 나머지는 공용 대역 그대로."""

    def __init__(self, *, open_orders: list[ProviderOrder] | None = None, fail: bool = False):
        super().__init__()
        self._scripted_open_orders = open_orders or []
        self._fail = fail

    async def get_open_orders(self, symbol: str | None = None) -> list[ProviderOrder]:
        if self._fail:
            raise TimeoutError("provider timed out (test double)")
        return self._scripted_open_orders


def _provider_order(cid: str, quantity: Decimal, filled_quantity: Decimal) -> ProviderOrder:
    return ProviderOrder(
        exchange_order_id=f"ex-{uuid.uuid4().hex[:8]}",
        client_order_id=cid,
        strategy_id="s1",
        strategy_version="1.0.0",
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=quantity,
        status=OrderStatus.SUBMITTED,
        filled_quantity=filled_quantity,
        asset_class=AssetClass.CRYPTO,
    )


async def _insert_open_order(
    pool,
    tenant_id: uuid.UUID,
    *,
    client_order_id: str,
    quantity: Decimal,
    filled_quantity: Decimal,
) -> uuid.UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO orders (
                user_id, client_order_id, strategy_id, strategy_version, symbol,
                exchange, side, order_type, quantity, status, filled_quantity
            ) VALUES ($1, $2, 'oms-recon-test', '1.0.0', 'BTC/USDT', 'bitget', 'BUY',
                      'LIMIT', $3, 'SUBMITTED', $4)
            RETURNING order_id
            """,
            tenant_id,
            client_order_id,
            quantity,
            filled_quantity,
        )


async def _active_account_controls(pool, tenant_id: uuid.UUID) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM safety_control WHERE scope = 'ACCOUNT' AND scope_ref = $1 "
            "AND state = 'ACTIVE'",
            str(tenant_id),
        )


def _profile() -> VenueCapabilityProfile:
    return VenueCapabilityProfile(
        venue="bitget",
        asset_classes=[AssetClass.CRYPTO],
        order_types={OrderType.MARKET, OrderType.LIMIT},
        time_in_force={"GTC", "IOC"},
        supports_client_order_id=True,
        client_order_id_max_len=40,
        client_order_id_charset="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        id_policy="STABLE",
        supports_modify=True,
        supports_cancel="YES",
        supports_ws_orders=True,
        supports_batch=False,
        price_tick={},
        qty_lot={},
        min_notional={},
        rate_limits={},
        submit_timeout=TimeoutBudget(),
        query_timeout=TimeoutBudget(),
        market_hours=None,
        max_open_orders_per_symbol=20,
        verified="DOC_ONLY",
    )


def _registry() -> SymbolRegistry:
    reg = SymbolRegistry()
    reg.register(
        "BTC/USDT", "bitget", "BTCUSDT",
        tick=Decimal("0.1"), lot=Decimal("0.0001"), min_notional=Decimal("5"), quote_ccy="USDT",
    )
    return reg


async def _create_running_execution(pool, user_id: uuid.UUID) -> int:
    strategy_id = f"oms-recon-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', '{}'::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id, user_id,
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


def _submit_cmd(user_id: uuid.UUID, execution_id: int) -> SubmitOrderCommand:
    scope = OrderIdempotencyScope(
        tenant_id=user_id, account_ref="acct-1", provider="bitget", strategy_id="s1",
        strategy_version="1.0.0", execution_id=execution_id, intent_seq=1,
        window_start=datetime.now(timezone.utc),
    )
    return SubmitOrderCommand(
        command_id=uuid.uuid4(), trace_id=uuid.uuid4(), scope=scope, symbol="BTC/USDT",
        side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO, actor_subject_id=user_id,
        issued_at=datetime.now(timezone.utc),
    )


async def test_reconcile_healthy_reports_zero_discrepancies(pool):
    """REC-001 — 내부·거래소가 완전히 일치하면 discrepancies는 0건."""
    user_id = await create_test_tenant(pool)
    client_id = f"recon-cid-{uuid.uuid4().hex[:10]}"
    await _insert_open_order(
        pool, user_id, client_order_id=client_id,
        quantity=Decimal("5"), filled_quantity=Decimal("2"),
    )
    adapter = _ScriptedAdapter(
        open_orders=[_provider_order(client_id, Decimal("5"), Decimal("2"))]
    )

    summary = await reconcile_account(
        pool=pool, adapter=adapter, tenant_id=user_id, connection_id=None,
        account_ref=str(user_id), window=timedelta(minutes=5),
    )

    assert summary.overall_classification == "HEALTHY"
    assert summary.discrepancies == []
    assert await _active_account_controls(pool, user_id) == 0


async def test_reconcile_material_mismatch_denies_and_resolving_allows_submit(pool):
    """REC-002 + DoD (b) — 체결수량 불일치는 MATERIAL_MISMATCH, 그동안 같은
    계정의 신규 submit_order는 DENY, 해소되면 같은 명령이 다시 허용된다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    client_id = f"recon-cid-{uuid.uuid4().hex[:10]}"
    await _insert_open_order(
        pool, user_id, client_order_id=client_id,
        quantity=Decimal("10"), filled_quantity=Decimal("3"),
    )
    mismatched_adapter = _ScriptedAdapter(
        open_orders=[_provider_order(client_id, Decimal("10"), Decimal("7"))]
    )

    summary = await reconcile_account(
        pool=pool, adapter=mismatched_adapter, tenant_id=user_id, connection_id=None,
        account_ref=str(user_id), window=timedelta(minutes=5),
    )

    assert summary.overall_classification == "MATERIAL_MISMATCH"
    assert len(summary.discrepancies) == 1
    discrepancy = summary.discrepancies[0]
    assert discrepancy.kind == "FILLED_QTY_MISMATCH"
    assert discrepancy.internal_value == Decimal("3")
    assert discrepancy.provider_value == Decimal("7")
    assert await _active_account_controls(pool, user_id) == 1

    submit_cmd = _submit_cmd(user_id, execution_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    with pytest.raises(OrderSubmitDeniedError) as exc_info:
        await submit_order(
            submit_cmd, pool=pool, profile=_profile(), registry=_registry(), pre_submit_gate=gate,
            entity_context=entity_context, entity_repo=PostgresEntityRepository(pool),
        )
    assert any(code.startswith("RISK_KILL_SWITCH_ACTIVE_") for code in exc_info.value.reason_codes)
    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
    assert order_count == 0

    resolved_adapter = _ScriptedAdapter(
        open_orders=[_provider_order(client_id, Decimal("10"), Decimal("3"))]
    )
    resolved_summary = await reconcile_account(
        pool=pool, adapter=resolved_adapter, tenant_id=user_id, connection_id=None,
        account_ref=str(user_id), window=timedelta(minutes=5),
    )
    assert resolved_summary.overall_classification == "HEALTHY"
    assert await _active_account_controls(pool, user_id) == 0

    allowed = await submit_order(
        submit_cmd, pool=pool, profile=_profile(), registry=_registry(), pre_submit_gate=gate,
        entity_context=entity_context, entity_repo=PostgresEntityRepository(pool),
    )
    assert allowed.status == OrderStatus.VALIDATED


async def test_reconcile_provider_unavailable_never_assumes_zero(pool):
    """REC-003 — provider 조회 실패(타임아웃)는 PROVIDER_UNAVAILABLE이고,
    체결/잔고를 0으로 가정하지 않는다(provider_value는 None으로 남는다)."""
    user_id = await create_test_tenant(pool)
    client_id = f"recon-cid-{uuid.uuid4().hex[:10]}"
    await _insert_open_order(
        pool, user_id, client_order_id=client_id,
        quantity=Decimal("4"), filled_quantity=Decimal("1"),
    )
    adapter = _ScriptedAdapter(fail=True)

    summary = await reconcile_account(
        pool=pool, adapter=adapter, tenant_id=user_id, connection_id=None,
        account_ref=str(user_id), window=timedelta(minutes=5),
    )

    assert summary.overall_classification == "PROVIDER_UNAVAILABLE"
    assert len(summary.discrepancies) == 1
    discrepancy = summary.discrepancies[0]
    assert discrepancy.provider_value is None
    assert discrepancy.internal_value == Decimal("1")
    assert await _active_account_controls(pool, user_id) == 1


async def test_reconcile_rerun_dedupe_does_not_duplicate_safety_control(pool):
    """REC-006 — 같은 불일치로 재실행해도 ACCOUNT 세이프티 컨트롤은 1개만
    유지된다(중복 활성화 없음)."""
    user_id = await create_test_tenant(pool)
    client_id = f"recon-cid-{uuid.uuid4().hex[:10]}"
    await _insert_open_order(
        pool, user_id, client_order_id=client_id,
        quantity=Decimal("6"), filled_quantity=Decimal("1"),
    )
    adapter = _ScriptedAdapter(
        open_orders=[_provider_order(client_id, Decimal("6"), Decimal("5"))]
    )

    first = await reconcile_account(
        pool=pool, adapter=adapter, tenant_id=user_id, connection_id=None,
        account_ref=str(user_id), window=timedelta(minutes=5),
    )
    second = await reconcile_account(
        pool=pool, adapter=adapter, tenant_id=user_id, connection_id=None,
        account_ref=str(user_id), window=timedelta(minutes=5),
    )

    assert first.overall_classification == "MATERIAL_MISMATCH"
    assert second.overall_classification == "MATERIAL_MISMATCH"
    assert await _active_account_controls(pool, user_id) == 1
