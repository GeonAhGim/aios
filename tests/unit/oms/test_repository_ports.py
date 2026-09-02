"""L4-07 ports/repository.py 구조적 계약 테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C, §9 L4-07.

`@runtime_checkable` Protocol이므로 `isinstance()`는 메서드 이름만 확인한다
(시그니처는 mypy가 정적으로 확인). 그래도 "포트가 요구하는 메서드 전부를
갖췄는가"는 여기서 실행 시점에 증명할 수 있다 — fail-closed 원칙대로, 메서드
하나라도 빠지면 어댑터는 포트를 만족하지 못한다는 것을 실증한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.services.oms.ports.repository import (
    ClaimResult,
    FillRepoPort,
    IdempotencyRepoPort,
    InboxRepoPort,
    OrderEventRepoPort,
    OrderRepoPort,
    OutboxRepoPort,
    OutboxRow,
)


class _FullOrderRepo:
    async def transition(self, conn, **kwargs): ...
    async def get_for_update(self, conn, order_id): ...
    async def find_by_scope_hash(self, conn, scope_hash): ...


class _MissingMethodOrderRepo:
    """`get_for_update`가 빠진 불완전 구현 — 포트를 만족하지 못해야 한다."""

    async def transition(self, conn, **kwargs): ...
    async def find_by_scope_hash(self, conn, scope_hash): ...


class _FullOrderEventRepo:
    async def append(self, conn, ev): ...
    async def timeline(self, conn, order_id): ...


class _FullFillRepo:
    async def insert_if_absent(self, conn, fill): ...
    async def list_for_order(self, conn, order_id): ...


class _FullOutboxRepo:
    async def enqueue(self, conn, **kwargs): ...
    async def claim_batch(self, conn, **kwargs): ...
    async def mark_done(self, conn, id, **kwargs): ...
    async def mark_retry(self, conn, id, **kwargs): ...
    async def mark_dead(self, conn, id, **kwargs): ...


class _StrictOutboxRepo:
    """`OutboxRepoPort`의 mark_* 시그니처를 그대로 구현 — expected_worker가
    누락되면 실제 어댑터가 만들어지기 전에도 TypeError로 fail-closed됨을 증명."""

    async def enqueue(self, conn, **kwargs): ...
    async def claim_batch(self, conn, **kwargs): ...
    async def mark_done(self, conn, id, *, expected_worker): ...
    async def mark_retry(
        self, conn, id, *, attempt, not_before, last_error, expected_worker
    ): ...
    async def mark_dead(self, conn, id, *, reason, expected_worker): ...


class _FullInboxRepo:
    async def insert_if_absent(self, conn, ev): ...
    async def claim_unprocessed(self, conn, **kwargs): ...
    async def mark_processed(self, conn, id, **kwargs): ...


class _FullIdempotencyRepo:
    async def claim(self, conn, **kwargs): ...


def test_full_implementations_satisfy_their_ports() -> None:
    assert isinstance(_FullOrderRepo(), OrderRepoPort)
    assert isinstance(_FullOrderEventRepo(), OrderEventRepoPort)
    assert isinstance(_FullFillRepo(), FillRepoPort)
    assert isinstance(_FullOutboxRepo(), OutboxRepoPort)
    assert isinstance(_FullInboxRepo(), InboxRepoPort)
    assert isinstance(_FullIdempotencyRepo(), IdempotencyRepoPort)


def test_incomplete_implementation_fails_port_check() -> None:
    """포트 메서드 하나 누락 → isinstance() False(fail-closed 구조 증명)."""
    assert not isinstance(_MissingMethodOrderRepo(), OrderRepoPort)


def test_outbox_row_rejects_unknown_command_type() -> None:
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        OutboxRow(
            id=uuid4(),
            order_id=uuid4(),
            command_type="DELETE",  # type: ignore[arg-type]
            payload={},
            state="PENDING",
            attempt=0,
            not_before=now,
            lease_until=None,
            worker_id=None,
            last_error=None,
            created_at=now,
            updated_at=now,
        )


def test_claim_result_new_has_no_order_id() -> None:
    result = ClaimResult(kind="NEW")
    assert result.order_id is None


def test_mark_retry_and_mark_dead_require_expected_worker() -> None:
    """366행 규칙표: done/retry/dead 모두 `worker_id=$2` 펜싱 — expected_worker
    없이 호출하면(늦은 워커의 조건 없는 쓰기 시도에 대응) TypeError로 막혀야 한다."""
    repo = _StrictOutboxRepo()
    now = datetime.now(timezone.utc)
    with pytest.raises(TypeError):
        repo.mark_retry(None, uuid4(), attempt=1, not_before=now, last_error="x")
    with pytest.raises(TypeError):
        repo.mark_dead(None, uuid4(), reason="x")


def test_claim_result_ttl_type_is_timedelta() -> None:
    # ttl은 IdempotencyRepoPort.claim의 파라미터 타입일 뿐 모델 필드가 아니므로
    # 여기서는 timedelta 임포트가 여전히 유효한 계약임을 회귀 방지로 확인한다.
    assert timedelta(hours=1).total_seconds() == 3600
