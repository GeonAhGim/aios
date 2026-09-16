"""H-7a 백엔드 e2e #3 — 프로세스 재시작 복구, 풀스택.

Spec: docs/design/ADR-2026-09-09-B-mvp1-hardening-and-mvp2-scope.md H-7
("백엔드 e2e 3건: ... 재시작 복구").
Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §6 F6(① 만료 lease
회수, ④ 미종결 주문 거래소 진실 재동기화, ⑤ 복구 완료 전 submit_order
거부), §9 L4-18a/b. docs/design/INVARIANTS.md I-10(fail-closed).

`tests/integration/oms/test_restart_recovery.py`가 각 조각(lease 회수,
outbox 재진입, 복구 게이트)을 단위/통합 수준에서 이미 D3까지 증명한다 —
이 e2e는 그걸 반복하지 않고, "죽은 프로세스가 남긴 실제 어긋난 상태
(만료 execution_leases + 거래소 진실과 다른 SUBMITTED orders 행)를 실
Postgres에 심어 두고, 새 프로세스(새 `PaperSimulatorAdapter` 인스턴스 +
새 `RecoveryState`)가 `run_startup_recovery` 단 1회 호출만으로 둘 다
정합해짐"을 하나의 흐름으로 증명하고, 복구 게이트가 실제 프로덕션
`make_foundation_pre_submit_gate`와 실 paper 체결에 이어지는 것까지
확인한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import AccountBalance, Order, OrderSide, OrderStatus, OrderType
from src.exchanges.paper.fee_model import FeeModel
from src.exchanges.paper.fill_model import FillModel
from src.exchanges.paper.latency_model import LatencyModel
from src.exchanges.paper.ledger_repository import PaperLedgerRepository
from src.exchanges.paper.simulator_adapter import PaperSimulatorAdapter
from src.services.execution_loop import recovery_wiring
from src.services.oms.application.restart_recovery import (
    RECOVERY_IN_PROGRESS_REASON,
    RecoveryState,
    make_recovery_gate,
)
from src.services.order_service import repository
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.gate import GateOutcome, OrderContext
from src.services.order_service.submit import OrderDeniedByRiskGateError, submit_order
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.foundation.execution_ownership.conftest import create_execution
from tests.support.paper_sim_fakes import FakeReferenceAdapter, SeqRandom, fixed_adv, instant_sleep

_NO_SLIPPAGE_FILL = FillModel(
    spread_bps=Decimal("0"),
    impact_bps_per_pct_adv=Decimal("0"),
    partial_fill_prob=0.0,
    partial_min_pct=Decimal("100"),
)
_TAKER_FEE = FeeModel(maker_bps=Decimal("0"), taker_bps=Decimal("10"), fee_currency=Currency.USDT)
_NO_DROP_LATENCY = LatencyModel(ack_ms_p50=0, ack_ms_p99=0, drop_response_prob=0.0)

_INSERT_LEASE_SQL = """
    INSERT INTO execution_leases (execution_id, owner_id, fencing_token, heartbeat_at, expires_at)
    VALUES ($1, $2, 0, now() - interval '1 hour', $3)
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_adapter(pool: asyncpg.Pool, account_id: UUID) -> PaperSimulatorAdapter:
    reference = FakeReferenceAdapter(bid=Decimal("29990"), ask=Decimal("30000"))
    return PaperSimulatorAdapter(
        reference,
        PaperLedgerRepository(),
        _NO_SLIPPAGE_FILL,
        _TAKER_FEE,
        _NO_DROP_LATENCY,
        clock=_now,
        rng=SeqRandom([0.0]),
        account_id=account_id,
        pool=pool,
        adv_provider=fixed_adv,
        sleeper=instant_sleep,
    )


async def _seed_usdt(pool: asyncpg.Pool, account_id: UUID, amount: Decimal) -> None:
    ledger = PaperLedgerRepository()
    async with pool.acquire() as conn:
        await ledger.deposit(conn, account_id, "USDT", amount)


async def _seed_expired_lease(pool: asyncpg.Pool, execution_id: int, *, owner_id: str) -> None:
    expires_at = _now() - timedelta(seconds=30)
    async with pool.acquire() as conn:
        await conn.execute(_INSERT_LEASE_SQL, execution_id, owner_id, expires_at)


def _make_market_order(execution_id: int, client_order_id_label: str) -> Order:
    # uuid 접미사 격리(docs/TESTING.md 관례) — 재실행 시 UNIQUE 충돌 방지.
    return Order(
        client_order_id=f"{client_order_id_label}-{uuid4().hex[:8]}",
        strategy_id="e2e-restart-recovery",
        strategy_version="1.0.0",
        execution_id=execution_id,
        symbol="BTC/USDT",
        exchange="paper_sim",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        status=OrderStatus.CREATED,
        asset_class=AssetClass.CRYPTO,
    )


async def test_restart_recovery_reclaims_lease_and_resyncs_stale_order(pool: asyncpg.Pool) -> None:
    user_id = await create_test_tenant(pool)
    execution_id = await create_execution(pool, user_id)

    # -- "죽은 프로세스" 이전 상태: 지정가 주문을 내고 취소했다(거래소
    # 진실 = CANCELLED). 하지만 그 확인을 DB에 반영하기 전에 프로세스가
    # 죽었다고 가정하고, orders 행은 그 이전 상태(SUBMITTED)로 심어 둔다.
    suffix = uuid4().hex[:8]
    adapter_before = _make_adapter(pool, user_id)
    await _seed_usdt(pool, user_id, Decimal("100000"))
    open_order = await adapter_before.place_order(
        Order(
            client_order_id=f"restart-open-{suffix}",
            strategy_id="e2e-restart-recovery",
            strategy_version="1.0.0",
            execution_id=execution_id,
            symbol="BTC/USDT",
            exchange="paper_sim",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            price=Money(amount=Decimal("100"), currency=Currency.USDT),
            status=OrderStatus.CREATED,
            asset_class=AssetClass.CRYPTO,
        )
    )
    assert open_order.status is OrderStatus.ACKNOWLEDGED
    assert open_order.exchange_order_id is not None
    assert await adapter_before.cancel_order(open_order.exchange_order_id) is True

    stale_order = Order(
        client_order_id=f"restart-stale-{suffix}",
        exchange_order_id=open_order.exchange_order_id,
        strategy_id="e2e-restart-recovery",
        strategy_version="1.0.0",
        execution_id=execution_id,
        symbol="BTC/USDT",
        exchange="paper_sim",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        price=Money(amount=Decimal("100"), currency=Currency.USDT),
        status=OrderStatus.SUBMITTED,
        asset_class=AssetClass.CRYPTO,
    )
    async with pool.acquire() as conn:
        await repository.insert(conn, stale_order, user_id=user_id)

    await _seed_expired_lease(pool, execution_id, owner_id="dead-process")

    # -- "재시작": 새 어댑터 인스턴스(= 새 프로세스, 인메모리 상태 공유 없음) +
    # 새 RecoveryState로 복구를 1회 호출한다.
    adapter_after = _make_adapter(pool, user_id)
    published: list[tuple[str, dict[str, object]]] = []

    async def resolve_adapter(_user_id: UUID, _exchange: str) -> PaperSimulatorAdapter:
        return adapter_after

    async def publish(topic: str, payload: dict[str, object]) -> None:
        published.append((topic, payload))

    state = await recovery_wiring.run_startup_recovery(
        pool, resolve_adapter=resolve_adapter, publish=publish
    )

    assert state.complete is True

    async with pool.acquire() as conn:
        lease_owner = await conn.fetchval(
            "SELECT owner_id FROM execution_leases WHERE execution_id = $1", execution_id
        )
        order_status = await conn.fetchval(
            "SELECT status FROM orders WHERE client_order_id = $1", stale_order.client_order_id
        )
        audit_rows = await conn.fetch(
            "SELECT decision_data FROM audit_log WHERE action_type = 'system.restart_recovery'"
        )
    assert lease_owner is None  # 만료 lease 회수됨
    assert order_status == "CANCELLED"  # 거래소 진실(취소됨)로 재동기화됨
    assert audit_rows

    assert published
    assert published[-1][0] == "order.status.changed"
    assert published[-1][1]["status"] == "CANCELLED"


async def test_submit_order_denied_until_recovery_completes_then_allowed(
    pool: asyncpg.Pool,
) -> None:
    """복구 게이트가 실제 프로덕션 게이트(`make_foundation_pre_submit_gate`)
    및 실 paper 체결과 이어짐을 증명 — 복구 완료 전에는 거부되고, 완료
    후에는 (다른 조건이 전부 ALLOW라면) 실제로 체결까지 간다."""
    user_id = await create_test_tenant(pool)
    execution_id = await create_execution(pool, user_id)
    # 이 서브테스트는 완료 후 실제 submit_order() round trip(claim ->
    # 체결 -> update_from_exchange)을 타므로, 그 파이프라인이 요구하는
    # "어댑터가 order_id를 보존한다" 계약을 지키는 FakeExchangeAdapter를
    # 쓴다(test_order_execution_settlement.py 모듈 docstring 참조 —
    # PaperSimulatorAdapter는 이 계약을 어겨 여기 쓸 수 없다).
    adapter = FakeExchangeAdapter(
        exchange_name="paper_sim",
        is_paper_trading=True,
        place_order_result_status=OrderStatus.FILLED,
        closes=[Decimal("30000")] * 30,
        usdt_balance=AccountBalance(
            exchange="paper_sim", asset="USDT", total=Decimal("100000"), available=Decimal("100000")
        ),
    )
    order = _make_market_order(execution_id, "restart-gated-order")
    state = RecoveryState()  # 새로 시작한 프로세스 — 복구가 아직 안 끝났다

    async def never_delegate(context: object) -> None:
        raise AssertionError("복구 미완료 상태에서는 실제 게이트로 위임하면 안 된다")

    with pytest.raises(OrderDeniedByRiskGateError) as exc_info:
        await submit_order(
            order,
            user_id=user_id,
            adapter=adapter,
            pool=pool,
            pre_submit_gate=make_recovery_gate(state, never_delegate),  # type: ignore[arg-type]
        )
    assert exc_info.value.reason_codes == (RECOVERY_IN_PROGRESS_REASON,)
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM orders WHERE client_order_id = $1", order.client_order_id
        )
    assert exists is None

    state.mark_complete()  # run_startup_recovery가 이 시점에 이미 호출됐다고 가정
    real_gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    submitted = await submit_order(
        order,
        user_id=user_id,
        adapter=adapter,
        pool=pool,
        pre_submit_gate=make_recovery_gate(state, real_gate),
    )
    assert submitted.status is OrderStatus.FILLED


async def test_recovery_failure_injection_keeps_submissions_denied(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """변조 케이스(실패 주입) — `reclaim_expired_leases`가 예외를 던지면
    (DB 장애 시뮬레이션) `run_startup_recovery_gated`는 완료 표시 없이
    돌아와야 하고, 그 뒤 어댑터 조회/이벤트 발행이 전혀 없어야 하며,
    복구 게이트는 계속 DENY해야 한다(I-10 fail-closed). 만약 예외 처리
    분기가 "일단 완료로 표시하자"로 바뀐다면 이 테스트가 바로 FAIL한다
    — 그것이 위 두 테스트의 "복구 완료 후에만 허용" 전제를 지키는
    안전장치다."""

    async def boom(_pool: asyncpg.Pool) -> int:
        raise RuntimeError("simulated lease-reclaim DB failure")

    monkeypatch.setattr(recovery_wiring, "reclaim_expired_leases", boom)

    async def resolve_adapter_must_not_be_called(_user_id: UUID, _exchange: str) -> None:
        raise AssertionError("lease reclaim이 실패했으면 어댑터를 조회하면 안 된다")

    async def publish_must_not_be_called(_topic: str, _payload: dict[str, object]) -> None:
        raise AssertionError("lease reclaim이 실패했으면 이벤트를 발행하면 안 된다")

    state = await recovery_wiring.run_startup_recovery_gated(
        pool,
        resolve_adapter=resolve_adapter_must_not_be_called,  # type: ignore[arg-type]
        publish=publish_must_not_be_called,
        enabled=True,
    )

    assert state.complete is False

    async def never_delegate(context: object) -> None:
        raise AssertionError("복구 실패 후에도 게이트가 위임되면 안 된다")

    gate = make_recovery_gate(state, never_delegate)  # type: ignore[arg-type]

    decision = await gate(
        OrderContext(
            user_id=uuid4(), execution_id=1, exchange="paper_sim", mandate_revision_id=None
        )
    )
    assert decision.outcome == GateOutcome.DENY
    assert decision.reason_codes == (RECOVERY_IN_PROGRESS_REASON,)
