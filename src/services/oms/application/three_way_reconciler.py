"""L4-24 — 3자 대사(내부 orders vs 거래소 조회) 오케스트레이션 + I12 배선.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C `three_way_reconciler.py`,
§9 L4-24, §4.1 I11/I12, §6 F7.

범위 축소(문서화, `foundation/reconciliation/application/run_reconciliation.py`
docstring과 같은 전례): 이 코드베이스에는 아직 OMS가 참조할 수 있는 내부
잔고/포지션 원장이 없다("paper_control(FND-07)에 아직 fill/position/balance
내부 원장이 없다") — 그래서 이 리프는 **주문만** 비교한다(local open orders
vs `ExchangeAdapter.get_open_orders()`). 명세 §2-C 표가 나열한
`get_order_history`/`get_fills`/`get_balance` 의존은 내부 원장이 생기기
전까지는 비교할 대상이 없어 호출하지 않는다 — 원장이 생기면 `_compare`에
BALANCE_MISMATCH/FILL_MISSING_INTERNAL 패스를 추가하기만 하면 된다
(`reconcile_rules.compare_triple`이 이미 그 파라미터를 받는다).

I12("MATERIAL_MISMATCH/PROVIDER_UNAVAILABLE 집계 시 ACCOUNT 스코프 safety
control ACTIVE 전까지 새 SUBMIT 거부")는 여기서 `activate_safety_control`/
`deactivate_safety_control`(scope=ACCOUNT, scope_ref=str(tenant_id))을 직접
호출해 강제한다. `submit_order`(`order_service/foundation_gate.py`)의
`pre_submit_gate`는 이미 매 제출마다 `fence_pairs_for`의 ACCOUNT 쌍을 포함한
5쌍 전부를 읽어 ACTIVE control이 하나라도 있으면 DENY한다(I-01, 변경 없이
재사용) — 이 파일이 하는 배선은 그 기존 게이트가 볼 control 행을 만들고
지우는 것뿐이다. 이 파일의 `activate_safety_control` 호출을 제거하면
`test_three_way_reconciler.py`의 DENY 단언이 실패한다(배선 증명).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.data.models.trading import Order as ProviderOrder
from src.data.models.trading import OrderStatus
from src.exchanges.common.adapter import ExchangeAdapter
from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.reconciliation.contracts.v1 import Classification
from src.foundation.reconciliation.domain.models import MaterialityPolicy
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.application.deactivate_safety_control import (
    deactivate_safety_control,
)
from src.foundation.risk_gate.domain.models import SafetyScope
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.services.oms.application.order_query import list_orders
from src.services.oms.contracts.v1_events import Discrepancy
from src.services.oms.contracts.v1_views import OrderView, ReconcileSummaryView
from src.services.oms.domain.reconcile_rules import compare_triple

logger = logging.getLogger(__name__)

DEFAULT_POLICY = MaterialityPolicy(
    absolute_tolerance=Decimal("0.01"), relative_tolerance_pct=Decimal("0.1")
)
_OPEN_STATUSES = (OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED)
_BLOCKING = frozenset({Classification.MATERIAL_MISMATCH, Classification.PROVIDER_UNAVAILABLE})
_BLOCK_REASON_PREFIX = "INTEGRITY_RECONCILIATION_MISMATCH"
# 80번 §2 "typed severity"와 같은 순서. `compare_triple`은 HEALTHY 항목을
# 결과에 아예 담지 않으므로(§9 DoD "정상 케이스는 0건 보고"), 빈 리스트는
# `_aggregate`가 아니라 호출부가 직접 HEALTHY로 판정한다(빈 튜플→PENDING인
# `foundation.reconciliation.domain.rules.aggregate_classification`은 "한
# 번도 대사된 적 없음"을 뜻해 이 맥락과 다르다).
_SEVERITY = (
    Classification.MATERIAL_MISMATCH,
    Classification.PROVIDER_UNAVAILABLE,
    Classification.MINOR_DIFFERENCE,
    Classification.HEALTHY,
)


class ProviderUnavailableError(Exception):
    """`adapter.get_open_orders()` 실패(타임아웃/네트워크/미인증) 내부 신호 —
    이 모듈 밖으로 전파하지 않고 REC-003 `PROVIDER_UNAVAILABLE`로 흡수한다."""


def _aggregate(materialities: list[Classification]) -> Classification:
    if not materialities:
        return Classification.HEALTHY
    present = set(materialities)
    for candidate in _SEVERITY:
        if candidate in present:
            return candidate
    return Classification.HEALTHY


def _provider_order_view(internal: OrderView, provider: ProviderOrder) -> OrderView:
    """internal과 같은 `order_id`를 그대로 써 `compare_triple`의 dict 조인이
    한 주문으로 매칭하게 한다(실제 매칭 키는 호출부가 이미 확인한
    `client_order_id` — `OrderView.order_id`는 OMS 내부에서만 의미 있는
    키라 provider 쪽엔 대응값이 없다)."""
    return internal.model_copy(
        update={
            "status": provider.status,
            "filled_quantity": provider.filled_quantity,
            "average_fill_price": (
                provider.average_fill_price.amount
                if provider.average_fill_price is not None
                else None
            ),
        }
    )


async def _fetch_provider_orders(adapter: ExchangeAdapter) -> list[ProviderOrder]:
    try:
        return await adapter.get_open_orders()
    except Exception as exc:  # noqa: BLE001 — I11: 어댑터 실패는 0 가정이 아니라 PROVIDER_UNAVAILABLE
        raise ProviderUnavailableError(str(exc)) from exc


async def _connection_unavailable(pool: asyncpg.Pool, connection_id: UUID | None) -> bool:
    if connection_id is None:
        return False
    health = await PostgresConnectionRepository(pool).get_latest_health(connection_id)
    return health is None or health.state.value != "HEALTHY"


async def _apply_account_gate(
    risk_repo: RiskGateRepository, *, tenant_id: UUID, blocked: bool, reason: str
) -> None:
    """I12 — MATERIAL_MISMATCH/PROVIDER_UNAVAILABLE가 열려 있는 동안 ACCOUNT
    범위 safety control을 ACTIVE로 유지하고, 해소되면 즉시 해제한다.
    REC-006 재실행 dedupe — `activate_safety_control`(risk_gate)은 자체
    idempotency_digest를 채우지 않아(`insert_safety_control` 참조) 매 호출이
    새 행을 만든다. 같은 `reason`의 ACTIVE ACCOUNT control이 이미 있으면
    다시 만들지 않는 건 이 함수가 진다."""
    scope_ref = str(tenant_id)
    existing = [
        c
        for c in await risk_repo.list_active_controls(tenant_id=tenant_id)
        if c.scope == SafetyScope.ACCOUNT and c.scope_ref == scope_ref
    ]

    if blocked:
        if any(c.reason == reason for c in existing):
            return
        # 사람이 아니라 대사 엔진 자신의 판단(run_reconciliation.py와 동일 근거).
        await activate_safety_control(
            risk_repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            actor_is_admin=True,
            scope=SafetyScope.ACCOUNT,
            scope_ref=scope_ref,
            reason=reason,
        )
        return

    for control in existing:
        if control.reason.startswith(_BLOCK_REASON_PREFIX):
            await deactivate_safety_control(
                risk_repo, tenant_id=tenant_id, actor_is_admin=True, control_id=control.id
            )


async def reconcile_account(
    *,
    pool: asyncpg.Pool,
    adapter: ExchangeAdapter,
    tenant_id: UUID,
    connection_id: UUID | None,
    account_ref: str,
    window: timedelta,
    policy: MaterialityPolicy = DEFAULT_POLICY,
) -> ReconcileSummaryView:
    now = datetime.now(timezone.utc)
    window_start = now - window
    risk_repo = PostgresRiskGateRepository(pool)

    internal_orders, _ = await list_orders(pool, tenant_id=tenant_id, statuses=_OPEN_STATUSES)

    provider_unavailable = await _connection_unavailable(pool, connection_id)
    discrepancies: list[Discrepancy] = []
    if not provider_unavailable:
        try:
            provider_orders_raw = await _fetch_provider_orders(adapter)
        except ProviderUnavailableError:
            provider_unavailable = True
        else:
            by_client_id = {o.client_order_id: o for o in provider_orders_raw}
            provider_views = [
                _provider_order_view(internal, by_client_id[internal.client_order_id])
                for internal in internal_orders
                if internal.client_order_id in by_client_id
            ]
            discrepancies = compare_triple(internal_orders, provider_views, [], {}, {}, policy)

    if provider_unavailable:
        targets: list[OrderView | None] = list(internal_orders) or [None]
        discrepancies = [
            Discrepancy(
                kind="FILLED_QTY_MISMATCH",
                entity_key=str(order.order_id) if order is not None else account_ref,
                internal_value=order.filled_quantity if order is not None else None,
                provider_value=None,
                materiality=Classification.PROVIDER_UNAVAILABLE,
            )
            for order in targets
        ]
        overall = Classification.PROVIDER_UNAVAILABLE
    elif not discrepancies:
        overall = Classification.HEALTHY
    else:
        overall = _aggregate([d.materiality for d in discrepancies])

    await _apply_account_gate(
        risk_repo,
        tenant_id=tenant_id,
        blocked=overall in _BLOCKING,
        reason=f"{_BLOCK_REASON_PREFIX}:{overall.value}",
    )

    return ReconcileSummaryView(
        account_ref=account_ref,
        window_start=window_start,
        window_end=now,
        discrepancies=discrepancies,
        overall_classification=overall.value,
        checked_at=now,
    )
