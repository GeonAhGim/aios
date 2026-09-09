"""Bulk-cancel open orders within a kill-switch scope.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.8, §5 (standard 105), §9 (R-39, preceded by R-38).
FA-16 (task-2406): docs/specs/ibor_fund_accounting_and_resilience.md#§9 —
every row that changes `orders.status` must carry a companion `order_events`
row in the same transaction (I-10). This module originally transitioned every
matching row in one `UPDATE ... RETURNING`, and that path never inserted
`order_events` at all — the defect task-2394's diagnosis caught (the cause of
the orders-projection mismatch in replay_verify). `073beca589d5`'s
`oms_enforce_order_transition_trg` runs `FOR EACH ROW` and consumes the
`oms.event_written` flag as soon as it checks it (the flag set by the first
row in a tx is already gone by the second row), so the real fix is not
"one flag for the whole batch" but "set the flag per row -> insert the event
-> conditional UPDATE for that row only". So candidate selection still uses
one read query (the §3.8 mapping isn't computed twice), but the actual
transition moved to per-order.

The §3.8 GLOBAL/PROVIDER/TENANT/ACCOUNT conditions reuse the parsing and
validation (scope_ref format, exception types) that R-38's
`legacy_execution_pauser._condition_for` already implements, since `orders`
and `strategy_executions` share the same column names (`exchange`,
`user_id`) — it is not reimplemented. Only STRATEGY_DEPLOYMENT targets a
different column (`orders.execution_id` is an FK pointing at
`strategy_executions.id`, not a PK itself).

TOCTOU/concurrency (unchanged after moving to per-order transitions): the
state can change between the candidate-selection query and the actual
transition, so each order re-locks only that one row and re-checks its
state with `SELECT ... FOR UPDATE SKIP LOCKED` — if it's no longer
cancelable by the time it's locked (another sweep already took it, or it
filled/was rejected meanwhile), it is silently skipped into `raced`. Once
locked with `FOR UPDATE`, the conditional UPDATE (standard 105, §3.8) should
always match within that lock window, so a `ConcurrencyConflictError` there
is not the race this function handles — it's a genuine invariant violation
and is propagated as-is. Re-invoking with the same control_id is naturally
idempotent because a row already transitioned to CANCEL_REQUESTED no longer
matches the candidate-selection query's `status IN (...)` (unchanged from
the original behavior).

An exception from the adapter `cancel_order` is only recorded as that
individual order's failure and does not block the rest of the sweep — since
reconcile owns the final truth of cancel success/failure, the DB state is
not rolled back here (it is left as CANCEL_REQUESTED so it stays eligible
for retry/lookup).

Known limitation (task-2406 DoD(e), accepted as a documented gap — closing
it is delegated to task-2432): `orders.status = 'CANCEL_REQUESTED'` is a
kill-switch-only string that is not a member of L4-06 `OrderStatus` (the
frozen contract of shared touchpoint 01, `src/data/models/trading.py`).
This value is required for DoD(c)'s idempotency (the re-selection query
only looks at `status IN ('SUBMITTED','PARTIALLY_FILLED')`, so a row
already at CANCEL_REQUESTED is not matched again) — without transitioning
to a value outside `_CANCELABLE_STATUSES`, re-running the same sweep would
try to cancel the same order again. So `order_events` cannot carry that
value either (`OrderTransitionEvent.to_status` is cast to `OrderStatus`,
and `scripts/replay_verify.py` selects every order this sweep touched via
`order_events` and reads it with `PostgresOrderEventRepository.timeline()`
— putting 'CANCEL_REQUESTED' there was measured to crash all of
replay_verify with a `ValueError`). So this event is left as a self-loop
(`from_status == to_status`, the same convention as OMS
`cancel_order.py`'s ACKNOWLEDGED/PARTIALLY_FILLED self-loop) — replay_verify
doesn't crash, but the order's `orders.status` (actually
'CANCEL_REQUESTED') and the orders projection's folded state (unchanged,
because it's a self-loop) still diverge, so that order is caught as a
mismatch in replay_verify's orders stream (not a crash). This mismatch
cannot be eliminated without a migration that adds a real
`CANCEL_REQUESTED` state to `OrderStatus`/073beca589d5's `_ALLOWED_PAIRS`
(DoD(f) forbids a new migration here) — DoD(e)'s "0 cases" cannot be met by
this leaf alone. Reproduction:
`test_sweep_open_orders_event_does_not_crash_replay_verify_timeline_read`
(proves no crash; the projection mismatch itself remains). The promotion
migration was split off into task-2432 (after the §C serialization-order
migration chain)."""
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
    """One order, one transaction: re-lock only this row with
    `FOR UPDATE SKIP LOCKED` (returns `None` if a race already made it
    uncancelable or another sweep is holding the lock), then perform
    `SET LOCAL` -> `order_events` INSERT -> conditional UPDATE in the §5.1
    order, all within the same transaction. The return value is
    `order_id, exchange, exchange_order_id`, needed for the adapter cancel
    call."""
    async with conn.transaction():
        locked = await conn.fetchrow(
            "SELECT status FROM orders "  # noqa: S608 — only constants are interpolated, values are parameters
            f"WHERE order_id = $1 AND status IN ({_CANCELABLE_STATUSES_SQL}) "
            "FOR UPDATE SKIP LOCKED",
            order_id,
        )
        if locked is None:
            return None
        from_status = locked["status"]

        # I6: set right before this UPDATE, consumed by the very next
        # order_events INSERT. Valid only within this transaction (= this
        # row), not the whole batch.
        #
        # order_events.to_status carries `from_status` as-is, not `_TO_STATUS`
        # ('CANCEL_REQUESTED', the orders.status literal) — a self-loop.
        # Reason (see "known gap" at the bottom of the module docstring):
        # `_TO_STATUS` is a kill-switch-only string absent from the frozen
        # L4-06 `OrderStatus` contract (shared touchpoint 01), so
        # `PostgresOrderEventRepository._row_to_event()`'s
        # `OrderStatus(row["to_status"])` raises when it meets that value —
        # putting it straight into `order_events.to_status` crashes the
        # moment `scripts/replay_verify.py` reads that row (any order this
        # sweep touches ends up in order_events). The self-loop follows the
        # same convention the real OMS `cancel_order.py` uses for its
        # ACKNOWLEDGED/PARTIALLY_FILLED self-loop CANCEL_REQUESTED event
        # (§3.3, "new events must not touch the orders.status frozen
        # contract") — the event column has no enum cast (raw string), so it
        # can keep 'CANCEL_REQUESTED' as-is.
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
    """Transitions only the `orders` rows within `scope`/`scope_ref` that are
    `SUBMITTED`/`PARTIALLY_FILLED` to `CANCEL_REQUESTED` (one transaction per
    order, each with a companion `order_events` row), then attempts an
    exchange adapter cancel for each order actually transitioned. An
    individual adapter failure is only recorded in `adapter_failed` and does
    not block the rest of the orders."""
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
