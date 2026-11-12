"""L4-24 `three_way_reconciler` 에지/부정/실패주입/게이트 적색 통합테스트.

`test_three_way_reconciler.py`에서 분리했다(500-LOC 정책, ADR-2026-09-10-C §7)
— 공용 스캐폴딩은 `_reconciler_test_support.py`.
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
from src.services.oms.application.submit_order import submit_order
from src.services.oms.application.three_way_reconciler import reconcile_account
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from tests.foundation.integration.risk_gate.conftest import (
    activate_mandate_with_defaults,
    mandate_repo,  # noqa: F401
    trust_repo,  # noqa: F401
)
from tests.integration.oms._reconciler_test_support import (
    ScriptedAdapter,
    active_account_controls,
    create_running_execution,
    insert_open_order,
    profile,
    provider_order,
    registry,
    submit_cmd,
)
from tests.integration.oms.conftest import create_test_tenant, seed_entity_context
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders


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
    pool,
    monkeypatch,
    mandate_repo,  # noqa: F811
    trust_repo,  # noqa: F811
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
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=user_id)
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
