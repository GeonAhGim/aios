"""L4-14 outbox 디스패처 테스트용 포트 대역(메모리) — §5.1 SQL 의미론의 모델.

Postgres 어댑터(L4-08)와 `order_command_outbox`/`order_events` 스키마(L4-06)가
아직 없어(task-1538 note) 디스패처는 포트(`OutboxRepoPort`/`OrderRepoPort`)에
대해서만 증명한다. 이 대역이 지키는 계약:

- `claim_batch`는 await 없이 한 번에 실행된다 — asyncio 단일 스레드에서 `FOR
  UPDATE SKIP LOCKED`와 같은 원자성(두 워커가 같은 행을 받을 수 없음).
- `mark_done/retry/dead`는 `state='SENDING' AND worker_id=expected_worker`가
  아니면 `ConcurrencyConflictError`(105번 §2 RETURNING 0행).
- `transition`은 expected_status·expected_version 불일치 시 같은 예외, 전이표
  (`ALLOWED`) 밖이면 `InvalidOrderTransitionError`, version +1, 이벤트 1행.
- `FakeConn.transaction()`은 예외 시 그 트랜잭션 안의 쓰기를 전부 되돌린다 —
  "펜스 먼저, 전이 나중"이 같은 tx라는 디스패처 불변을 검증할 수 있다.

실DB 변형(3워커 SKIP LOCKED·늦은 쓰기 RETURNING 0행)은 L4-06/08 이후
`tests/integration/oms/`에 같은 케이스로 추가한다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from src.core.db.conditional_write import ConcurrencyConflictError
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.ports.repository import CommandType, OutboxRow

Undo = Callable[[], None]

# `transition()` patch가 실제 쓰는 `orders` 컬럼 전체 — `OrderView.model_fields`
# 만으로는 부족하다: `sent_at`은 outbox_dispatcher._send_submit()이 SENT 전이에
# 쓰는 실컬럼(073beca589d5 이후 c1f4a9e7b3d6, task-1567)인데 OrderView는 노출하지
# 않는다(스펙 §2-C 79번 행에 없음). 예전엔 model_fields에 없는 키를 조용히
# 버려(dict comprehension 필터) 이 스키마 갭을 이 대역이 숨겼다(task-1567 note) —
# 지금은 목록 밖 키는 오타/미지 컬럼으로 간주해 fail-closed(예외)한다.
_PATCHABLE_COLUMNS = frozenset(OrderView.model_fields) | {"sent_at"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FixedClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 9, 6, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


# ---- 커넥션/트랜잭션 대역 -------------------------------------------------------
class FakeTransaction:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeTransaction:
        self._conn.frames.append([])
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        frame = self._conn.frames.pop()
        if exc_type is not None:
            for undo in reversed(frame):
                undo()
        elif self._conn.frames:
            self._conn.frames[-1].extend(frame)
        return False


class FakeConn:
    def __init__(self) -> None:
        self.frames: list[list[Undo]] = []

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    def on_rollback(self, undo: Undo) -> None:
        if self.frames:
            self.frames[-1].append(undo)


class _Acquire:
    def __init__(self) -> None:
        self._conn = FakeConn()

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakePool:
    def acquire(self) -> _Acquire:
        return _Acquire()


# ---- outbox 대역 ------------------------------------------------------------------
class InMemoryOutboxRepo:
    def __init__(self, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.rows: dict[UUID, OutboxRow] = {}
        self._clock = clock
        self.claim_calls = 0

    async def enqueue(
        self,
        conn: FakeConn,
        *,
        order_id: UUID,
        command_type: CommandType,
        payload: dict[str, Any],
        not_before: datetime,
    ) -> UUID:
        row_id = uuid4()
        now = self._clock()
        self._put(
            conn,
            OutboxRow(
                id=row_id,
                order_id=order_id,
                command_type=command_type,
                payload=payload,
                state="PENDING",
                attempt=0,
                not_before=not_before,
                lease_until=None,
                worker_id=None,
                last_error=None,
                created_at=now,
                updated_at=now,
            ),
        )
        return row_id

    async def claim_batch(
        self, conn: FakeConn, *, worker_id: str, limit: int, lease_sec: int
    ) -> list[OutboxRow]:
        # await 없음 — SKIP LOCKED 원자성 모델(모듈 docstring).
        self.claim_calls += 1
        now = self._clock()
        candidates = sorted(
            (r for r in self.rows.values() if r.state == "PENDING" and r.not_before <= now),
            key=lambda r: r.created_at,
        )[:limit]
        claimed: list[OutboxRow] = []
        for row in candidates:
            new = row.model_copy(
                update={
                    "state": "SENDING",
                    "worker_id": worker_id,
                    "lease_until": now + timedelta(seconds=lease_sec),
                    "updated_at": now,
                }
            )
            self._put(conn, new)
            claimed.append(new)
        return claimed

    async def reclaim_stuck_sending(
        self, conn: FakeConn, *, worker_id: str, limit: int, lease_sec: int
    ) -> list[OutboxRow]:
        now = self._clock()
        candidates = sorted(
            (
                r
                for r in self.rows.values()
                if r.state == "SENDING" and r.lease_until is not None and r.lease_until < now
            ),
            key=lambda r: r.created_at,
        )[:limit]
        claimed: list[OutboxRow] = []
        for row in candidates:
            new = row.model_copy(
                update={
                    "worker_id": worker_id,
                    "lease_until": now + timedelta(seconds=lease_sec),
                    "updated_at": now,
                }
            )
            self._put(conn, new)
            claimed.append(new)
        return claimed

    async def mark_done(self, conn: FakeConn, id: UUID, *, expected_worker: str) -> None:
        row = self._fenced(id, expected_worker)
        self._put(conn, row.model_copy(update={"state": "DONE", "updated_at": self._clock()}))

    async def mark_retry(
        self,
        conn: FakeConn,
        id: UUID,
        *,
        attempt: int,
        not_before: datetime,
        last_error: str,
        expected_worker: str,
    ) -> None:
        row = self._fenced(id, expected_worker)
        self._put(
            conn,
            row.model_copy(
                update={
                    "state": "PENDING",
                    "attempt": attempt,
                    "not_before": not_before,
                    "last_error": last_error,
                    "worker_id": None,
                    "lease_until": None,
                    "updated_at": self._clock(),
                }
            ),
        )

    async def mark_dead(
        self, conn: FakeConn, id: UUID, *, reason: str, expected_worker: str
    ) -> None:
        row = self._fenced(id, expected_worker)
        self._put(
            conn,
            row.model_copy(
                update={"state": "DEAD", "last_error": reason, "updated_at": self._clock()}
            ),
        )

    def force(self, id: UUID, **update: Any) -> None:
        """테스트 전용 — 복구 워커/다른 프로세스의 쓰기를 흉내 낸다."""
        self.rows[id] = self.rows[id].model_copy(update=update)

    def _fenced(self, id: UUID, expected_worker: str) -> OutboxRow:
        row = self.rows.get(id)
        if row is None or row.state != "SENDING" or row.worker_id != expected_worker:
            raise ConcurrencyConflictError(
                f"outbox {id}: state={row.state if row else None} "
                f"worker={row.worker_id if row else None} expected={expected_worker}"
            )
        return row

    def _put(self, conn: FakeConn, row: OutboxRow) -> None:
        prev = self.rows.get(row.id)

        def undo(prev: OutboxRow | None = prev, row_id: UUID = row.id) -> None:
            if prev is None:
                self.rows.pop(row_id, None)
            else:
                self.rows[row_id] = prev

        self.rows[row.id] = row
        conn.on_rollback(undo)


# Re-export order/adapter fakes for backward compatibility
from tests.support.oms_outbox_fakes_orders import (  # noqa: F401, E402
    InMemoryOrderRepo,
    ScriptedAdapter,
    allow_gate,
    deny_gate,
    enqueue,
    gate_param_has_no_default,
    make_dispatcher,
    make_order_view,
    make_venue_order,
    submit_payload,
)
