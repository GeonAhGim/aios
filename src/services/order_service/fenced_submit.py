"""R-37 — fence 확인 후에만 어댑터를 호출하는 제출(§3.6 시퀀스 4~10).

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2.5 `order_service/fenced_submit.py`
행, §3.6, §4.1 I1/I4, §5 "`orders` INSERT with `risk_decision_id`" 행, §6
"kill switch와 submit 경합"/"어댑터 호출 후 fence 변경" 행, §7 post-fence SLO 0.

`gate.py`와 같은 이유로 이 모듈은 foundation을 import하지 않는다 — fence는
`read_fences: FenceReader`(호출부가 `foundation_gate` 쪽에서 만들어 주입)가
돌려주는 평탄화 스냅샷(`"{SafetyScope}:{scope_ref}" -> token`,
`GateDecision.fence_snapshot`과 같은 형식)으로만 비교한다.

시퀀스(§3.6 번호 그대로):
  2  gate outcome이 ALLOW가 아니면 `OrderDeniedByRiskGateError`
  3  `gate_decision.decision_id`가 None이면 `RiskDecisionMissingError` —
     I1 fail-closed. DB 트리거(`93c0e7f6b8d9`)가 tenant·outcome·만료를
     한 번 더 검사하므로 코드가 결정을 위조해도 INSERT에서 막힌다.
  3' (task-1532, I4·I10) `decision_reader.get_for_tenant(decision_id, user_id)`
     로 WORM `risk_decision` 행을 재조회해 `decision_binding.verify_decision_
     binding`으로 execution_ref·intent(symbol·side·quantity)·F0를 주문·호출자
     값과 대조한다. 행이 없거나(타 tenant 포함) 하나라도 다르면 claim 전에
     감사 `risk_decision_integrity_rejected`(reason
     INTEGRITY_RISK_FINGERPRINT_MISMATCH, §3.4 재사용) + `RiskDecisionIntegrity
     Error`. 이후 F0는 **호출자 값이 아니라 WORM 값**이다 — 호출자
     `fence_snapshot`은 WORM과 같아야 하고 다르면 거부(무시 아님).
     `decision_reader`는 `read_fences`처럼 조립부가 주입한다(foundation 비
     import 원칙). 기본값 없음(I-01) — 우회 경로가 되지 않도록 시그니처
     테스트가 고정한다.
  4  INSERT orders(status=CREATED, risk_decision_id=...)  # claim, 멱등
  5  F1 := read_fences()                                   # 부작용 직전 재조회
  6  F1이 F0보다 증가했으면 claim 행을 FAILED로(조건부 UPDATE) + 감사
     `post_fence_side_effect_prevented` + `FenceStaleError`. 어댑터 호출 없음.
     스펙은 `status='ABORTED_FENCE'`를 적었지만 `OrderStatus`는 01번 §2.3
     동결 공유 계약(`oms/domain/state_machine.py` 전이표가 전수 검사)이라
     값을 추가하지 않고, 이미 허용된 CREATED→FAILED 전이를 쓰며 사유는
     감사 행 `reason_code=RISK_FENCE_STALE`로 남긴다(PM 결정 대기 — 추가
     승인 시 이 한 곳만 바꾸면 된다).
  7  adapter.place_order — 유일한 부작용. 예외면 claim 삭제 후 전파(submit.py 동일)
  8  F2 := read_fences()
  9  F2 > F1이면 진짜 post-fence 부작용: 메트릭 +1, best-effort cancel,
     감사 CRITICAL `post_fence_side_effect_detected`. SLO는 0이며 이 분기는
     "막지 못한 것을 반드시 계수·되돌린다"는 보루다.
 10  기존 영속화·포지션 기록 경로.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import conditional_update
from src.core.logging.audit_log import record_audit_log
from src.core.observability.metric_names import SAFETY_POST_FENCE_SIDE_EFFECT_COUNT_TOTAL
from src.core.observability.metrics import MetricsPort, NullMetrics
from src.data.models.trading import Order, OrderStatus
from src.exchanges.common.adapter import ExchangeAdapter
from src.services.order_service import repository
from src.services.order_service.gate import GateDecision, GateOutcome
from src.services.order_service.position_ledger import record_fill_in_position_ledger
from src.services.order_service.submit import OrderDeniedByRiskGateError
from src.services.order_service.worm_decision_check import DecisionReader, bind_to_worm_decision

FenceReader = Callable[[], Awaitable[Mapping[str, int]]]

_ACTOR = "order_service.fenced_submit"
AUDIT_FENCE_STALE_PREVENTED = "post_fence_side_effect_prevented"
AUDIT_POST_FENCE_DETECTED = "post_fence_side_effect_detected"
_CANCELLABLE = frozenset({OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED, OrderStatus.UNKNOWN})


class FenceStaleError(Exception):
    """§6 "kill switch와 submit 경합" — 부작용 직전 재조회에서 fence가 이미
    증가해 있었다. 거래소는 호출되지 않았고 claim 행은 FAILED다."""

    def __init__(self, stale_pairs: tuple[str, ...], *, order_id: UUID) -> None:
        self.stale_pairs = stale_pairs
        self.order_id = order_id
        super().__init__(f"fence stale before submit (order {order_id}): {stale_pairs}")


class RiskDecisionMissingError(Exception):
    """I1 — `GateDecision.decision_id`가 없으면 주문을 claim조차 하지 않는다."""


def stale_pairs(observed: Mapping[str, int], current: Mapping[str, int]) -> tuple[str, ...]:
    """§3.6 — 토큰 *증가*만 stale이다(감소는 DB 제약상 불가). `current`에만
    있는 pair는 0→N 증가로 본다. 순수 함수, 결정론적 정렬."""
    return tuple(
        sorted(pair for pair, token in current.items() if token > observed.get(pair, 0))
    )


async def submit_with_fence(
    pool: asyncpg.Pool,
    adapter: ExchangeAdapter,
    order: Order,
    *,
    user_id: UUID,
    gate_decision: GateDecision,
    read_fences: FenceReader,
    decision_reader: DecisionReader,
    metrics: MetricsPort | None = None,
    trace_id: UUID | None = None,
) -> Order:
    metrics = metrics if metrics is not None else NullMetrics()

    if gate_decision.outcome != GateOutcome.ALLOW:
        raise OrderDeniedByRiskGateError(gate_decision.reason_codes)
    if gate_decision.decision_id is None:
        raise RiskDecisionMissingError(
            f"주문 {order.client_order_id}: gate 결정에 risk_decision_id가 없다 — I1, 제출 거부"
        )

    # 3' — WORM 재조회·결속 대조. F0의 출처는 여기서부터 WORM 행이다.
    f0 = await bind_to_worm_decision(
        pool, order, user_id=user_id, actor=_ACTOR, gate_decision=gate_decision,
        decision_reader=decision_reader, trace_id=trace_id,
    )

    # 4 — claim(멱등). submit.py FD-4.2-a와 동일한 UNIQUE 선점.
    async with pool.acquire() as conn:
        try:
            claimed = await repository.insert(
                conn, order, user_id=user_id, risk_decision_id=gate_decision.decision_id
            )
        except asyncpg.UniqueViolationError:
            existing = await repository.get_by_client_order_id(conn, order.client_order_id)
            if existing is None:
                raise
            return existing

    # 5~6 — 부작용 직전 재조회.
    f1 = await read_fences()
    stale = stale_pairs(f0, f1)
    if stale:
        async with pool.acquire() as conn, conn.transaction():
            await conditional_update(
                conn,
                table="orders",
                id_column="order_id",
                id_value=claimed.order_id,
                expected_state_column="status",
                expected_state_value=OrderStatus.CREATED.value,
                set_values={
                    "status": OrderStatus.FAILED.value,
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            await record_audit_log(
                conn,
                actor_agent=_ACTOR,
                action_type=AUDIT_FENCE_STALE_PREVENTED,
                user_id=user_id,
                target_type="order",
                target_id=str(claimed.order_id),
                decision_data={
                    "reason_code": "RISK_FENCE_STALE",
                    "stale_pairs": list(stale),
                    "observed": dict(f0),
                    "current": dict(f1),
                    "decision_id": str(gate_decision.decision_id),
                },
                trace_id=trace_id,
            )
        raise FenceStaleError(stale, order_id=claimed.order_id)

    # 7 — 유일한 부작용.
    try:
        submitted = await adapter.place_order(claimed)
    except Exception:
        async with pool.acquire() as conn:
            await repository.delete(conn, claimed.order_id)
        raise

    # 8 — 호출 후 재조회.
    f2 = await read_fences()
    post_fence = stale_pairs(f1, f2)

    async with pool.acquire() as conn:
        persisted = await repository.update_from_exchange(
            conn, submitted, expected_status=claimed.status
        )
    if post_fence:
        persisted = await _contain_post_fence(
            pool, adapter, persisted, metrics=metrics, user_id=user_id,
            stale=post_fence, before=f1, after=f2, trace_id=trace_id,
        )
    await record_fill_in_position_ledger(pool, persisted, metrics=metrics)
    return persisted


async def _contain_post_fence(
    pool: asyncpg.Pool,
    adapter: ExchangeAdapter,
    persisted: Order,
    *,
    metrics: MetricsPort,
    user_id: UUID,
    stale: tuple[str, ...],
    before: Mapping[str, int],
    after: Mapping[str, int],
    trace_id: UUID | None,
) -> Order:
    """9 — 막지 못한 post-fence 부작용의 계수·되돌리기. cancel은 best-effort
    (실패해도 예외를 삼키지 않고 감사 행에 `cancel_error`로 남긴다 — reconcile이
    후속 처리)."""
    metrics.counter(
        SAFETY_POST_FENCE_SIDE_EFFECT_COUNT_TOTAL, labels={"exchange": persisted.exchange}
    )
    cancelled = False
    cancel_error: str | None = None
    if persisted.exchange_order_id is not None and persisted.status in _CANCELLABLE:
        try:
            cancelled = bool(await adapter.cancel_order(persisted.exchange_order_id))
        except Exception as exc:  # noqa: BLE001 — best-effort, 감사로 보고
            cancel_error = repr(exc)
    async with pool.acquire() as conn, conn.transaction():
        if cancelled:
            persisted = await repository.update_from_exchange(
                conn,
                persisted.model_copy(update={"status": OrderStatus.CANCELLED}),
                expected_status=persisted.status,
            )
        await record_audit_log(
            conn,
            actor_agent=_ACTOR,
            action_type=AUDIT_POST_FENCE_DETECTED,
            user_id=user_id,
            target_type="order",
            target_id=str(persisted.order_id),
            decision_data={
                "severity": "CRITICAL",
                "stale_pairs": list(stale),
                "before": dict(before),
                "after": dict(after),
                "cancelled": cancelled,
                "cancel_error": cancel_error,
                "exchange_order_id": persisted.exchange_order_id,
            },
            trace_id=trace_id,
        )
    return persisted
