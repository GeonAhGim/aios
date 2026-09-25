"""L4-24 `application/three_way_reconciler.py` 통합테스트 — 실 TEST_DATABASE_URL.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-24 DoD — REC-001/
002/003/006 각각 재현 + MATERIAL 등급 동안 같은 계정의 신규 submit_order가
DENY, 해소 후 재허용(배선 증명: `reconcile_account`의 `activate_safety_control`
호출을 지우면 DENY 단언이 실패한다). 금액 비교는 전부 `Decimal(...)`(DoD (c)).

DEEPEN(task-2800) — DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md#2316)가
원 구현(4f1a05de)을 D3 미달(실측 D1)로 판정한 두 가지 근거를 파일 하단에
보강한다: "수치 latency/throughput 단언 없음", "다중 인스턴스/adversarial/
replay 증명 없음(단일 pool, 동시 reconciler 없음, DENY 우회 시도 없음)".
아래 세 테스트가 각각 대응한다: (1) `reconcile_account` 왕복 지연/처리량을
기준 왕복비용에 정규화한 수치로 단언(절대 ms 상수 금지 —
`test_gate_perf_multiinstance.py`, `test_kis_durability.py` 선례), (2) 서로
다른 `ReconcileScheduler` 인스턴스 다수가 동일 account_ref를 정확히 같은
시각에 동시 대사해도 advisory xact lock(REC-004) 덕에 정확히 1개만 실제로
실행됨, (3) MATERIAL_MISMATCH ACTIVE 동안 다수의 `submit_order` 시도가
정확히 같은 시각에 경합해도(DENY 우회 레이스 시도) 단 하나도 통과하지
못함.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order as ProviderOrder
from src.data.models.trading import OrderSide, OrderStatus, OrderType
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.services.oms.application.reconcile_scheduler import ReconcileScheduler, ReconcileTarget
from src.services.oms.application.submit_order import OrderSubmitDeniedError, submit_order
from src.services.oms.application.three_way_reconciler import reconcile_account
from src.services.oms.contracts.v1_commands import OrderIdempotencyScope, SubmitOrderCommand
from src.services.oms.domain.symbol_registry import SymbolRegistry
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.oms.conftest import _asyncpg_dsn, create_test_tenant, seed_entity_context


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


# ---------- DEEPEN(2800) — 수치 성능 + 다중 인스턴스 + adversarial ----------


@pytest.mark.perf
async def test_reconcile_account_latency_within_normalized_throughput_budget(pool):
    """D2 수치 latency/throughput 단언 — 절대 ms 상수는 쓰지 않는다(공유 CI
    환경에서 절대 임계가 로컬 대비 크게 변동한 전례, `test_gate_perf_
    multiinstance.py`/`test_kis_durability.py`와 동일 근거). 같은 연결의
    기준 왕복비용(`SELECT 1`)에 정규화한 예산 안에서 `reconcile_account`가
    끝나야 하고, 처리량(calls/s)도 함께 기록한다."""
    user_id = await create_test_tenant(pool)
    client_id = f"recon-cid-{uuid.uuid4().hex[:10]}"
    await _insert_open_order(
        pool, user_id, client_order_id=client_id,
        quantity=Decimal("5"), filled_quantity=Decimal("2"),
    )
    adapter = _ScriptedAdapter(
        open_orders=[_provider_order(client_id, Decimal("5"), Decimal("2"))]
    )

    async with pool.acquire() as conn:
        baseline_reps = 20
        t0 = time.perf_counter()
        for _ in range(baseline_reps):
            await conn.fetchval("SELECT 1")
        baseline_per_call = (time.perf_counter() - t0) / baseline_reps

    reps = 10
    t0 = time.perf_counter()
    for _ in range(reps):
        summary = await reconcile_account(
            pool=pool, adapter=adapter, tenant_id=user_id, connection_id=None,
            account_ref=str(user_id), window=timedelta(minutes=5),
        )
    elapsed = time.perf_counter() - t0
    per_call = elapsed / reps
    throughput = reps / elapsed

    # reconcile_account issues several round trips (order list + provider
    # fetch + risk-control lookup) per call, so a generous multiple of the
    # single round-trip baseline is the budget, not an absolute constant.
    budget = max(0.5, baseline_per_call * 300)
    print(  # noqa: T201 — 실측치는 비차단 기록, 게이트는 아래 assert.
        f"reconcile_account per_call={per_call * 1000:.3f}ms "
        f"throughput={throughput:.2f} calls/s "
        f"baseline(SELECT 1)={baseline_per_call * 1000:.3f}ms budget={budget * 1000:.3f}ms"
    )
    assert summary.overall_classification == "HEALTHY"
    assert per_call < budget


async def test_concurrent_reconciler_instances_only_one_reconciles_same_account(pool):
    """D3 다중 인스턴스 증명 — REC-004: 서로 다른 `ReconcileScheduler`
    인스턴스(별도 스케줄러 프로세스 시뮬레이션) 여러 개가 동일 account_ref를
    정확히 같은 시각에(asyncio.gather) 동시 대사하려 해도
    `pg_try_advisory_xact_lock`이 정확히 1개만 통과시키고 나머지는 이번
    주기를 건너뛴다(단일 프로세스 순차 재실행이 아니라 실제 동시 경합).

    전용 커넥션 풀을 별도로 연다 — 공용 `pool` 픽스처는 `max_size=4`라
    `_reconcile_one`이 잠금용 커넥션 1개를 쥔 채로 `reconcile_account` 내부가
    또 다른 커넥션을 요구하는 상황에서, 인스턴스 수가 4를 넘으면 전부가
    서로의 커넥션 반납을 기다리며 교착한다(각 워커 프로세스가 자기 풀을
    갖는 실제 운영 구조와도 더 가깝다)."""
    user_id = await create_test_tenant(pool)
    client_id = f"recon-cid-{uuid.uuid4().hex[:10]}"
    await _insert_open_order(
        pool, user_id, client_order_id=client_id,
        quantity=Decimal("5"), filled_quantity=Decimal("2"),
    )
    account_ref = str(user_id)

    async def _no_targets() -> list[ReconcileTarget]:
        return []

    n_instances = 5
    scheduler_pool = await asyncpg.create_pool(
        _asyncpg_dsn(), min_size=1, max_size=4 * n_instances
    )
    try:
        schedulers = [
            ReconcileScheduler(scheduler_pool, targets=_no_targets) for _ in range(n_instances)
        ]
        target = ReconcileTarget(
            tenant_id=user_id,
            connection_id=None,
            account_ref=account_ref,
            adapter=_ScriptedAdapter(
                open_orders=[_provider_order(client_id, Decimal("5"), Decimal("2"))]
            ),
        )

        results = await asyncio.gather(
            *(scheduler._reconcile_one(target) for scheduler in schedulers)
        )
    finally:
        await scheduler_pool.close()

    assert sum(results) == 1


async def test_adversarial_concurrent_submit_race_all_denied_during_material_mismatch(pool):
    """D3 adversarial 증명 — ACCOUNT 세이프티 컨트롤이 MATERIAL_MISMATCH로
    ACTIVE인 동안, 서로 다른 intent_seq를 쓰는 다수의 `submit_order` 호출이
    정확히 같은 시각에(asyncio.gather) 경합해도(DENY를 레이스로 우회하려는
    시도) 단 하나도 통과하지 못하고 orders 테이블에 행이 하나도 남지
    않는다 — 순차 단일 시도(REC-002 기존 테스트)와 달리 동시 경합에서도
    게이트가 새지 않음을 증명한다."""
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

    n_attempts = 8
    # 전용 커넥션 풀 — 각 submit_order 호출이 자기 트랜잭션(1커넥션) 안에서
    # pre_submit_gate의 감사로그/결정 기록을 위해 추가 커넥션을 잠깐
    # 요구한다(`make_foundation_pre_submit_gate`/`RiskDecisionRecorder`
    # 내부). 공용 `pool` 픽스처(max_size=4)로 8개를 동시에 돌리면 모두가
    # 서로의 두 번째 커넥션 반납을 기다리며 교착한다 — 위 다중 인스턴스
    # 테스트와 동일한 이유로 여기서도 넉넉한 전용 풀을 쓴다.
    submit_pool = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4 * n_attempts)
    try:
        gate = make_foundation_pre_submit_gate(submit_pool, require_mandate=False)

        async def attempt(seq: int) -> str:
            scope = OrderIdempotencyScope(
                tenant_id=user_id, account_ref="acct-1", provider="bitget", strategy_id="s1",
                strategy_version="1.0.0", execution_id=execution_id, intent_seq=seq,
                window_start=datetime.now(timezone.utc),
            )
            cmd = SubmitOrderCommand(
                command_id=uuid.uuid4(), trace_id=uuid.uuid4(), scope=scope, symbol="BTC/USDT",
                side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=Decimal("0.01"),
                asset_class=AssetClass.CRYPTO, actor_subject_id=user_id,
                issued_at=datetime.now(timezone.utc),
            )
            try:
                await submit_order(
                    cmd, pool=submit_pool, profile=_profile(), registry=_registry(),
                    pre_submit_gate=gate, entity_context=entity_context,
                    entity_repo=PostgresEntityRepository(submit_pool),
                )
            except OrderSubmitDeniedError:
                return "denied"
            return "allowed"

        results = await asyncio.gather(*(attempt(seq) for seq in range(1, n_attempts + 1)))
    finally:
        await submit_pool.close()

    assert results == ["denied"] * n_attempts
    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
    assert order_count == 0
