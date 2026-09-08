"""L4-18a(task-2151) — 재시작 복구 1/2: 만료 execution lease 회수 +
복구 완료 전 submit_order 거부.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §6 F6, §9 L4-18.
F6은 5단계(①outbox lease 만료→UNKNOWN ②UNKNOWN 전부 resolver ③비터미널
주문 RESYNC ④기존 recovery_wiring ⑤완료 전 submit_order 거부)로 정의돼
있지만, 이 배치(task-2151 decision)는 execution_ownership `execution_leases`
회수와 ⑤ submit 거부만 다룬다 — outbox SENDING 재진입/UNKNOWN 전환(①②③)은
손대지 않는다(후속 task-2310).

이 모듈은 순수 조각만 제공한다: 실제 오케스트레이션(리스 회수 →
`recover_orders_on_startup` → 완료 표시)은 기존 조립 지점인
`execution_loop/recovery_wiring.py`가 이어서 한다(§C 중복 컨텍스트 금지 —
새 오케스트레이터를 여기 또 만들지 않는다).
"""
from __future__ import annotations

import logging

import asyncpg

from src.core.logging.audit_log import record_audit_log
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext, PreSubmitGate

logger = logging.getLogger(__name__)

RECOVERY_IN_PROGRESS_REASON = "OMS_RECOVERY_IN_PROGRESS"
_LEASE_RECLAIM_ACTOR = "main_process"
_LEASE_RECLAIM_ACTION_TYPE = "system.restart_recovery.leases"


class RecoveryState:
    """재시작 복구 완료 여부를 프로세스 전역에서 공유하는 가변 상태.

    `asyncio.Event`가 아니라 단순 bool 플래그다 — `make_recovery_gate`는
    복구가 끝나길 기다렸다(`await event.wait()`) 통과시키면 안 되고, 끝나지
    않았으면 그 자리에서 즉시 거부해야 한다(§6 F6 ⑤). 한 프로세스 수명 동안
    한 인스턴스만 만들어 `recovery_wiring.run_startup_recovery`가 채우고
    `background_loops.py`가 게이트 생성자에 그대로 넘긴다."""

    def __init__(self) -> None:
        self._complete = False

    @property
    def complete(self) -> bool:
        return self._complete

    def mark_complete(self) -> None:
        self._complete = True


async def reclaim_expired_leases(pool: asyncpg.Pool) -> int:
    """만료된(`expires_at < now()`) `execution_leases` 행을 삭제해 회수한다.

    재시작한 새 프로세스는 죽은 프로세스의 `owner_id`(`{host}:{pid}:{uuid}`
    형태, `background_loops.py`)를 모르므로 `release_all(old_owner)`을 부를
    수 없다 — 이미 TTL이 지난 리스는 다음 `acquire_or_renew_many`가 자연히
    인수하지만(`postgres_repository.py`의 `WHERE ... expires_at < now()`),
    재시작 직후 명시적으로 청소해 두면 실행 루프가 뜨기 전에 소유권 상태를
    깨끗하게 만든다. 아직 TTL이 남은(만료 전) 행은 건드리지 않는다 — 다른
    살아있는 프로세스가 정상적으로 쥐고 있을 수 있다."""
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM execution_leases WHERE expires_at < now()")
    reclaimed = int(result.split()[-1])
    if reclaimed:
        async with pool.acquire() as conn:
            await record_audit_log(
                conn,
                actor_agent=_LEASE_RECLAIM_ACTOR,
                action_type=_LEASE_RECLAIM_ACTION_TYPE,
                decision_data={"reclaimed_leases": reclaimed},
                target_type="system",
                target_id=_LEASE_RECLAIM_ACTOR,
            )
    return reclaimed


def make_recovery_gate(state: RecoveryState, delegate: PreSubmitGate) -> PreSubmitGate:
    """§6 F6 ⑤ — 복구가 끝나기 전에는 `delegate`(보통
    `make_foundation_pre_submit_gate`)를 아예 부르지 않고 무조건 DENY한다.
    복구 완료 후에는 매 호출마다 `state.complete`를 다시 확인만 하고(별도
    캐싱 없음) `delegate`로 위임한다."""

    async def gate(context: OrderContext) -> GateDecision:
        if not state.complete:
            logger.info(
                "pre_submit_gate: 재시작 복구 미완료 — execution_id=%s 거부(%s)",
                context.execution_id,
                RECOVERY_IN_PROGRESS_REASON,
            )
            return GateDecision(
                outcome=GateOutcome.DENY,
                reason_codes=(RECOVERY_IN_PROGRESS_REASON,),
            )
        return await delegate(context)

    return gate
