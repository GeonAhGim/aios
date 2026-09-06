"""task-1718 P0-E — c9f4e2a1b6d7이 foundation 8 테이블에 건 `FORCE ROW LEVEL
SECURITY`가 실제로 켜져 있는지 확인한다.

`pg_class.relforcerowsecurity`는 PostgreSQL이 그 테이블에 FORCE 플래그가
서 있는지를 직접 보여주는 카탈로그 컬럼이다 — 이 테스트는 그 플래그의
존재만 확인한다. 이 환경의 DATABASE_URL 롤이 슈퍼유저(rolbypassrls=true)라
FORCE가 서 있어도 이 세션 자체의 쿼리 결과는 전혀 달라지지 않는다(PG는
슈퍼유저에 행 보안을 아예 적용하지 않는다, FORCE 여부와 무관) — 그래서
"FORCE가 실제로 교차 테넌트 접근을 막는다"는 동작 증명은 여기서 하지 않는다
(운영 DSN이 비슈퍼유저로 전환된 뒤에야 의미가 생긴다, task note 참조).
"""

from __future__ import annotations

import asyncpg

_FOUNDATION_TABLES = (
    "consent_record",
    "account_connection",
    "risk_evaluation",
    "paper_deployment",
    "reconciliation_run",
    "valuation_snapshot",
    "portfolio_mandate",
    "foundation_audit_event",
)


async def test_foundation_tables_have_force_row_level_security(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT relname, relforcerowsecurity FROM pg_class "
            "WHERE relname = ANY($1::text[])",
            list(_FOUNDATION_TABLES),
        )
    flags = {row["relname"]: row["relforcerowsecurity"] for row in rows}
    assert set(flags) == set(_FOUNDATION_TABLES)
    assert all(flags.values()), flags
