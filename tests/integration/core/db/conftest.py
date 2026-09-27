"""PLT-30 RLS 통합테스트 공용 픽스처.

`aios_app`은 로그인 비밀번호가 없어(src/db/roles.sql `LOGIN`만, PASSWORD
없음) 별도 자격증명으로 직접 접속할 수 없다 — 로컬/CI `TEST_DATABASE_URL`은
owner(=migrator) 계정 하나만 갖는다. 그래서
tests/adversarial/ledger/test_role_bypass.py·test_db_roles.py와 동일하게,
슈퍼유저 커넥션 안에서 `SET ROLE aios_app`을 트랜잭션 스코프로만 쓰고 매
케이스 끝에 항상 롤백한다 — PostgreSQL에서 `SET`(비-LOCAL)은 커밋되면
세션에 남으므로, 커밋 경로를 절대 타지 않게 해 role이 커넥션 풀 밖으로
새는 것을 원천 차단한다.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import asyncpg
import pytest
from dotenv import dotenv_values

_PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _asyncpg_dsn() -> str:
    env = dotenv_values(_PROJECT_ROOT / ".env")
    url = env.get("DATABASE_URL")
    assert url, ".env에 DATABASE_URL이 없습니다"
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


class AppRoleTx:
    """`SET ROLE aios_app` + `app.tenant_id`/`app.role` GUC를 트랜잭션
    스코프로 묶고, 성공·실패 무관하게 항상 롤백한다."""

    def __init__(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: UUID | None = None,
        system: bool = False,
    ) -> None:
        if tenant_id is not None and not isinstance(tenant_id, UUID):
            raise TypeError("tenant_id must be UUID or None")
        if not isinstance(system, bool):
            raise TypeError("system must be bool")
        self._conn = conn
        self._tenant_id = tenant_id
        self._system = system
        self._tx = conn.transaction()

    async def __aenter__(self) -> asyncpg.Connection:
        await self._tx.start()
        try:
            await self._conn.execute("SET ROLE aios_app")
            await self._conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                "" if self._tenant_id is None else str(self._tenant_id),
            )
            if self._system:
                await self._conn.execute("SELECT set_config('app.role', 'system', true)")
        except BaseException:
            # __aexit__ is not called when entry fails, including cancellation.
            await self._tx.rollback()
            raise
        return self._conn

    async def __aexit__(self, *exc_info: object) -> None:
        await self._tx.rollback()


class TestAppRoleTx:
    """task-8441: fixture isolation and fail-closed regression tests (PLT-30)."""

    @staticmethod
    def connection():
        tx = Mock(start=AsyncMock(), rollback=AsyncMock(), commit=AsyncMock())
        return Mock(transaction=Mock(return_value=tx), execute=AsyncMock()), tx

    @pytest.mark.parametrize("tenant_id", ["not-a-uuid", "'; SET ROLE postgres; --", 42])
    def test_negative_invalid_tenant_rejected_before_transaction(self, tenant_id):
        conn, _ = self.connection()
        with pytest.raises(TypeError, match="tenant_id"):
            AppRoleTx(conn, tenant_id=tenant_id)
        conn.transaction.assert_not_called()
        conn.execute.assert_not_called()

    @pytest.mark.parametrize("system", ["false", 1, None])
    def test_negative_non_boolean_system_rejected(self, system):
        conn, _ = self.connection()
        with pytest.raises(TypeError, match="system"):
            AppRoleTx(conn, system=system)
        conn.transaction.assert_not_called()

    @pytest.mark.parametrize("step", [0, 1, 2])
    @pytest.mark.parametrize("cancelled", [False, True])
    async def test_failure_injection_setup_rolls_back(self, monkeypatch, step, cancelled):
        conn, tx = self.connection()
        error = asyncio.CancelledError() if cancelled else RuntimeError("injected setup failure")
        execute = AsyncMock(side_effect=[None] * step + [error])
        monkeypatch.setattr(conn, "execute", execute)
        entered = False
        with pytest.raises(type(error)) as caught:
            async with AppRoleTx(conn, system=True):
                entered = True
        assert caught.value is error
        assert not entered
        assert execute.await_count == step + 1
        tx.start.assert_awaited_once_with()
        tx.rollback.assert_awaited_once_with()
        tx.commit.assert_not_called()

    @pytest.mark.parametrize("fail_body", [False, True])
    async def test_exit_always_rolls_back(self, fail_body):
        conn, tx = self.connection()
        tenant = UUID("00000000-0000-0000-0000-000000000001")
        error = RuntimeError("injected body failure")
        try:
            async with AppRoleTx(conn, tenant_id=tenant) as active:
                assert active is conn
                if fail_body:
                    raise error
        except RuntimeError as caught:
            assert fail_body and caught is error
        conn.execute.assert_any_await("SET ROLE aios_app")
        conn.execute.assert_any_await("SELECT set_config('app.tenant_id', $1, true)", str(tenant))
        tx.rollback.assert_awaited_once_with()
        tx.commit.assert_not_called()

    async def test_missing_tenant_binds_empty_scope(self):
        conn, tx = self.connection()
        async with AppRoleTx(conn):
            pass
        conn.execute.assert_any_await("SELECT set_config('app.tenant_id', $1, true)", "")
        assert conn.execute.await_count == 2
        tx.rollback.assert_awaited_once_with()
