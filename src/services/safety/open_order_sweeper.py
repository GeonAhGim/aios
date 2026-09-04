"""Kill switch 범위 내 미체결 주문 일괄 취소.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.8, §5(105번 표), §9(R-39, 선행 R-38).

§3.8의 GLOBAL/PROVIDER/TENANT/ACCOUNT 조건은 `orders`와 `strategy_executions`가
컬럼명(`exchange`, `user_id`)이 같아 R-38 `legacy_execution_pauser._condition_for`가
이미 구현한 파싱·검증(scope_ref 형식, 예외 타입)을 그대로 재사용한다 — 재구현하지
않는다. STRATEGY_DEPLOYMENT만 대상 컬럼이 다르다(`orders.execution_id`는
`strategy_executions.id`를 가리키는 FK이지 그 자체가 PK가 아니다).

105번 §5 표의 "행별 UPDATE" 표기는 R-38과 동일하게 "스코프 조건에 매칭되는
모든 행을 한 UPDATE...RETURNING으로" 구현한다(개별 order_id 루프로 사전
SELECT하지 않는다) — 그래야 대상 선별 경로가 그 UPDATE 하나뿐이라서
TOCTOU가 없다(같은 control_id로 재호출해도 이미 CANCEL_REQUESTED로 전이된
행은 `status IN (...)` 조건에 다시 걸리지 않아 자연히 멱등하다).

어댑터 `cancel_order` 예외는 개별 주문 실패로만 집계하고 스윕 전체를 막지
않는다 — 취소 성공/실패의 최종 진실은 reconcile이 소유하므로, 여기서는
DB 상태를 되돌리지 않는다(CANCEL_REQUESTED로 남겨 재시도/조회 대상이
되게 한다)."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from src.exchanges.common.adapter import ExchangeAdapter
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.safety.legacy_execution_pauser import _condition_for

logger = logging.getLogger(__name__)

_CANCELABLE_STATUSES = ("SUBMITTED", "PARTIALLY_FILLED")
_CANCELABLE_STATUSES_SQL = ", ".join(f"'{s}'" for s in _CANCELABLE_STATUSES)


@dataclass(frozen=True)
class SweepReport:
    control_id: UUID
    scope: SafetyScope
    scope_ref: str
    cancel_requested: tuple[UUID, ...] = ()
    adapter_failed: tuple[UUID, ...] = ()
    skipped: tuple[UUID, ...] = ()


def _orders_condition_for(scope: SafetyScope, scope_ref: str) -> tuple[str | None, list[object]]:
    """§3.8 매핑을 `orders` 컬럼으로 옮긴다. STRATEGY_DEPLOYMENT의
    `exec:<int>`만 대상 컬럼이 `id`(strategy_executions PK)가 아니라
    `execution_id`(orders의 FK)라서 조건 문자열을 바꿔치기한다 — 파싱·검증
    자체는 `_condition_for`가 이미 한 것을 그대로 쓴다."""
    condition, params = _condition_for(scope, scope_ref)
    if scope is not SafetyScope.STRATEGY_DEPLOYMENT or condition is None:
        return condition, params
    return "execution_id = $1", params


async def sweep_open_orders(
    pool: asyncpg.Pool,
    adapters: Mapping[str, ExchangeAdapter],
    *,
    control_id: UUID,
    scope: SafetyScope,
    scope_ref: str,
) -> SweepReport:
    """`scope`/`scope_ref` 범위 안의 `orders` 중 `SUBMITTED`/`PARTIALLY_FILLED`인
    행만 `CANCEL_REQUESTED`로 전이시키고(단일 조건부 UPDATE...RETURNING),
    반환된 각 주문에 대해 거래소 어댑터 cancel을 시도한다. 개별 어댑터
    실패는 `adapter_failed`에만 집계되고 나머지 주문 처리를 막지 않는다."""
    condition, params = _orders_condition_for(scope, scope_ref)
    if condition is None:
        logger.info(
            "sweep_open_orders: scope=%s scope_ref=%s control_id=%s는 orders 대상이 "
            "아닙니다(예: STRATEGY_DEPLOYMENT dep:<uuid>는 paper_control 전용) — 0건.",
            scope.value,
            scope_ref,
            control_id,
        )
        return SweepReport(control_id=control_id, scope=scope, scope_ref=scope_ref)

    # condition은 _orders_condition_for()가 돌려주는 고정 상수 중 하나다(호출자
    # 입력이 SQL 문자열로 직접 들어가지 않는다 — 값은 전부 $n 파라미터).
    async with pool.acquire() as conn:
        requested_rows = await conn.fetch(
            "UPDATE orders SET status = 'CANCEL_REQUESTED', updated_at = now() "  # noqa: S608
            f"WHERE status IN ({_CANCELABLE_STATUSES_SQL}) AND ({condition}) "
            "RETURNING order_id, exchange, exchange_order_id",
            *params,
        )
        skipped_rows = await conn.fetch(
            f"SELECT order_id FROM orders WHERE ({condition}) "  # noqa: S608
            f"AND status NOT IN ({_CANCELABLE_STATUSES_SQL}, 'CANCEL_REQUESTED')",
            *params,
        )

    cancel_requested: list[UUID] = []
    adapter_failed: list[UUID] = []
    for row in requested_rows:
        order_id: UUID = row["order_id"]
        cancel_requested.append(order_id)
        idempotency_key = f"sweep:{control_id}:{order_id}"
        adapter = adapters.get(row["exchange"])
        exchange_order_id = row["exchange_order_id"]
        if adapter is None or exchange_order_id is None:
            adapter_failed.append(order_id)
            logger.warning(
                "sweep_open_orders(%s): 취소 불가 — adapter_configured=%s "
                "exchange_order_id=%s",
                idempotency_key,
                adapter is not None,
                exchange_order_id,
            )
            continue
        try:
            await adapter.cancel_order(exchange_order_id)
        except Exception:  # noqa: BLE001 — 개별 주문 실패가 전체 스윕을 막지 않는다
            adapter_failed.append(order_id)
            logger.exception("sweep_open_orders(%s): 어댑터 cancel 실패", idempotency_key)
        else:
            logger.info("sweep_open_orders(%s): 취소 요청 완료", idempotency_key)

    skipped = tuple(row["order_id"] for row in skipped_rows)
    logger.info(
        "sweep_open_orders(scope=%s, scope_ref=%s, control_id=%s): 요청 %d건, 실패 %d건, "
        "skip %d건",
        scope.value,
        scope_ref,
        control_id,
        len(cancel_requested),
        len(adapter_failed),
        len(skipped),
    )
    return SweepReport(
        control_id=control_id,
        scope=scope,
        scope_ref=scope_ref,
        cancel_requested=tuple(cancel_requested),
        adapter_failed=tuple(adapter_failed),
        skipped=skipped,
    )
