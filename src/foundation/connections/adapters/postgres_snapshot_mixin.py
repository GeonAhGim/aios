"""ConnectionRepository의 asyncpg 구현 — 스냅샷/헬스 부분.

Spec: AIOSproject 74번 §2/§5, 105번(동시성 표준).

task-1723 P1-D: postgres_repository.py(330줄, P6 300줄 초과)에서 스냅샷/헬스
관련 메서드를 믹스인으로 분리한 파일(순수 이동, KISWebSocketMixin과 동일한
분리 관례). `persist_snapshot_if_syncable()`이 CON-004(동시 revoke와 sync
경합)의 실제 방어 지점이다 — "connection이 여전히
ACTIVE_READONLY/DEGRADED인가" 재확인과 snapshot/health 저장을 한 트랜잭션 +
row lock(`SELECT ... FOR UPDATE`)으로 묶는다. 처음엔 `get_connection()`으로
먼저 읽고 나중에 별도 호출로 `insert_snapshot()`하는 두 단계였는데, 그 두
왕복 사이에 revoke가 커밋될 수 있는 진짜 TOCTOU 틈이 있었다(리뷰 중 발견,
2026-09-02) — 재확인과 쓰기가 같은 트랜잭션에 있어야만 그 틈이 없어진다는
걸 확인하고 이 메서드로 합쳤다.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.connections.domain.models import (
    AccountSnapshot,
    ConnectionHealth,
    ConnectionState,
    HealthState,
    SnapshotValue,
)


def _row_to_snapshot(
    row: asyncpg.Record, values: tuple[SnapshotValue, ...] = ()
) -> AccountSnapshot:
    return AccountSnapshot(
        id=row["id"],
        connection_id=row["connection_id"],
        captured_at=row["captured_at"],
        provider_as_of=row["provider_as_of"],
        freshness=row["freshness"],
        currency=row["currency"],
        source_evidence_ref=row["source_evidence_ref"],
        values=values,
    )


def _row_to_health(row: asyncpg.Record) -> ConnectionHealth:
    return ConnectionHealth(
        connection_id=row["connection_id"],
        evaluated_at=row["evaluated_at"],
        state=HealthState(row["state"]),
        error_code=row["error_code"],
        retry_after=row["retry_after"],
        provider_trace_ref=row["provider_trace_ref"],
    )


class _SnapshotHealthMixin:
    """`PostgresConnectionRepository`가 상속한다 — `self._pool`을 기대한다."""

    _pool: asyncpg.Pool

    async def persist_snapshot_if_syncable(
        self,
        connection_id: UUID,
        snapshot: AccountSnapshot,
        health: ConnectionHealth,
    ) -> AccountSnapshot:
        async with self._pool.acquire() as conn, conn.transaction():
            # CON-004 진짜 방어 지점 — 이 SELECT가 행을 잠가서, 이 트랜잭션이
            # 커밋될 때까지 같은 connection에 대한 revoke_connection()의
            # transition_connection_state() UPDATE는 블록된다(같은 행을 대상으로
            # 하므로). 재확인과 쓰기 사이에 별도 왕복이 없어 TOCTOU 틈이 없다.
            row = await conn.fetchrow(
                "SELECT state FROM account_connection WHERE id = $1 FOR UPDATE", connection_id
            )
            if row is None or row["state"] in (
                ConnectionState.REVOKED.value,
                ConnectionState.DISCONNECTED.value,
            ):
                raise ConcurrencyConflictError(
                    f"account_connection.id={connection_id}: sync 도중 연결이 "
                    "종료됐습니다(동시 처리 충돌) — 스냅샷을 저장하지 않습니다."
                )

            # CON-006 방어의 마지막 층 — application 계층의 classify_provider_
            # response()는 이 트랜잭션 밖에서 latest_snapshot을 조회하므로, 두
            # sync가 동시에 "이 provider_as_of는 처음 본다"고 판단하고 여기까지
            # 올 수 있다. UNIQUE(connection_id, provider_as_of, source_evidence_ref)
            # 위반을 에러로 올리지 않고 ON CONFLICT DO NOTHING + 기존 행 재조회로
            # 흡수한다 — "이미 그 정확한 스냅샷이 있다"는 실패가 아니라 중복
            # 응답의 정상적인 결과다.
            snapshot_row = await conn.fetchrow(
                "INSERT INTO account_snapshot (connection_id, provider_as_of, freshness, "
                " currency, source_evidence_ref) VALUES ($1, $2, $3, $4, $5) "
                "ON CONFLICT (connection_id, provider_as_of, source_evidence_ref) DO NOTHING "
                "RETURNING *",
                snapshot.connection_id,
                snapshot.provider_as_of,
                snapshot.freshness,
                snapshot.currency,
                snapshot.source_evidence_ref,
            )
            newly_inserted = snapshot_row is not None
            if snapshot_row is None:
                snapshot_row = await conn.fetchrow(
                    "SELECT * FROM account_snapshot WHERE connection_id = $1 "
                    "AND provider_as_of = $2 AND source_evidence_ref = $3",
                    snapshot.connection_id,
                    snapshot.provider_as_of,
                    snapshot.source_evidence_ref,
                )
            if newly_inserted:
                # 이 트랜잭션이 실제로 새로 만든 행일 때만 값을 쓴다 — 위
                # ON CONFLICT DO NOTHING으로 기존 행을 재조회한 경우(중복
                # 응답)는 그 값도 이미 저장돼 있다.
                for value in snapshot.values:
                    await conn.execute(
                        "INSERT INTO account_snapshot_value "
                        "(snapshot_id, entity_type, entity_key, value) "
                        "VALUES ($1, $2, $3, $4)",
                        snapshot_row["id"],
                        value.entity_type,
                        value.entity_key,
                        value.value,
                    )
            value_rows = await conn.fetch(
                "SELECT entity_type, entity_key, value FROM account_snapshot_value "
                "WHERE snapshot_id = $1",
                snapshot_row["id"],
            )
            await conn.execute(
                "INSERT INTO connection_health (connection_id, state, error_code, "
                " retry_after, provider_trace_ref) VALUES ($1, $2, $3, $4, $5)",
                health.connection_id,
                health.state.value,
                health.error_code,
                health.retry_after,
                health.provider_trace_ref,
            )
            if row["state"] == ConnectionState.DEGRADED.value:
                await conn.execute(
                    "UPDATE account_connection SET state = $2 WHERE id = $1",
                    connection_id,
                    ConnectionState.ACTIVE_READONLY.value,
                )
        values = tuple(
            SnapshotValue(
                entity_type=v["entity_type"], entity_key=v["entity_key"], value=v["value"]
            )
            for v in value_rows
        )
        return _row_to_snapshot(snapshot_row, values)

    async def get_latest_snapshot(self, connection_id: UUID) -> AccountSnapshot | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM account_snapshot WHERE connection_id = $1 "
                "ORDER BY captured_at DESC LIMIT 1",
                connection_id,
            )
            if row is None:
                return None
            value_rows = await conn.fetch(
                "SELECT entity_type, entity_key, value FROM account_snapshot_value "
                "WHERE snapshot_id = $1",
                row["id"],
            )
        values = tuple(
            SnapshotValue(
                entity_type=v["entity_type"], entity_key=v["entity_key"], value=v["value"]
            )
            for v in value_rows
        )
        return _row_to_snapshot(row, values)

    async def insert_health_record(self, health: ConnectionHealth) -> ConnectionHealth:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO connection_health (connection_id, state, error_code, "
                " retry_after, provider_trace_ref) VALUES ($1, $2, $3, $4, $5) RETURNING *",
                health.connection_id,
                health.state.value,
                health.error_code,
                health.retry_after,
                health.provider_trace_ref,
            )
        return _row_to_health(row)

    async def get_latest_health(self, connection_id: UUID) -> ConnectionHealth | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM connection_health WHERE connection_id = $1 "
                "ORDER BY evaluated_at DESC LIMIT 1",
                connection_id,
            )
        return _row_to_health(row) if row is not None else None
