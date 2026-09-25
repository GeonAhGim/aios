"""18.3 UserAdminService 단위테스트 (task-4641) — 실DB 없이 커버리지 확보.

실DB 시나리오(SUSPENDED 사용자 로그인 거부 등 AuthService 연동)는
tests/integration/test_user_admin_service.py에 있다. 여기서는 fake
asyncpg pool/connection으로 분기 로직(DELETED/PENDING_DELETION 거부,
404, 실패주입)만 실DB 없이 고정한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

import asyncpg
import pytest

from src.services.user_admin_service import (
    UserAdminError,
    UserAdminNotFoundError,
    UserAdminService,
    UserStatusChangeResult,
    UserSummary,
)

ADMIN_ID = uuid4()


def _user_row(user_id: object) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "email": "user@example.com",
        "status": "ACTIVE",
        "created_at": datetime.now(timezone.utc),
    }


class _FakeConnection:
    def __init__(
        self,
        *,
        fetch_rows: list[dict[str, Any]] | None = None,
        fetchrow_result: dict[str, Any] | None = None,
        raise_on_execute: Exception | None = None,
    ) -> None:
        self._fetch_rows = fetch_rows or []
        self._fetchrow_result = fetchrow_result
        self._raise_on_execute = raise_on_execute
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[object, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        self.fetch_calls.append((query, args))
        return self._fetch_rows

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


def _service(conn: _FakeConnection) -> UserAdminService:
    return UserAdminService(cast(asyncpg.Pool, _FakePool(conn)))


async def test_list_users_without_search_orders_by_created_at() -> None:
    user_id = uuid4()
    row = _user_row(user_id)
    conn = _FakeConnection(fetch_rows=[row])
    service = _service(conn)

    results = await service.list_users()

    assert results == [UserSummary(**row)]
    query, args = conn.fetch_calls[0]
    assert "ORDER BY created_at DESC" in query
    assert args == ()


async def test_list_users_with_search_filters_by_email() -> None:
    user_id = uuid4()
    conn = _FakeConnection(fetch_rows=[_user_row(user_id)])
    service = _service(conn)

    results = await service.list_users(email_search="user")

    assert len(results) == 1
    query, args = conn.fetch_calls[0]
    assert "ILIKE" in query
    assert args == ("%user%",)


async def test_list_users_empty_result() -> None:
    conn = _FakeConnection(fetch_rows=[])
    service = _service(conn)

    results = await service.list_users()

    assert results == []


async def test_change_status_to_active_succeeds() -> None:
    user_id = uuid4()
    conn = _FakeConnection(fetchrow_result={"user_id": user_id})
    service = _service(conn)

    result = await service.change_status(user_id, "ACTIVE", admin_user_id=ADMIN_ID)

    assert isinstance(result, UserStatusChangeResult)
    assert result.user_id == user_id
    assert result.status == "ACTIVE"
    assert len(conn.execute_calls) == 1
    _, audit_args = conn.execute_calls[0]
    assert audit_args[1] == str(ADMIN_ID)
    assert audit_args[2] == "user.status_changed"


async def test_change_status_rejects_deleted() -> None:
    conn = _FakeConnection()
    service = _service(conn)

    with pytest.raises(UserAdminError):
        await service.change_status(uuid4(), "DELETED", admin_user_id=ADMIN_ID)

    assert conn.fetchrow_calls == []


async def test_change_status_rejects_pending_deletion() -> None:
    conn = _FakeConnection()
    service = _service(conn)

    with pytest.raises(UserAdminError):
        await service.change_status(uuid4(), "PENDING_DELETION", admin_user_id=ADMIN_ID)


async def test_change_status_rejects_unknown_status() -> None:
    conn = _FakeConnection()
    service = _service(conn)

    with pytest.raises(UserAdminError):
        await service.change_status(uuid4(), "BANNED", admin_user_id=ADMIN_ID)


async def test_change_status_raises_not_found_for_missing_user() -> None:
    conn = _FakeConnection(fetchrow_result=None)
    service = _service(conn)

    with pytest.raises(UserAdminNotFoundError):
        await service.change_status(uuid4(), "SUSPENDED", admin_user_id=ADMIN_ID)

    assert conn.execute_calls == []


async def test_change_status_propagates_audit_log_failure() -> None:
    """실패주입 — 감사로그 기록이 예외를 던지면 상태 변경 결과를 숨기지 않고
    그대로 전파해야 한다(성공한 것처럼 위장 금지)."""
    user_id = uuid4()
    conn = _FakeConnection(
        fetchrow_result={"user_id": user_id},
        raise_on_execute=ConnectionError("connection lost"),
    )
    service = _service(conn)

    with pytest.raises(ConnectionError):
        await service.change_status(user_id, "SUSPENDED", admin_user_id=ADMIN_ID)
