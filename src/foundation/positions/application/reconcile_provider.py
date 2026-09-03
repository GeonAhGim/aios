"""LB-16 — 거래소 잔고 대사(application/reconcile_provider.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9.3 LB-16.

내부 오픈 포지션(수량 합계)과 공급자(거래소) 잔고를 LB-6
`reconciliation_rules.build_entity_snapshots`로 `EntitySnapshot` 목록으로
조립하고, 분류·집계는 FND-08 `run_reconciliation`(주입된 `recon`)에 전부
위임한다 — 허용오차·판정 등급을 여기서 재구현하지 않는다(task-726 decision).
대사는 읽기 전용이다: 이 함수는 `pos_snapshot`/저널을 갱신하지 않고,
FND-08이 이미 갖고 있는 `reconciliation_run/item/state` 테이블에만 쓴다
(새 테이블 없음, task-726 decision).

`entity_key`는 `PositionKey.instrument_id`를 자산 코드로 근사해 provider
`AccountBalance.asset`과 맞춘다 — 실제 거래소 잔고 자산 코드와 정확히
일치한다는 보장은 없다(미검증). 파생상품·복합 심볼의 정확한 매핑은 후속
리프 과제로 남긴다.

`recon` 호출의 `connection_id`는 항상 `None`으로 고정한다: FND-08
`run_reconciliation`은 `connection_id`가 있으면 FND-05
`account_connection`의 헬스 상태를 먼저 확인해 unhealthy면 개별 판정 이전에
전체를 `PROVIDER_UNAVAILABLE`로 덮어쓴다(80번 §2). 이 리프가 받는
`connection_id`는 `provider.balances()`가 어댑터를 찾는 키일 뿐 반드시
`account_connection` 행을 가리키지 않으므로, 그 값을 그대로 넘기면 존재하지
않는 FK를 참조하거나(대사 저장 실패) 의도치 않은 헬스 게이트가 걸린다 —
FND-05 연동은 이 리프의 범위 밖이라 우회한다.

`provider.balances()`가 던지는 예외는 삼키지 않고 그대로 전파한다
(fail-closed, DoD #3) — 계좌별로 별도 호출이라 한 계좌의 조회 실패가
다른 계좌 호출에 영향을 주는 공유 상태가 없다.
"""
from __future__ import annotations

from collections.abc import Awaitable
from decimal import Decimal
from typing import Protocol
from uuid import UUID

import asyncpg

from src.core.observability.metric_names import POSITIONS_RECONCILIATION_MISMATCH_COUNT_TOTAL
from src.core.observability.metrics_registry import MetricsRegistry
from src.foundation.positions.domain.position_key import PositionKey
from src.foundation.positions.domain.reconciliation_rules import (
    InternalEntityValue,
    build_entity_snapshots,
)
from src.foundation.positions.ports.exchange_balance_source import ProviderBalanceSource
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository
from src.foundation.reconciliation.contracts.v1 import (
    Classification,
    EntitySnapshot,
    ReconciliationRunView,
)

__all__ = ["RunReconciliation", "reconcile_account"]

_TARGET_TYPE = "POSITIONS_EXCHANGE_BALANCE"
_ENTITY_TYPE = "EXCHANGE_BALANCE"


class RunReconciliation(Protocol):
    """FND-08 `run_reconciliation`(저장소 3개가 이미 바인딩된 형태)의 호출
    계약 — 이 리프는 저장소를 모르고 이 Protocol만 안다(71번 §4)."""

    def __call__(
        self,
        *,
        tenant_id: UUID,
        target_type: str,
        target_ref: UUID,
        connection_id: UUID | None,
        entities: list[EntitySnapshot],
    ) -> Awaitable[ReconciliationRunView]: ...


async def reconcile_account(
    tenant_id: UUID,
    account_id: UUID,
    *,
    connection_id: UUID,
    snapshots: SnapshotRepository,
    provider: ProviderBalanceSource,
    recon: RunReconciliation,
    pool: asyncpg.Pool,
    registry: MetricsRegistry,
) -> ReconciliationRunView:
    balances = await provider.balances(connection_id)
    provider_map: dict[str, Decimal] = {b.asset: b.total for b in balances}

    async with pool.acquire() as conn:
        open_positions = await snapshots.list_open(conn, tenant_id, account_id)

    internal_by_asset: dict[str, Decimal] = {}
    for snapshot in open_positions:
        asset = PositionKey.parse(snapshot.position_key).instrument_id
        internal_by_asset[asset] = internal_by_asset.get(asset, Decimal(0)) + snapshot.quantity

    internal = [
        InternalEntityValue(entity_type=_ENTITY_TYPE, entity_key=asset, value=quantity)
        for asset, quantity in internal_by_asset.items()
    ]
    entities = build_entity_snapshots(internal, provider_map)

    result = await recon(
        tenant_id=tenant_id,
        target_type=_TARGET_TYPE,
        target_ref=account_id,
        connection_id=None,
        entities=entities,
    )

    material_count = sum(
        1 for item in result.items if item.classification == Classification.MATERIAL_MISMATCH
    )
    if material_count:
        registry.counter(POSITIONS_RECONCILIATION_MISMATCH_COUNT_TOTAL).inc(material_count)

    return result
