"""L4-18a/b(task-2151/task-2310) — 재시작 복구: 만료 execution lease 회수 +
outbox SENDING 재진입/UNKNOWN 전환 + 복구 완료 전 submit_order 거부.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §6 F6, §9 L4-18.
F6은 5단계(①outbox lease 만료→UNKNOWN ②UNKNOWN 전부 resolver ③비터미널
주문 RESYNC ④기존 recovery_wiring ⑤완료 전 submit_order 거부)로 정의돼
있다. task-2151(배치 1/2)은 execution_ownership `execution_leases` 회수와
⑤ submit 거부를 다뤘다. 이 배치(task-2310, L4-18b)는 ①②를 잇는다 —
`recover_stuck_outbox_commands`가 outbox SENDING(lease 만료) 행을 §4.2/§4.4
상태기계에 따라 재진입(PENDING) 또는 UNKNOWN 전환하고, UNKNOWN이 된 주문은
`unknown_resolver.resolve_unknown`(task-1604)에 곧장 위임한다 — 재구현
금지. ③(비터미널 주문 RESYNC)은 여전히 범위 밖이다(decision).

이 모듈은 순수 조각만 제공한다: 실제 오케스트레이션(리스 회수 → outbox
복구 → `recover_orders_on_startup` → 완료 표시)은 기존 조립 지점인
`execution_loop/recovery_wiring.py`가 이어서 한다(§C 중복 컨텍스트 금지 —
새 오케스트레이터를 여기 또 만들지 않는다).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

import asyncpg

from src.core.logging.audit_log import record_audit_log
from src.data.models.trading import OrderStatus
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.services.oms.application.outbox_commands import AdapterResolver
from src.services.oms.application.outbox_writes import OutboxWrites, utcnow
from src.services.oms.application.unknown_resolver import resolve_unknown
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.domain.state_machine import OrderEvent
from src.services.oms.ports.repository import OrderRepoPort, OutboxRepoPort, OutboxRow
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext, PreSubmitGate

logger = logging.getLogger(__name__)

RECOVERY_IN_PROGRESS_REASON = "OMS_RECOVERY_IN_PROGRESS"
_LEASE_RECLAIM_ACTOR = "main_process"
_LEASE_RECLAIM_ACTION_TYPE = "system.restart_recovery.leases"

DEFAULT_STUCK_OUTBOX_LEASE_SEC = 30
_STUCK_OUTBOX_RECOVERY_WORKER = "restart_recovery"
_REENTRY_REASON = "RESTART_RECOVERY_REENTRY"
_LEASE_EXPIRED_UNKNOWN_REASON = "RESTART_RECOVERY_LEASE_EXPIRED"


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


async def recover_stuck_outbox_commands(
    pool: asyncpg.Pool,
    *,
    order_repo: OrderRepoPort,
    outbox_repo: OutboxRepoPort,
    resolve_adapter: AdapterResolver,
    risk_gate_repo: RiskGateRepository,
    worker_id: str = _STUCK_OUTBOX_RECOVERY_WORKER,
    lease_sec: int = DEFAULT_STUCK_OUTBOX_LEASE_SEC,
    limit: int = 500,
    clock: Callable[[], datetime] = utcnow,
) -> int:
    """§6 F6 ①② — lease가 만료된 SENDING outbox 행을 §4.2/§4.4 상태기계에
    따라 재진입(PENDING) 또는 UNKNOWN 전환한다.

    CANCEL/MODIFY는 멱등 계열(`outbox_commands.py` 모듈 docstring)이라
    무조건 재진입해도 안전하다. SUBMIT은 어댑터 호출 여부가 불확실할 때만
    (`outbox_dispatcher.py`의 `SENT` tx가 커밋돼 주문이 이미 SUBMITTED인데
    그 뒤 finalize tx가 못 돈 경우) 재전송 금지 원칙(§5.4)에 따라 UNKNOWN
    으로 보낸다 — 주문이 아직 VALIDATED면(그 tx조차 커밋 안 됨) 어댑터
    호출 0회가 확정이라 재진입이 안전하다. UNKNOWN이 된 주문은 곧장
    `unknown_resolver.resolve_unknown`에 위임한다(재구현 금지, task-1604).
    """
    if risk_gate_repo is None:  # I-01 — 안전 게이트 인자는 None 기본값을 갖지 않는다
        raise TypeError(
            "risk_gate_repo는 필수입니다(I-01) — None을 명시적으로 넘길 수 없습니다."
        )
    async with pool.acquire() as conn, conn.transaction():
        rows = await outbox_repo.reclaim_stuck_sending(
            conn, worker_id=worker_id, limit=limit, lease_sec=lease_sec
        )
    writes = OutboxWrites(
        outbox_repo=outbox_repo, order_repo=order_repo, worker_id=worker_id, clock=clock
    )
    newly_unknown: list[OrderView] = []
    for row in rows:
        if row.command_type != "SUBMIT":
            async with pool.acquire() as conn, conn.transaction():
                await outbox_repo.mark_retry(
                    conn, row.id, attempt=row.attempt, not_before=clock(),
                    last_error=_REENTRY_REASON, expected_worker=worker_id,
                )
            continue
        order = await _recover_stuck_submit(pool, order_repo, outbox_repo, writes, row, clock)
        if order is not None:
            newly_unknown.append(order)
    for order in newly_unknown:
        adapter = await resolve_adapter(order.tenant_id, order.exchange)
        await resolve_unknown(
            order.order_id,
            adapter=adapter,
            pool=pool,
            risk_gate_repo=risk_gate_repo,
            order_repo=order_repo,
            clock=clock,
        )
    return len(rows)


async def _recover_stuck_submit(
    pool: asyncpg.Pool,
    order_repo: OrderRepoPort,
    outbox_repo: OutboxRepoPort,
    writes: OutboxWrites,
    row: OutboxRow,
    clock: Callable[[], datetime],
) -> OrderView | None:
    async with pool.acquire() as conn, conn.transaction():
        order = await order_repo.get_for_update(conn, row.order_id)
        if order.status not in (OrderStatus.VALIDATED, OrderStatus.SUBMITTED):
            await writes.done(conn, row)  # 이미 다른 경로(inbox 등)로 확정 — 명령 소진
            return None
        if order.status is OrderStatus.VALIDATED:
            await outbox_repo.mark_retry(
                conn, row.id, attempt=row.attempt, not_before=clock(),
                last_error=_REENTRY_REASON, expected_worker=writes.worker_id,
            )
            return None
        await writes.done(conn, row)  # 펜스 먼저(§5.1) — 재전송 금지
        return await writes.transition(
            conn, row, order, OrderStatus.UNKNOWN, OrderEvent.RESPONSE_LOST,
            _LEASE_EXPIRED_UNKNOWN_REASON, {"unknown_since": clock()},
        )
