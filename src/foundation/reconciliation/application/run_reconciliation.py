"""RunReconciliation 커맨드.

Spec: AIOSproject 50번 §3, 80번 §1/§2.

내부/provider 값은 둘 다 호출자가 `EntitySnapshot`으로 공급한다 —
paper_control(FND-07)에 아직 fill/position/balance 내부 원장이 없고,
connections(FND-05)의 account_snapshot도 실제 숫자를 갖지 않아(마이그레이션
docstring 참조) 이 리프가 원천 데이터를 직접 읽어올 대상이 없다. 이
커맨드가 실제로 제공하는 건 분류·집계·상태 갱신·kill switch 연동
로직이다 — 두 원장이 실제 값을 갖게 되면 입력 조립부만 교체한다.

MATERIAL_MISMATCH/PROVIDER_UNAVAILABLE 시 risk_gate(FND-06)에
STRATEGY_DEPLOYMENT 범위 safety control을 건다(80번 §1 "creates a
SafetyControl request and block new submissions") — 이 트리거는 사람이
아니라 reconciliation 엔진 자신이므로 `actor_is_admin=True`로 호출한다
(사람의 self-service ACCOUNT 범위 제한과는 별개 경로)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from src.foundation.connections.ports.repository import ConnectionRepository
from src.foundation.reconciliation.contracts.v1 import Classification as ContractClassification
from src.foundation.reconciliation.contracts.v1 import (
    EntitySnapshot,
    ReconciliationItemView,
    ReconciliationRunView,
)
from src.foundation.reconciliation.domain.models import (
    Classification,
    MaterialityPolicy,
    ReconciliationItem,
    ReconciliationRun,
    ReconciliationState,
    RunState,
)
from src.foundation.reconciliation.domain.rules import (
    aggregate_classification,
    classify_item,
    compute_input_hash,
)
from src.foundation.reconciliation.ports.repository import ReconciliationRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.domain.models import SafetyScope
from src.foundation.risk_gate.ports.repository import RiskGateRepository

RULE_VERSION = "v1"

_DEFAULT_POLICY = MaterialityPolicy(
    absolute_tolerance=Decimal("0.01"),
    relative_tolerance_pct=Decimal("0.1"),
)

_BLOCKING_CLASSIFICATIONS = frozenset(
    {Classification.MATERIAL_MISMATCH, Classification.PROVIDER_UNAVAILABLE}
)


def run_to_view(run: ReconciliationRun, aggregate: Classification) -> ReconciliationRunView:
    return ReconciliationRunView(
        id=run.id,
        target_type=run.target_type,
        target_ref=run.target_ref,
        items=[
            ReconciliationItemView(
                entity_type=i.entity_type,
                entity_key=i.entity_key,
                internal_value=i.internal_value,
                provider_value=i.provider_value,
                classification=ContractClassification(i.classification.value),
            )
            for i in run.items
        ],
        aggregate_classification=ContractClassification(aggregate.value),
        created_at=run.created_at,
    )


async def run_reconciliation(
    repo: ReconciliationRepository,
    connection_repo: ConnectionRepository,
    risk_repo: RiskGateRepository,
    *,
    tenant_id: UUID,
    target_type: str,
    target_ref: UUID,
    connection_id: UUID | None,
    entities: list[EntitySnapshot],
    policy: MaterialityPolicy = _DEFAULT_POLICY,
) -> ReconciliationRunView:
    input_hash = compute_input_hash(
        str(target_ref),
        {
            e.entity_key: (str(e.internal_value), str(e.provider_value))
            for e in entities
        },
    )

    existing = await repo.get_run_by_input_hash(target_ref, input_hash)
    if existing is not None:
        aggregate = aggregate_classification(tuple(i.classification for i in existing.items))
        return run_to_view(existing, aggregate)

    # 80번 §2 "Provider timeout creates PROVIDER_UNAVAILABLE; it never
    # assumes zero balance/fill" — connection 자체가 unhealthy면 개별 항목
    # 분류 이전에 전체를 PROVIDER_UNAVAILABLE로 본다.
    connection_unavailable = False
    if connection_id is not None:
        health = await connection_repo.get_latest_health(connection_id)
        connection_unavailable = health is None or health.state.value != "HEALTHY"

    items = []
    for entity in entities:
        classification = (
            Classification.PROVIDER_UNAVAILABLE
            if connection_unavailable
            else classify_item(entity.internal_value, entity.provider_value, policy)
        )
        items.append(
            ReconciliationItem(
                id=uuid4(),
                run_id=uuid4(),  # adapter가 실제 run_id로 덮어씀(insert_run_with_items)
                entity_type=entity.entity_type,
                entity_key=entity.entity_key,
                internal_value=entity.internal_value,
                provider_value=entity.provider_value,
                classification=classification,
            )
        )

    aggregate = aggregate_classification(tuple(i.classification for i in items))

    run = await repo.insert_run_with_items(
        ReconciliationRun(
            id=uuid4(),
            tenant_id=tenant_id,
            target_type=target_type,
            target_ref=target_ref,
            connection_id=connection_id,
            input_hash=input_hash,
            state=RunState.COMPLETED,
            rule_version=RULE_VERSION,
        ),
        tuple(items),
    )

    now = datetime.now(timezone.utc)
    safety_control_id: UUID | None = None
    blocking_reason: str | None = None
    if aggregate in _BLOCKING_CLASSIFICATIONS:
        blocking_reason = f"INTEGRITY_RECONCILIATION_MISMATCH:{aggregate.value}"
        control = await activate_safety_control(
            risk_repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            actor_is_admin=True,
            scope=SafetyScope.STRATEGY_DEPLOYMENT,
            scope_ref=str(target_ref),
            reason=blocking_reason,
        )
        safety_control_id = control.id

    await repo.upsert_state(
        ReconciliationState(
            target_ref=target_ref,
            target_type=target_type,
            tenant_id=tenant_id,
            aggregate_status=aggregate,
            last_healthy_at=now if aggregate == Classification.HEALTHY else None,
            last_checked_at=now,
            blocking_reason=blocking_reason,
            revision=0,
            safety_control_id=safety_control_id,
        )
    )

    return run_to_view(run, aggregate)
