"""ReconciliationRepository의 asyncpg 구현.

Spec: AIOSproject 80번 §1/§2, 105번(동시성 표준).

upsert_state()는 매 reconciliation run의 "최신 계산 결과가 항상 이긴다"는
원칙이라 조건부(expected_revision) 없이 revision을 무조건 증가시킨다 —
반면 transition_state_status()(resolve 등 사람이 트리거하는 개별 전이)는
동시에 새 run이 상태를 이미 바꿨을 수 있어 105번 표준대로 revision을
조건으로 건다.

F1(task-9456) — `reconciliation_run`은 RLS ENABLE+FORCE(b3c7f19ad2e6/
c9f4e2a1b6d7)가 걸려 있는데, `get_run_by_input_hash`/`insert_run_with_items`가
`pool.acquire()`만 쓰고 `tenant_transaction()`(GUC 바인딩)을 거치지 않아 —
이 환경의 DATABASE_URL 롤은 슈퍼유저(rolbypassrls=true)라 지금은 드러나지
않지만 — 운영에서 non-superuser 롤(`aios_app`)로 전환되는 순간 정상
테넌트도 자기 run을 조회/삽입하지 못하게 된다. 두 메서드 모두 이미
tenant_id를 갖고 있어(`get_run_by_input_hash`는 REC-004/006 dedupe 호출자가,
`insert_run_with_items`는 `run.tenant_id`가) `connections/adapters/
postgres_repository.py`/`mandates/adapters/postgres_repository.py`의
`get_mandate` 선례와 동일하게 `tenant_transaction()`으로 옮긴다. 명시적
`tenant_id = $N` WHERE/컬럼 조건은 그대로 남긴다 — 슈퍼유저인 동안의 실제
방어선이고, `tenant_transaction()`은 롤 전환 이후의 두 번째 방어선이다.
`reconciliation_item`/`reconciliation_state`는 RLS가 걸려 있지 않아(선례
마이그레이션 docstring — 부모 FK로 간접 격리되는 자식 테이블/상태 테이블은
제외) 이 리프의 범위 밖이다."""

from __future__ import annotations

from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.db.tenant_scope import tenant_transaction
from src.foundation.reconciliation.domain.models import (
    Classification,
    ReconciliationItem,
    ReconciliationRun,
    ReconciliationRunAlreadyExists,
    ReconciliationState,
    RunState,
)


def _row_to_item(row: asyncpg.Record) -> ReconciliationItem:
    return ReconciliationItem(
        id=row["id"],
        run_id=row["run_id"],
        entity_type=row["entity_type"],
        entity_key=row["entity_key"],
        internal_value=row["internal_value"],
        provider_value=row["provider_value"],
        classification=Classification(row["classification"]),
        created_at=row["created_at"],
    )


def _row_to_state(row: asyncpg.Record) -> ReconciliationState:
    return ReconciliationState(
        target_ref=row["target_ref"],
        target_type=row["target_type"],
        tenant_id=row["tenant_id"],
        aggregate_status=Classification(row["aggregate_status"]),
        last_healthy_at=row["last_healthy_at"],
        last_checked_at=row["last_checked_at"],
        blocking_reason=row["blocking_reason"],
        revision=row["revision"],
        safety_control_id=row["safety_control_id"],
        resolved_by=row["resolved_by"],
        resolution_reason=row["resolution_reason"],
        resolved_at=row["resolved_at"],
    )


class PostgresReconciliationRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_run_by_input_hash(
        self, target_ref: UUID, input_hash: str, tenant_id: UUID
    ) -> ReconciliationRun | None:
        # F1(task-9456) — see module docstring. Explicit tenant_id stays in
        # the WHERE (real line of defense while DATABASE_URL is a superuser);
        # tenant_transaction() binds app.tenant_id for once the role is not.
        async with tenant_transaction(self._pool, tenant_id) as conn:
            run_row = await conn.fetchrow(
                "SELECT * FROM reconciliation_run "
                "WHERE target_ref = $1 AND input_hash = $2 AND tenant_id = $3",
                target_ref,
                input_hash,
                tenant_id,
            )
            if run_row is None:
                return None
            item_rows = await conn.fetch(
                "SELECT * FROM reconciliation_item WHERE run_id = $1", run_row["id"]
            )
        return ReconciliationRun(
            id=run_row["id"],
            tenant_id=run_row["tenant_id"],
            target_type=run_row["target_type"],
            target_ref=run_row["target_ref"],
            connection_id=run_row["connection_id"],
            input_hash=run_row["input_hash"],
            state=RunState(run_row["state"]),
            rule_version=run_row["rule_version"],
            items=tuple(_row_to_item(r) for r in item_rows),
            created_at=run_row["created_at"],
        )

    async def insert_run_with_items(
        self, run: ReconciliationRun, items: tuple[ReconciliationItem, ...]
    ) -> ReconciliationRun:
        # REC-004 dedupe relies on `UNIQUE(target_ref, input_hash)` (migration
        # f2b8e5d1a734 docstring: "the second insert attempt returns the
        # existing row") — the
        # caller's own `get_run_by_input_hash` pre-check only closes the common
        # case; two concurrent callers with the same input can both pass that
        # check before either commits (task-8955: this is exactly what
        # `test_concurrent_resync_is_serialized_by_position_lock` hit as a raw
        # `UniqueViolationError`). Raising `ReconciliationRunAlreadyExists` with
        # the row the other transaction already committed lets the caller
        # (`run_reconciliation`) reuse it instead of crashing or re-running the
        # winner's side effects a second time.
        try:
            async with tenant_transaction(self._pool, run.tenant_id) as conn:
                run_row = await conn.fetchrow(
                    "INSERT INTO reconciliation_run "
                    "(tenant_id, target_type, target_ref, connection_id, input_hash, state, "
                    " rule_version) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *",
                    run.tenant_id,
                    run.target_type,
                    run.target_ref,
                    run.connection_id,
                    run.input_hash,
                    run.state.value,
                    run.rule_version,
                )
                inserted_items = []
                for item in items:
                    item_row = await conn.fetchrow(
                        "INSERT INTO reconciliation_item "
                        "(run_id, entity_type, entity_key, internal_value, provider_value, "
                        " classification) "
                        "VALUES ($1, $2, $3, $4, $5, $6) RETURNING *",
                        run_row["id"],
                        item.entity_type,
                        item.entity_key,
                        item.internal_value,
                        item.provider_value,
                        item.classification.value,
                    )
                    inserted_items.append(_row_to_item(item_row))
        except asyncpg.UniqueViolationError as exc:
            existing = await self.get_run_by_input_hash(
                run.target_ref, run.input_hash, run.tenant_id
            )
            if existing is None:
                raise
            raise ReconciliationRunAlreadyExists(existing) from exc
        return ReconciliationRun(
            id=run_row["id"],
            tenant_id=run_row["tenant_id"],
            target_type=run_row["target_type"],
            target_ref=run_row["target_ref"],
            connection_id=run_row["connection_id"],
            input_hash=run_row["input_hash"],
            state=RunState(run_row["state"]),
            rule_version=run_row["rule_version"],
            items=tuple(inserted_items),
            created_at=run_row["created_at"],
        )

    async def get_state(self, target_ref: UUID) -> ReconciliationState | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM reconciliation_state WHERE target_ref = $1", target_ref
            )
        return _row_to_state(row) if row is not None else None

    async def list_states(self, tenant_id: UUID) -> tuple[ReconciliationState, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM reconciliation_state WHERE tenant_id = $1 "
                "ORDER BY last_checked_at DESC",
                tenant_id,
            )
        return tuple(_row_to_state(row) for row in rows)

    async def upsert_state(self, state: ReconciliationState) -> ReconciliationState:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO reconciliation_state "
                "(target_ref, target_type, tenant_id, aggregate_status, last_healthy_at, "
                " last_checked_at, blocking_reason, revision, safety_control_id) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, 0, $8) "
                "ON CONFLICT (target_ref) DO UPDATE SET "
                " aggregate_status = EXCLUDED.aggregate_status, "
                " last_healthy_at = COALESCE(EXCLUDED.last_healthy_at, "
                "                             reconciliation_state.last_healthy_at), "
                " last_checked_at = EXCLUDED.last_checked_at, "
                " blocking_reason = EXCLUDED.blocking_reason, "
                " safety_control_id = COALESCE(EXCLUDED.safety_control_id, "
                "                               reconciliation_state.safety_control_id), "
                " revision = reconciliation_state.revision + 1 "
                "RETURNING *",
                state.target_ref,
                state.target_type,
                state.tenant_id,
                state.aggregate_status.value,
                state.last_healthy_at,
                state.last_checked_at,
                state.blocking_reason,
                state.safety_control_id,
            )
        return _row_to_state(row)

    async def transition_state_status(
        self,
        target_ref: UUID,
        *,
        expected_revision: int,
        new_status: Classification,
        blocking_reason: str | None,
        resolved_by: UUID | None = None,
        resolution_reason: str | None = None,
    ) -> ReconciliationState:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE reconciliation_state "
                "SET aggregate_status = $3::varchar(20), blocking_reason = $4, "
                " revision = revision + 1, resolved_by = $5, resolution_reason = $6, "
                " resolved_at = CASE WHEN $3::varchar(20) = 'RESOLVED' "
                "                    THEN now() ELSE resolved_at END "
                "WHERE target_ref = $1 AND revision = $2 "
                "RETURNING *",
                target_ref,
                expected_revision,
                new_status.value,
                blocking_reason,
                resolved_by,
                resolution_reason,
            )
        if row is None:
            raise ConcurrencyConflictError(
                f"reconciliation_state.target_ref={target_ref}: revision {expected_revision}은 "
                "더 이상 최신이 아닙니다 — 다시 조회 후 시도하세요."
            )
        return _row_to_state(row)
