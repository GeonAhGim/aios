"""ChartingRepository의 asyncpg 구현 — `chart_layout`/`chart_drawing_set`.

Spec: 105번(동시성 표준). `create_layout()`이 두 테이블을 하나의 트랜잭션으로
묶는 유일한 쓰기 경로다 — 그 결과 `put_drawings()`는 `chart_drawing_set`
행이 항상 존재한다고 가정하고 조건부 UPDATE 하나만 한다(INSERT-or-UPDATE
분기의 first-write 경합을 설계로 없앤다, ports/repository.py 참조)."""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.charting.domain.models import ChartDrawingSet, ChartLayout


def _row_to_layout(row: asyncpg.Record) -> ChartLayout:
    return ChartLayout(
        id=row["id"],
        tenant_id=row["tenant_id"],
        owner_subject_id=row["owner_subject_id"],
        name=row["name"],
        layout_state=json.loads(row["layout_state"]),
        revision=row["revision"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_drawing_set(row: asyncpg.Record) -> ChartDrawingSet:
    return ChartDrawingSet(
        layout_id=row["layout_id"],
        schema_version=row["schema_version"],
        drawings=tuple(json.loads(row["drawings"])),
        revision=row["revision"],
        updated_at=row["updated_at"],
    )


class PostgresChartingRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_layout(
        self,
        *,
        tenant_id: UUID,
        owner_subject_id: UUID,
        name: str,
        layout_state: dict[str, Any],
    ) -> ChartLayout:
        async with self._pool.acquire() as conn, conn.transaction():
            layout_row = await conn.fetchrow(
                "INSERT INTO chart_layout "
                "(tenant_id, owner_subject_id, name, layout_state) "
                "VALUES ($1, $2, $3, $4::jsonb) RETURNING *",
                tenant_id,
                owner_subject_id,
                name,
                json.dumps(layout_state),
            )
            await conn.execute(
                "INSERT INTO chart_drawing_set (layout_id, schema_version, drawings) "
                "VALUES ($1, 1, $2::jsonb)",
                layout_row["id"],
                json.dumps([]),
            )
        return _row_to_layout(layout_row)

    async def get_layout(self, layout_id: UUID) -> ChartLayout | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM chart_layout WHERE id = $1", layout_id)
        return _row_to_layout(row) if row is not None else None

    async def list_layouts(self, tenant_id: UUID) -> tuple[ChartLayout, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM chart_layout WHERE tenant_id = $1 ORDER BY created_at", tenant_id
            )
        return tuple(_row_to_layout(row) for row in rows)

    async def update_layout(
        self,
        layout_id: UUID,
        *,
        expected_revision: int,
        name: str | None,
        layout_state: dict[str, Any] | None,
    ) -> ChartLayout:
        # `conditional_write.conditional_update()`를 쓰지 않는다 — name/
        # layout_state가 각각 선택적 부분 갱신이고 jsonb 캐스트도 필요해
        # 공용 헬퍼의 고정 `set_values` 바인딩 방식으로는 표현할 수 없다.
        assignments = ["revision = revision + 1", "updated_at = now()"]
        params: list[Any] = [layout_id, expected_revision]
        if name is not None:
            params.append(name)
            assignments.append(f"name = ${len(params)}")
        if layout_state is not None:
            params.append(json.dumps(layout_state))
            assignments.append(f"layout_state = ${len(params)}::jsonb")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE chart_layout SET " + ", ".join(assignments) + " "  # noqa: S608
                "WHERE id = $1 AND revision = $2 RETURNING *",
                *params,
            )
        if row is None:
            raise ConcurrencyConflictError(
                f"chart_layout.id={layout_id}: revision {expected_revision}은 "
                "더 이상 최신이 아닙니다 — 다시 조회 후 시도하세요."
            )
        return _row_to_layout(row)

    async def delete_layout(self, layout_id: UUID) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM chart_layout WHERE id = $1", layout_id)

    async def get_drawing_set(self, layout_id: UUID) -> ChartDrawingSet | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM chart_drawing_set WHERE layout_id = $1", layout_id
            )
        return _row_to_drawing_set(row) if row is not None else None

    async def put_drawings(
        self,
        layout_id: UUID,
        *,
        expected_revision: int,
        schema_version: int,
        drawings: tuple[dict[str, Any], ...],
    ) -> ChartDrawingSet:
        # `conditional_write.conditional_update()`를 쓰지 않는다 — 그 헬퍼는
        # `set_values`를 캐스트 없는 `$N`으로 바인딩하는데, jsonb 컬럼에
        # `now()` 리터럴까지 같이 넣어야 해서(update_layout()과 동일 이유)
        # 여기서 직접 조건부 UPDATE 문을 쓴다.
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE chart_drawing_set SET schema_version = $3, drawings = $4::jsonb, "
                " revision = revision + 1, updated_at = now() "
                "WHERE layout_id = $1 AND revision = $2 RETURNING *",
                layout_id,
                expected_revision,
                schema_version,
                json.dumps(list(drawings)),
            )
        if row is None:
            raise ConcurrencyConflictError(
                f"chart_drawing_set.layout_id={layout_id}: revision {expected_revision}은 "
                "더 이상 최신이 아닙니다 — 다시 조회 후 시도하세요."
            )
        return _row_to_drawing_set(row)
