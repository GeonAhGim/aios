"""L4-24 `application/three_way_reconciler.py` 통합테스트 — 실 TEST_DATABASE_URL.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-24 DoD — REC-001/
002/003/006 각각 재현 + MATERIAL 등급 동안 같은 계정의 신규 submit_order가
DENY, 해소 후 재허용(배선 증명: `reconcile_account`의 `activate_safety_control`
호출을 지우면 DENY 단언이 실패한다). 금액 비교는 전부 `Decimal(...)`(DoD (c)).

DEEPEN(task-2800) — 수치 latency/throughput·다중 인스턴스·adversarial 케이스는
`test_three_way_reconciler_deepen.py`로 분리했다(500-LOC 정책,
ADR-2026-09-10-C §7) — 공용 스캐폴딩은 `_reconciler_test_support.py`.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from src.data.models.trading import OrderStatus
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.services.oms.application import three_way_reconciler as reconciler_module
from src.services.oms.application.submit_order import OrderSubmitDeniedError, submit_order
from src.services.oms.application.three_way_reconciler import reconcile_account
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from tests.integration.oms._reconciler_test_support import (
    ScriptedAdapter,
    active_account_controls,
    create_running_execution,
    insert_open_order,
    insert_order_with_status,
    order_status,
    profile,
    provider_order,
    record_cancel_requested,
    registry,
    submit_cmd,
)
from tests.integration.oms.conftest import create_test_tenant, seed_entity_context


class _CallTrackingAdapter(ScriptedAdapter):
    """REC-003 변형 — `get_open_orders()` 호출 여부를 기록한다(연결이 이미
    불건강으로 알려졌을 때 provider 호출을 스킵하는지 검증하는 negative
    전용 더블)."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.called = False

    async def get_open_orders(self, symbol: str | None = None) -> list:
        self.called = True
        return await super().get_open_orders(symbol)


async def test_reconcile_healthy_reports_zero_discrepancies(pool):
    """REC-001 — 내부·거래소가 완전히 일치하면 discrepancies는 0건."""
    user_id = await create_test_tenant(pool)
    client_id = f"recon-cid-{uuid.uuid4().hex[:10]}"
    await insert_open_order(
        pool,
        user_id,
        client_order_id=client_id,
        quantity=Decimal("5"),
        filled_quantity=Decimal("2"),
    )
    adapter = ScriptedAdapter(open_orders=[provider_order(client_id, Decimal("5"), Decimal("2"))])

    summary = await reconcile_account(
        pool=pool,
        adapter=adapter,
        tenant_id=user_id,
        connection_id=None,
        account_ref=str(user_id),
        window=timedelta(minutes=5),
    )

    assert summary.overall_classification == "HEALTHY"
    assert summary.discrepancies == []
    assert await active_account_controls(pool, user_id) == 0


async def test_reconcile_material_mismatch_denies_and_resolving_allows_submit(pool):
    """REC-002 + DoD (b) — 체결수량 불일치는 MATERIAL_MISMATCH, 그동안 같은
    계정의 신규 submit_order는 DENY, 해소되면 같은 명령이 다시 허용된다."""
    user_id = await create_test_tenant(pool)
    execution_id = await create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    client_id = f"recon-cid-{uuid.uuid4().hex[:10]}"
    await insert_open_order(
        pool,
        user_id,
        client_order_id=client_id,
        quantity=Decimal("10"),
        filled_quantity=Decimal("3"),
    )
    mismatched_adapter = ScriptedAdapter(
        open_orders=[provider_order(client_id, Decimal("10"), Decimal("7"))]
    )

    summary = await reconcile_account(
        pool=pool,
        adapter=mismatched_adapter,
        tenant_id=user_id,
        connection_id=None,
        account_ref=str(user_id),
        window=timedelta(minutes=5),
    )

    assert summary.overall_classification == "MATERIAL_MISMATCH"
    assert len(summary.discrepancies) == 1
    discrepancy = summary.discrepancies[0]
    assert discrepancy.kind == "FILLED_QTY_MISMATCH"
    assert discrepancy.internal_value == Decimal("3")
    assert discrepancy.provider_value == Decimal("7")
    assert await active_account_controls(pool, user_id) == 1

    cmd = submit_cmd(user_id, execution_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    with pytest.raises(OrderSubmitDeniedError) as exc_info:
        await submit_order(
            cmd,
            pool=pool,
            profile=profile(),
            registry=registry(),
            pre_submit_gate=gate,
            entity_context=entity_context,
            entity_repo=PostgresEntityRepository(pool),
        )
    assert any(code.startswith("RISK_KILL_SWITCH_ACTIVE_") for code in exc_info.value.reason_codes)
    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
    assert order_count == 0

    resolved_adapter = ScriptedAdapter(
        open_orders=[provider_order(client_id, Decimal("10"), Decimal("3"))]
    )
    resolved_summary = await reconcile_account(
        pool=pool,
        adapter=resolved_adapter,
        tenant_id=user_id,
        connection_id=None,
        account_ref=str(user_id),
        window=timedelta(minutes=5),
    )
    assert resolved_summary.overall_classification == "HEALTHY"
    assert await active_account_controls(pool, user_id) == 0

    allowed = await submit_order(
        cmd,
        pool=pool,
        profile=profile(),
        registry=registry(),
        pre_submit_gate=gate,
        entity_context=entity_context,
        entity_repo=PostgresEntityRepository(pool),
    )
    assert allowed.status == OrderStatus.VALIDATED


async def test_reconcile_confirmed_cancel_self_heals_and_reopens_gate(pool):
    """task-7978 F1 — 거래소가 실제로 취소를 확정한 주문(체결 없이 open-orders
    에서 사라짐)이 `CANCEL_REQUESTED` 이력을 갖고 있으면 대사가 스스로
    `VENUE_CANCELLED`로 전이시켜야 한다. 그러지 않으면 이 주문 하나가
    ORDER_MISSING_AT_PROVIDER/MATERIAL_MISMATCH로 영원히 고정돼 ACCOUNT
    게이트가 계속 ACTIVE로 남고, 같은 테넌트의 신규 submit_order가 무기한
    DENY된다(라이브니스 버그, task-7978 발견)."""
    user_id = await create_test_tenant(pool)
    execution_id = await create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    client_id = f"recon-cid-{uuid.uuid4().hex[:10]}"
    order_id = await insert_order_with_status(
        pool,
        user_id,
        client_order_id=client_id,
        quantity=Decimal("5"),
        filled_quantity=Decimal("2"),
        status="ACKNOWLEDGED",
    )
    await record_cancel_requested(pool, order_id, "ACKNOWLEDGED")
    # provider 쪽 open-orders에는 이 client_id가 없다 — 정상 취소가 확정돼
    # 사라진 상태를 흉내낸다.
    adapter = ScriptedAdapter(open_orders=[])

    summary = await reconcile_account(
        pool=pool,
        adapter=adapter,
        tenant_id=user_id,
        connection_id=None,
        account_ref=str(user_id),
        window=timedelta(minutes=5),
    )

    assert summary.overall_classification == "HEALTHY"
    assert summary.discrepancies == []
    assert await order_status(pool, order_id) == "CANCELLED"
    assert await active_account_controls(pool, user_id) == 0

    cmd = submit_cmd(user_id, execution_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    allowed = await submit_order(
        cmd,
        pool=pool,
        profile=profile(),
        registry=registry(),
        pre_submit_gate=gate,
        entity_context=entity_context,
        entity_repo=PostgresEntityRepository(pool),
    )
    assert allowed.status == OrderStatus.VALIDATED


async def test_reconcile_missing_without_cancel_history_stays_material_mismatch(pool):
    """negative — `CANCEL_REQUESTED` 이력이 없는 missing 주문은 자기치유
    대상이 아니다(임의의 missing 주문을 전부 CANCELLED로 세탁하지 않는다).
    실제 유실(체결 누락 등)일 수 있으므로 MATERIAL_MISMATCH로 남아 게이트가
    계속 ACTIVE여야 한다."""
    user_id = await create_test_tenant(pool)
    client_id = f"recon-cid-{uuid.uuid4().hex[:10]}"
    order_id = await insert_order_with_status(
        pool,
        user_id,
        client_order_id=client_id,
        quantity=Decimal("5"),
        filled_quantity=Decimal("2"),
        status="ACKNOWLEDGED",
    )
    adapter = ScriptedAdapter(open_orders=[])

    summary = await reconcile_account(
        pool=pool,
        adapter=adapter,
        tenant_id=user_id,
        connection_id=None,
        account_ref=str(user_id),
        window=timedelta(minutes=5),
    )

    assert summary.overall_classification == "MATERIAL_MISMATCH"
    assert len(summary.discrepancies) == 1
    assert summary.discrepancies[0].kind == "ORDER_MISSING_AT_PROVIDER"
    assert await order_status(pool, order_id) == "ACKNOWLEDGED"
    assert await active_account_controls(pool, user_id) == 1


async def test_reconcile_provider_unavailable_never_assumes_zero(pool):
    """REC-003 — provider 조회 실패(타임아웃)는 PROVIDER_UNAVAILABLE이고,
    체결/잔고를 0으로 가정하지 않는다(provider_value는 None으로 남는다)."""
    user_id = await create_test_tenant(pool)
    client_id = f"recon-cid-{uuid.uuid4().hex[:10]}"
    await insert_open_order(
        pool,
        user_id,
        client_order_id=client_id,
        quantity=Decimal("4"),
        filled_quantity=Decimal("1"),
    )
    adapter = ScriptedAdapter(fail=True)

    summary = await reconcile_account(
        pool=pool,
        adapter=adapter,
        tenant_id=user_id,
        connection_id=None,
        account_ref=str(user_id),
        window=timedelta(minutes=5),
    )

    assert summary.overall_classification == "PROVIDER_UNAVAILABLE"
    assert len(summary.discrepancies) == 1
    discrepancy = summary.discrepancies[0]
    assert discrepancy.provider_value is None
    assert discrepancy.internal_value == Decimal("1")
    assert await active_account_controls(pool, user_id) == 1


async def test_reconcile_rerun_dedupe_does_not_duplicate_safety_control(pool):
    """REC-006 — 같은 불일치로 재실행해도 ACCOUNT 세이프티 컨트롤은 1개만
    유지된다(중복 활성화 없음)."""
    user_id = await create_test_tenant(pool)
    client_id = f"recon-cid-{uuid.uuid4().hex[:10]}"
    await insert_open_order(
        pool,
        user_id,
        client_order_id=client_id,
        quantity=Decimal("6"),
        filled_quantity=Decimal("1"),
    )
    adapter = ScriptedAdapter(open_orders=[provider_order(client_id, Decimal("6"), Decimal("5"))])

    first = await reconcile_account(
        pool=pool,
        adapter=adapter,
        tenant_id=user_id,
        connection_id=None,
        account_ref=str(user_id),
        window=timedelta(minutes=5),
    )
    second = await reconcile_account(
        pool=pool,
        adapter=adapter,
        tenant_id=user_id,
        connection_id=None,
        account_ref=str(user_id),
        window=timedelta(minutes=5),
    )

    assert first.overall_classification == "MATERIAL_MISMATCH"
    assert second.overall_classification == "MATERIAL_MISMATCH"
    assert await active_account_controls(pool, user_id) == 1


async def test_reconcile_minor_difference_within_tolerance_does_not_activate_gate(pool):
    """negative — 체결수량 차이가 `DEFAULT_POLICY.absolute_tolerance`(0.01)
    이내면 MINOR_DIFFERENCE일 뿐 MATERIAL_MISMATCH가 아니다. I12는
    MATERIAL_MISMATCH/PROVIDER_UNAVAILABLE에서만 ACCOUNT 게이트를 켜므로,
    사소한 반올림 차이로 모든 신규 submit_order를 묶어버리지 않는지 검증한다
    (`DEFAULT_POLICY`를 건드려 과민 반응시키는 회귀를 잡는다)."""
    user_id = await create_test_tenant(pool)
    client_id = f"recon-cid-{uuid.uuid4().hex[:10]}"
    await insert_open_order(
        pool,
        user_id,
        client_order_id=client_id,
        quantity=Decimal("5"),
        filled_quantity=Decimal("2"),
    )
    adapter = ScriptedAdapter(
        open_orders=[provider_order(client_id, Decimal("5"), Decimal("2.005"))]
    )

    summary = await reconcile_account(
        pool=pool,
        adapter=adapter,
        tenant_id=user_id,
        connection_id=None,
        account_ref=str(user_id),
        window=timedelta(minutes=5),
    )

    assert summary.overall_classification == "MINOR_DIFFERENCE"
    assert await active_account_controls(pool, user_id) == 0


async def test_reconcile_skips_provider_fetch_when_connection_already_unhealthy(pool):
    """negative — `connection_id`가 가리키는 커넥션의 최신 헬스 레코드가 없으면
    (알 수 없음/불건강) `adapter.get_open_orders()`를 호출하지 않고 즉시
    PROVIDER_UNAVAILABLE로 단정한다 — 이미 죽은 걸로 알려진 연결에 또 쿼리를
    보내 타임아웃을 기다리지 않는다."""
    user_id = await create_test_tenant(pool)
    client_id = f"recon-cid-{uuid.uuid4().hex[:10]}"
    await insert_open_order(
        pool,
        user_id,
        client_order_id=client_id,
        quantity=Decimal("4"),
        filled_quantity=Decimal("1"),
    )
    tracking_adapter = _CallTrackingAdapter(
        open_orders=[provider_order(client_id, Decimal("4"), Decimal("1"))]
    )
    unknown_connection_id = uuid.uuid4()

    summary = await reconcile_account(
        pool=pool,
        adapter=tracking_adapter,
        tenant_id=user_id,
        connection_id=unknown_connection_id,
        account_ref=str(user_id),
        window=timedelta(minutes=5),
    )

    assert summary.overall_classification == "PROVIDER_UNAVAILABLE"
    assert tracking_adapter.called is False
    assert await active_account_controls(pool, user_id) == 1


async def test_reconcile_fails_closed_when_risk_gate_repository_raises(pool, monkeypatch):
    """실패 주입 — I12 배선이 쓰는 `PostgresRiskGateRepository.list_active_
    controls`가 예외(의존성 장애, 예: 커넥션 드롭)를 던지면 `reconcile_account`
    는 그 예외를 그대로 전파해야 한다. HEALTHY로 조용히 둘러대 ACCOUNT 게이트
    상태를 알 수 없는 채로 두는 것(fail-open)은 I12 위반이다."""

    async def _boom(self, *args: object, **kwargs: object) -> list:
        raise RuntimeError("risk gate repository unavailable (injected failure)")

    monkeypatch.setattr(PostgresRiskGateRepository, "list_active_controls", _boom)

    user_id = await create_test_tenant(pool)
    client_id = f"recon-cid-{uuid.uuid4().hex[:10]}"
    await insert_open_order(
        pool,
        user_id,
        client_order_id=client_id,
        quantity=Decimal("3"),
        filled_quantity=Decimal("1"),
    )
    adapter = ScriptedAdapter(open_orders=[provider_order(client_id, Decimal("3"), Decimal("1"))])

    with pytest.raises(RuntimeError, match="risk gate repository unavailable"):
        await reconcile_account(
            pool=pool,
            adapter=adapter,
            tenant_id=user_id,
            connection_id=None,
            account_ref=str(user_id),
            window=timedelta(minutes=5),
        )


async def test_reconcile_gate_red_when_account_gate_wiring_removed_allows_submit_during_mismatch(
    pool, monkeypatch
):
    """게이트 적색 재현 — 모듈 docstring의 경고("`activate_safety_control` 호출을
    지우면 DENY 단언이 실패한다")를 실제로 재현한다. `_apply_account_gate`
    배선을 no-op으로 바꾸면 MATERIAL_MISMATCH 동안에도 ACCOUNT 세이프티
    컨트롤이 전혀 켜지지 않고, 같은 계정의 신규 submit_order가 그대로
    통과한다 — 평소의 DENY 단언(`test_reconcile_material_mismatch_denies_
    and_resolving_allows_submit`)이 실제로 이 배선에 의존한다는 적색 증명."""

    async def _noop_apply_account_gate(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(reconciler_module, "_apply_account_gate", _noop_apply_account_gate)

    user_id = await create_test_tenant(pool)
    execution_id = await create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    client_id = f"recon-cid-{uuid.uuid4().hex[:10]}"
    await insert_open_order(
        pool,
        user_id,
        client_order_id=client_id,
        quantity=Decimal("10"),
        filled_quantity=Decimal("3"),
    )
    mismatched_adapter = ScriptedAdapter(
        open_orders=[provider_order(client_id, Decimal("10"), Decimal("7"))]
    )

    summary = await reconcile_account(
        pool=pool,
        adapter=mismatched_adapter,
        tenant_id=user_id,
        connection_id=None,
        account_ref=str(user_id),
        window=timedelta(minutes=5),
    )
    assert summary.overall_classification == "MATERIAL_MISMATCH"
    # 배선이 제거됐으므로 게이트가 전혀 켜지지 않는다 — 평소라면 1이어야 한다.
    assert await active_account_controls(pool, user_id) == 0

    cmd = submit_cmd(user_id, execution_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    allowed = await submit_order(
        cmd,
        pool=pool,
        profile=profile(),
        registry=registry(),
        pre_submit_gate=gate,
        entity_context=entity_context,
        entity_repo=PostgresEntityRepository(pool),
    )
    # 평소라면 OrderSubmitDeniedError가 발생해야 할 자리 — 배선 제거로 새어나간다.
    assert allowed.status == OrderStatus.VALIDATED
