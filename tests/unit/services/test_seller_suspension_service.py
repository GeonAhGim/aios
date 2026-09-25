"""18.4 SellerSuspensionService 단위테스트 (task-4662) — 실DB 없이 커버리지 확보.

실DB 시나리오(정지된 판매자의 신규 리스팅 거부, ListingService(13.2) 연동)는
tests/integration/test_seller_suspension_service.py에 있다. 여기서는 fake
asyncpg pool/connection/transaction으로 존재하지 않는 사용자 거부, 감사로그
실패주입, guard 순서(존재 확인 -> 감사로그) 만 실DB 없이 고정한다.
"""

from __future__ import annotations

import time
from typing import Any, cast
from uuid import uuid4

import asyncpg
import pytest

import src.services.seller_suspension_service as target
from src.services.seller_suspension_service import (
    SellerSuspensionError,
    SellerSuspensionResult,
    SellerSuspensionService,
)


class _FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeConnection:
    def __init__(
        self,
        *,
        fetchrow_result: dict[str, Any] | None,
        raise_on_execute: Exception | None = None,
    ) -> None:
        self._fetchrow_result = fetchrow_result
        self._raise_on_execute = raise_on_execute
        self.fetchrow_calls: list[tuple[str, tuple[object, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    async def fetchrow(self, query: str, *args: object) -> dict[str, Any] | None:
        self.fetchrow_calls.append((query, args))
        return self._fetchrow_result

    async def execute(self, query: str, *args: object) -> None:
        self.execute_calls.append((query, args))
        if self._raise_on_execute is not None:
            raise self._raise_on_execute


class _FakeAcquire:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakePool:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


def _service(conn: _FakeConnection) -> SellerSuspensionService:
    return SellerSuspensionService(cast(asyncpg.Pool, _FakePool(conn)))


# ---------------------------------------------------------------------------
# negative tests (D2, >=3, 경계값/잘못된 입력 포함)
# ---------------------------------------------------------------------------


async def test_suspend_raises_for_nonexistent_user() -> None:
    conn = _FakeConnection(fetchrow_result=None)
    service = _service(conn)

    with pytest.raises(SellerSuspensionError):
        await service.suspend(uuid4(), uuid4(), "사유")

    assert conn.execute_calls == []


async def test_suspend_error_message_does_not_leak_user_id() -> None:
    """존재하지 않는 사용자 오류 메시지는 고정 문자열이어야 한다 — user_id를
    메시지에 그대로 노출하면 열거 공격(enumeration)에 쓰일 수 있다."""
    conn = _FakeConnection(fetchrow_result=None)
    service = _service(conn)
    target_user_id = uuid4()

    with pytest.raises(SellerSuspensionError) as exc_info:
        await service.suspend(target_user_id, uuid4(), "사유")

    assert str(target_user_id) not in str(exc_info.value)


async def test_suspend_with_empty_reason_boundary_still_records_audit() -> None:
    """reason의 빈 문자열 경계값 — 서비스는 사유 내용을 검증하지 않고 있는
    그대로 감사로그에 남긴다(정책문서 8.10, 사유는 내부 기록 전용)."""
    user_id = uuid4()
    conn = _FakeConnection(fetchrow_result={"seller_suspended": True})
    service = _service(conn)

    await service.suspend(user_id, uuid4(), "")

    assert len(conn.execute_calls) == 1
    _, audit_args = conn.execute_calls[0]
    assert audit_args[5] == f'{{"target_user_id": "{user_id}", "reason": ""}}'


async def test_resuspending_already_suspended_seller_is_idempotent() -> None:
    """이미 정지된 판매자 재정지 시도는 오류가 아니라 현재 상태를 그대로
    반환한다(모듈 docstring 명시 사양) — boundary 상태 전이 없음 확인."""
    user_id = uuid4()
    conn = _FakeConnection(fetchrow_result={"seller_suspended": True})
    service = _service(conn)

    result = await service.suspend(user_id, uuid4(), "재정지 시도")

    assert isinstance(result, SellerSuspensionResult)
    assert result.seller_suspended is True


# ---------------------------------------------------------------------------
# failure injection (D2)
# ---------------------------------------------------------------------------


async def test_suspend_propagates_audit_log_write_failure() -> None:
    """감사로그 기록(conn.execute)이 실패하면(DB 드롭 등) 삼키지 않고
    그대로 전파한다 — fail-closed 기본 태세(CLAUDE.md §3), 성공한 것처럼
    위장 금지."""
    user_id = uuid4()
    conn = _FakeConnection(
        fetchrow_result={"seller_suspended": True},
        raise_on_execute=ConnectionError("connection lost"),
    )
    service = _service(conn)

    with pytest.raises(ConnectionError):
        await service.suspend(user_id, uuid4(), "사유")


# ---------------------------------------------------------------------------
# gate-red repro (D2) — 존재 확인(row is None 가드)이 감사로그 기록보다
# 먼저 실행됨을 증명한다. 이 가드가 없다면(또는 순서가 뒤바뀌면) 존재하지
# 않는 사용자에 대해서도 audit_log에 "seller.suspended" 행이 남는다.
# ---------------------------------------------------------------------------


async def test_gate_red_repro_nonexistent_user_guard_blocks_audit_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_calls: list[dict[str, Any]] = []

    async def _spy_record_audit_log(conn: object, **kwargs: object) -> None:
        audit_calls.append(kwargs)

    monkeypatch.setattr(target, "record_audit_log", _spy_record_audit_log)

    # green: guard active — UPDATE ... RETURNING returns no row for a
    # nonexistent user, so suspend() raises before ever calling
    # record_audit_log.
    conn = _FakeConnection(fetchrow_result=None)
    service = _service(conn)
    with pytest.raises(SellerSuspensionError):
        await service.suspend(uuid4(), uuid4(), "사유")
    assert audit_calls == []

    # red repro: without the `if row is None: raise` guard, a truthy
    # fetchrow result (e.g. a stale/mocked row) would flow straight into
    # record_audit_log even though no real user was updated.
    conn2 = _FakeConnection(fetchrow_result={"seller_suspended": True})
    service2 = _service(conn2)
    await service2.suspend(uuid4(), uuid4(), "사유")
    assert len(audit_calls) == 1
    assert audit_calls[0]["action_type"] == "seller.suspended"


# ---------------------------------------------------------------------------
# perf assertion (D2)
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_suspend_perf_budget_p95_latency() -> None:
    """FD-18.4에 전용 예산이 없어 가장 가까운 공개 예산("order submit ->
    ACK p95 50ms, paper", ADR-2026-09-09-C Decision 1)과 동일 자릿수를
    기준으로 삼는다 — 이 경로도 단일 conditional UPDATE + 감사로그 1건으로
    구성된 fail-closed 트랜잭션이며, 여기서는 in-memory fake로 서빙된다."""
    samples = 50
    durations_ms: list[float] = []

    for _ in range(samples):
        conn = _FakeConnection(fetchrow_result={"seller_suspended": True})
        service = _service(conn)

        start = time.perf_counter()
        await service.suspend(uuid4(), uuid4(), "사유")
        durations_ms.append((time.perf_counter() - start) * 1000)

    durations_ms.sort()
    p95 = durations_ms[int(samples * 0.95) - 1]
    assert p95 < 50.0, (
        f"SellerSuspensionService.suspend p95 latency {p95:.3f}ms exceeded 50ms budget"
    )
