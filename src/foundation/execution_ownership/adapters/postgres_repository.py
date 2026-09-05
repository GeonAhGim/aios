"""ExecutionLeaseRepository의 asyncpg 구현.

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md
§3.2, §5.1, §7. `acquire_or_renew_many`는 §5.1 조건부 UPSERT SQL을
`UNNEST($1::bigint[])`로 배치 확장해 execution_id 여러 개를 **1회 왕복**
으로 처리한다(§7 "execution마다 개별 왕복하지 않는다"). asyncpg는 Python
`list[int]` → `bigint[]` 바인딩을 기본 지원하므로(§10 "타입 바인딩 확인
필요"였던 항목 — 이 구현으로 확인 완료) `executemany` 폴백은 필요하지
않았다. 배치 1왕복 단언은
`tests/integration/foundation/execution_ownership/test_postgres_lease_repository.py`
가 `conn.fetch` 호출 횟수를 세어 증명한다.

execution_ids에 같은 id가 두 번 들어오면 Postgres는 한 문장 안에서 같은
행을 두 번 갱신할 수 없어 `CardinalityViolationError`("ON CONFLICT DO
UPDATE command cannot affect row a second time")로 **배치 전체**를 실패
시킨다 — 그러면 그 주기의 모든 execution이 tick되지 못한다. 그래서
바인딩 전에 순서를 유지한 채 중복을 제거한다(QA task-1143에서 실DB로
재현). 존재하지 않는 execution_id(FK 위반)는 의도적으로 걸러내지 않는다
— §5.1 SQL을 그대로 쓰고, 호출자(EO-03 `list_candidates`)가
`strategy_executions`에서 읽은 id만 넘기는 것이 계약이다.
"""
from __future__ import annotations

import asyncpg


class PostgresExecutionLeaseRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def acquire_or_renew_many(
        self, execution_ids: list[int], *, owner_id: str, ttl_seconds: float
    ) -> set[int]:
        unique_ids = list(dict.fromkeys(execution_ids))
        if not unique_ids:
            return set()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                INSERT INTO execution_leases
                    (execution_id, owner_id, fencing_token, heartbeat_at, expires_at)
                SELECT eid, $2, 0, now(), now() + $3 * interval '1 second'
                FROM UNNEST($1::bigint[]) AS eid
                ON CONFLICT (execution_id) DO UPDATE SET
                    owner_id = EXCLUDED.owner_id,
                    heartbeat_at = now(),
                    expires_at = EXCLUDED.expires_at,
                    fencing_token = CASE
                        WHEN execution_leases.owner_id = EXCLUDED.owner_id
                            THEN execution_leases.fencing_token
                        ELSE execution_leases.fencing_token + 1
                    END
                WHERE execution_leases.owner_id = EXCLUDED.owner_id
                   OR execution_leases.expires_at < now()
                RETURNING execution_id
                """,
                unique_ids,
                owner_id,
                ttl_seconds,
            )
        return {row["execution_id"] for row in rows}

    async def release_all(self, owner_id: str) -> int:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM execution_leases WHERE owner_id = $1", owner_id
            )
        return int(result.split()[-1])
