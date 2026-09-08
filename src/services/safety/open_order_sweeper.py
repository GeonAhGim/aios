"""Kill switch 범위 내 미체결 주문 일괄 취소.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.8, §5(105번 표), §9(R-39, 선행 R-38).
FA-16(task-2406): docs/specs/ibor_fund_accounting_and_resilience.md#§9 —
`orders.status`를 바꾸는 모든 행은 같은 트랜잭션의 `order_events` 행을
동반해야 한다(I-10). 이 모듈은 원래 단일 `UPDATE ... RETURNING`으로 매칭되는
모든 행을 한 번에 전이시켰는데, 그 경로에는 `order_events` INSERT가 전혀
없었다 — task-2394 진단이 잡은 결함(replay_verify의 orders 투영 불일치
원인). `073beca589d5`의 `oms_enforce_order_transition_trg`는 `FOR EACH ROW`로
동작하고 `oms.event_written` 플래그를 검사 즉시 소모하므로(같은 tx의 첫
row에서 세운 플래그는 두 번째 row에서 이미 사라짐), 진짜 고정은 "배치
전체에 플래그 한 번"이 아니라 "행마다 플래그 세우기 → 이벤트 INSERT →
그 행만의 조건부 UPDATE"다. 그래서 대상 선별은 여전히 한 번의 읽기 쿼리로
하되(§3.8 매핑을 두 번 계산하지 않는다), 실제 전이는 주문 단위로 옮겼다.

§3.8의 GLOBAL/PROVIDER/TENANT/ACCOUNT 조건은 `orders`와 `strategy_executions`가
컬럼명(`exchange`, `user_id`)이 같아 R-38 `legacy_execution_pauser._condition_for`
가 이미 구현한 파싱·검증(scope_ref 형식, 예외 타입)을 그대로 재사용한다 —
재구현하지 않는다. STRATEGY_DEPLOYMENT만 대상 컬럼이 다르다(`orders.
execution_id`는 `strategy_executions.id`를 가리키는 FK이지 그 자체가 PK가
아니다).

TOCTOU/동시성(주문 단위 전이로 바뀐 뒤에도 그대로 유지): 후보 선별 쿼리와
실제 전이 사이에 상태가 바뀔 수 있으므로, 각 주문은 `SELECT ... FOR UPDATE
SKIP LOCKED`로 그 주문 하나만 다시 잠그고 상태를 재확인한다 — 잠긴 시점에
더 이상 취소 가능 상태가 아니면(다른 sweep이 먼저 가져갔거나, 그 사이 체결/
거부됐거나) 조용히 `raced`로 건너뛴다. `FOR UPDATE`로 잠근 뒤의 조건부
UPDATE는(§3.8 105번 표준) 그 잠금 구간 안에서 항상 매치해야 정상이라
`ConcurrencyConflictError`가 나면 그건 이 함수가 다루는 경합이 아니라 진짜
불변조건 위반이라 그대로 전파한다. 같은 control_id로 재호출해도 이미
CANCEL_REQUESTED로 전이된 행은 후보 선별 쿼리(`status IN (...)`)에 다시
안 걸려 자연히 멱등하다(원래 동작 그대로).

어댑터 `cancel_order` 예외는 개별 주문 실패로만 집계하고 스윕 전체를 막지
않는다 — 취소 성공/실패의 최종 진실은 reconcile이 소유하므로, 여기서는
DB 상태를 되돌리지 않는다(CANCEL_REQUESTED로 남겨 재시도/조회 대상이
되게 한다).

알려진 한계(task-2406 DoD(e), 문서화된 갭으로 수용 — 해소는 task-2432 위임):
`orders.status =
'CANCEL_REQUESTED'`는 L4-06 `OrderStatus`(01번 공유접점 동결 계약, `src/
data/models/trading.py`)에 없는 kill-switch 전용 문자열이다. 이 값은
DoD(c)의 멱등성(재선별 쿼리가 `status IN ('SUBMITTED','PARTIALLY_FILLED')`만
보므로, 이미 CANCEL_REQUESTED인 행은 다시 안 걸린다)을 위해 반드시 필요하다
— `_CANCELABLE_STATUSES` 밖의 값으로 전이하지 않으면 같은 sweep을 다시
돌렸을 때 같은 주문을 또 취소 시도하게 된다. 그래서 `order_events`에는
그 값을 못 쓴다(`OrderTransitionEvent.to_status`는 `OrderStatus`로 캐스팅되고,
`scripts/replay_verify.py`가 이 sweep이 닿은 모든 주문을 `order_events`
기준으로 골라 `PostgresOrderEventRepository.timeline()`으로 읽는다 —
'CANCEL_REQUESTED'를 넣으면 `ValueError`로 replay_verify 전체가 죽는 것을
실측 확인했다). 그래서 이 이벤트는 자기루프(`from_status == to_status`,
OMS `cancel_order.py`의 ACKNOWLEDGED/PARTIALLY_FILLED 자기루프와 같은 관례)
로 남긴다 — replay_verify는 죽지 않지만, `orders.status`(실제 'CANCEL_
REQUESTED')와 orders 투영이 접는 상태(자기루프라 안 바뀜)가 여전히 달라
그 주문은 replay_verify의 orders 스트림에서 불일치로 잡힌다(크래시는 아님).
`OrderStatus`/073beca589d5 `_ALLOWED_PAIRS`에 실제 `CANCEL_REQUESTED` 상태를
추가하는 마이그레이션 없이는(DoD(f)가 새 마이그레이션을 금지) 이 불일치
자체를 없앨 수 없다 — DoD(e) "0건"은 이 리프 단독으로는 못 채운다. 재현:
`test_sweep_open_orders_event_does_not_crash_replay_verify_timeline_read`
(크래시는 없음을 증명, 투영 불일치 자체는 남음). 승격 마이그레이션은
task-2432로 분리했다(§C 직렬화상 마이그레이션 사슬 뒤)."""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

import asyncpg

from src.core.db.conditional_write import conditional_update
from src.exchanges.common.adapter import ExchangeAdapter
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.safety.legacy_execution_pauser import _condition_for

logger = logging.getLogger(__name__)

_CANCELABLE_STATUSES = ("SUBMITTED", "PARTIALLY_FILLED")
_CANCELABLE_STATUSES_SQL = ", ".join(f"'{s}'" for s in _CANCELABLE_STATUSES)
# CANCEL_REQUESTED is not an OrderStatus member yet; replay_verify orders
# projection cannot byte-match until task-2432 promotes it.
_TO_STATUS = "CANCEL_REQUESTED"
_EVENT = "CANCEL_REQUESTED"


@dataclass(frozen=True)
class SweepReport:
    control_id: UUID
    scope: SafetyScope
    scope_ref: str
    cancel_requested: tuple[UUID, ...] = ()
    adapter_failed: tuple[UUID, ...] = ()
    skipped: tuple[UUID, ...] = ()
    raced: tuple[UUID, ...] = ()


def _orders_condition_for(scope: SafetyScope, scope_ref: str) -> tuple[str | None, list[object]]:
    """§3.8 매핑을 `orders` 컬럼으로 옮긴다. STRATEGY_DEPLOYMENT의
    `exec:<int>`만 대상 컬럼이 `id`(strategy_executions PK)가 아니라
    `execution_id`(orders의 FK)라서 조건 문자열을 바꿔치기한다 — 파싱·검증
    자체는 `_condition_for`가 이미 한 것을 그대로 쓴다."""
    condition, params = _condition_for(scope, scope_ref)
    if scope is not SafetyScope.STRATEGY_DEPLOYMENT or condition is None:
        return condition, params
    return "execution_id = $1", params


def _event_payload_hash(order_id: UUID, control_id: UUID, from_status: str) -> str:
    canonical = json.dumps(
        {"order_id": str(order_id), "control_id": str(control_id), "from_status": from_status},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _transition_to_cancel_requested(
    conn: asyncpg.Connection,
    order_id: UUID,
    *,
    control_id: UUID,
    scope: SafetyScope,
) -> asyncpg.Record | None:
    """주문 하나, 트랜잭션 하나: `FOR UPDATE SKIP LOCKED`로 이 행만 다시
    잠그고(경합으로 이미 취소 불가 상태가 됐거나 다른 sweep이 잠그고 있으면
    `None`), §5.1 순서대로 `SET LOCAL` → `order_events` INSERT → 조건부
    UPDATE를 같은 트랜잭션에서 수행한다. 반환값은 어댑터 취소 호출에 필요한
    `order_id, exchange, exchange_order_id`."""
    async with conn.transaction():
        locked = await conn.fetchrow(
            "SELECT status FROM orders "  # noqa: S608 — 상수만 보간, 값은 파라미터
            f"WHERE order_id = $1 AND status IN ({_CANCELABLE_STATUSES_SQL}) "
            "FOR UPDATE SKIP LOCKED",
            order_id,
        )
        if locked is None:
            return None
        from_status = locked["status"]

        # I6: 이 UPDATE 직전에 세우고, 바로 다음 order_events INSERT로 소비된다.
        # 배치 전체가 아니라 이 트랜잭션(=이 행) 안에서만 유효하다.
        #
        # order_events.to_status는 `_TO_STATUS`('CANCEL_REQUESTED', orders.status
        # 리터럴)가 아니라 `from_status`를 그대로 쓴다 — 자기루프. 이유(모듈
        # docstring 하단 "known gap" 참조): `_TO_STATUS`는 L4-06 `OrderStatus`
        # 동결 계약(01번 공유접점)에 없는 kill-switch 전용 문자열이라
        # `PostgresOrderEventRepository._row_to_event()`의 `OrderStatus(row[
        # "to_status"])`가 그 값을 만나면 예외를 던진다 — `order_events.
        # to_status`에 그대로 넣으면 `scripts/replay_verify.py`가 그 행을 읽는
        # 순간(어느 주문이든 이 sweep을 거치면 order_events에 잡힌다) 죽는다.
        # 자기루프는 실제 OMS `cancel_order.py`가 ACKNOWLEDGED/PARTIALLY_FILLED
        # 자기루프 CANCEL_REQUESTED 이벤트를 쓰는 것과 같은 관례(§3.3 "신규
        # 이벤트가 orders.status 동결 계약을 건드리지 않도록") — event 컬럼은
        # enum 캐스팅이 없어(raw string) 'CANCEL_REQUESTED'를 그대로 남길 수
        # 있다.
        await conn.execute("SELECT set_config('oms.event_written', '1', true)")
        await conn.execute(
            """
            INSERT INTO order_events (
                order_id, from_status, to_status, event, reason_code,
                actor_subject_id, trace_id, command_id, occurred_at, payload_hash
            ) VALUES ($1, $2, $2, $3, $4, 'system', $5, $6, now(), $7)
            """,
            order_id,
            from_status,
            _EVENT,
            f"kill_switch:{scope.value}",
            uuid4(),
            control_id,
            _event_payload_hash(order_id, control_id, from_status),
        )
        return await conditional_update(
            conn,
            table="orders",
            id_column="order_id",
            id_value=order_id,
            expected_state_column="status",
            expected_state_value=from_status,
            set_values={"status": _TO_STATUS, "updated_at": datetime.now(timezone.utc)},
            returning="order_id, exchange, exchange_order_id",
        )


async def sweep_open_orders(
    pool: asyncpg.Pool,
    adapters: Mapping[str, ExchangeAdapter],
    *,
    control_id: UUID,
    scope: SafetyScope,
    scope_ref: str,
) -> SweepReport:
    """`scope`/`scope_ref` 범위 안의 `orders` 중 `SUBMITTED`/`PARTIALLY_FILLED`인
    행만 `CANCEL_REQUESTED`로 전이시키고(주문 단위 트랜잭션, 각각
    `order_events` 동반), 실제로 전이된 각 주문에 대해 거래소 어댑터 cancel을
    시도한다. 개별 어댑터 실패는 `adapter_failed`에만 집계되고 나머지 주문
    처리를 막지 않는다."""
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
        candidate_rows = await conn.fetch(
            f"SELECT order_id FROM orders WHERE status IN ({_CANCELABLE_STATUSES_SQL}) "  # noqa: S608
            f"AND ({condition})",
            *params,
        )
        skipped_rows = await conn.fetch(
            f"SELECT order_id FROM orders WHERE ({condition}) "  # noqa: S608
            f"AND status NOT IN ({_CANCELABLE_STATUSES_SQL}, '{_TO_STATUS}')",
            *params,
        )

        transitioned_rows: list[asyncpg.Record] = []
        raced: list[UUID] = []
        for candidate in candidate_rows:
            order_id: UUID = candidate["order_id"]
            result = await _transition_to_cancel_requested(
                conn, order_id, control_id=control_id, scope=scope
            )
            if result is None:
                raced.append(order_id)
            else:
                transitioned_rows.append(result)

    cancel_requested: list[UUID] = []
    adapter_failed: list[UUID] = []
    for row in transitioned_rows:
        order_id = row["order_id"]
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
        "skip %d건, race %d건",
        scope.value,
        scope_ref,
        control_id,
        len(cancel_requested),
        len(adapter_failed),
        len(skipped),
        len(raced),
    )
    return SweepReport(
        control_id=control_id,
        scope=scope,
        scope_ref=scope_ref,
        cancel_requested=tuple(cancel_requested),
        adapter_failed=tuple(adapter_failed),
        skipped=skipped,
        raced=tuple(raced),
    )
