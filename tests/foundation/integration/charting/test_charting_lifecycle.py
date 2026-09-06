"""CH-5 통합·교차테넌트 테스트 — 실 Postgres(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.6
CH-5 DoD "낙관적 잠금 409, 타 테넌트 404"."""
from __future__ import annotations

from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.charting.adapters.postgres_repository import PostgresChartingRepository
from src.foundation.charting.application.create_layout import create_layout
from src.foundation.charting.application.delete_layout import delete_layout
from src.foundation.charting.application.errors import (
    ChartLayoutNotFoundError,
    CrossTenantChartLayoutAccessError,
)
from src.foundation.charting.application.get_drawings import get_drawings
from src.foundation.charting.application.get_layout import get_layout
from src.foundation.charting.application.list_layouts import list_layouts
from src.foundation.charting.application.put_drawings import put_drawings
from src.foundation.charting.application.update_layout import update_layout
from src.foundation.charting.domain.rules import DrawingValidationError
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresChartingRepository(pool)


async def _tenant(pool) -> object:
    return await create_test_user(pool)


def _sample_trendline() -> dict:
    return {
        "id": "d1",
        "kind": "trendline",
        "points": [{"time": 1, "price": 100.0}, {"time": 2, "price": 110.0}],
    }


async def test_create_layout_starts_with_empty_drawings(repo, pool):
    tenant_id = await _tenant(pool)
    layout = await create_layout(
        repo,
        tenant_id=tenant_id,
        owner_subject_id=tenant_id,
        name="my layout",
        layout_state={"symbol": "BTCUSDT", "timeframe": "1h"},
    )
    assert layout.revision == 0
    assert layout.name == "my layout"

    fetched = await get_layout(repo, tenant_id=tenant_id, layout_id=layout.id)
    assert fetched.id == layout.id

    document = await get_drawings(repo, tenant_id=tenant_id, layout_id=layout.id)
    assert document.schema_version == 1
    assert document.drawings == []
    assert document.revision == 0

    layouts = await list_layouts(repo, tenant_id=tenant_id)
    assert [layer.id for layer in layouts] == [layout.id]


async def test_get_layout_unknown_id_raises_not_found(repo, pool):
    tenant_id = await _tenant(pool)
    from uuid import uuid4

    with pytest.raises(ChartLayoutNotFoundError):
        await get_layout(repo, tenant_id=tenant_id, layout_id=uuid4())


async def test_get_layout_cross_tenant_raises_not_found_not_forbidden(repo, pool):
    """DoD "타 테넌트 404" — 존재는 하지만 남의 tenant 소유인 layout_id는
    403이 아니라 404다(자기 것과 남의 것을 구분하는 신호를 흘리지 않는다)."""
    owner_tenant = await _tenant(pool)
    other_tenant = await _tenant(pool)
    layout = await create_layout(
        repo,
        tenant_id=owner_tenant,
        owner_subject_id=owner_tenant,
        name="owner-only",
        layout_state={},
    )

    with pytest.raises(CrossTenantChartLayoutAccessError):
        await get_layout(repo, tenant_id=other_tenant, layout_id=layout.id)


async def test_update_layout_wrong_revision_raises_concurrency_conflict(repo, pool):
    """DoD "낙관적 잠금 409" — 먼저 읽은 revision이 이미 낡았으면
    ConcurrencyConflictError(전역 409 매핑)."""
    tenant_id = await _tenant(pool)
    layout = await create_layout(
        repo, tenant_id=tenant_id, owner_subject_id=tenant_id, name="v0", layout_state={}
    )

    updated = await update_layout(
        repo,
        tenant_id=tenant_id,
        layout_id=layout.id,
        expected_revision=layout.revision,
        name="v1",
        layout_state=None,
    )
    assert updated.revision == layout.revision + 1
    assert updated.name == "v1"

    with pytest.raises(ConcurrencyConflictError):
        await update_layout(
            repo,
            tenant_id=tenant_id,
            layout_id=layout.id,
            expected_revision=layout.revision,  # 이미 낡은 값 재사용
            name="v2-stale",
            layout_state=None,
        )


async def test_update_layout_cross_tenant_raises_not_found(repo, pool):
    owner_tenant = await _tenant(pool)
    other_tenant = await _tenant(pool)
    layout = await create_layout(
        repo, tenant_id=owner_tenant, owner_subject_id=owner_tenant, name="mine", layout_state={}
    )

    with pytest.raises(CrossTenantChartLayoutAccessError):
        await update_layout(
            repo,
            tenant_id=other_tenant,
            layout_id=layout.id,
            expected_revision=layout.revision,
            name="hijacked",
            layout_state=None,
        )


async def test_delete_layout_cascades_drawing_set(repo, pool):
    tenant_id = await _tenant(pool)
    layout = await create_layout(
        repo, tenant_id=tenant_id, owner_subject_id=tenant_id, name="to-delete", layout_state={}
    )
    await delete_layout(repo, tenant_id=tenant_id, layout_id=layout.id)

    with pytest.raises(ChartLayoutNotFoundError):
        await get_layout(repo, tenant_id=tenant_id, layout_id=layout.id)
    assert await repo.get_drawing_set(layout.id) is None


async def test_put_drawings_round_trips_and_rejects_stale_revision(repo, pool):
    tenant_id = await _tenant(pool)
    layout = await create_layout(
        repo, tenant_id=tenant_id, owner_subject_id=tenant_id, name="drawn", layout_state={}
    )

    document = await put_drawings(
        repo,
        tenant_id=tenant_id,
        layout_id=layout.id,
        expected_revision=0,
        schema_version=1,
        drawings=[_sample_trendline()],
    )
    assert document.revision == 1
    assert document.drawings == [_sample_trendline()]

    fetched = await get_drawings(repo, tenant_id=tenant_id, layout_id=layout.id)
    assert fetched.drawings == [_sample_trendline()]

    with pytest.raises(ConcurrencyConflictError):
        await put_drawings(
            repo,
            tenant_id=tenant_id,
            layout_id=layout.id,
            expected_revision=0,  # 이미 1로 올라감 — 낡은 값
            schema_version=1,
            drawings=[],
        )


async def test_put_drawings_cross_tenant_raises_not_found(repo, pool):
    owner_tenant = await _tenant(pool)
    other_tenant = await _tenant(pool)
    layout = await create_layout(
        repo, tenant_id=owner_tenant, owner_subject_id=owner_tenant, name="mine", layout_state={}
    )

    with pytest.raises(CrossTenantChartLayoutAccessError):
        await put_drawings(
            repo,
            tenant_id=other_tenant,
            layout_id=layout.id,
            expected_revision=0,
            schema_version=1,
            drawings=[],
        )


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 2, "drawings": []},
        {"schema_version": 1, "drawings": [{"kind": "trendline"}]},
        {"schema_version": 1, "drawings": [{"id": "d1", "kind": "unknown-kind"}]},
        {"schema_version": 1, "drawings": [{"id": "d1", "kind": "horizontal-line"}]},
        {
            "schema_version": 1,
            "drawings": [{"id": "d1", "kind": "horizontal-line", "price": "not-a-number"}],
        },
        {
            "schema_version": 1,
            "drawings": [
                {"id": "dup", "kind": "vertical-line", "time": 1},
                {"id": "dup", "kind": "vertical-line", "time": 2},
            ],
        },
    ],
)
async def test_put_drawings_rejects_malformed_documents(repo, pool, document):
    tenant_id = await _tenant(pool)
    layout = await create_layout(
        repo, tenant_id=tenant_id, owner_subject_id=tenant_id, name="strict", layout_state={}
    )

    with pytest.raises(DrawingValidationError):
        await put_drawings(
            repo,
            tenant_id=tenant_id,
            layout_id=layout.id,
            expected_revision=0,
            schema_version=document["schema_version"],
            drawings=document["drawings"],
        )
