"""L4-14 — outbox 디스패처: SENDING 선점 → 거래소 호출 → 전이.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C, §4 I8, §5.1, §5.3,
§5.4, §6 F1~F4/F13/F14, §9 L4-14.

행당 트랜잭션 3개(§5.3 거래소 호출은 tx 밖): tx1 `claim_batch`(SKIP LOCKED →
SENDING, I8 유일 호출 지점) → tx2 주문 잠금 + 전송 게이트(kill switch 우회 금지)
+ `VALIDATED→SUBMITTED`(SENT, 어댑터 호출 직전) → 어댑터 호출 → tx3 outbox 펜스
(`expected_worker`) **먼저**, 주문 전이(`expected_version`) 나중 — 같은 tx라 리스를
잃은 워커의 늦은 쓰기는 펜스의 `ConcurrencyConflictError`로 막히고 전이도 함께
롤백된다.

포트 계약(L4-08): `mark_done/retry/dead`는 RETURNING 0행이면
`ConcurrencyConflictError`(core/db/conditional_write); `transition`은
expected_status/version 불일치 시 동일. payload 계약(L4-09/17): SUBMIT
`{"order": Order.model_dump(mode="json"), "trace_id"?, "command_id"?}`, CANCEL
없음(`orders.exchange_order_id`), MODIFY `{"changes": {...}}`(`modify_order(**changes)`).

재시도(§5.4): 재클레임된 SUBMITTED 주문은 `find_order_by_client_id` 역조회 먼저 —
있으면 채택, 없으면 재전송, 미지원 venue면 UNKNOWN. `last_error`가 `NOT_SENT:`
(회로 OPEN·역조회 단계 실패)면 거래소에 닿지 않았음이 확정이라 역조회 생략.

전송 전/후 DEAD 구분(CA 2026-09-06, §4.2 `SEND_ABANDONED` 신설로 명세 정합):
outbox DEAD 시점에 주문이 아직 `VALIDATED`면(어댑터 호출 0회 확정) `SEND_ABANDONED`
전이로 `FAILED` 확정. 이미 `SUBMITTED`면(재클레임 중 과거 시도가 어댑터를 불렀을
가능성) 확정할 수 없으니 `UNKNOWN`(resolver 대상)으로 남긴다 — 자금이 움직였을
수 있는 주문을 FAILED로 성급히 닫지 않는다(§5.4). `EXCH_AUTH`는 §3.4 REJECTED
(reason=AUTH) 그대로 — 어댑터 호출 후(이미 SUBMITTED)라 `_finalize_submit`의
`VENUE_REJECTED` 경로를 쓴다(`dispatch_outcome._VENUE_REJECT_KINDS` 참조).
"""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.trading import Order, OrderStatus
from src.exchanges.common.adapter import ExchangeAdapter, UnsupportedCapabilityError
from src.exchanges.common.http_policy import RetryPolicy
from src.services.oms.application.dispatch_outcome import (
    OutcomeKind,
    SendOutcome,
    classify_lookup_failure,
    classify_submit_failure,
    classify_submit_response,
)
from src.services.oms.application.outbox_commands import (
    AdapterResolver,
    CommandCounters,
    send_cancel,
    send_modify,
)
from src.services.oms.application.outbox_writes import (
    DEFAULT_MAX_ATTEMPTS,
    NOT_SENT_PREFIX,
    OUTBOX_RETRY_POLICY,
    OutboxWrites,
    adopt,
    order_from_payload,
    utcnow,
)
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.domain.state_machine import OrderEvent
from src.services.oms.ports.repository import OrderRepoPort, OutboxRepoPort, OutboxRow
from src.services.order_service.gate import GateOutcome, OrderContext, PreSubmitGate

logger = logging.getLogger(__name__)

DEFAULT_LEASE_SEC = 30
DEFAULT_POLL_INTERVAL_SEC = 0.1  # §7.1 outbox 지연 p99 ≤ 200 ms


@dataclass
class DispatchReport(CommandCounters):
    claimed: int = 0
    rejected: int = 0
    unknown: int = 0
    gate_denied: int = 0
    conflicts: int = 0  # 펜스/버전 불일치로 거부된 늦은 쓰기
    errors: int = 0


class OutboxDispatcher:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        outbox_repo: OutboxRepoPort,
        order_repo: OrderRepoPort,
        resolve_adapter: AdapterResolver,
        pre_send_gate: PreSubmitGate,  # I-01 — 기본값 없음, None 불허
        worker_id: str,
        lease_sec: int = DEFAULT_LEASE_SEC,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_policy: RetryPolicy = OUTBOX_RETRY_POLICY,
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
        clock: Callable[[], datetime] = utcnow,
        rng: Callable[[], float] = random.random,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if pre_send_gate is None:  # 정적 검사 우회(런타임 None 주입) 방어, I-01
            raise ValueError("pre_send_gate는 필수입니다(I-01)")
        self._pool = pool
        self._outbox = outbox_repo
        self._orders = order_repo
        self._resolve_adapter = resolve_adapter
        self._gate = pre_send_gate
        self._worker_id = worker_id
        self._lease_sec = lease_sec
        self._poll_interval_sec = poll_interval_sec
        self._sleep = sleep
        self._writes = OutboxWrites(
            outbox_repo=outbox_repo,
            order_repo=order_repo,
            worker_id=worker_id,
            max_attempts=max_attempts,
            retry_policy=retry_policy,
            clock=clock,
            rng=rng,
        )

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def dispatch_once(self, *, limit: int = 50) -> DispatchReport:
        report = DispatchReport()
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await self._outbox.claim_batch(
                conn, worker_id=self._worker_id, limit=limit, lease_sec=self._lease_sec
            )
        report.claimed = len(rows)
        send = {
            "SUBMIT": self._send_submit, "CANCEL": self._send_cancel, "MODIFY": self._send_modify
        }
        for row in rows:
            try:
                await send[row.command_type](row, report)
            except ConcurrencyConflictError:
                # 리스를 잃은 뒤의 늦은 쓰기 — 다른 워커/복구가 이미 처리했다.
                report.conflicts += 1
                logger.warning("outbox_dispatcher: 늦은 쓰기 거부 outbox_id=%s worker=%s",
                               row.id, self._worker_id)
            except Exception:
                # 행은 SENDING+lease로 남는다 → lease 만료 시 복구(§6 F2)가 UNKNOWN 처리.
                report.errors += 1
                logger.exception("outbox_dispatcher: outbox_id=%s 처리 실패", row.id)
        return report

    async def run_forever(self) -> None:
        """백그라운드 태스크 본체(조립은 wiring.py). 한 주기의 실패가 루프를
        죽이지 않는다. 밀린 행이 있으면 즉시 재폴링(§7.1)."""
        while True:
            claimed = 0
            try:
                claimed = (await self.dispatch_once()).claimed
            except Exception:
                logger.exception("outbox_dispatcher: 이번 주기 실패 — 다음 주기에 재시도")
            await self._sleep(0.0 if claimed else self._poll_interval_sec)

    async def _send_submit(self, row: OutboxRow, report: DispatchReport) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            order = await self._orders.get_for_update(conn, row.order_id)
            if order.status not in (OrderStatus.VALIDATED, OrderStatus.SUBMITTED):
                await self._writes.done(conn, row)  # 복구/inbox가 이미 진행 — 명령 소진
                report.completed += 1
                return
            venue_order = order_from_payload(row, order)
            if venue_order is None:
                await self._writes.dead(conn, row, "PAYLOAD_INVALID")
                await self._abandon_pre_send(conn, row, order, "PAYLOAD_INVALID")
                report.dead += 1
                return
            if not await self._gate_allows(order):
                await self._writes.dead(conn, row, "SEND_GATE_DENIED")
                await self._abandon_pre_send(conn, row, order, "SEND_GATE_DENIED")
                report.gate_denied += 1
                return
            verify_first = order.status is OrderStatus.SUBMITTED and not (
                row.last_error or ""
            ).startswith(NOT_SENT_PREFIX)
            if order.status is OrderStatus.VALIDATED:
                order = await self._writes.transition(
                    conn, row, order, OrderStatus.SUBMITTED, OrderEvent.SENT, None,
                    {"sent_at": self._writes.clock()},
                )
        adapter = await self._resolve_adapter(order.tenant_id, order.exchange)
        outcome = await self._call_submit(adapter, venue_order, verify_first)
        async with self._pool.acquire() as conn, conn.transaction():
            await self._finalize_submit(conn, row, order, outcome, report)

    async def _abandon_pre_send(
        self, conn: asyncpg.Connection, row: OutboxRow, order: OrderView, reason: str
    ) -> None:
        """outbox DEAD(펜스는 호출부가 먼저 씀) 뒤 주문 쪽 확정 — CA 2026-09-06.
        `VALIDATED`는 어댑터 호출 0회가 확정이라 `SEND_ABANDONED`로 `FAILED`.
        `SUBMITTED`는 재클레임 중 과거 시도가 어댑터를 이미 불렀을 수 있어
        확정 불가 → `UNKNOWN`(resolver 대상)."""
        if order.status is OrderStatus.VALIDATED:
            await self._writes.transition(
                conn, row, order, OrderStatus.FAILED, OrderEvent.SEND_ABANDONED, reason, {},
            )
        else:
            await self._writes.transition(
                conn, row, order, OrderStatus.UNKNOWN, OrderEvent.RESPONSE_LOST, reason,
                {"unknown_since": self._writes.clock()},
            )

    async def _gate_allows(self, order: OrderView) -> bool:
        decision = await self._gate(
            OrderContext(
                user_id=order.tenant_id,
                execution_id=order.execution_id,
                exchange=order.exchange,
                mandate_revision_id=None,
            )
        )
        if decision.outcome is not GateOutcome.ALLOW:
            logger.warning("outbox_dispatcher: 전송 게이트 거부 order_id=%s reasons=%s",
                           order.order_id, decision.reason_codes)
            return False
        return True

    async def _call_submit(
        self, adapter: ExchangeAdapter, venue_order: Order, verify_first: bool
    ) -> SendOutcome:
        if verify_first:  # §5.4 재시도 전 반드시 역조회
            try:
                existing = await adapter.find_order_by_client_id(venue_order.client_order_id)
            except UnsupportedCapabilityError:
                return SendOutcome(OutcomeKind.UNKNOWN, "RESEND_UNVERIFIABLE")
            except Exception as exc:  # noqa: BLE001 — 분류는 dispatch_outcome 책임
                return classify_lookup_failure(exc)
            if existing is not None:
                return adopt(existing, "RESEND_ADOPTED")
        try:
            submitted = await adapter.place_order(venue_order)
        except Exception as exc:  # noqa: BLE001 — 분류는 dispatch_outcome 책임
            outcome = classify_submit_failure(exc)
            if outcome.kind is not OutcomeKind.ADOPT:
                return outcome
        else:
            return classify_submit_response(submitted)
        # §6 F14 — DUPLICATE_CLIENT_ID: 기존 주문 채택, 못 찾으면 UNKNOWN(resolver).
        try:
            existing = await adapter.find_order_by_client_id(venue_order.client_order_id)
        except Exception:  # noqa: BLE001
            return SendOutcome(OutcomeKind.UNKNOWN, "DUPLICATE_CLIENT_ID_LOOKUP_FAILED")
        if existing is None:
            return SendOutcome(OutcomeKind.UNKNOWN, "DUPLICATE_CLIENT_ID_NOT_FOUND")
        return adopt(existing, "DUPLICATE_ADOPTED")

    async def _finalize_submit(
        self,
        conn: asyncpg.Connection,
        row: OutboxRow,
        order: OrderView,
        outcome: SendOutcome,
        report: DispatchReport,
    ) -> None:
        """펜스(`done`/`dead`/`retry`) 먼저, 주문 전이는 그 뒤 — 같은 tx(§5.1)."""
        w = self._writes
        kind = outcome.kind
        if kind is OutcomeKind.ACK:
            await w.done(conn, row)
            await w.transition(
                conn, row, order, OrderStatus.ACKNOWLEDGED, OrderEvent.ACK, outcome.reason,
                {"exchange_order_id": outcome.exchange_order_id},
            )
            report.acknowledged += 1
        elif kind is OutcomeKind.REJECTED:
            await w.done(conn, row)
            await w.transition(
                conn, row, order, OrderStatus.REJECTED, OrderEvent.VENUE_REJECTED,
                outcome.reason, {},
            )
            report.rejected += 1
        elif kind is OutcomeKind.UNKNOWN:  # §6 F3 — DONE(재전송 금지) + UNKNOWN
            await w.done(conn, row)
            await w.transition(
                conn, row, order, OrderStatus.UNKNOWN, OrderEvent.RESPONSE_LOST,
                outcome.reason, {"unknown_since": w.clock()},
            )
            report.unknown += 1
        elif kind is OutcomeKind.DEFER:  # §6 F4/F13 — attempt 불변, not_before 연기
            await w.defer(conn, row, outcome)
            report.deferred += 1
        elif await w.retry_or_dead(conn, row, outcome):
            await w.transition(
                conn, row, order, OrderStatus.UNKNOWN, OrderEvent.RESPONSE_LOST,
                "OUTBOX_DEAD", {"unknown_since": w.clock()},
            )
            report.dead += 1
        else:
            report.retried += 1

    async def _send_cancel(self, row: OutboxRow, report: DispatchReport) -> None:
        await send_cancel(
            pool=self._pool, writes=self._writes, orders=self._orders,
            resolve_adapter=self._resolve_adapter, row=row, counters=report,
        )

    async def _send_modify(self, row: OutboxRow, report: DispatchReport) -> None:
        await send_modify(
            pool=self._pool, writes=self._writes, orders=self._orders,
            resolve_adapter=self._resolve_adapter, row=row, counters=report,
        )
