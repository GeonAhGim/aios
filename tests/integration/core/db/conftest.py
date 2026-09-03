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

from pathlib import Path
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
        self._conn = conn
        self._tenant_id = tenant_id
        self._system = system
        self._tx = conn.transaction()

    async def __aenter__(self) -> asyncpg.Connection:
        await self._tx.start()
        await self._conn.execute("SET ROLE aios_app")
        await self._conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)",
            "" if self._tenant_id is None else str(self._tenant_id),
        )
        if self._system:
            await self._conn.execute("SELECT set_config('app.role', 'system', true)")
        return self._conn

    async def __aexit__(self, *exc_info: object) -> None:
        await self._tx.rollback()
